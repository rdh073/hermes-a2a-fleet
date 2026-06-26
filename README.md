# hermes-a2a-fleet

Multi-agent **A2A discovery + parallel fan-out** plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

The upstream Hermes `a2a` plugin lets the agent discover and call **one** peer
at a time (`a2a_discover(url)`, `a2a_call(agent, msg)`). This plugin is the
**fleet** side:

- **Existence discovery** — find *many* A2A agents automatically, no
  hand-listed URLs: zero-config **mDNS auto-discovery** on the LAN (a continuous
  listener that reflects agents joining and leaving in real time) and/or a
  tailnet sweep across networks.
- **Parallel execution** — broadcast one task to all of them at once
  (scatter-gather), or `dispatch` a *different* task to each peer in parallel,
  and aggregate the replies.
- **Sequential pipelines** — `chain` a task through peers in order (each
  agent's reply feeds the next); a broken link short-circuits and reports.
- **Async / fire-and-forget** — `submit` a task and get a `task_id`, then
  `poll` it to completion (A2A `tasks/get`).

It is **standalone** (no dependency on the upstream `a2a` plugin) and makes
**zero core edits** — it registers tools through the public `ctx` surface, the
same contract upstream uses.

## Why a separate Registry

A2A is unicast (one `message/send` per agent), so "run 10 agents in parallel" is
a client-side fan-out. The piece that makes it *less coupled* is the **Registry
port**: the broadcast/orchestration logic depends only on `Registry`, never on
mDNS or Tailscale directly. Discovery sources are wired in at one composition
root (`registry.build_registry`), so adding a new source is additive.

```
tools / broadcast ──depends on──▶ Registry (port)
                                    ▲  composed at build_registry()
              ┌──────────┬─────────┴───────────┐
        StaticSource   MdnsSource          TailnetSource
        (config)       (_a2a._tcp LAN)     (tailscale status sweep)
```

Each source *proposes* candidates; the Registry *verifies* them by fetching the
A2A agent card (which also fills in capabilities), so an online node that is not
running A2A is dropped automatically.

## Tools (toolset `a2a_fleet`)

| Tool | What it does |
|---|---|
| `a2a_fleet_discover` | Refresh discovery; list reachable peers + capabilities |
| `a2a_fleet_list` | Show the cached fleet (no network) |
| `a2a_fleet_broadcast` | Send **one** task to many peers **in parallel**, aggregate (`mode=collect\|first`, optional `capability` / `agents` filter) |
| `a2a_fleet_dispatch` | Send a **different** task to each peer **in parallel** (`tasks=[{agent, message}, …]`), aggregate |
| `a2a_fleet_chain` | Pipe one task through peers **in order** (`agents=[a, b, c]`); each reply feeds the next, a failed step breaks the chain |
| `a2a_fleet_submit` | Fire one task at a peer **without waiting**; returns a `task_id` |
| `a2a_fleet_poll` | Poll a submitted `task_id` for its state + result (A2A `tasks/get`) |

## Install

Drop-in (matches upstream plugin layout):

```bash
cp -r hermes_a2a_fleet plugin.yaml ~/.hermes/plugins/a2a-fleet/
# optional LAN discovery:
pip install "zeroconf>=0.131"
```

Loaded on next Hermes start; enable the `a2a_fleet` toolset.

## Configure (Hermes `config.yaml`)

```yaml
a2a_fleet:
  sources: [static, tailnet, mdns]   # default: [static, tailnet]
  timeout: 30                        # per-peer call timeout (s)
  deadline: 20                       # optional whole-call cap (s); peers past it -> 'deadline'
  max_concurrency: 10
  probe_ports: [9900]                # ports the tailnet sweep probes per node
  cache_ttl: 60
  auth:                              # bearer tokens for DISCOVERED (mDNS/tailnet) peers
    default: ""                      # fleet-wide token for any peer without a host match
    hosts:                           # host[:port] -> token (overrides default)
      "100.64.0.7:9900": "tok-…"
  agents:                            # static peers (merged with upstream a2a_agents)
    researcher: { url: "http://100.64.0.5:9900", auth: { type: bearer, token: "..." } }
```

Env overrides: `A2A_FLEET_SOURCES`, `TAILSCALE_BIN`.

## Coverage & fleet onboarding

How a peer becomes discoverable depends on the network:

| Network | Source | New-peer coverage |
|---|---|---|
| Same LAN | `mdns` (continuous listener) | **instant** — a peer's mDNS announce lands in the live set immediately |
| Across a tailnet | `tailnet` (sweep) | **next refresh** (≤ `cache_ttl`) — the node is enumerated, probed, and added if it serves A2A |

The tailnet sweep enumerates *every* node, so a new device is **auto-covered**
(zero manual steps) only when three conventions hold. Template them once and new
agents join hands-free:

1. **Tag on join.** Gate A2A traffic with an ACL on a tag, and have agents carry
   that tag from their auth key — the ACL then applies the moment they join (an
   *untagged* node is blocked and never reachable). With Headscale:
   ```jsonc
   // ACL: let orchestrators reach agents on the A2A port
   { "action": "accept", "src": ["tag:hub"], "dst": ["tag:agent:9900"] }
   ```
   ```bash
   # an auth key that auto-tags every device created with it
   headscale preauthkeys create --user <id> --reusable --tags tag:agent -e 24h
   ```
2. **Fleet default token.** If agents gate their card behind a bearer
   (recommended), set one fleet token so a freshly-discovered peer verifies
   without per-host config:
   ```yaml
   a2a_fleet: { auth: { default: "${A2A_FLEET_TOKEN}" } }
   ```
   `auth.hosts` overrides it when a specific peer needs its own token.
3. **Port convention.** Agents serve A2A on a port listed in `probe_ports`, so
   the sweep probes the right port.

With all three: a new agent = *join the tailnet* → auto-tagged → ACL-reachable →
card verifies with the fleet token → in the registry on the next refresh, with
no edits to the orchestrator.

> Discovery stays reachability-only: each agent's own bearer gate decides
> access; the fleet token just lets the registry read the card to verify it.

## Design notes

- **One scatter primitive.** Broadcast and dispatch are the same operation over
  a different work-list — the unit is a `(peer, message)` pair, so broadcast is
  "all pairs share the message" and dispatch is "a message per pair". No second
  code path.
- **Chain vs fan-out failure semantics are opposites — on purpose.** A parallel
  peer failing is *independent* (partial results); a pipeline step failing is
  *fatal* to everything after it (no output to feed forward), so `chain`
  short-circuits and reports how far it got.
- **One response decoder.** Fan-out, chain, and submit/poll all route a raw
  `message/send` / `tasks/get` response through `interpret_result`, so "ok",
  "reply", the task id, and the lifecycle state mean the same thing everywhere.
- **Async tracker is in-memory + same-process.** `submit` returns a per-peer
  handle (`agent#task_id`) and remembers which peer it went to so `poll` hits the
  same agent with the peer's own id. Task ids are unique only per agent, so the
  handle is namespaced to avoid cross-peer collisions. It is not a durable or
  cross-process queue — submit and poll within one running session.
- **Partial failure, never retry a dead peer.** The fan-out makes one attempt
  per peer with a hard timeout; offline peers are *reported*, not retried (an
  unreachable node is non-transient).
- **Discovery = reachability hint only.** Each peer's own bearer gate still
  decides access; the registry never trusts a candidate without a card.
- **Degrade, don't crash.** No `zeroconf` → `mdns` source returns `[]`; no
  `tailscale` binary → `tailnet` source returns `[]`. The others keep working.

## Develop / test

```bash
pip install -e ".[dev]"
pytest
```

The suite is property-based where the input space is large (hypothesis):
`dedupe_by_url` idempotence/conservation, capability filtering, fan-out count
conservation + ok/err partition, and reply round-trip.
