"""Static discovery — peers declared in config.

Reads two config maps and merges them. On a URL collision the FIRST-listed
wins (the registry dedupes keeping the first occurrence), so this source emits
``a2a_fleet.agents`` BEFORE ``a2a_agents`` — the plugin's own list takes
precedence over the reused upstream entries:
  - ``a2a_fleet.agents`` — this plugin's own list (wins on collision).
  - ``a2a_agents``      — reuse the upstream Hermes ``a2a`` plugin's peers, so a
    fleet sweep includes anything you already configured there.

Each entry: ``{url, auth?: {type, token}, capabilities?: [..], description?}``.
"""

from __future__ import annotations

from ..types import AgentRef
from .base import DiscoverySource


def _entries_to_refs(mapping: dict, source: str) -> list[AgentRef]:
    refs: list[AgentRef] = []
    for name, entry in (mapping or {}).items():
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "").strip()
        if not url:
            continue
        caps = entry.get("capabilities") or []
        refs.append(
            AgentRef(
                name=str(name),
                url=url,
                description=str(entry.get("description", "")),
                capabilities=tuple(str(c) for c in caps if isinstance(c, (str,))),
                auth=entry.get("auth") or {},
                source=source,
            )
        )
    return refs


class StaticSource(DiscoverySource):
    name = "static"

    def __init__(self, cfg) -> None:
        self._cfg = cfg

    def discover(self) -> list[AgentRef]:
        raw = self._cfg.raw or {}
        refs = _entries_to_refs(raw.get("a2a_fleet", {}).get("agents", {}), "static")
        refs += _entries_to_refs(raw.get("a2a_agents", {}), "static:a2a")
        return refs
