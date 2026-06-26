"""Fan-out: count conservation, partition, partial-failure, summarize modes."""

from __future__ import annotations

from _helpers import FakeClient, agentref_lists
from hypothesis import given
from hypothesis import strategies as st

from hermes_a2a_fleet.fanout import fan_out, partition, scatter, summarize
from hermes_a2a_fleet.types import AgentRef, FanResult

# --- scatter: each peer can get its OWN message, run in parallel -------------

def test_scatter_sends_a_distinct_message_per_agent():
    class EchoArgClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.seen: dict[str, str] = {}

        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            self.seen[rpc_url] = message
            return super().send_message(rpc_url, message, auth=auth, timeout=timeout, context_id=context_id)

    a = AgentRef(name="a", url="http://a:9900")
    b = AgentRef(name="b", url="http://b:9900")
    client = EchoArgClient()
    results = scatter([(a, "task-A"), (b, "task-B")], client)

    assert len(results) == 2
    by = {r.agent: r for r in results}
    assert by["a"].reply == "reply:task-A" and by["b"].reply == "reply:task-B"
    assert client.seen == {"http://a:9900": "task-A", "http://b:9900": "task-B"}


def test_fan_out_is_scatter_with_one_shared_message():
    # metamorphic: broadcast == scatter where every item shares the message
    agents = [AgentRef(name="a", url="http://a:9900"), AgentRef(name="b", url="http://b:9900")]
    f = fan_out(agents, "same", FakeClient())
    s = scatter([(x, "same") for x in agents], FakeClient())
    assert {(r.agent, r.reply, r.terminal) for r in f} == {(r.agent, r.reply, r.terminal) for r in s}


def test_scatter_empty_is_empty():
    assert scatter([], FakeClient()) == []

# --- count conservation + ok/err partition (the core scatter-gather law) ----

@given(agentref_lists, st.randoms())
def test_fanout_returns_exactly_one_result_per_agent(refs, rnd):
    # dedupe by url the way the registry would, so names are the unit of count
    agents = list({r.url: r for r in refs}.values())
    fail = {a.url for a in agents if rnd.random() < 0.5}
    client = FakeClient(fail_urls=fail)
    results = fan_out(agents, "ping", client, max_workers=4, timeout=5)
    assert len(results) == len(agents)
    oks, errs = partition(results)
    assert len(oks) + len(errs) == len(agents)
    # a peer fails iff its url was in the fail set
    failed_names = {r.agent for r in errs}
    expected_failed = {a.name for a in agents if a.url in fail}
    assert failed_names == expected_failed


def test_fanout_empty_is_empty():
    assert fan_out([], "x", FakeClient()) == []


def test_fanout_partial_failure_does_not_abort_batch():
    agents = [
        AgentRef(name="ok1", url="http://h1:9900"),
        AgentRef(name="bad", url="http://h2:9900"),
        AgentRef(name="ok2", url="http://h3:9900"),
    ]
    client = FakeClient(fail_urls={"http://h2:9900"})
    results = fan_out(agents, "hello", client)
    by = {r.agent: r for r in results}
    assert by["ok1"].ok and by["ok2"].ok
    assert not by["bad"].ok and "down" in by["bad"].error
    assert by["ok1"].reply == "reply:hello"


def test_fanout_peer_jsonrpc_error_is_a_failure_not_a_crash():
    class ErrClient(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            return {"error": {"code": -32000, "message": "task rejected"}}

    agents = [AgentRef(name="x", url="http://h:9900")]
    results = fan_out(agents, "hi", ErrClient())
    assert not results[0].ok
    assert "task rejected" in results[0].error


def test_fanout_failed_task_is_not_ok_even_without_jsonrpc_error():
    # A successful RPC carrying a FAILED task (an agent's result.task wrapper, state
    # 'failed') must NOT be reported as ok. Verified shape against a live A2A agent.
    class FailedTaskClient(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            return {"result": {"task": {"status": {"state": "failed"}, "artifacts": []}}}

    results = fan_out([AgentRef(name="x", url="http://h:9900")], "hi", FailedTaskClient())
    assert not results[0].ok
    assert "failed" in results[0].error


# --- summarize -------------------------------------------------------------

def test_summarize_first_returns_a_success_when_any_exists():
    results = [
        FanResult("slow", True, reply="R-slow", elapsed_ms=200),
        FanResult("fast", True, reply="R-fast", elapsed_ms=10),
        FanResult("dead", False, error="down", elapsed_ms=5),
    ]
    out = summarize(results, mode="first")
    assert "R-fast" in out and "fast" in out
    assert "R-slow" not in out


def test_summarize_first_all_failed_rolls_up_errors():
    results = [FanResult("a", False, error="down"), FanResult("b", False, error="timeout")]
    out = summarize(results, mode="first")
    assert "All peers failed" in out and "down" in out and "timeout" in out


def test_summarize_collect_includes_every_peer():
    results = [
        FanResult("a", True, reply="ra", elapsed_ms=1),
        FanResult("b", False, error="boom", elapsed_ms=2),
    ]
    out = summarize(results, mode="collect")
    assert "1/2 succeeded" in out
    assert "ra" in out and "boom" in out and "a" in out and "b" in out


def test_summarize_empty():
    assert "nothing" in summarize([], mode="collect").lower()


# --- Slice A': whole-call deadline, early-exit, terminal accounting ----------

def test_fanout_deadline_bounds_wallclock_with_a_hanging_peer():
    import time as _t

    class SlowClient(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            if "slow" in rpc_url:
                _t.sleep(0.6)  # well past the deadline
            return super().send_message(rpc_url, message, auth=auth, timeout=timeout, context_id=context_id)

    agents = [AgentRef(name="fast", url="http://fast:9900"), AgentRef(name="slow", url="http://slow:9900")]
    t0 = _t.monotonic()
    results = fan_out(agents, "hi", SlowClient(), deadline=0.15, timeout=5)
    elapsed = _t.monotonic() - t0

    assert elapsed < 0.5  # bounded liveness: returned before the 0.6s straggler
    by = {r.agent: r for r in results}
    assert set(by) == {"fast", "slow"}  # exactly-one-terminal: every peer accounted
    assert by["fast"].ok and by["fast"].terminal == "ok"
    assert not by["slow"].ok and by["slow"].terminal == "deadline"


def test_fanout_first_early_exit_accounts_every_peer_once():
    agents = [AgentRef(name=f"p{i}", url=f"http://h{i}:9900") for i in range(4)]
    results = fan_out(agents, "hi", FakeClient(), stop_on_first=True)
    assert len(results) == len(agents)
    assert len({r.agent for r in results}) == 4  # exactly once each
    assert any(r.ok for r in results)  # at least the first success
    assert all(r.terminal in {"ok", "abandoned", "error", "failed", "deadline"} for r in results)


def test_fanout_first_with_deadline_labels_leftovers_abandoned_not_deadline():
    # 'first' passes a deadline too; a fast success must label leftovers
    # 'abandoned' (early-exit), NOT 'deadline' (the cap never fired).
    agents = [AgentRef(name=f"p{i}", url=f"http://h{i}:9900") for i in range(3)]
    results = fan_out(agents, "hi", FakeClient(), stop_on_first=True, deadline=5.0)
    left = [r for r in results if not r.ok]
    assert any(r.ok for r in results)
    assert all(r.terminal == "abandoned" for r in left)


def test_fanout_terminal_vocabulary():
    assert fan_out([AgentRef(name="ok", url="http://ok:9900")], "x", FakeClient())[0].terminal == "ok"

    class ErrC(FakeClient):
        def send_message(self, *a, **k):
            return {"error": {"message": "boom"}}

    assert fan_out([AgentRef(name="e", url="http://e:9900")], "x", ErrC())[0].terminal == "error"

    class FailC(FakeClient):
        def send_message(self, *a, **k):
            return {"result": {"task": {"status": {"state": "failed"}}}}

    assert fan_out([AgentRef(name="f", url="http://f:9900")], "x", FailC())[0].terminal == "failed"
