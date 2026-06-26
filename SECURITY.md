# Security Policy

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 1.x     | active             |
| 0.x     | no fixes           |

<!-- Update this table when you cut a new major version. -->

## Reporting a vulnerability

**Do not open a public GitHub issue for security bugs.**

Email <257243234+rdh073@users.noreply.github.com> with:

1. A description of the vulnerability
2. Steps to reproduce (or a proof-of-concept)
3. Affected versions
4. Your assessment of severity and impact
5. Whether you've disclosed this to anyone else

If you want encrypted comms, my PGP key is at
https://github.com/rdh073.gpg or attach your key in the first email and
I'll switch.

You can also file privately via GitHub Security Advisories:
https://github.com/rdh073/hermes-a2a-fleet/security/advisories/new

## What to expect

- **Acknowledgement:** within 72 hours.
- **Initial assessment:** within 7 days. I'll tell you whether it's
  confirmed, severity rating, and rough fix timeline.
- **Fix + advisory:** depends on severity. Critical: days. High: weeks.
  Medium/Low: next release.
- **Credit:** if you want it, you'll be named in the advisory and release
  notes. If you want anonymity, that's also fine — just say so.

## Scope

In scope:
- The hermes-a2a-fleet code in this repo
- Default configuration shipped with releases
- Dependencies' integration if our usage introduces a vuln that upstream
  doesn't have

Out of scope:
- Bugs in upstream dependencies (report to them, link us)
- Issues that require local code execution as the user already running
  hermes-a2a-fleet (you already have the keys)
- Social engineering against project maintainers
- Rate limiting on someone else's API endpoints we happen to call

## Bug bounty

There isn't one. This is a personal project. I'll credit you, fix it
fast, and that's the deal.
