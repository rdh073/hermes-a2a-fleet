"""Registry: pure-helper properties + Registry verification behaviour."""

from __future__ import annotations

from _helpers import FakeClient, agentref_lists
from hypothesis import given
from hypothesis import strategies as st

from hermes_a2a_fleet.registry import (
    Registry,
    caps_from_card,
    dedupe_by_url,
    filter_by_capability,
)
from hermes_a2a_fleet.types import AgentRef


def _norm(u: str) -> str:
    return u.rstrip("/").lower()


# --- dedupe_by_url : idempotence, conservation, no-duplicates ---------------

@given(agentref_lists)
def test_dedupe_is_idempotent(refs):
    once = dedupe_by_url(refs)
    assert dedupe_by_url(once) == once


@given(agentref_lists)
def test_dedupe_conserves_distinct_urls(refs):
    out = dedupe_by_url(refs)
    in_urls = {_norm(r.url) for r in refs if r.url}
    out_urls = {_norm(r.url) for r in out}
    assert in_urls == out_urls


@given(agentref_lists)
def test_dedupe_output_has_no_duplicate_urls(refs):
    out = dedupe_by_url(refs)
    keys = [_norm(r.url) for r in out]
    assert len(keys) == len(set(keys))


# --- filter_by_capability : subset + correctness ---------------------------

@given(agentref_lists, st.sampled_from(["chat", "search", "code", "vision", None]))
def test_filter_returns_only_matching_subset(refs, cap):
    out = filter_by_capability(refs, cap)
    assert all(r in refs for r in out)
    assert all(r.matches(cap) for r in out)


@given(agentref_lists)
def test_filter_none_capability_returns_all(refs):
    assert filter_by_capability(refs, None) == refs


# --- caps_from_card --------------------------------------------------------

def test_caps_from_card_pulls_tags_and_names_without_dupes():
    card = {
        "skills": [
            {"name": "search", "tags": ["web", "search"]},
            {"name": "search", "tags": ["search"]},  # repeats collapse
            {"id": "x"},  # no name/tags -> contributes nothing
        ]
    }
    caps = caps_from_card(card)
    assert caps == ("web", "search")


def test_caps_from_card_empty():
    assert caps_from_card({}) == ()


# --- Registry.refresh : verification drops unreachable, fills from card -----

def test_registry_keeps_reachable_drops_unreachable():
    refs = [
        AgentRef(name="up", url="http://h1:9900", source="static"),
        AgentRef(name="down", url="http://h2:9900", source="static"),
    ]

    class OneSource:
        name = "fake"

        def discover(self):
            return refs

    client = FakeClient(cards={"http://h1:9900": {"name": "Up Agent", "skills": [{"name": "chat"}]}})
    reg = Registry([OneSource()], client, ttl=0)
    out = reg.refresh()
    assert [a.name for a in out] == ["Up Agent"]            # reachable kept, renamed from card
    assert out[0].capabilities == ("chat",)                 # caps filled from card
    assert reg.get("Up Agent") is not None
    assert reg.get("down") is None


def test_registry_dedupes_across_sources():
    shared = AgentRef(name="dup", url="http://h1:9900/", source="a")
    other = AgentRef(name="dup2", url="http://h1:9900", source="b")  # same url, trailing slash diff

    class S:
        def __init__(self, refs, n):
            self._refs, self.name = refs, n

        def discover(self):
            return self._refs

    client = FakeClient(cards={"http://h1:9900": {"name": "H1"}})
    reg = Registry([S([shared], "a"), S([other], "b")], client, ttl=0)
    out = reg.refresh()
    assert len(out) == 1


def test_registry_broken_source_does_not_sink_others():
    good = AgentRef(name="g", url="http://h1:9900", source="good")

    class Broken:
        name = "broken"

        def discover(self):
            raise RuntimeError("boom")

    class Good:
        name = "good"

        def discover(self):
            return [good]

    client = FakeClient(cards={"http://h1:9900": {"name": "G"}})
    reg = Registry([Broken(), Good()], client, ttl=0)
    out = reg.refresh()
    assert [a.name for a in out] == ["G"]
