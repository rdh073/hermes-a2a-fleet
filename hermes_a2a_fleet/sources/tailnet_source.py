"""Tailnet discovery — the cross-network counterpart to mDNS.

mDNS is link-local and does not cross a WireGuard mesh, so for peers reachable
only over Tailscale/Headscale we enumerate tailnet nodes and propose one
candidate per (peer, probe-port). The Registry then verifies each by fetching
its agent card — so a node that is online but not running A2A is dropped
naturally.

The tailnet node list comes from ``tailscale status --json`` (the binary is
discovered at runtime; override with ``TAILSCALE_BIN``). For testability the
status provider is injectable, so unit tests never shell out.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Callable

from ..types import AgentRef
from .base import DiscoverySource

logger = logging.getLogger(__name__)


def _default_status_provider() -> dict:
    binary = os.getenv("TAILSCALE_BIN") or shutil.which("tailscale")
    if not binary:
        logger.info("tailnet: 'tailscale' binary not found — skipping (set TAILSCALE_BIN)")
        return {}
    try:
        out = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return json.loads(out.stdout or "{}")
    except Exception as e:
        logger.info("tailnet: 'tailscale status' failed (%s) — skipping", e)
        return {}


def _peers_from_status(status: dict) -> list[tuple[str, str]]:
    """Return (display_name, ip) for each peer that has a usable IPv4-ish addr."""
    peers: list[tuple[str, str]] = []
    for node in (status.get("Peer") or {}).values():
        if not isinstance(node, dict):
            continue
        ips = node.get("TailscaleIPs") or []
        ip = next((a for a in ips if ":" not in a), ips[0] if ips else None)
        if not ip:
            continue
        name = (node.get("DNSName") or node.get("HostName") or ip).split(".")[0]
        peers.append((name, ip))
    # include self too, so a single-node tailnet can still self-discover
    self_node = status.get("Self")
    if isinstance(self_node, dict):
        ips = self_node.get("TailscaleIPs") or []
        ip = next((a for a in ips if ":" not in a), ips[0] if ips else None)
        if ip:
            name = (self_node.get("DNSName") or self_node.get("HostName") or ip).split(".")[0]
            peers.append((name, ip))
    return peers


class TailnetSource(DiscoverySource):
    name = "tailnet"

    def __init__(self, cfg, status_provider: Callable[[], dict] = _default_status_provider) -> None:
        self._cfg = cfg
        self._status_provider = status_provider

    def discover(self) -> list[AgentRef]:
        status = self._status_provider() or {}
        refs: list[AgentRef] = []
        for name, ip in _peers_from_status(status):
            for port in self._cfg.probe_ports:
                refs.append(
                    AgentRef(
                        name=f"{name}:{port}" if len(self._cfg.probe_ports) > 1 else name,
                        url=f"http://{ip}:{port}",
                        source="tailnet",
                    )
                )
        return refs
