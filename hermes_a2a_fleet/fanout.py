"""Parallel scatter-gather over a set of A2A peers.

A2A is unicast (one ``message/send`` per agent), so "run N agents in parallel"
is a client-side fan-out: one bounded thread pool, one attempt per peer, a hard
per-peer timeout, and partial results — a slow or offline peer never blocks the
batch and is reported, NOT retried (an unreachable node is a non-transient
failure).

``fan_out`` and ``summarize`` are pure w.r.t. an injected client, so their
invariants (count conservation, ok/err partition, first-mode semantics) are
property-tested without a network.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout

from .types import AgentRef, FanResult


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(value, hi))


def fan_out(
    agents: Iterable[AgentRef],
    message: str,
    client,
    *,
    max_workers: int = 10,
    timeout: int = 30,
    context_id: str = "",
    deadline: float | None = None,
    stop_on_first: bool = False,
    clock=time.monotonic,
) -> list[FanResult]:
    """Send ``message`` to every agent concurrently; collect every outcome.

    Two liveness controls beyond the per-peer ``timeout``:

    - ``deadline`` (seconds): a WHOLE-CALL wall-clock cap. When it elapses, any
      peer still running is recorded as ``terminal='deadline'`` and the call
      returns — a single hung socket can no longer stall the batch.
    - ``stop_on_first``: return as soon as one peer SUCCEEDS; the rest are
      recorded as ``terminal='abandoned'``. We do NOT claim remote cancellation
      — a Python future already executing cannot be killed, so abandoned peers
      may still finish in the background; we simply stop waiting on them.

    Invariants: ``len(result) == len(agents)`` (exactly-one-terminal accounting —
    every peer appears once), and the call returns within roughly ``deadline``
    when one is set. Results are sorted by agent name for deterministic output.
    """
    agents = list(agents)
    if not agents:
        return []
    workers = _clamp(max_workers, 1, len(agents))

    def call(ref: AgentRef) -> FanResult:
        start = clock()
        try:
            resp = client.send_message(
                ref.rpc_url or ref.url,
                message,
                auth=ref.auth,
                timeout=timeout,
                context_id=context_id,
            )
            elapsed = int((clock() - start) * 1000)
            if isinstance(resp, dict) and resp.get("error"):
                err = resp["error"]
                msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                return FanResult(ref.name, False, error=f"peer error: {msg}", elapsed_ms=elapsed, terminal="error")
            result = resp.get("result", {}) if isinstance(resp, dict) else {}
            obj = client.unwrap_result(result)
            reply = client.reply_text(result)
            state = (obj.get("status") or {}).get("state", "") if isinstance(obj, dict) else ""
            ctx = obj.get("contextId", context_id) if isinstance(obj, dict) else context_id
            # A successful RPC can still carry a FAILED task — that is not 'ok'.
            if state in ("failed", "canceled", "cancelled", "rejected"):
                detail = f": {reply}" if reply else ""
                return FanResult(
                    ref.name, False, error=f"task {state}{detail}",
                    elapsed_ms=elapsed, context_id=ctx or "", terminal="failed",
                )
            return FanResult(
                ref.name, True, reply=reply,
                elapsed_ms=elapsed, context_id=ctx or "", terminal="ok",
            )
        except Exception as e:  # transform per-peer failure into a result, never abort the batch
            return FanResult(ref.name, False, error=str(e), elapsed_ms=int((clock() - start) * 1000), terminal="error")

    results: list[FanResult] = []
    ex = ThreadPoolExecutor(max_workers=workers)
    pending = {ex.submit(call, a): a for a in agents}
    timed_out = False
    try:
        try:
            for fut in as_completed(list(pending), timeout=deadline):
                results.append(fut.result())
                pending.pop(fut, None)
                if stop_on_first and results[-1].ok:
                    break
        except FuturesTimeout:
            timed_out = True  # whole-call deadline hit; pending peers are 'deadline'
        # Distinguish WHY a peer was left pending: the deadline fired
        # ('deadline'), or an earlier success ended the wait ('abandoned').
        reason = "deadline" if timed_out else "abandoned"
        detail = "deadline reached" if timed_out else "abandoned after an earlier success"
        for ref in pending.values():
            results.append(FanResult(ref.name, False, error=detail, terminal=reason))
    finally:
        # Do not block on stragglers — cancel pending (not-yet-started) futures and
        # let any already-running threads finish in the background (their per-peer
        # timeout bounds them). This is what makes the deadline real.
        ex.shutdown(wait=False, cancel_futures=True)
    results.sort(key=lambda r: r.agent)
    return results


def partition(results: Iterable[FanResult]) -> tuple[list[FanResult], list[FanResult]]:
    """Split into (succeeded, failed). ok + err always equals the input count."""
    oks, errs = [], []
    for r in results:
        (oks if r.ok else errs).append(r)
    return oks, errs


def summarize(results: list[FanResult], mode: str = "collect") -> str:
    """Render the fan-out outcome.

    - ``collect`` — every peer's reply (or error), plus an ok/total header.
    - ``first``   — the first successful reply; falls back to the error roll-up.
    """
    if not results:
        return "No agents matched — discovery returned nothing."
    oks, errs = partition(results)
    if mode == "first":
        if oks:
            top = min(oks, key=lambda r: r.elapsed_ms)
            return f"[{top.agent} · {top.elapsed_ms}ms]\n{top.reply or '(no text reply)'}"
        return "All peers failed:\n" + "\n".join(f"  - {r.agent}: {r.error}" for r in errs)

    lines = [f"Fan-out: {len(oks)}/{len(results)} succeeded."]
    for r in oks:
        lines.append(f"\n[{r.agent} · {r.elapsed_ms}ms]\n{r.reply or '(no text reply)'}")
    for r in errs:
        lines.append(f"\n[{r.agent} · FAILED] {r.error}")
    return "\n".join(lines)
