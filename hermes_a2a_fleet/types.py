"""Shared value types for the A2A fleet plugin.

Kept dependency-free (stdlib dataclasses only) so every other module — sources,
registry, fan-out — can depend on these shapes without importing each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentRef:
    """A discovered A2A peer.

    ``url`` is the base (e.g. ``http://100.64.0.5:9900``); ``rpc_url`` is where
    JSON-RPC ``message/send`` is posted — taken from the agent card's ``url``
    when present, else the base. ``capabilities`` are the coarse skill tags the
    card advertises, used to filter a broadcast to relevant peers.
    """

    name: str
    url: str
    rpc_url: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()
    auth: dict = field(default_factory=dict, compare=False)
    source: str = "unknown"

    def with_rpc(self, rpc_url: str) -> AgentRef:
        return AgentRef(
            name=self.name,
            url=self.url,
            rpc_url=rpc_url or self.url,
            description=self.description,
            capabilities=self.capabilities,
            auth=self.auth,
            source=self.source,
        )

    def matches(self, capability: str | None) -> bool:
        if not capability:
            return True
        cap = capability.lower()
        return any(cap == c.lower() or cap in c.lower() for c in self.capabilities)


@dataclass
class FanResult:
    """One peer's outcome in a parallel broadcast.

    ``terminal`` is the explicit terminal-reason vocabulary used for accounting
    — every selected peer ends in exactly one of: ``ok`` (succeeded), ``error``
    (transport / JSON-RPC error), ``failed`` (RPC ok but the A2A task failed/
    canceled), ``deadline`` (whole-call deadline reached before it finished), or
    ``abandoned`` (dropped after an early first-success exit).
    """

    agent: str
    ok: bool
    reply: str = ""
    error: str = ""
    elapsed_ms: int = 0
    context_id: str = ""
    terminal: str = ""
