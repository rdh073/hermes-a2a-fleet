"""Test doubles + hypothesis strategies shared across the suite."""

from __future__ import annotations

from hypothesis import strategies as st

from hermes_a2a_fleet.client import A2AClient
from hermes_a2a_fleet.types import AgentRef

_HOSTS = ["a", "b", "c", "host1", "host2", "100.64.0.5"]
_PORTS = [9900, 9000, 8080]
_CAPS = ["chat", "search", "code", "vision"]

url_strategy = st.builds(
    lambda h, p: f"http://{h}:{p}",
    st.sampled_from(_HOSTS),
    st.sampled_from(_PORTS),
)

agentref_strategy = st.builds(
    AgentRef,
    name=st.text(alphabet="abcXYZ", min_size=1, max_size=4),
    url=url_strategy,
    capabilities=st.lists(st.sampled_from(_CAPS), max_size=3).map(tuple),
)

agentref_lists = st.lists(agentref_strategy, max_size=12)


class FakeClient:
    """Stand-in for A2AClient: deterministic, no network.

    ``cards`` maps base-url -> agent-card dict (return for fetch_card).
    ``fail_urls`` are rpc/base urls whose send_message raises (simulating an
    offline peer).
    """

    def __init__(self, fail_urls=None, cards=None):
        self.fail_urls = set(fail_urls or [])
        self.cards = {k.rstrip("/"): v for k, v in (cards or {}).items()}
        self.calls: list[str] = []

    def fetch_card(self, base_url, auth=None, timeout=8):
        return self.cards.get(base_url.rstrip("/"))

    def send_message(self, rpc_url, message, auth=None, timeout=30, context_id=""):
        self.calls.append(rpc_url)
        if rpc_url in self.fail_urls:
            raise ConnectionError("peer down")
        return {
            "result": {
                "artifacts": [{"parts": [{"kind": "text", "text": f"reply:{message}"}]}],
                "contextId": context_id or "c1",
            }
        }

    @staticmethod
    def reply_text(result):
        return A2AClient.reply_text(result)

    @staticmethod
    def unwrap_result(result):
        return A2AClient.unwrap_result(result)
