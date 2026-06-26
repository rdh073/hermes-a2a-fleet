"""Sequential chain: ordered output->input threading, short-circuit, single==send."""

from __future__ import annotations

from _helpers import FakeClient, agentref_lists
from hypothesis import given

from hermes_a2a_fleet.chain import run_chain, summarize_chain
from hermes_a2a_fleet.types import AgentRef


def _chain(n):
    return [AgentRef(name=f"a{i}", url=f"http://h{i}:9900") for i in range(n)]


# --- core threading law -----------------------------------------------------

def test_chain_threads_each_output_into_the_next_input():
    res = run_chain(_chain(3), "hi", FakeClient())
    assert res.completed
    assert len(res.steps) == 3
    assert res.steps[0].sent == "hi"
    for i in range(1, 3):
        assert res.steps[i].sent == res.steps[i - 1].reply  # output -> next input
    # FakeClient wraps each input as reply:<input>, so it nests down the chain
    assert res.final == "reply:reply:reply:hi"
    assert res.steps[-1].reply == res.final


def test_chain_single_agent_equals_one_send():
    # metamorphic: a chain of length 1 is exactly a single send_message
    res = run_chain(_chain(1), "ping", FakeClient())
    assert res.completed and len(res.steps) == 1
    assert res.final == "reply:ping"


def test_chain_empty_is_empty():
    res = run_chain([], "x", FakeClient())
    assert res.steps == [] and not res.completed and res.final == ""
    assert "no agents" in summarize_chain(res).lower()


# --- short-circuit: a broken link stops everything after it ------------------

def test_chain_short_circuits_on_transport_failure():
    agents = [
        AgentRef(name="a0", url="http://h0:9900"),
        AgentRef(name="a1", url="http://bad:9900"),   # dead link
        AgentRef(name="a2", url="http://h2:9900"),    # must never be contacted
    ]
    client = FakeClient(fail_urls={"http://bad:9900"})
    res = run_chain(agents, "go", client)
    assert not res.completed
    assert [s.agent for s in res.steps] == ["a0", "a1"]  # stopped at the break
    assert res.steps[-1].ok is False and "down" in res.steps[-1].error
    assert "http://h2:9900" not in client.calls          # downstream untouched


def test_chain_short_circuits_on_failed_task():
    class FailMid(FakeClient):
        def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
            self.calls.append(rpc_url)
            if "mid" in rpc_url:
                return {"result": {"task": {"status": {"state": "failed"}}}}
            return super().send_message(rpc_url, message, auth=auth, timeout=timeout, context_id=context_id)

    agents = [
        AgentRef(name="a", url="http://a:9900"),
        AgentRef(name="b", url="http://mid:9900"),
        AgentRef(name="c", url="http://c:9900"),
    ]
    client = FailMid()
    res = run_chain(agents, "go", client)
    assert not res.completed
    assert [s.agent for s in res.steps] == ["a", "b"]
    assert "failed" in res.steps[-1].error
    assert "http://c:9900" not in client.calls


# --- property: step count + threading invariant over arbitrary chains -------

@given(agentref_lists)
def test_chain_step_count_and_threading_invariant(refs):
    # dedupe by url the way the registry would
    agents = list({r.url: r for r in refs}.values())
    res = run_chain(agents, "seed", FakeClient())
    # every (all-succeeding) agent yields exactly one step
    assert len(res.steps) == len(agents)
    assert res.completed == (len(agents) > 0)
    for i in range(1, len(res.steps)):
        assert res.steps[i].sent == res.steps[i - 1].reply
    if agents:
        assert res.final == res.steps[-1].reply


def test_summarize_chain_completed_shows_final():
    out = summarize_chain(run_chain(_chain(2), "hi", FakeClient()))
    assert "completed (2/2" in out and "final output" in out


def test_summarize_chain_broken_marks_break_point():
    client = FakeClient(fail_urls={"http://h1:9900"})
    out = summarize_chain(run_chain(_chain(3), "hi", client))
    assert "broke at step 2" in out and "BROKEN" in out
