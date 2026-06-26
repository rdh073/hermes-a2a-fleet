"""Async submit/poll: task tracking, tasks/get wire shape, terminal cleanup."""

from __future__ import annotations

import pytest
from _helpers import FakeClient

import hermes_a2a_fleet.tools as T
from hermes_a2a_fleet.client import A2AClient, interpret_result
from hermes_a2a_fleet.types import AgentRef


class _Reg:
    """Minimal Registry stand-in: name -> AgentRef lookup, no network."""

    def __init__(self, refs):
        self._by = {r.name: r for r in refs}

    def get(self, name):
        return self._by.get(name)

    def list(self, *a, **k):
        return list(self._by.values())


class _Cfg:
    timeout = 5


class AsyncClient(FakeClient):
    """A peer that returns a non-terminal Task on send, then 'working' on the
    first poll and 'completed' (with a reply) on the second."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.polls: list[tuple] = []

    def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
        self.calls.append(rpc_url)
        return {"result": {"task": {"id": "T-1", "status": {"state": "submitted"}}}}

    def get_task(self, rpc_url, task_id, auth=None, timeout=None):
        self.polls.append((rpc_url, task_id))
        if len(self.polls) == 1:
            return {"result": {"task": {"id": task_id, "status": {"state": "working"}}}}
        return {"result": {"task": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": "done!"}]}],
        }}}


@pytest.fixture(autouse=True)
def _isolate():
    T._tasks.clear()
    yield
    T._tasks.clear()
    T._registry = T._transport = T._config = None  # don't leak injection into other tests


def _wire(client, refs):
    T.set_components(_Reg(refs), client, _Cfg())


# --- submit tracks, poll resolves, terminal evicts --------------------------

def test_submit_tracks_task_then_poll_resolves_to_completion():
    ref = AgentRef(name="worker", url="http://w:9900", rpc_url="http://w:9900/a2a")
    client = AsyncClient()
    _wire(client, [ref])

    out = T.a2a_fleet_submit({"agent": "worker", "message": "do it"})
    assert "T-1" in out and "submitted" in out.lower()
    assert "T-1" in T._tasks                       # tracked for polling
    assert client.calls == ["http://w:9900/a2a"]   # used the verified rpc_url, not bare url

    first = T.a2a_fleet_poll({"task_id": "T-1"})
    assert "working" in first.lower()
    assert "T-1" in T._tasks                        # non-terminal -> still tracked

    second = T.a2a_fleet_poll({"task_id": "T-1"})
    assert "completed" in second.lower() and "done!" in second
    assert "T-1" not in T._tasks                     # terminal -> evicted
    assert client.polls[0] == ("http://w:9900/a2a", "T-1")  # polled the SAME peer


def test_submit_unknown_agent_is_an_error():
    _wire(AsyncClient(), [])
    out = T.a2a_fleet_submit({"agent": "ghost", "message": "x"})
    assert "unknown agent" in out.lower()
    assert T._tasks == {}


def test_submit_requires_agent_and_message():
    _wire(AsyncClient(), [AgentRef(name="w", url="http://w:9900")])
    assert "required" in T.a2a_fleet_submit({"message": "x"}).lower()
    assert "required" in T.a2a_fleet_submit({"agent": "w"}).lower()


def test_poll_unknown_task_is_an_error():
    _wire(AsyncClient(), [])
    assert "unknown task" in T.a2a_fleet_poll({"task_id": "nope"}).lower()


def test_submit_synchronous_completion_returns_reply_and_does_not_track():
    class SyncClient(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            self.calls.append(rpc_url)
            return {"result": {"task": {
                "id": "S-1", "status": {"state": "completed"},
                "artifacts": [{"parts": [{"kind": "text", "text": "instant"}]}],
            }}}

    _wire(SyncClient(), [AgentRef(name="w", url="http://w:9900")])
    out = T.a2a_fleet_submit({"agent": "w", "message": "x"})
    assert "completed" in out.lower() and "instant" in out
    assert T._tasks == {}  # already done — nothing to poll


def test_submit_bare_message_has_no_task_id():
    class MsgClient(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            self.calls.append(rpc_url)
            return {"result": {"artifacts": [{"parts": [{"kind": "text", "text": "hi back"}]}]}}

    _wire(MsgClient(), [AgentRef(name="w", url="http://w:9900")])
    out = T.a2a_fleet_submit({"agent": "w", "message": "x"})
    assert "synchron" in out.lower() and "hi back" in out
    assert T._tasks == {}


def test_submit_terminal_failure_reports_and_does_not_track():
    class FailClient(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            self.calls.append(rpc_url)
            return {"result": {"task": {"id": "F-1", "status": {"state": "failed"}}}}

    _wire(FailClient(), [AgentRef(name="w", url="http://w:9900")])
    out = T.a2a_fleet_submit({"agent": "w", "message": "x"})
    assert "failed" in out.lower()
    assert T._tasks == {}


# --- get_task builds the tasks/get JSON-RPC body ----------------------------

def test_get_task_wire_shape_is_tasks_get():
    captured: dict = {}
    c = A2AClient(5)

    def fake_post(url, body, headers, timeout):
        captured.update(url=url, body=body, headers=headers)
        return {"result": {"task": {"id": body["params"]["id"], "status": {"state": "working"}}}}

    c._post_json = fake_post  # type: ignore[method-assign]
    resp = c.get_task("http://w:9900/a2a", "T-9", auth={"type": "bearer", "token": "t"})

    assert captured["url"] == "http://w:9900/a2a"
    assert captured["body"]["jsonrpc"] == "2.0"
    assert captured["body"]["method"] == "tasks/get"
    assert captured["body"]["params"] == {"id": "T-9"}
    assert captured["headers"]["Authorization"] == "Bearer t"
    assert interpret_result(c, resp).state == "working"
