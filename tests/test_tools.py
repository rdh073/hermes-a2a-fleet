"""Tools layer: JSON-RPC wire shape, worker-cap resolution, broadcast plumbing.

These pin the two fixes the earlier suite missed: the exact ``message/send``
request body, and that broadcast actually honours the configured
``max_concurrency`` (and uses the injected transport, not Registry internals).
"""

from __future__ import annotations

from _helpers import FakeClient
from hypothesis import given
from hypothesis import strategies as st

import hermes_a2a_fleet.tools as T
from hermes_a2a_fleet.client import build_send_body
from hermes_a2a_fleet.config import load_fleet_config
from hermes_a2a_fleet.registry import Registry
from hermes_a2a_fleet.tools import _resolve_deadline, _resolve_workers
from hermes_a2a_fleet.types import AgentRef

# --- build_send_body : the intentional message/send JSON-RPC wire shape ------

@given(st.text(), st.text())
def test_send_body_is_jsonrpc_message_send(text, ctx):
    body = build_send_body(text, ctx)
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "message/send"  # JSON-RPC binding name, NOT gRPC 'SendMessage'
    msg = body["params"]["message"]
    assert msg["role"] == "user"
    assert msg["parts"] == [{"kind": "text", "text": text}]
    assert ("contextId" in msg) == bool(ctx)
    if ctx:
        assert msg["contextId"] == ctx


# --- _resolve_workers : boundary + invariant --------------------------------

@given(
    st.integers(min_value=1, max_value=50),
    st.integers(min_value=1, max_value=50),
    st.one_of(st.none(), st.integers(min_value=-5, max_value=50)),
)
def test_resolve_workers_never_exceeds_caps(n_agents, cfg_max, requested):
    w = _resolve_workers(requested, n_agents, cfg_max)
    assert 1 <= w <= n_agents
    assert w <= cfg_max
    if isinstance(requested, int) and requested > 0:
        assert w <= requested


def test_resolve_workers_falls_back_to_agent_count_when_cfg_zero():
    assert _resolve_workers(None, 4, 0) == 4


def test_resolve_workers_tolerates_garbage_request():
    assert _resolve_workers("not-a-number", 4, 3) == 3


# --- broadcast honours the configured concurrency cap + uses the transport ---

def test_broadcast_caps_workers_at_config_max_concurrency(monkeypatch):
    agents = [AgentRef(name=f"p{i}", url=f"http://h{i}:9900") for i in range(5)]

    class S:
        name = "s"

        def discover(self):
            return agents

    client = FakeClient(cards={f"http://h{i}:9900": {"name": f"p{i}"} for i in range(5)})
    cfg = load_fleet_config(raw={"a2a_fleet": {"max_concurrency": 2}})
    reg = Registry([S()], client, ttl=0)

    captured: dict = {}
    real_fan = T.fan_out

    def spy(agents_, msg, transport, *, max_workers, timeout, context_id="", deadline=None, stop_on_first=False):
        captured["workers"] = max_workers
        captured["transport_injected"] = transport is client
        return real_fan(agents_, msg, transport, max_workers=max_workers, timeout=timeout,
                        context_id=context_id, deadline=deadline, stop_on_first=stop_on_first)

    monkeypatch.setattr(T, "fan_out", spy)
    T.set_components(reg, transport=client, config=cfg)
    try:
        out = T.a2a_fleet_broadcast({"message": "hi"})
    finally:
        T._registry = T._transport = T._config = None  # don't leak injection into other tests

    assert captured["workers"] == 2          # capped at config, not len(agents)=5
    assert captured["transport_injected"]    # injected transport, not reg internals
    assert "5/5 succeeded" in out


def test_resolve_deadline_override_and_fallback():
    assert _resolve_deadline(2.5, None) == 2.5
    assert _resolve_deadline(None, 4.0) == 4.0
    assert _resolve_deadline("garbage", 4.0) == 4.0
    assert _resolve_deadline(-1, 4.0) == 4.0      # non-positive -> config value
    assert _resolve_deadline(None, None) is None


def test_broadcast_first_mode_enables_stop_on_first(monkeypatch):
    agents = [AgentRef(name=f"p{i}", url=f"http://h{i}:9900") for i in range(3)]

    class S:
        name = "s"

        def discover(self):
            return agents

    client = FakeClient(cards={f"http://h{i}:9900": {"name": f"p{i}"} for i in range(3)})
    reg = Registry([S()], client, ttl=0)
    captured: dict = {}
    real = T.fan_out

    def spy(a, m, t, *, max_workers, timeout, context_id="", deadline=None, stop_on_first=False):
        captured["stop_on_first"] = stop_on_first
        return real(a, m, t, max_workers=max_workers, timeout=timeout,
                    context_id=context_id, deadline=deadline, stop_on_first=stop_on_first)

    monkeypatch.setattr(T, "fan_out", spy)
    T.set_components(reg, transport=client, config=load_fleet_config(raw={}))
    try:
        T.a2a_fleet_broadcast({"message": "hi", "mode": "first"})
    finally:
        T._registry = T._transport = T._config = None

    assert captured["stop_on_first"] is True


# --- dispatch: a different message per peer, in parallel --------------------

def test_dispatch_sends_each_peer_its_own_message():
    agents = [AgentRef(name=f"p{i}", url=f"http://h{i}:9900") for i in range(3)]

    class S:
        name = "s"

        def discover(self):
            return agents

    client = FakeClient(cards={f"http://h{i}:9900": {"name": f"p{i}"} for i in range(3)})
    reg = Registry([S()], client, ttl=0)
    T.set_components(reg, transport=client, config=load_fleet_config(raw={}))
    try:
        out = T.a2a_fleet_dispatch({"tasks": [
            {"agent": "p0", "message": "alpha"},
            {"agent": "p2", "message": "gamma"},
        ]})
    finally:
        T._registry = T._transport = T._config = None

    assert "2/2 succeeded" in out
    assert "reply:alpha" in out and "reply:gamma" in out  # each got ITS message
    assert "p1" not in out                                 # only the addressed peers


def test_dispatch_rejects_unknown_agent_and_bad_shape():
    class S:
        name = "s"

        def discover(self):
            return [AgentRef(name="p0", url="http://h0:9900")]

    client = FakeClient(cards={"http://h0:9900": {"name": "p0"}})
    reg = Registry([S()], client, ttl=0)
    T.set_components(reg, transport=client, config=load_fleet_config(raw={}))
    try:
        assert "non-empty list" in T.a2a_fleet_dispatch({"tasks": []})
        assert "unknown agent" in T.a2a_fleet_dispatch({"tasks": [{"agent": "ghost", "message": "x"}]}).lower()
        assert "needs both" in T.a2a_fleet_dispatch({"tasks": [{"agent": "p0"}]})
    finally:
        T._registry = T._transport = T._config = None
