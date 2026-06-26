"""Plugin configuration — Hermes config.yaml + env, with safe defaults.

Everything is optional. With no config at all the plugin enables the ``static``
and ``tailnet`` sources, a 30s call timeout, and 10-way concurrency — sensible
for a small fleet reachable over a tailnet.

Config block (``a2a_fleet`` in Hermes config.yaml)::

    a2a_fleet:
      sources: [static, tailnet, mdns]
      timeout: 30
      max_concurrency: 10
      probe_ports: [9900]
      cache_ttl: 60
      agents:
        researcher: { url: "http://100.64.0.5:9900", auth: { type: bearer, token: "..." } }
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _clamp(value, lo, hi, default):
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


@dataclass
class FleetConfig:
    sources: list[str] = field(default_factory=lambda: ["static", "tailnet"])
    timeout: int = 30
    verify_timeout: int = 8
    max_concurrency: int = 10
    probe_ports: list[int] = field(default_factory=lambda: [9900])
    cache_ttl: float = 60.0
    mdns_timeout: float = 2.0
    deadline: float | None = None  # whole-call broadcast cap (s); None = rely on per-peer timeout
    drop_unreachable: bool = True
    auth_default: str = ""  # bearer token for discovered peers without a host-specific one
    auth_hosts: dict = field(default_factory=dict)  # host[:port] -> bearer token
    raw: dict = field(default_factory=dict)


def _load_hermes_config() -> dict:
    try:
        from hermes_cli.config import load_config
        return load_config() or {}
    except Exception:
        return {}


def load_fleet_config(raw: dict | None = None) -> FleetConfig:
    """Build a FleetConfig from Hermes config (or an injected dict for tests)."""
    raw = raw if raw is not None else _load_hermes_config()
    block = raw.get("a2a_fleet", {}) if isinstance(raw, dict) else {}

    sources = block.get("sources")
    if not isinstance(sources, list) or not sources:
        env_sources = os.getenv("A2A_FLEET_SOURCES")
        sources = [s.strip() for s in env_sources.split(",") if s.strip()] if env_sources else ["static", "tailnet"]

    ports = block.get("probe_ports")
    if not isinstance(ports, list) or not ports:
        ports = [9900]
    probe_ports = [p for p in (_clamp(p, 1, 65535, 0) for p in ports) if p]

    deadline = block.get("deadline")
    if deadline is not None:
        try:
            deadline = float(deadline)
            if deadline <= 0:
                deadline = None
        except (TypeError, ValueError):
            deadline = None

    auth_block = block.get("auth") if isinstance(block.get("auth"), dict) else {}
    auth_default = str(auth_block.get("default") or "")
    auth_hosts = {str(k): str(v) for k, v in (auth_block.get("hosts") or {}).items() if v}

    return FleetConfig(
        sources=[str(s) for s in sources],
        timeout=_clamp(block.get("timeout", 30), 1, 600, 30),
        verify_timeout=_clamp(block.get("verify_timeout", 8), 1, 60, 8),
        max_concurrency=_clamp(block.get("max_concurrency", 10), 1, 100, 10),
        probe_ports=probe_ports or [9900],
        cache_ttl=float(_clamp(block.get("cache_ttl", 60), 0, 86400, 60)),
        mdns_timeout=float(_clamp(block.get("mdns_timeout", 2), 1, 30, 2)),
        deadline=deadline,
        drop_unreachable=bool(block.get("drop_unreachable", True)),
        auth_default=auth_default,
        auth_hosts=auth_hosts,
        raw=raw if isinstance(raw, dict) else {},
    )
