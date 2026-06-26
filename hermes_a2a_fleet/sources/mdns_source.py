"""mDNS / DNS-SD discovery — continuous, event-driven.

mDNS already provides register (announce), heartbeat (re-announce / TTL) and
deregister (goodbye) for free, so the robust LAN design is to LISTEN
continuously rather than poll-browse on every call. A single long-lived
background browser maintains a live set of A2A peers, updated as peers join
(add/update) and leave (goodbye / TTL expiry). ``discover()`` then returns the
current snapshot instantly — no per-call browse delay, and a peer that joins or
dies is reflected in real time.

Uses the optional ``zeroconf`` package; degrades to an empty list without it
(the static/tailnet sources keep working).

Browses ``_a2a._tcp`` (the dedicated A2A type) and ``_http._tcp`` for peers
that advertise an agent-card path via a TXT hint.
"""

from __future__ import annotations

import logging
import threading
import time

from ..types import AgentRef
from .base import DiscoverySource

logger = logging.getLogger(__name__)

_SERVICE_TYPES = ("_a2a._tcp.local.", "_http._tcp.local.")


def _decode_txt(props: dict) -> dict:
    out: dict[str, str] = {}
    for k, v in (props or {}).items():
        try:
            key = k.decode() if isinstance(k, bytes) else str(k)
            val = v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))
            out[key] = val
        except Exception:
            continue
    return out


def _service_to_ref(type_: str, instance_name: str, host: str | None, port: int | None, txt: dict) -> AgentRef | None:
    """Pure: turn resolved service data into an A2A AgentRef, or None.

    An ``_http`` peer counts as A2A only when its TXT advertises an A2A
    agent-card path (a ``path`` value containing ``agent``, e.g.
    ``/.well-known/agent-card.json``); an ``_a2a`` peer always does.
    """
    if not host or not port:
        return None
    is_a2a_type = type_.startswith("_a2a")
    if not is_a2a_type and "agent" not in txt.get("path", ""):
        return None
    base = f"http://{host}:{port}"
    label = (instance_name or "").split(".")[0] or base
    caps = tuple(t for t in txt.get("tags", "").split(",") if t)
    return AgentRef(name=label, url=base, capabilities=caps, source="mdns")


class _Collector:
    """zeroconf ServiceListener maintaining ``{service_name: AgentRef}`` live.

    Keyed by the fully-qualified service name so a goodbye (remove_service)
    deletes exactly the entry an announce (add_service) created. The
    ``resolve`` seam (zc, type, name, timeout_ms) -> ServiceInfo is injectable,
    so the add/remove maintenance is unit-tested without a real network.
    """

    def __init__(self, peers: dict, lock: threading.Lock, resolve, txt_timeout_ms: int) -> None:
        self._peers = peers
        self._lock = lock
        self._resolve = resolve
        self._txt_timeout = txt_timeout_ms

    def _ref(self, type_: str, name: str, info) -> AgentRef | None:
        if not info:
            return None
        addrs = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
        host = addrs[0] if addrs else None
        txt = _decode_txt(getattr(info, "properties", {}) or {})
        return _service_to_ref(type_, getattr(info, "name", None) or name, host, getattr(info, "port", None), txt)

    def add_service(self, zc, type_, name):
        ref = self._ref(type_, name, self._resolve(zc, type_, name, self._txt_timeout))
        if ref:
            with self._lock:
                self._peers[name] = ref

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name):
        with self._lock:
            self._peers.pop(name, None)


class MdnsListener:
    """Owns the long-lived Zeroconf + browsers and the live peer set."""

    def __init__(self, cfg) -> None:
        self._cfg = cfg
        self._peers: dict[str, AgentRef] = {}
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._zc = None
        self._started = False
        self._available = False

    def start(self) -> bool:
        """Idempotent + thread-safe. Returns True if mDNS is available."""
        if self._started:
            return self._available
        with self._start_lock:  # never create two browsers on a concurrent first call
            if self._started:
                return self._available
            try:
                from zeroconf import ServiceBrowser, Zeroconf  # type: ignore
            except Exception:
                logger.info("mdns: 'zeroconf' not installed — LAN discovery disabled (pip install zeroconf)")
                self._started, self._available = True, False
                return False

            def resolve(zc, type_, name, timeout_ms):
                return zc.get_service_info(type_, name, timeout=timeout_ms)

            self._zc = Zeroconf()
            collector = _Collector(self._peers, self._lock, resolve, int(self._cfg.mdns_timeout * 1000))
            for service_type in _SERVICE_TYPES:
                ServiceBrowser(self._zc, service_type, collector)
            # One-time settle so the FIRST snapshot sees the initial announces;
            # after this the browser keeps the set live with zero per-call delay.
            time.sleep(max(0.5, self._cfg.mdns_timeout))
            self._started, self._available = True, True
            return True

    def snapshot(self) -> list[AgentRef]:
        with self._lock:
            return list(self._peers.values())

    def close(self) -> None:
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
        self._zc = None
        self._started = False
        self._available = False


# One listener per process; repeated MdnsSource builds share it so the browser
# stays up (and the live set warm) across registry refreshes.
_listener: MdnsListener | None = None
_listener_lock = threading.Lock()


def _get_listener(cfg) -> MdnsListener:
    global _listener
    if _listener is None:
        with _listener_lock:  # double-checked: one listener per process
            if _listener is None:
                _listener = MdnsListener(cfg)
    return _listener


class MdnsSource(DiscoverySource):
    name = "mdns"

    def __init__(self, cfg) -> None:
        self._cfg = cfg

    def discover(self) -> list[AgentRef]:
        listener = _get_listener(self._cfg)
        if not listener.start():
            return []
        return listener.snapshot()
