"""Continuous mDNS source: pure service->ref mapping + live-set maintenance."""

from __future__ import annotations

import threading

from hermes_a2a_fleet.sources.mdns_source import _Collector, _service_to_ref

# --- _service_to_ref (pure) -------------------------------------------------

def test_service_to_ref_a2a_type_always_accepted():
    ref = _service_to_ref("_a2a._tcp.local.", "X._a2a._tcp.local.", "h", 9, {})
    assert ref and ref.url == "http://h:9" and ref.source == "mdns"


def test_service_to_ref_http_requires_agent_card_path():
    assert _service_to_ref("_http._tcp.local.", "X", "h", 9, {}) is None
    assert _service_to_ref("_http._tcp.local.", "X", "h", 9, {"path": "/status"}) is None
    assert _service_to_ref("_http._tcp.local.", "X", "h", 9, {"path": "/.well-known/agent-card.json"}) is not None


def test_service_to_ref_needs_host_and_port():
    assert _service_to_ref("_a2a._tcp.local.", "X", "", 9, {}) is None
    assert _service_to_ref("_a2a._tcp.local.", "X", "h", None, {}) is None


def test_service_to_ref_label_and_caps():
    ref = _service_to_ref("_a2a._tcp.local.", "My Agent._a2a._tcp.local.", "h", 9, {"tags": "chat,ui"})
    assert ref.name == "My Agent"  # first dotted label
    assert ref.capabilities == ("chat", "ui")


# --- _Collector: announce -> live set, goodbye -> removed -------------------

class _FakeInfo:
    def __init__(self, addrs, port, props, name):
        self._addrs, self.port, self.properties, self.name = addrs, port, props, name

    def parsed_addresses(self):
        return self._addrs


def test_collector_add_then_remove_maintains_live_set():
    peers, lock = {}, threading.Lock()
    info = _FakeInfo(
        ["192.168.1.5"], 9000,
        {b"path": b"/.well-known/agent-card.json", b"tags": b"chat"},
        "Agent._http._tcp.local.",
    )
    col = _Collector(peers, lock, lambda zc, t, n, ms: info, 1000)

    col.add_service(None, "_http._tcp.local.", "Agent._http._tcp.local.")
    assert len(peers) == 1
    ref = next(iter(peers.values()))
    assert ref.url == "http://192.168.1.5:9000" and "chat" in ref.capabilities

    col.remove_service(None, "_http._tcp.local.", "Agent._http._tcp.local.")
    assert peers == {}  # goodbye removes exactly the entry the announce created


def test_collector_ignores_non_a2a_http_peer():
    peers, lock = {}, threading.Lock()
    info = _FakeInfo(["10.0.0.1"], 80, {}, "Printer._http._tcp.local.")  # no a2a hint
    col = _Collector(peers, lock, lambda zc, t, n, ms: info, 1000)
    col.add_service(None, "_http._tcp.local.", "Printer._http._tcp.local.")
    assert peers == {}


def test_collector_update_replaces_entry():
    peers, lock = {}, threading.Lock()
    seq = [
        _FakeInfo(["1.1.1.1"], 9000, {}, "P._a2a._tcp.local."),
        _FakeInfo(["2.2.2.2"], 9000, {}, "P._a2a._tcp.local."),
    ]
    box = {"i": 0}

    def resolve(zc, t, n, ms):
        info = seq[box["i"]]
        box["i"] = min(box["i"] + 1, 1)
        return info

    col = _Collector(peers, lock, resolve, 1000)
    col.add_service(None, "_a2a._tcp.local.", "P._a2a._tcp.local.")
    col.update_service(None, "_a2a._tcp.local.", "P._a2a._tcp.local.")
    assert len(peers) == 1  # same service name -> replaced, not duplicated
    assert next(iter(peers.values())).url == "http://2.2.2.2:9000"
