"""The gateway: every model call and tool call goes through here.

Model call:  price check -> budget reservation -> provider -> settle actual
             cost -> redacted audit entry -> OTel span/metrics.
Tool call:   policy decision -> (human approval) -> execute -> audit entry.

None of these decisions are made by the model.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from . import telemetry
from .approvals import ApprovalQueue
from .audit import AuditLog
from .budget import BudgetLedger
from .policy import Decision, ToolPolicy
from .pricing import PriceTable
from .providers import Completion, Provider
from .redact import redact


class ToolDenied(Exception):
    pass


@dataclass
class PendingApproval:
    approval_id: str
    tool: str


class Gateway:
    def __init__(self, provider: Provider, budgets: BudgetLedger, audit: AuditLog,
                 prices: PriceTable | None = None, policy: ToolPolicy | None = None,
                 approvals: ApprovalQueue | None = None):
        self.provider = provider
        self.budgets = budgets
        self.audit = audit
        self.prices = prices or PriceTable()
        self.policy = policy or ToolPolicy()
        self.approvals = approvals or ApprovalQueue()
        self._tools: dict[str, Callable[..., Any]] = {}

    # ---- model calls -------------------------------------------------------

    def complete(self, team: str, model: str, messages: list[dict], max_tokens: int = 1024,
                 system: str = "") -> Completion:
        self.prices.price(model)  # fail fast on an unpriced model
        est_in = self.provider.count_input_tokens(model, system, messages)
        reservation = self.budgets.reserve(team, self.prices.max_cost(model, est_in, max_tokens))
        start = time.monotonic()
        try:
            with telemetry.model_call_span(self.provider.name, model, team) as span:
                result = self.provider.complete(model, system, messages, max_tokens)
                # Price by the model that served the call (a fallback may differ).
                cost = self.prices.cost(result.model, result.usage)
                telemetry.record_usage(span, self.provider.name, result.model, team,
                                       result.usage.input_tokens, result.usage.output_tokens,
                                       cost, time.monotonic() - start)
        except Exception as exc:
            self.budgets.release(reservation)
            self.audit.record("model_call_failed", team=team, model=model, error=type(exc).__name__)
            raise
        self.budgets.settle(reservation, cost)
        self.audit.record(
            "model_call", team=team, requested_model=model, served_model=result.model,
            input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
            cost_usd=str(cost.quantize(Decimal("0.000001"))), refused=result.refused,
            prompt=redact(str(messages[-1]["content"]))[:500], response=redact(result.text)[:500],
        )
        return result

    # ---- tool calls --------------------------------------------------------

    def register_tool(self, name: str, fn: Callable[..., Any]) -> None:
        self._tools[name] = fn

    def call_tool(self, agent: str, tool: str, args: dict, approval_id: str | None = None):
        decision = self.policy.decide(agent, tool)
        if decision is Decision.DENY or tool not in self._tools:
            self.audit.record("tool_denied", agent=agent, tool=tool, args=redact(str(args)))
            raise ToolDenied(f"agent {agent!r} may not call {tool!r}")

        approved_by = None
        if decision is Decision.REQUIRE_APPROVAL:
            if approval_id is None:
                aid = self.approvals.request(agent, tool, args)
                self.audit.record("tool_approval_requested", agent=agent, tool=tool,
                                  approval_id=aid, args=redact(str(args)))
                return PendingApproval(aid, tool)
            approved_by = self.approvals.consume(approval_id, agent, tool, args)

        result = self._tools[tool](**args)
        self.audit.record("tool_executed", agent=agent, tool=tool, args=redact(str(args)),
                          approval_id=approval_id, approved_by=approved_by)
        return result
