# Changelog

All notable changes to this project are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/). MAJOR for breaking CLI/API/output
changes, MINOR for new features, PATCH for bug fixes.

## [Unreleased]

### Added

- `a2a_fleet_dispatch` — send a different task to each peer in parallel
  (`tasks=[{agent, message}, …]`), aggregating the replies.
- `a2a_fleet_chain` — pipe one task through peers in order; each agent's reply
  feeds the next, and a failed step short-circuits the chain.
- `a2a_fleet_submit` / `a2a_fleet_poll` — async fire-and-forget: submit a task,
  get a `task_id`, poll it to completion via A2A `tasks/get`.

### Changed

- Unified the parallel core on a single `scatter((peer, message) pairs)`
  primitive; `fan_out` (broadcast) is now a thin specialisation of it.
- Centralised A2A response decoding in `interpret_result`, shared by fan-out,
  chain, and submit/poll (removes the duplicated unwrap/state logic).

### Deprecated

### Removed

### Fixed

### Security
