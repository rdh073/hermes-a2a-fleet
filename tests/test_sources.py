"""Discovery sources: static merge, tailnet sweep (injected status), mDNS txt."""

from __future__ import annotations

from hermes_a2a_fleet.config import load_fleet_config
from hermes_a2a_fleet.sources.mdns_source import _decode_txt
from hermes_a2a_fleet.sources.static_source import StaticSource
from hermes_a2a_fleet.sources.tailnet_source import TailnetSource, _peers_from_status


def test_static_source_merges_fleet_and_legacy_maps():
    raw = {
        "a2a_fleet": {"agents": {"r": {"url": "http://x:9900", "capabilities": ["search"]}}},
        "a2a_agents": {"legacy": {"url": "http://y:9000", "auth": {"type": "bearer", "token": "t"}}},
    }
    refs = StaticSource(load_fleet_config(raw=raw)).discover()
    by = {r.name: r for r in refs}
    assert set(by) == {"r", "legacy"}
    assert by["r"].capabilities == ("search",)
    assert by["legacy"].auth.get("token") == "t"


def test_static_source_skips_entries_without_url():
    raw = {"a2a_fleet": {"agents": {"bad": {"capabilities": ["x"]}}}}
    assert StaticSource(load_fleet_config(raw=raw)).discover() == []


def test_peers_from_status_extracts_ipv4_for_self_and_peers():
    status = {
        "Self": {"HostName": "kyubi", "TailscaleIPs": ["100.64.0.2", "fd7a:115c::2"]},
        "Peer": {"k": {"DNSName": "hub.example.com.", "TailscaleIPs": ["100.64.0.1"]}},
    }
    peers = dict(_peers_from_status(status))
    assert peers["hub"] == "100.64.0.1"
    assert peers["kyubi"] == "100.64.0.2"  # IPv4 chosen over IPv6


def test_tailnet_source_builds_one_candidate_per_probe_port():
    cfg = load_fleet_config(raw={"a2a_fleet": {"probe_ports": [9900, 9000]}})
    status = {"Peer": {"k": {"HostName": "hub", "TailscaleIPs": ["100.64.0.1"]}}}
    refs = TailnetSource(cfg, status_provider=lambda: status).discover()
    urls = {r.url for r in refs}
    assert urls == {"http://100.64.0.1:9900", "http://100.64.0.1:9000"}
    assert all(r.source == "tailnet" for r in refs)


def test_tailnet_source_empty_status_yields_nothing():
    cfg = load_fleet_config(raw={})
    assert TailnetSource(cfg, status_provider=lambda: {}).discover() == []


def test_decode_txt_handles_bytes_and_str():
    assert _decode_txt({b"path": b"/.well-known/agent-card.json", b"kind": b"a2a", "x": None}) == {
        "path": "/.well-known/agent-card.json",
        "kind": "a2a",
        "x": "",
    }
