"""Security hardening from the pre-push review.

Cross-origin agent-card url must not redirect calls/auth; the fleet auth
resolver must not touch static/legacy peers; response reads are capped.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest
from _helpers import FakeClient

from hermes_a2a_fleet.auth import build_auth_resolver
from hermes_a2a_fleet.client import MAX_RESPONSE_BYTES, _read_capped
from hermes_a2a_fleet.registry import Registry, _same_origin
from hermes_a2a_fleet.types import AgentRef

# --- cross-origin agent-card url must NOT redirect calls / bearer auth -------

def _source(name, url, src):
    class S:
        def __init__(self):
            self.name = "src"

        def discover(self):
            return [AgentRef(name=name, url=url, source=src)]

    return S()


def test_registry_ignores_cross_origin_card_url():
    class XClient(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            return {"name": "X", "url": "http://evil.example:9000/a2a"}  # foreign host

    out = Registry([_source("p", "http://good:9000", "mdns")], XClient(), ttl=0).refresh()
    assert out[0].rpc_url == "http://good:9000"  # foreign card url ignored -> stays same-host


def test_registry_uses_same_origin_card_url():
    class CClient(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            return {"name": "X", "url": "http://good:9000/a2a"}

    out = Registry([_source("p", "http://good:9000", "mdns")], CClient(), ttl=0).refresh()
    assert out[0].rpc_url == "http://good:9000/a2a"  # same host -> trusted


# --- fleet auth resolver scope: discovered peers only -----------------------

def _capturing_client(box):
    class C(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            box["auth"] = auth
            return {"name": "X"}

    return C()


def test_resolver_not_applied_to_static_source():
    box = {}
    reg = Registry([_source("p", "http://h:9000", "static")], _capturing_client(box), ttl=0,
                   auth_resolver=build_auth_resolver(default_token="LEAK"))
    reg.refresh()
    assert box["auth"] == {}  # the fleet default token is NEVER sent to a static peer


def test_resolver_applied_to_discovered_source():
    box = {}
    reg = Registry([_source("p", "http://h:9000", "tailnet")], _capturing_client(box), ttl=0,
                   auth_resolver=build_auth_resolver(default_token="tok"))
    reg.refresh()
    assert box["auth"] == {"type": "bearer", "token": "tok"}  # discovered peer DOES get it


# --- capped response read ---------------------------------------------------

class _FakeResp:
    def __init__(self, size):
        self._size = size

    def read(self, n):
        return b"x" * min(n, self._size)


def test_read_capped_allows_small_and_rejects_oversize():
    assert _read_capped(_FakeResp(10)) == b"x" * 10
    with pytest.raises(ValueError):
        _read_capped(_FakeResp(MAX_RESPONSE_BYTES + 5))


# --- redirects are refused (no cross-origin bearer-token forwarding) ---------

def _serve_302():
    import http.server

    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://attacker.invalid/")
            self.end_headers()

        do_POST = do_GET

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_client_refuses_redirects_so_bearer_is_not_forwarded():
    from hermes_a2a_fleet.client import _OPENER, A2AClient

    srv = _serve_302()
    port = srv.server_address[1]
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        with pytest.raises(urllib.error.HTTPError) as ei:
            _OPENER.open(req, timeout=5)  # 3xx raised, NOT followed
        assert ei.value.code == 302
        # a redirecting peer reads as unreachable; the token never reaches the
        # redirect target.
        card = A2AClient(5).fetch_card(f"http://127.0.0.1:{port}", auth={"type": "bearer", "token": "secret"})
        assert card is None
    finally:
        srv.shutdown()


# --- _same_origin is strict + fails closed ----------------------------------

def test_same_origin_strict_and_exception_safe():
    assert _same_origin("http://h:9000/a2a", "http://h:9000") is True
    assert _same_origin("https://h:9000/a2a", "http://h:9000") is False  # scheme differs
    assert _same_origin("http://evil:9000/a2a", "http://h:9000") is False
    assert _same_origin("http://h:99999999/a2a", "http://h:9000") is False  # bad port -> closed, no crash
    assert _same_origin("http://h:notaport/a2a", "http://h:9000") is False


def test_registry_survives_malformed_card_url():
    class BadClient(FakeClient):
        def fetch_card(self, base_url, auth=None, timeout=8):
            return {"name": "X", "url": "http://h:999999/a2a"}  # out-of-range port

    out = Registry([_source("p", "http://good:9000", "mdns")], BadClient(), ttl=0).refresh()
    assert out[0].rpc_url == "http://good:9000"  # malformed card url ignored, refresh did not crash


def test_resolver_not_applied_to_static_a2a_source():
    box = {}
    reg = Registry([_source("p", "http://h:9000", "static:a2a")], _capturing_client(box), ttl=0,
                   auth_resolver=build_auth_resolver(default_token="LEAK"))
    reg.refresh()
    assert box["auth"] == {}  # the 'static:a2a' source is not a discovered source either
