"""A2AClient: reply extraction (round-trip) + auth header derivation."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from hermes_a2a_fleet.client import A2AClient

# --- round-trip: text placed in an artifact comes back out ----------------

@given(st.text(min_size=1))
def test_reply_text_roundtrips_artifact_text(txt):
    result = {"artifacts": [{"parts": [{"kind": "text", "text": txt}]}]}
    assert A2AClient.reply_text(result) == txt.strip()


def test_reply_text_prefers_artifact_over_status():
    result = {
        "artifacts": [{"parts": [{"kind": "text", "text": "final"}]}],
        "status": {"message": {"parts": [{"kind": "text", "text": "interim"}]}},
    }
    assert A2AClient.reply_text(result) == "final"


def test_reply_text_falls_back_to_status_message():
    result = {"status": {"message": {"parts": [{"kind": "text", "text": "clarify?"}]}}}
    assert A2AClient.reply_text(result) == "clarify?"


def test_reply_text_bare_message():
    result = {"parts": [{"type": "text", "text": "bare"}]}  # legacy 'type' discriminator
    assert A2AClient.reply_text(result) == "bare"


# --- result.task / result.message wrapping (verified against a live A2A agent) ----

def test_reply_text_unwraps_result_task_wrapper():
    # some agents return the Task wrapped as result.task, not at the top level.
    result = {
        "task": {
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": "the answer"}]}],
        }
    }
    assert A2AClient.reply_text(result) == "the answer"


def test_reply_text_unwraps_result_message_wrapper():
    assert A2AClient.reply_text({"message": {"parts": [{"kind": "text", "text": "hi"}]}}) == "hi"


def test_task_state_unwraps_wrapper():
    assert A2AClient.task_state({"task": {"status": {"state": "failed"}}}) == "failed"
    assert A2AClient.task_state({"status": {"state": "completed"}}) == "completed"  # also un-wrapped
    assert A2AClient.task_state({}) == ""


# --- auth headers ----------------------------------------------------------

def test_auth_headers_bearer():
    assert A2AClient.auth_headers({"type": "bearer", "token": "sek"}) == {"Authorization": "Bearer sek"}


def test_auth_headers_absent_or_unsupported():
    assert A2AClient.auth_headers(None) == {}
    assert A2AClient.auth_headers({}) == {}
    assert A2AClient.auth_headers({"type": "basic"}) == {}
