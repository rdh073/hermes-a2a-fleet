"""Registry — the discovery PORT the orchestrator depends on.

The fan-out / tools layer talks to a ``Registry`` only; it never knows whether
agents came from static config, mDNS, or a tailnet sweep. Concrete
``DiscoverySource`` implementations are wired in at the composition root
(:func:`build_registry`), so adding a new source is additive — zero edits here
or in the caller (Dependency Inversion).

Pure helpers (:func:`dedupe_by_url`, :func:`caps_from_card`,
:func:`filter_by_capability`) are kept module-level and side-effect-free so the
invariants below can be property-tested without any network.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

from .client import A2AClient
from .sources.base import DiscoverySource
from .types import AgentRef

# Only these (dynamically discovered) sources get a token from the fleet auth
# resolver. A static peer carries its own auth and must NEVER receive the fleet
# default token (it could be a third-party / legacy URL).
DISCOVERED_SOURCES = {"mdns", "tailnet"}

# --------------------------------------------------------------------------
# Pure helpers (property-tested)
# --------------------------------------------------------------------------

def _norm_url(url: str) -> str:
    return (url or "").rstrip("/").lower()


def _same_origin(a: str, b: str) -> bool:
    """True if two URLs share scheme + host + port. Fails CLOSED on a parse
    error — an attacker-controlled malformed port (urlparse(...).port raises
    ValueError) must be treated as cross-origin, not crash verification."""
    try:
        pa, pb = urlparse(a), urlparse(b)
        return bool(pa.hostname) and (pa.scheme, pa.hostname, pa.port) == (pb.scheme, pb.hostname, pb.port)
    except ValueError:
        return False


def _reorigin(foreign_url: str, base_url: str) -> str:
    """Keep the RPC PATH a (cross-origin) card advertises, but on the origin we
    actually reached. A multi-homed agent may advertise a card url on a
    different address than the one we discovered it at (e.g. its LAN IP while we
    reached it over a tailnet); we must not follow that to a foreign host, but
    dropping the path entirely would break the call. Splicing the card's path
    onto the reached origin is safe — same host we already trust — and keeps the
    ``/a2a`` endpoint. Falls back to ``base_url`` if the card has no usable path.
    """
    try:
        fu, bu = urlparse(foreign_url), urlparse(base_url)
        path = fu.path if fu.path.startswith("/") else ""
        return f"{bu.scheme}://{bu.netloc}{path}" if path else base_url
    except ValueError:
        return base_url


def dedupe_by_url(refs: Iterable[AgentRef]) -> list[AgentRef]:
    """Collapse refs sharing a base URL, keeping the first seen.

    Invariant: the result has no two refs with the same normalized URL, and
    every distinct URL from the input survives exactly once (conservation).
    Idempotent: dedupe_by_url(dedupe_by_url(x)) == dedupe_by_url(x).
    """
    seen: set[str] = set()
    out: list[AgentRef] = []
    for ref in refs:
        key = _norm_url(ref.url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def caps_from_card(card: dict) -> tuple[str, ...]:
    """Extract coarse capability tags from an A2A agent card's skills."""
    caps: list[str] = []
    for skill in (card.get("skills") or []):
        if not isinstance(skill, dict):
            continue
        for tag in (skill.get("tags") or []):
            if isinstance(tag, str):
                caps.append(tag)
        name = skill.get("name")
        if isinstance(name, str):
            caps.append(name)
    # de-dup while preserving order
    out: list[str] = []
    for c in caps:
        if c not in out:
            out.append(c)
    return tuple(out)


def filter_by_capability(refs: Iterable[AgentRef], capability: str | None) -> list[AgentRef]:
    """Subset of refs matching ``capability`` (all of them when it is falsy)."""
    return [r for r in refs if r.matches(capability)]


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

class Registry:
    """Holds discovery sources + a verification client, caches the result.

    ``refresh()`` runs every source, dedupes, and verifies each candidate by
    fetching its agent card (which also fills in rpc_url + capabilities). Only
    reachable peers are kept unless ``drop_unreachable`` is False.
    """

    def __init__(
        self,
        sources: list[DiscoverySource],
        client: A2AClient,
        *,
        ttl: float = 60.0,
        drop_unreachable: bool = True,
        verify_timeout: int = 8,
        auth_resolver: Callable[[str], dict] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sources = sources
        self._client = client
        self._ttl = ttl
        self._drop_unreachable = drop_unreachable
        self._verify_timeout = verify_timeout
        self._auth_resolver = auth_resolver or (lambda _url: {})
        self._clock = clock
        self._cache: list[AgentRef] = []
        self._fetched_at: float | None = None

    @property
    def source_names(self) -> list[str]:
        return [s.name for s in self._sources]

    def _verify(self, ref: AgentRef) -> AgentRef | None:
        # A peer's own auth wins; otherwise resolve a token by host — but ONLY
        # for discovered (mDNS/tailnet) peers, so the fleet default token never
        # leaks to a static/third-party URL that brought no auth of its own.
        resolved = self._auth_resolver(ref.url) if ref.source in DISCOVERED_SOURCES else {}
        auth = ref.auth or resolved
        card = self._client.fetch_card(ref.url, auth=auth, timeout=self._verify_timeout)
        if not card:
            return None if self._drop_unreachable else ref
        # Trust the card's advertised url ONLY when it is the SAME host we
        # reached — a cross-origin url from an untrusted card would redirect
        # calls (SSRF) and send the bearer token to a different host.
        card_url = card.get("url")
        if isinstance(card_url, str) and card_url:
            # Same-origin: trust the card url as-is. Cross-origin: keep the card's
            # path but on the host WE reached (never follow it to a foreign host).
            rpc = card_url if _same_origin(card_url, ref.url) else _reorigin(card_url, ref.url)
        else:
            rpc = ref.url
        return AgentRef(
            name=card.get("name") or ref.name,
            url=ref.url,
            rpc_url=rpc,
            description=card.get("description", ref.description),
            capabilities=caps_from_card(card) or ref.capabilities,
            auth=auth,
            source=ref.source,
        )

    def refresh(self) -> list[AgentRef]:
        candidates: list[AgentRef] = []
        for src in self._sources:
            try:
                candidates.extend(src.discover())
            except Exception:
                # A broken source must not sink discovery from the others.
                continue
        verified: list[AgentRef] = []
        for ref in dedupe_by_url(candidates):
            try:
                got = self._verify(ref)
            except Exception:
                # One malformed peer (e.g. a bad card) must not sink the refresh.
                continue
            if got is not None:
                verified.append(got)
        verified.sort(key=lambda r: r.name.lower())
        self._cache = verified
        self._fetched_at = self._clock()
        return verified

    def _fresh(self) -> bool:
        return self._fetched_at is not None and (self._clock() - self._fetched_at) < self._ttl

    def list(self, capability: str | None = None, *, refresh: bool = False) -> list[AgentRef]:
        if refresh or not self._fresh():
            self.refresh()
        return filter_by_capability(self._cache, capability)

    def get(self, name: str) -> AgentRef | None:
        for ref in self.list():
            if ref.name == name:
                return ref
        return None


def build_registry(config=None, client: A2AClient | None = None) -> Registry:
    """Composition root: the ONE place that imports concrete sources.

    Reads the plugin config, instantiates the enabled discovery sources, and
    returns a ready Registry. Importing concretes only here keeps the rest of
    the package depending on the ``DiscoverySource`` / ``Registry`` abstractions.
    The verification client may be supplied so the caller can share ONE
    transport between verification and the fan-out (the composition root then
    owns it, rather than callers reaching into ``Registry`` internals).
    """
    from .auth import build_auth_resolver
    from .config import load_fleet_config
    from .sources.mdns_source import MdnsSource
    from .sources.static_source import StaticSource
    from .sources.tailnet_source import TailnetSource

    cfg = config or load_fleet_config()
    available: dict[str, Callable[[], DiscoverySource]] = {
        "static": lambda: StaticSource(cfg),
        "mdns": lambda: MdnsSource(cfg),
        "tailnet": lambda: TailnetSource(cfg),
    }
    sources = [available[name]() for name in cfg.sources if name in available]
    if client is None:
        client = A2AClient(default_timeout=cfg.timeout)
    return Registry(
        sources,
        client,
        ttl=cfg.cache_ttl,
        drop_unreachable=cfg.drop_unreachable,
        verify_timeout=cfg.verify_timeout,
        auth_resolver=build_auth_resolver(cfg.auth_default, cfg.auth_hosts),
    )
