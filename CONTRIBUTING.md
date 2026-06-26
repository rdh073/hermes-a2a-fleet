# Contributing to hermes-a2a-fleet

Thanks for considering a contribution. Read this before opening a PR.

## Ground rules

1. **Root cause, not symptom.** If you're fixing a bug, the patch goes at
   the place the invariant was first broken, not where it crashed.
2. **One logical change per PR.** Bug fix + refactor + rename = three PRs.
3. **No breaking changes without discussion.** Public CLI flags, output
   schemas, exit codes, and the public API are contracts. File an
   issue first.
4. **Tests are not optional.** Bug fix → regression test. Feature → unit
   + integration test.
5. **Read the existing code first.** If your change duplicates an
   existing helper or contradicts an existing convention, it won't be
   merged.

## Setting up

```bash
git clone https://github.com/rdh073/hermes-a2a-fleet
cd hermes-a2a-fleet
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"   # runtime is stdlib-only; dev adds pytest / hypothesis / ruff
pytest                    # run the test suite
ruff check .              # run the linter
```

Optional LAN discovery needs one extra package: `pip install "zeroconf>=0.131"`.

## Workflow

1. **File an issue first** for anything bigger than a typo. Don't write a
   1000-line PR and hope it gets merged.
2. **Fork → branch → PR.** Branch naming: `fix/<short-desc>` or
   `feat/<short-desc>`.
3. **Keep commits clean.** Each commit should build and pass tests.
   Squash WIP commits before opening the PR.
4. **Commit messages:** see below.
5. **Open the PR.** Fill out the template. CI must be green before review.

## Commit message format

```
<area>: <imperative summary, max 72 chars>

Body explaining WHY, not WHAT. The diff shows what changed.
What broke before? What invariant was being violated? What's the
root cause? Why is this fix correct and not just a band-aid?

Fixes #123
```

Areas: customize per your codebase. Common defaults: `core`, `cli`,
`api`, `tests`, `docs`, `build`, `deps`.

**Good:**
```
parser: fix off-by-one in token boundary detection

The lexer compared position N against N-2 instead of N-1, missing the
boundary on adjacent identical tokens. Replaces the cached previous
index with a deque to make the windowing explicit.

Fixes #87
```

**Bad:**
```
fix bug
```

## Code style

- Run the project linter and type checker. No `# type: ignore`,
  `@ts-ignore`, or equivalent without an attached reason.
- No band-aid fixes. Don't wrap a broken function in `try/except` and
  call it fixed. See the project's forbidden-patterns list if one
  exists.
- No drive-by reformatting in a feature PR. Run the formatter on your
  changes only.

## Testing

- Unit tests live next to source (or in the project's standard test
  location).
- A bug fix without a regression test will not be merged.

## Review process

- Maintainer aims to triage within 7 days.
- "Looks good" PRs ship within 14 days.
- "Needs another pass" PRs may sit until you respond. If you go silent
  for 30 days the PR is closed; reopen when ready.
- Direct feedback is the norm. "This is wrong because X" is not hostile —
  it's the fastest way to converge.

## What gets rejected

- PRs without an issue for anything non-trivial
- PRs that touch >500 lines across unrelated areas
- "Cleanup" PRs that change formatting/style without a coding-standards
  discussion first
- New dependencies added without justification (size, maintenance, license)
- Code that breaks an existing public contract without a `BREAKING CHANGE`
  note and a deprecation path

## What gets merged fast

- Reproducing test + minimal fix at the root cause
- Single-purpose, well-scoped feature with tests and docs
- Performance fix with before/after numbers
- Doc fixes that are correct
