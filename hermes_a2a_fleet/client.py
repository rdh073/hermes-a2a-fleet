"""Minimal, self-contained A2A JSON-RPC client (stdlib only).

Vendored on purpose: this plugin must work whether or not the upstream Hermes
``a2a`` platform plugin is installed, so we do NOT import its ``protocol``
module. Wire shape follows the A2A spec — Agent Card at a well-known path,
tasks via JSON-RPC 2.0 ``message/send``.
"""

from __future__ import annotations

import json
import urllib.request
import uuid
from typing import Any

# Newer A2A spec uses agent-card.json; older Hermes builds serve agent.json.
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")

# Discovered peers can be untrusted; never load an unbounded response body.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _read_capped(resp) -> bytes:
    """Read at most MAX_RESPONSE_BYTES; reject anything larger (memory DoS)."""
    data = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} bytes")
    return data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow HTTP redirects. urllib would otherwise follow a peer's
    3xx and forward the ``Authorization`` header to the redirect target —
    leaking the bearer token cross-origin BEFORE the agent-card url is validated.
    Returning None makes urllib raise the 3xx as an error instead of following.
    """

    def redirect_request(self, *args, **kwargs):
        return None


# One opener whose redirect handler refuses redirects; reused for every request.
_OPENER = urllib.request.build_opener(_NoRedirect)


def build_send_body(text: str, context_id: str = "") -> dict:
    """Build the A2A JSON-RPC request body for sending a message.

    The method is ``message/send`` — the JSON-RPC binding's name for the A2A
    SendMessage operation (the gRPC binding names it ``SendMessage``; the
    JSON-RPC binding does not). This is exactly what the upstream Hermes ``a2a``
    plugin and other A2A agents speak, which is the interop target. Kept pure/standalone so
    the wire shape can be pinned by a test.
    """
    message: dict = {
        "role": "user",
        "parts": [{"kind": "text", "text": text}],
        "messageId": uuid.uuid4().hex,
    }
    if context_id:
        message["contextId"] = context_id
    return {
        "jsonrpc": "2.0",
        "id": "task-" + uuid.uuid4().hex[:16],
        "method": "message/send",
        "params": {"message": message},
    }


class A2AClient:
    """Tiny blocking A2A client. One instance is reused across the fan-out."""

    def __init__(self, default_timeout: int = 30) -> None:
        self.default_timeout = default_timeout

    # -- transport -----------------------------------------------------------

    @staticmethod
    def _get_json(url: str, headers: dict, timeout: int) -> Any:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 (no-redirect opener)
            return json.loads(_read_capped(resp).decode("utf-8"))

    @staticmethod
    def _post_json(url: str, body: dict, headers: dict, timeout: int) -> Any:
        data = json.dumps(body).encode("utf-8")
        hdrs = {"Content-Type": "application/json", **headers}
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        with _OPENER.open(req, timeout=timeout) as resp:  # noqa: S310 (no-redirect opener)
            return json.loads(_read_capped(resp).decode("utf-8"))

    @staticmethod
    def auth_headers(auth: dict | None) -> dict:
        if auth and auth.get("type") == "bearer" and auth.get("token"):
            return {"Authorization": f"Bearer {auth['token']}"}
        return {}

    # -- A2A operations ------------------------------------------------------

    def fetch_card(self, base_url: str, auth: dict | None = None, timeout: int | None = None) -> dict | None:
        """Return the agent card dict, or None if the peer serves none / is down."""
        headers = self.auth_headers(auth)
        t = timeout or min(self.default_timeout, 10)
        base = base_url.rstrip("/")
        for path in CARD_PATHS:
            try:
                card = self._get_json(base + path, headers, t)
                if isinstance(card, dict) and card.get("name"):
                    return card
            except Exception:
                continue
        return None

    def send_message(
        self,
        rpc_url: str,
        text: str,
        auth: dict | None = None,
        timeout: int | None = None,
        context_id: str = "",
    ) -> dict:
        """POST an A2A ``message/send`` task; return the raw JSON-RPC response.

        Raises on transport/HTTP failure — the fan-out layer turns that into a
        per-agent FanResult, so callers never need to retry here (a dead peer is
        a non-transient failure, not something to loop on).
        """
        body = build_send_body(text, context_id)
        return self._post_json(rpc_url, body, self.auth_headers(auth), timeout or self.default_timeout)

    @staticmethod
    def unwrap_result(result: Any) -> Any:
        """Return the inner Task/Message from a JSON-RPC ``result``.

        The result may BE the Task/Message directly, or be wrapped as
        ``result.task`` / ``result.message``. Verified against a real A2A server
        which wraps the Task in ``result.task`` — so extraction
        must unwrap to interoperate, not assume a top-level Task.
        """
        if isinstance(result, dict):
            inner = result.get("task") or result.get("message")
            if isinstance(inner, dict):
                return inner
        return result

    @staticmethod
    def task_state(result: Any) -> str:
        """The A2A task state (e.g. completed/failed/canceled), '' if none."""
        obj = A2AClient.unwrap_result(result)
        if isinstance(obj, dict):
            return (obj.get("status") or {}).get("state", "") or ""
        return ""

    @staticmethod
    def reply_text(result: Any) -> str:
        """Extract the human-readable reply from a message/send result."""
        obj = A2AClient.unwrap_result(result)
        if not isinstance(obj, dict):
            return str(obj)

        def parts_text(msg: Any) -> str:
            if not isinstance(msg, dict):
                return ""
            chunks = []
            for part in msg.get("parts", []) or []:
                if not isinstance(part, dict):
                    continue
                if part.get("kind") in (None, "text") or part.get("type") == "text":
                    txt = part.get("text")
                    if isinstance(txt, str):
                        chunks.append(txt)
            return "\n".join(chunks).strip()

        for artifact in obj.get("artifacts", []) or []:
            txt = parts_text(artifact)
            if txt:
                return txt
        status = obj.get("status", {}) or {}
        if status.get("message"):
            txt = parts_text(status["message"])
            if txt:
                return txt
        return parts_text(obj)
