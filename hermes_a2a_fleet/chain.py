"""Sequential A2A chain — each agent's reply becomes the next agent's input.

Where :func:`fanout.fan_out` (scatter-gather) sends ONE message to MANY peers in
parallel, a chain threads ONE message through an ORDERED list of peers:
``agents[0]`` receives the prompt, ``agents[1]`` receives ``agents[0]``'s reply,
and so on. The last agent's reply is the chain's output.

A broken link has no output to feed forward, so a failed step SHORT-CIRCUITS the
chain: execution stops and the trace records exactly how far it got. This is the
deliberate opposite of fan-out's partial-results behaviour — a parallel peer
failing is independent; a pipeline step failing is fatal to everything after it.

Each agent gets the caller's ``context_id`` (usually empty) — an A2A context id
is per-peer, so threading one agent's context into a different agent would be
meaningless. Only the TEXT flows down the chain.

``run_chain`` is pure w.r.t. an injected client, so its invariants (step count,
input/output threading, short-circuit) are property-tested without a network.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .client import interpret_result
from .types import AgentRef


@dataclass
class ChainStep:
    agent: str
    ok: bool
    sent: str = ""        # the input this agent received
    reply: str = ""       # its output (fed to the next agent on success)
    error: str = ""
    elapsed_ms: int = 0


@dataclass
class ChainResult:
    steps: list[ChainStep] = field(default_factory=list)
    completed: bool = False   # every agent ran AND succeeded
    final: str = ""           # the last agent's reply (the chain's output)

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.steps if s.ok)


def run_chain(
    agents: Iterable[AgentRef],
    message: str,
    client,
    *,
    timeout: int = 30,
    context_id: str = "",
    clock=time.monotonic,
) -> ChainResult:
    """Thread ``message`` through ``agents`` in order; stop at the first failure.

    Invariants:
    - ``completed`` => ``len(steps) == len(agents)`` and every step succeeded.
    - not ``completed`` => the LAST recorded step is the only failed one (no
      agent after the broken link is contacted).
    - ``steps[i].sent == steps[i-1].reply`` (one agent's output is the next's
      input).
    """
    result = ChainResult()
    current = message
    for ref in agents:
        start = clock()
        try:
            resp = client.send_message(
                ref.rpc_url or ref.url, current,
                auth=ref.auth, timeout=timeout, context_id=context_id,
            )
            elapsed = int((clock() - start) * 1000)
        except Exception as e:  # a dead link breaks the pipeline, never silently skipped
            result.steps.append(ChainStep(
                ref.name, False, sent=current, error=str(e),
                elapsed_ms=int((clock() - start) * 1000),
            ))
            return result
        out = interpret_result(client, resp)
        if not out.ok:
            result.steps.append(ChainStep(
                ref.name, False, sent=current, reply=out.reply,
                error=out.error, elapsed_ms=elapsed,
            ))
            return result
        result.steps.append(ChainStep(ref.name, True, sent=current, reply=out.reply, elapsed_ms=elapsed))
        current = out.reply   # output -> next agent's input
    if not result.steps:
        return result          # zero agents: not 'completed', final stays empty
    result.completed = True     # fell through the loop with no early return -> all ok
    result.final = current
    return result


def summarize_chain(result: ChainResult) -> str:
    """Render the chain trace: numbered steps + final output (or break point)."""
    if not result.steps:
        return "Chain had no agents — nothing to run."
    n = len(result.steps)
    if result.completed:
        lines = [f"Chain completed ({n}/{n} agents)."]
    else:
        lines = [f"Chain broke at step {n} ({result.ok_count}/{n} succeeded)."]
    for i, s in enumerate(result.steps, 1):
        if s.ok:
            lines.append(f"\n[{i}. {s.agent} · {s.elapsed_ms}ms]\n{s.reply or '(no text reply)'}")
        else:
            lines.append(f"\n[{i}. {s.agent} · BROKEN] {s.error}")
    if result.completed:
        lines.append(f"\n— final output —\n{result.final or '(empty)'}")
    return "\n".join(lines)
