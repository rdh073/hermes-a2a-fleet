## What & Why

<!-- WHAT this PR changes in one sentence. -->
<!-- WHY: what was broken, what invariant was violated, or what need this serves. -->
<!-- The diff already shows what changed line-by-line. Tell me why. -->

## Root cause (bug fixes only)

<!-- Where was the invariant FIRST broken? Not where it crashed. -->

## Linked issue

Fixes #

## Type

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (CLI flag / API / output format / exit code)
- [ ] Docs only
- [ ] Internal / refactor (no behavior change)

## Checklist

- [ ] One logical change. No drive-by reformatting or unrelated refactors.
- [ ] Tests added or updated. Bug fixes have a regression test that fails without the fix.
- [ ] Linter is clean.
- [ ] Test suite passes locally.
- [ ] No new dependencies, OR new deps justified in the description (size, license, maintenance).
- [ ] No public API / CLI / output-format change, OR I've added a `BREAKING CHANGE:` section below.
- [ ] No band-aid fixes: I am not catching an exception just to silence it, adding a null check without explaining why the value is null, or sleeping to fix a race.

## BREAKING CHANGE

<!-- Delete this section if not applicable. -->
<!-- What breaks. Who is affected. Migration path. -->

## Screenshots / output

<!-- For UI/CLI/output-format changes. Before & after. -->
