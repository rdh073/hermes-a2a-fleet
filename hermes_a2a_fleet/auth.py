"""Auth resolution for discovered peers.

mDNS / tailnet discovery yields peers with NO credentials, but a real agent
gates its agent card behind a bearer token (verified live: an unauthenticated
card fetch returns 403, so the Registry would drop the peer). This resolver
attaches a token to a discovered peer by host, so a gated agent can be verified
and called without being hand-listed in static config.

Precedence (most specific first): ``host:port`` -> ``host`` -> a fleet default.
A peer that already carries its own auth (e.g. from static config) keeps it —
the Registry only consults the resolver when ``ref.auth`` is empty.

Resolved by host, NOT advertised: mDNS only signals ``auth=bearer`` (a hint);
the token itself lives in local config and is never put on the wire by discovery.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse


def _host_keys(url: str) -> list[str]:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    keys: list[str] = []
    if host and parsed.port:
        keys.append(f"{host}:{parsed.port}")
    if host:
        keys.append(host)
    return keys


def build_auth_resolver(
    default_token: str = "",
    host_tokens: dict[str, str] | None = None,
) -> Callable[[str], dict]:
    """Return ``resolve(url) -> auth_dict`` ({} when nothing matches).

    ``host_tokens`` maps ``host`` or ``host:port`` to a bearer token string.
    """
    tokens = {str(k): str(v) for k, v in (host_tokens or {}).items() if v}

    def resolve(url: str) -> dict:
        for key in _host_keys(url):
            token = tokens.get(key)
            if token:
                return {"type": "bearer", "token": token}
        if default_token:
            return {"type": "bearer", "token": default_token}
        return {}

    return resolve
