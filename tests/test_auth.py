"""Discovery auth: host-precedence resolution + Registry applying it."""

from __future__ import annotations

from _helpers import FakeClient

from hermes_a2a_fleet.auth import build_auth_resolver
from hermes_a2a_fleet.config import load_fleet_config
from hermes_a2a_fleet.registry import Registry, build_registry
from hermes_a2a_fleet.types import AgentRef


def test_resolver_precedence_hostport_over_host_over_default():
    r = build_auth_resolver(default_token="D", host_tokens={"h:9000": "A", "h": "B"})
    assert r("http://h:9000/a2a") == {"type": "bearer", "token": "A"}   # host:port wins
    assert r("http://h:9100/a2a") == {"type": "bearer", "token": "B"}   # host fallback
    assert r("http://other:1/a2a") == {"type": "bearer", "token": "D"}  # default
    assert build_auth_resolver()("http://x:1") == {}                    # nothing configured


def test_registry_attaches_resolved_token_to_unauthed_discovered_peer():
    # the agent gates its card: the fake returns it ONLY when the bearer is present.
    class GatedClient(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            if (auth or {}).get("token") != "tok":
                return None  # 403-equivalent
            return {"name": "Agent", "url": base_url.rstrip("/") + "/a2a", "skills": [{"name": "ui"}]}

    class S:
        name = "mdns"

        def discover(self):  # discovered with NO auth, like mDNS/tailnet
            return [AgentRef(name="agent", url="http://192.168.1.5:9000", source="mdns")]

    resolver = build_auth_resolver(host_tokens={"192.168.1.5:9000": "tok"})
    reg = Registry([S()], GatedClient(), ttl=0, auth_resolver=resolver)
    out = reg.refresh()

    assert len(out) == 1  # verified (not dropped) because the resolved token unlocked the card
    assert out[0].auth == {"type": "bearer", "token": "tok"}  # carried into the cached ref → fan-out can call it
    assert out[0].rpc_url.endswith("/a2a")


def test_registry_drops_gated_peer_when_no_token_resolves():
    class GatedClient(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            return None if not auth else {"name": "x"}

    class S:
        name = "mdns"

        def discover(self):
            return [AgentRef(name="agent", url="http://h:9000", source="mdns")]

    reg = Registry([S()], GatedClient(), ttl=0)  # no resolver configured
    assert reg.refresh() == []  # gated card, no token -> dropped


def test_static_auth_beats_resolver():
    # A peer that already carries auth (static config) keeps it; the resolver is not consulted.
    class CheckClient(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            assert (auth or {}).get("token") == "static"  # NOT the resolver's "resolved"
            return {"name": "x"}

    ref = AgentRef(name="p", url="http://h:9000", auth={"type": "bearer", "token": "static"})

    class S:
        name = "static"

        def discover(self):
            return [ref]

    reg = Registry([S()], CheckClient(), ttl=0,
                   auth_resolver=build_auth_resolver(default_token="resolved"))
    out = reg.refresh()
    assert out[0].auth["token"] == "static"


def test_build_registry_wires_resolver_from_config():
    cfg = load_fleet_config(raw={"a2a_fleet": {"auth": {"default": "d", "hosts": {"h:9000": "x"}}}})
    assert cfg.auth_default == "d"
    assert cfg.auth_hosts == {"h:9000": "x"}
    reg = build_registry(cfg, client=FakeClient())
    assert reg._auth_resolver("http://h:9000") == {"type": "bearer", "token": "x"}
