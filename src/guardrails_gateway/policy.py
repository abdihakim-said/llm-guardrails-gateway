"""Which tools each agent may call, and which need a human to approve.

Deterministic and deny-by-default: the model can ask for any tool, but only
this policy decides whether the call runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass
class AgentPolicy:
    allowed: set[str] = field(default_factory=set)
    needs_approval: set[str] = field(default_factory=set)


class ToolPolicy:
    def __init__(self, agents: dict[str, AgentPolicy] | None = None):
        self._agents = dict(agents or {})

    def add_agent(self, agent: str, allowed: set[str], needs_approval: set[str] = frozenset()) -> None:
        unknown = set(needs_approval) - set(allowed)
        if unknown:
            raise ValueError(f"tools need approval but are not allowed: {sorted(unknown)}")
        self._agents[agent] = AgentPolicy(set(allowed), set(needs_approval))

    def decide(self, agent: str, tool: str) -> Decision:
        policy = self._agents.get(agent)
        if policy is None or tool not in policy.allowed:
            return Decision.DENY
        if tool in policy.needs_approval:
            return Decision.REQUIRE_APPROVAL
        return Decision.ALLOW
