from decimal import Decimal
from types import SimpleNamespace

import pytest

from guardrails_gateway import (
    AnthropicProvider,
    ApprovalError,
    AuditLog,
    BudgetExceeded,
    BudgetLedger,
    FakeProvider,
    Gateway,
    PendingApproval,
    PriceTable,
    ToolDenied,
    ToolPolicy,
    UnknownModelError,
    Usage,
    verify,
)
from guardrails_gateway.cli import main as cli
from guardrails_gateway.redact import is_valid_nhs_number, passes_luhn, redact


@pytest.fixture
def gw(tmp_path):
    budgets = BudgetLedger(limits={"search-team": Decimal("0.01")})
    policy = ToolPolicy()
    policy.add_agent("support-bot", allowed={"lookup_order", "refund_order"},
                     needs_approval={"refund_order"})
    g = Gateway(FakeProvider(output_tokens=100), budgets, AuditLog(tmp_path / "audit.jsonl"),
                policy=policy)
    g.register_tool("lookup_order", lambda order_id: {"id": order_id, "status": "shipped"})
    g.register_tool("refund_order", lambda order_id, amount: {"refunded": amount})
    g.register_tool("delete_customer", lambda customer_id: "deleted")
    return g


# ---- pricing ---------------------------------------------------------------

def test_cost_includes_cache_tokens():
    prices = PriceTable()
    # claude-sonnet-5: $2 in / $10 out per MTok
    usage = Usage(1_000_000, 100_000, cache_read_input_tokens=1_000_000,
                  cache_creation_input_tokens=1_000_000)
    # $2 input + $1 output + $0.20 cache read (0.1x) + $2.50 cache write (1.25x)
    assert prices.cost("claude-sonnet-5", usage) == Decimal("5.7")


def test_unknown_model_is_refused_not_guessed():
    with pytest.raises(UnknownModelError):
        PriceTable().cost("mystery-model", Usage(1, 1))


# ---- budgets ---------------------------------------------------------------

def test_budget_stops_calls_before_they_run(gw):
    msgs = [{"role": "user", "content": "hello"}]
    # fake-model worst case for 100 output tokens ~ $0.0015, so a $0.01 budget fits ~6 calls
    for _ in range(6):
        gw.complete("search-team", "fake-model", msgs, max_tokens=100)
    calls_before = len(gw.provider.calls)
    with pytest.raises(BudgetExceeded):
        for _ in range(5):
            gw.complete("search-team", "fake-model", msgs, max_tokens=100)
    assert gw.budgets.spent("search-team") <= Decimal("0.01")
    assert len(gw.provider.calls) - calls_before < 5  # the blocked call never reached the provider


def test_team_without_budget_is_denied(gw):
    with pytest.raises(BudgetExceeded):
        gw.complete("unknown-team", "fake-model", [{"role": "user", "content": "hi"}])
    assert gw.provider.calls == []


def test_failed_call_releases_reservation(gw):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    gw.provider.complete = boom
    with pytest.raises(RuntimeError):
        gw.complete("search-team", "fake-model", [{"role": "user", "content": "hi"}], max_tokens=100)
    assert gw.budgets.spent("search-team") == 0


# ---- redaction -------------------------------------------------------------

def test_redaction_of_personal_data_and_secrets():
    text = ("Email jo@example.co.uk, call 07700 900123, NHS 943 476 5919, "
            "card 4111 1111 1111 1111, NI AB 12 34 56 C, key sk-ant-abcdefghijklmnop")
    out = redact(text)
    for token in ("[EMAIL]", "[PHONE]", "[NHS_NUMBER]", "[CARD]", "[NI_NUMBER]", "[SECRET]"):
        assert token in out
    assert "jo@example" not in out and "4111" not in out


def test_checksums_avoid_false_positives():
    assert "QQ123456C" in redact("sample QQ123456C")  # QQ is not a valid NI prefix
    assert is_valid_nhs_number("943 476 5919")
    assert not is_valid_nhs_number("943 476 5918")
    assert passes_luhn("4111111111111111")
    assert "1234 5678 9012" in redact("order ref 1234 5678 9012")


def test_audit_log_never_stores_raw_personal_data(gw, tmp_path):
    gw.complete("search-team", "fake-model",
                [{"role": "user", "content": "my email is jo@example.com"}], max_tokens=10)
    log = (tmp_path / "audit.jsonl").read_text()
    assert "jo@example.com" not in log and "[EMAIL]" in log


# ---- tool policy and approvals ----------------------------------------------

def test_unlisted_tool_is_denied(gw):
    with pytest.raises(ToolDenied):
        gw.call_tool("support-bot", "delete_customer", {"customer_id": "c1"})


def test_unknown_agent_is_denied(gw):
    with pytest.raises(ToolDenied):
        gw.call_tool("other-bot", "lookup_order", {"order_id": "o1"})


def test_safe_tool_runs_directly(gw):
    assert gw.call_tool("support-bot", "lookup_order", {"order_id": "o1"})["status"] == "shipped"


def test_risky_tool_waits_for_a_human(gw):
    args = {"order_id": "o1", "amount": 40}
    pending = gw.call_tool("support-bot", "refund_order", args)
    assert isinstance(pending, PendingApproval)

    with pytest.raises(ApprovalError):  # not approved yet
        gw.call_tool("support-bot", "refund_order", args, approval_id=pending.approval_id)

    gw.approvals.decide(pending.approval_id, approve=True, decided_by="alice")
    assert gw.call_tool("support-bot", "refund_order", args,
                        approval_id=pending.approval_id) == {"refunded": 40}

    with pytest.raises(ApprovalError):  # single use
        gw.call_tool("support-bot", "refund_order", args, approval_id=pending.approval_id)


def test_approval_cannot_be_reused_for_different_arguments(gw):
    pending = gw.call_tool("support-bot", "refund_order", {"order_id": "o1", "amount": 5})
    gw.approvals.decide(pending.approval_id, approve=True, decided_by="alice")
    with pytest.raises(ApprovalError):
        gw.call_tool("support-bot", "refund_order", {"order_id": "o1", "amount": 5000},
                     approval_id=pending.approval_id)


def test_rejected_approval_blocks_the_call(gw):
    args = {"order_id": "o1", "amount": 40}
    pending = gw.call_tool("support-bot", "refund_order", args)
    gw.approvals.decide(pending.approval_id, approve=False, decided_by="bob")
    with pytest.raises(ApprovalError):
        gw.call_tool("support-bot", "refund_order", args, approval_id=pending.approval_id)


def test_policy_rejects_approval_for_disallowed_tool():
    with pytest.raises(ValueError):
        ToolPolicy().add_agent("a", allowed={"x"}, needs_approval={"y"})


# ---- audit chain -----------------------------------------------------------

def test_audit_chain_detects_tampering(gw, tmp_path):
    gw.call_tool("support-bot", "lookup_order", {"order_id": "o1"})
    gw.call_tool("support-bot", "lookup_order", {"order_id": "o2"})
    path = tmp_path / "audit.jsonl"
    assert verify(path) == (True, 2)
    path.write_text(path.read_text().replace("o1", "o9"))
    assert verify(path)[0] is False


def test_cli_approve_and_verify(tmp_path, capsys):
    from guardrails_gateway import ApprovalQueue

    db = str(tmp_path / "approvals.db")
    aid = ApprovalQueue(db).request("bot", "refund_order", {"amount": 1})
    assert cli(["approve", "--db", db, aid, "--by", "alice"]) == 0
    assert cli(["approve", "--db", db, aid, "--by", "alice"]) == 1  # already decided
    log = tmp_path / "a.jsonl"
    AuditLog(log).record("x")
    assert cli(["verify", str(log)]) == 0


# ---- Anthropic adapter (mocked client, no network) --------------------------

def _fake_client(stop_reason="end_turn", model="claude-opus-5"):
    usage = SimpleNamespace(input_tokens=12, output_tokens=7, cache_read_input_tokens=None,
                            cache_creation_input_tokens=None)
    response = SimpleNamespace(stop_reason=stop_reason, model=model, usage=usage,
                               content=[SimpleNamespace(type="text", text="hi")])
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return response

    client = SimpleNamespace(
        beta=SimpleNamespace(messages=SimpleNamespace(create=create)),
        messages=SimpleNamespace(count_tokens=lambda **k: SimpleNamespace(input_tokens=12)),
    )
    return client, seen


def test_anthropic_adapter_maps_usage_and_enables_fallback():
    client, seen = _fake_client()
    out = AnthropicProvider(client).complete("claude-opus-5", "", [{"role": "user", "content": "x"}], 64)
    assert out.text == "hi" and out.usage == Usage(12, 7) and not out.refused
    assert seen["fallbacks"] == "default"
    assert seen["betas"] == ["server-side-fallback-2026-07-01"]


def test_anthropic_adapter_reports_refusal_without_reading_content():
    client, _ = _fake_client(stop_reason="refusal")
    out = AnthropicProvider(client).complete("claude-opus-5", "", [{"role": "user", "content": "x"}], 64)
    assert out.refused and out.text == ""


def test_cost_uses_the_model_that_served_the_call(tmp_path):
    client, _ = _fake_client(model="claude-opus-4-8")  # e.g. served by a fallback
    budgets = BudgetLedger(limits={"t": Decimal("1")})
    g = Gateway(AnthropicProvider(client), budgets, AuditLog(tmp_path / "a.jsonl"))
    g.complete("t", "claude-opus-5", [{"role": "user", "content": "x"}], max_tokens=64)
    assert '"served_model": "claude-opus-4-8"' in (tmp_path / "a.jsonl").read_text()


def test_unpriced_fallback_model_is_charged_the_reservation(tmp_path):
    client, _ = _fake_client(model="some-unpriced-fallback")
    budgets = BudgetLedger(limits={"t": Decimal("1")})
    g = Gateway(AnthropicProvider(client), budgets, AuditLog(tmp_path / "a.jsonl"))
    g.complete("t", "claude-opus-5", [{"role": "user", "content": "x"}], max_tokens=64)
    assert budgets.spent("t") > 0
