"""hermes-a2a-fleet — multi-agent A2A discovery + parallel fan-out for Hermes.

Complements the upstream Hermes ``a2a`` platform plugin (single-peer
discover/call) with the FLEET side: auto-discovery of many A2A peers (mDNS on
the LAN, tailnet sweep across networks) behind one swappable Registry, plus a
parallel broadcast tool that scatter-gathers a task across all of them.

Zero core edits — everything registers through the public ``ctx`` surface
(``ctx.register_tool``), exactly like the upstream a2a plugin.
"""

from __future__ import annotations

__all__ = ["register"]
__version__ = "0.1.0"


def register(ctx) -> None:
    """Plugin entry point — called by the Hermes plugin system on load.

    Deliberately does NOT swallow registration errors. This is a tools-only
    plugin: a silent ``except`` here would let the loader mark the plugin
    'enabled' while it exposes zero tools. Letting the failure propagate makes a
    broken load visible instead of a confusing enabled-but-empty state.
    """
    from .tools import register_tools

    register_tools(ctx)
