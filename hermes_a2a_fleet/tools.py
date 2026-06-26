"""Hermes tools exposed by the plugin (toolset ``a2a_fleet``).

  - a2a_fleet_discover()  -> refresh discovery, list reachable A2A peers
  - a2a_fleet_list()      -> show the cached fleet (no network)
  - a2a_fleet_broadcast() -> send one task to many peers IN PARALLEL, aggregate
  - a2a_fleet_chain()     -> pipe one task through peers IN ORDER (output->input)
  - a2a_fleet_submit()    -> fire one task at a peer, return a task id to poll
  - a2a_fleet_poll()      -> poll a submitted task by id until it is terminal

These depend only on the ``Registry`` abstraction and the ``fan_out`` /
``run_chain`` functions; they know nothing about mDNS vs tailnet. The registry
is built once (lazily) at the composition root in registry.build_registry().
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .chain import run_chain, summarize_chain
from .client import TERMINAL_STATES, A2AClient, interpret_result
from .config import FleetConfig, load_fleet_config
from .fanout import fan_out, scatter, summarize
from .registry import Registry, build_registry

logger = logging.getLogger(__name__)

_registry: Registry | None = None
_transport: A2AClient | None = None
_config: FleetConfig | None = None

# Async submit/poll tracker: task_id -> {rpc_url, auth, agent}. A submitted task
# must be polled against the SAME peer that created it, so we remember where it
# went. In-memory and per-process on purpose — a submit and its polls live in
# one session; it is not a durable queue. Bounded so a never-polled flood cannot
# grow without limit (oldest is evicted, insertion-ordered).
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()
_MAX_TRACKED_TASKS = 256


def _components() -> tuple[Registry, A2AClient, FleetConfig]:
    """Lazily build + cache the (registry, transport, config) triple.

    The composition root: ONE A2AClient is created here and shared as both the
    registry's verification client and the fan-out transport, so the tools layer
    holds an explicit transport instead of reaching into Registry internals.
    """
    global _registry, _transport, _config
    if _registry is None:
        _config = load_fleet_config()
        _transport = A2AClient(default_timeout=_config.timeout)
        _registry = build_registry(_config, client=_transport)
    return _registry, _transport, _config


def set_components(registry: Registry, transport: A2AClient, config: FleetConfig) -> None:
    """Inject components (tests / host app)."""
    global _registry, _transport, _config
    _registry, _transport, _config = registry, transport, config


def _resolve_workers(requested, n_agents: int, cfg_max: int) -> int:
    """Thread-pool size: never above the configured cap, the request, or the
    agent count; never below 1. (Fixes broadcast ignoring max_concurrency.)"""
    cap = cfg_max if cfg_max and cfg_max > 0 else n_agents
    if requested:
        try:
            cap = min(cap, int(requested))
        except (TypeError, ValueError):
            pass
    return max(1, min(cap, n_agents))


def _resolve_deadline(requested, cfg_deadline):
    """Per-call deadline override; fall back to the configured one. A positive
    number wins; anything else (None / garbage / <=0) keeps the config value."""
    if requested is not None:
        try:
            d = float(requested)
            return d if d > 0 else cfg_deadline
        except (TypeError, ValueError):
            return cfg_deadline
    return cfg_deadline


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def a2a_fleet_discover(args: dict | None = None, **_: Any) -> str:
    reg = _components()[0]
    agents = reg.list(refresh=True)
    if not agents:
        return (
            "No A2A peers discovered. Check that sources are enabled "
            f"({', '.join(reg.source_names) or 'none'}) and that peers serve an agent card."
        )
    lines = [f"Discovered {len(agents)} A2A peer(s):"]
    for a in agents:
        caps = ", ".join(a.capabilities) if a.capabilities else "-"
        lines.append(f"  - {a.name}  [{a.source}]  {a.url}  caps: {caps}")
    return "\n".join(lines)


def a2a_fleet_list(args: dict | None = None, **_: Any) -> str:
    reg = _components()[0]
    agents = reg.list()
    if not agents:
        return "Fleet is empty (run a2a_fleet_discover first)."
    return "\n".join(f"  - {a.name}  {a.url}  caps: {', '.join(a.capabilities) or '-'}" for a in agents)


def a2a_fleet_broadcast(args: dict, **_: Any) -> str:
    message = str(args.get("message") or args.get("text") or "").strip()
    if not message:
        return "Error: 'message' is required."
    capability = str(args.get("capability") or "").strip() or None
    names = args.get("agents")
    mode = str(args.get("mode") or "collect").strip().lower()
    if mode not in ("collect", "first"):
        mode = "collect"

    reg, transport, cfg = _components()
    agents = reg.list(capability=capability)
    if isinstance(names, list) and names:
        wanted = {str(n) for n in names}
        agents = [a for a in agents if a.name in wanted]
    if not agents:
        scope = f" matching capability '{capability}'" if capability else ""
        return f"No agents{scope} to broadcast to. Run a2a_fleet_discover or check filters."

    workers = _resolve_workers(args.get("max_concurrency"), len(agents), cfg.max_concurrency)
    timeout = int(args.get("timeout") or cfg.timeout)
    deadline = _resolve_deadline(args.get("deadline"), cfg.deadline)
    results = fan_out(
        agents,
        message,
        transport,
        max_workers=workers,
        timeout=timeout,
        context_id=str(args.get("context_id") or ""),
        deadline=deadline,
        stop_on_first=(mode == "first"),
    )
    return summarize(results, mode=mode)


def a2a_fleet_dispatch(args: dict, **_: Any) -> str:
    tasks = args.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return "Error: 'tasks' must be a non-empty list of {agent, message} objects."

    reg, transport, cfg = _components()
    items: list[tuple] = []
    for i, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            return f"Error: task #{i} must be an object with 'agent' and 'message'."
        name = str(task.get("agent") or task.get("name") or "").strip()
        message = str(task.get("message") or task.get("text") or "").strip()
        if not name or not message:
            return f"Error: task #{i} needs both 'agent' and 'message'."
        ref = reg.get(name)
        if ref is None:
            return f"Error: unknown agent '{name}' in task #{i}. Run a2a_fleet_discover first."
        items.append((ref, message))

    workers = _resolve_workers(args.get("max_concurrency"), len(items), cfg.max_concurrency)
    timeout = int(args.get("timeout") or cfg.timeout)
    deadline = _resolve_deadline(args.get("deadline"), cfg.deadline)
    results = scatter(
        items,
        transport,
        max_workers=workers,
        timeout=timeout,
        context_id=str(args.get("context_id") or ""),
        deadline=deadline,
    )
    return summarize(results, mode="collect")


def a2a_fleet_chain(args: dict, **_: Any) -> str:
    message = str(args.get("message") or args.get("text") or "").strip()
    names = args.get("agents")
    if not message:
        return "Error: 'message' is required."
    if not isinstance(names, list) or not names:
        return "Error: 'agents' must be a non-empty, ORDERED list of peer names."

    reg, transport, cfg = _components()
    refs = []
    for name in names:
        ref = reg.get(str(name))
        if ref is None:
            return f"Error: unknown agent '{name}'. Run a2a_fleet_discover first."
        refs.append(ref)

    timeout = int(args.get("timeout") or cfg.timeout)
    result = run_chain(
        refs, message, transport,
        timeout=timeout, context_id=str(args.get("context_id") or ""),
    )
    return summarize_chain(result)


def a2a_fleet_submit(args: dict, **_: Any) -> str:
    agent = str(args.get("agent") or args.get("name") or "").strip()
    message = str(args.get("message") or args.get("text") or "").strip()
    if not agent:
        return "Error: 'agent' (a discovered peer name) is required."
    if not message:
        return "Error: 'message' is required."

    reg, transport, cfg = _components()
    ref = reg.get(agent)
    if ref is None:
        return f"Error: unknown agent '{agent}'. Run a2a_fleet_discover first."

    rpc_url = ref.rpc_url or ref.url
    try:
        resp = transport.send_message(
            rpc_url, message, auth=ref.auth,
            timeout=int(args.get("timeout") or cfg.timeout),
            context_id=str(args.get("context_id") or ""),
        )
    except Exception as e:  # a dead peer is a non-transient failure — report, don't retry
        return f"Error: submit to '{agent}' failed — {e}"

    out = interpret_result(transport, resp)
    if out.kind == "error":
        return f"[{agent}] peer rejected the task: {out.error}"
    if not out.task_id:
        # The peer answered synchronously with a bare Message — nothing to poll.
        return f"[{agent}] answered synchronously (no task id):\n{out.reply or '(empty reply)'}"
    if out.state == "completed":
        return f"[{agent}] task {out.task_id} completed synchronously:\n{out.reply or '(no text reply)'}"
    if out.state in TERMINAL_STATES:
        return f"[{agent}] task {out.task_id} ended '{out.state}': {out.error}"

    with _tasks_lock:
        if len(_tasks) >= _MAX_TRACKED_TASKS:
            _tasks.pop(next(iter(_tasks)))  # evict oldest; bounds an unpolled flood
        _tasks[out.task_id] = {"rpc_url": rpc_url, "auth": ref.auth, "agent": agent}
    return (
        f"Submitted to {agent}. task_id={out.task_id} state={out.state or 'submitted'}.\n"
        "Poll with a2a_fleet_poll(task_id) to retrieve the result."
    )


def a2a_fleet_poll(args: dict, **_: Any) -> str:
    task_id = str(args.get("task_id") or args.get("id") or "").strip()
    if not task_id:
        return "Error: 'task_id' (returned by a2a_fleet_submit) is required."

    with _tasks_lock:
        entry = _tasks.get(task_id)
    if entry is None:
        return (
            f"Error: unknown task_id '{task_id}'. Submit it this session — the "
            "tracker is in-memory and does not survive a restart."
        )

    _, transport, cfg = _components()
    try:
        resp = transport.get_task(
            entry["rpc_url"], task_id, auth=entry["auth"],
            timeout=int(args.get("timeout") or cfg.timeout),
        )
    except Exception as e:
        return f"Error: poll '{task_id}' failed — {e}"

    out = interpret_result(transport, resp)
    if out.kind == "error":
        return f"[{entry['agent']} · task {task_id}] poll error: {out.error}"

    state = out.state or "unknown"
    if out.state == "completed" or out.state in TERMINAL_STATES:
        with _tasks_lock:
            _tasks.pop(task_id, None)  # terminal — stop tracking
    head = f"[{entry['agent']} · task {task_id} · {state}]"
    if out.state in TERMINAL_STATES:
        return f"{head}\n{out.error}"
    return f"{head}\n{out.reply or '(working — no output yet)'}"


# --------------------------------------------------------------------------
# Schemas + registration
# --------------------------------------------------------------------------

_SCHEMAS: dict[str, dict] = {
    "a2a_fleet_discover": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_discover",
            "description": (
                "Discover all reachable A2A peer agents on the fleet (via mDNS on "
                "the LAN and/or a tailnet sweep) and list them with their "
                "capabilities. Run this before broadcasting to refresh the roster."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "a2a_fleet_list": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_list",
            "description": "List the currently-known A2A fleet peers from cache (no network call).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "a2a_fleet_broadcast": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_broadcast",
            "description": (
                "Send one task to MANY A2A peers in parallel and aggregate the "
                "replies (scatter-gather). Optionally filter peers by 'capability' "
                "or an explicit 'agents' name list. mode='collect' returns every "
                "reply; mode='first' returns the fastest successful one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The task to send every selected peer."},
                    "capability": {"type": "string", "description": "Optional: only peers advertising this skill tag."},
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: explicit peer names to target (overrides capability filter scope).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["collect", "first"],
                        "description": (
                            "collect = every reply; first = return as soon as one peer "
                            "succeeds (the rest are abandoned)."
                        ),
                    },
                    "deadline": {
                        "type": "number",
                        "description": (
                            "Optional whole-call deadline (seconds). Peers not finished by "
                            "then are reported as 'deadline' instead of stalling the batch."
                        ),
                    },
                    "context_id": {"type": "string", "description": "Optional A2A context id to continue an exchange."},
                },
                "required": ["message"],
            },
        },
    },
    "a2a_fleet_dispatch": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_dispatch",
            "description": (
                "Send DIFFERENT tasks to different peers IN PARALLEL and aggregate "
                "the replies. Unlike broadcast (one shared message), each entry in "
                "'tasks' pairs a peer name with its own message — use this to give "
                "each agent its own job at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "agent": {"type": "string", "description": "Peer name to send this task to."},
                                "message": {"type": "string", "description": "The task for THIS peer."},
                            },
                            "required": ["agent", "message"],
                        },
                        "description": "Per-peer work items, each its own {agent, message}.",
                    },
                    "deadline": {
                        "type": "number",
                        "description": (
                            "Optional whole-call deadline (seconds); unfinished peers are "
                            "reported instead of stalling the batch."
                        ),
                    },
                    "context_id": {"type": "string", "description": "Optional A2A context id to continue an exchange."},
                },
                "required": ["tasks"],
            },
        },
    },
    "a2a_fleet_chain": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_chain",
            "description": (
                "Pipe one task through peers IN ORDER: the first agent's reply "
                "becomes the second agent's input, and so on (a sequential "
                "pipeline). A failed step breaks the chain and reports how far it "
                "got. 'agents' is an ORDERED list of peer names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The initial input handed to the first agent."},
                    "agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered peer names; output of each feeds the next.",
                    },
                    "context_id": {"type": "string", "description": "Optional A2A context id passed to each step."},
                },
                "required": ["message", "agents"],
            },
        },
    },
    "a2a_fleet_submit": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_submit",
            "description": (
                "Fire one task at a single peer WITHOUT waiting for the answer "
                "(async). Returns a task_id to poll later with a2a_fleet_poll. If "
                "the peer answers immediately, the reply is returned inline instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Peer name to send the task to."},
                    "message": {"type": "string", "description": "The task to send."},
                    "context_id": {"type": "string", "description": "Optional A2A context id to continue an exchange."},
                },
                "required": ["agent", "message"],
            },
        },
    },
    "a2a_fleet_poll": {
        "type": "function",
        "function": {
            "name": "a2a_fleet_poll",
            "description": (
                "Poll a task submitted with a2a_fleet_submit by its task_id. "
                "Returns the current state and, once terminal, the result. The "
                "tracker is in-memory — poll within the same session you submitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "The id returned by a2a_fleet_submit."},
                },
                "required": ["task_id"],
            },
        },
    },
}

_HANDLERS = {
    "a2a_fleet_discover": a2a_fleet_discover,
    "a2a_fleet_list": a2a_fleet_list,
    "a2a_fleet_broadcast": a2a_fleet_broadcast,
    "a2a_fleet_dispatch": a2a_fleet_dispatch,
    "a2a_fleet_chain": a2a_fleet_chain,
    "a2a_fleet_submit": a2a_fleet_submit,
    "a2a_fleet_poll": a2a_fleet_poll,
}


def register_tools(ctx) -> None:
    """Register every fleet tool in the ``a2a_fleet`` toolset."""
    for name, schema in _SCHEMAS.items():
        ctx.register_tool(
            name=name,
            toolset="a2a_fleet",
            schema=schema,
            handler=_HANDLERS[name],
            description=schema["function"]["description"],
            emoji="\U0001f578",  # spider web — the mesh
        )
