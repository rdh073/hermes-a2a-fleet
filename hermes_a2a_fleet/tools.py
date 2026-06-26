"""Hermes tools exposed by the plugin (toolset ``a2a_fleet``).

  - a2a_fleet_discover()  -> refresh discovery, list reachable A2A peers
  - a2a_fleet_list()      -> show the cached fleet (no network)
  - a2a_fleet_broadcast() -> send one task to many peers IN PARALLEL, aggregate

These depend only on the ``Registry`` abstraction and the ``fan_out`` function;
they know nothing about mDNS vs tailnet. The registry is built once (lazily) at
the composition root in registry.build_registry().
"""

from __future__ import annotations

import logging
from typing import Any

from .client import A2AClient
from .config import FleetConfig, load_fleet_config
from .fanout import fan_out, summarize
from .registry import Registry, build_registry

logger = logging.getLogger(__name__)

_registry: Registry | None = None
_transport: A2AClient | None = None
_config: FleetConfig | None = None


def _components() -> tuple[Registry, A2AClient, FleetConfig]:
    """Lazily build + cache the (registry, transport, config) triple.

    The composition root: ONE A2AClient is created here and shared as both the
    registry's verification client and the fan-out transport, so the tools layer
    holds an explicit transport instead of reaching into Registry internals.
    """
    global _registry, _transport, _config
    if _registry is None:
        _config = load_fleet_config()
        _transport = A2AClient(default_timeout=_config.timeout)
        _registry = build_registry(_config, client=_transport)
    return _registry, _transport, _config


def set_components(registry: Registry, transport: A2AClient, config: FleetConfig) -> None:
    """Inject components (tests / host app)."""
    global _registry, _transport, _config
    _registry, _transport, _config = registry, transport, config


def _resolve_workers(requested, n_agents: int, cfg_max: int) -> int:
    """Thread-pool size: never above the configured cap, the request, or the
    agent count; never below 1. (Fixes broadcast ignoring max_concurrency.)"""
    cap = cfg_max if cfg_max and cfg_max > 0 else n_agents
    if requested:
        try:
            cap = min(cap, int(requested))
        except (TypeError, ValueError):
            pass
    return max(1, min(cap, n_agents))


def _resolve_deadline(requested, cfg_deadline):
    """Per-call deadline override; fall back to the configured one. A positive
    number wins; anything else (None / garbage / <=0) keeps the config value."""
    if requested is not None:
        try:
            d = float(requested)
            return d if d > 0 else cfg_deadline
        except (TypeError, ValueError):
            return cfg_deadline
    return cfg_deadline


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def a2a_fleet_discover(args: dict | None = None, **_: Any) -> str:
    reg = _components()[0]
    agents = reg.list(refresh=True)
    if not agents:
        return (
            "No A2A peers discovered. Check that sources are enabled "
            f"({', '.join(reg.source_names) or 'none'}) and that peers serve an agent card."
        )
    lines = [f"Discovered {len(agents)} A2A peer(s):"]
    for a in agents:
        caps = ", ".join(a.capabilities) if a.capabilities else "-"
        lines.append(f"  - {a.name}  [{a.source}]  {a.url}  caps: {caps}")
    return "\n".join(lines)


def a2a_fleet_list(args: dict | None = None, **_: Any) -> str:
    reg = _components()[0]
    agents = reg.list()
    if not agents:
        return "Fleet is empty (run a2a_fleet_discover first)."
    return "\n".join(f"  - {a.name}  {a.url}  caps: {', '.join(a.capabilities) or '-'}" for a in agents)


def a2a_fleet_broadcast(args: dict, **_: Any) -> str:
    message = str(args.get("message") or args.get("text") or "").strip()
    if not message:
        return "Error: 'message' is required."
    capability = str(args.get("capability") or "").strip() or None
    names = args.get("agents")
    mode = str(args.get("mode") or "collect").strip().lower()
    if mode not in ("collect", "first"):
        mode = "collect"

    reg, transport, cfg = _components()
    agents = reg.list(capability=capability)
    if isinstance(names, list) and names:
        wanted = {str(n) for n in names}
        agents = [a for a in agents if a.name in wanted]
    if not agents:
        scope = f" matching capability '{capability}'" if capability else ""
        return f"No agents{scope} to broadcast to. Run a2a_fleet_discover or check filters."

    workers = _resolve_workers(args.get("max_concurrency"), len(agents), cfg.max_concurrency)
    timeout = int(args.get("timeout") or cfg.timeout)
    deadline = _resolve_deadline(args.get("deadline"), cfg.deadline)
    results = fan_out(
        agents,
        message,
        transport,
        max_workers=workers,
        timeout=timeout,
        context_id=str(args.get("context_id") or ""),
        deadline=deadline,
        stop_on_first=(mode == "first"),
    )
    return summarize(results, mode=mode)


# --------------------------------------------------------------------------
# Schemas + registration
# --------------------------------------------------------------------------

_SCHEMAS: dict[str, dict] = {
    "a2a_fleet_discover": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_discover",
            "description": (
                "Discover all reachable A2A peer agents on the fleet (via mDNS on "
                "the LAN and/or a tailnet sweep) and list them with their "
                "capabilities. Run this before broadcasting to refresh the roster."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "a2a_fleet_list": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_list",
            "description": "List the currently-known A2A fleet peers from cache (no network call).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "a2a_fleet_broadcast": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_broadcast",
            "description": (
                "Send one task to MANY A2A peers in parallel and aggregate the "
                "replies (scatter-gather). Optionally filter peers by 'capability' "
                "or an explicit 'agents' name list. mode='collect' returns every "
                "reply; mode='first' returns the fastest successful one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The task to send every selected peer."},
                    "capability": {"type": "string", "description": "Optional: only peers advertising this skill tag."},
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: explicit peer names to target (overrides capability filter scope).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["collect", "first"],
                        "description": (
                            "collect = every reply; first = return as soon as one peer "
                            "succeeds (the rest are abandoned)."
                        ),
                    },
                    "deadline": {
                        "type": "number",
                        "description": (
                            "Optional whole-call deadline (seconds). Peers not finished by "
                            "then are reported as 'deadline' instead of stalling the batch."
                        ),
                    },
                    "context_id": {"type": "string", "description": "Optional A2A context id to continue an exchange."},
                },
                "required": ["message"],
            },
        },
    },
}

_HANDLERS = {
    "a2a_fleet_discover": a2a_fleet_discover,
    "a2a_fleet_list": a2a_fleet_list,
    "a2a_fleet_broadcast": a2a_fleet_broadcast,
}


def register_tools(ctx) -> None:
    """Register the three fleet tools in the ``a2a_fleet`` toolset."""
    for name, schema in _SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="a2a_fleet",
            schema=schema,
            handler=_HANDLERS[name],
            description=schema["function"]["description"],
            emoji="\U0001f578",  # spider web — the mesh
        )
