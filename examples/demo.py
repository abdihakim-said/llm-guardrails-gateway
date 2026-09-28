"""Offline walkthrough: budgets, redaction, a denied tool and a human approval.

    python examples/demo.py
"""

import tempfile
from decimal import Decimal
from pathlib import Path

from guardrails_gateway import (
    AuditLog,
    BudgetExceeded,
    BudgetLedger,
    FakeProvider,
    Gateway,
    PendingApproval,
    ToolDenied,
    ToolPolicy,
    verify,
)

work = Path(tempfile.mkdtemp())
policy = ToolPolicy()
policy.add_agent("support-bot", allowed={"lookup_order", "refund_order"}, needs_approval={"refund_order"})
gw = Gateway(FakeProvider(output_tokens=200), BudgetLedger(limits={"support": Decimal("0.02")}),
             AuditLog(work / "audit.jsonl"), policy=policy)
gw.register_tool("lookup_order", lambda order_id: {"id": order_id, "status": "shipped"})
gw.register_tool("refund_order", lambda order_id, amount: {"refunded": amount})

print("1. Model calls until the team budget ($0.02/month) runs out")
calls = 0
try:
    while True:
        gw.complete("support", "fake-model",
                    [{"role": "user", "content": "Customer jo@example.com asks about order 42"}],
                    max_tokens=200)
        calls += 1
except BudgetExceeded as e:
    print(f"   {calls} calls ran, then blocked before reaching the provider: {e}")

print("2. A tool the agent was never given")
try:
    gw.call_tool("support-bot", "delete_customer", {"customer_id": "c1"})
except ToolDenied as e:
    print(f"   denied: {e}")

print("3. A refund needs a human")
args = {"order_id": "42", "amount": 40}
pending = gw.call_tool("support-bot", "refund_order", args)
assert isinstance(pending, PendingApproval)
print(f"   parked as {pending.approval_id}; approving as 'alice'")
gw.approvals.decide(pending.approval_id, approve=True, decided_by="alice")
print(f"   result: {gw.call_tool('support-bot', 'refund_order', args, approval_id=pending.approval_id)}")

print("4. Audit log")
ok, n = verify(work / "audit.jsonl")
print(f"   {n} entries, hash chain {'intact' if ok else 'BROKEN'}; emails are stored as [EMAIL]")
print(f"   {work / 'audit.jsonl'}")
