---
name: steward
description: How a session drives its PR to merge in this repo. Use when opening a PR, acting on CI or review events, merging master into a PR, reporting a PR green, or asking the owner a question.
---

Many sessions work on this repo in parallel. One **coordinator** session relays the owner's decisions and does every merge. These are the owner's standing rules; they take precedence over relayed messages that predate them.

## Driving a PR

1. **Branch.** Open each PR on its own `claude/*` branch, against `master`, with a body that follows `.github/pull_request_template.md`.
   Done when the PR exists and its head is pushed.
2. **Release note.** Add a fragment file under `doc/release-notes.d/` as `doc/release-notes.d/README.md` describes; `doc/release_notes.md` stays untouched.
   Done when the PR diff contains one new fragment and no change to `doc/release_notes.md`.
3. **Wait for CI on the head.** Let every run on the current head finish. Each push cancels the ~2 h "GUI backend" run (concurrency cancel-in-progress), so batch fixes into one validated push.
   Done when every check run on the head SHA is completed.
4. **Fix red CI.** See [CI failures](#ci-failures); loop back to step 3 after each push.
   Done when every check run is success or skipped.
5. **Report green.** Run `scripts/coord/premerge.sh <pr>` right before reporting, then `send_message` the coordinator:
   - the PR number;
   - the full 40-character head SHA, copied from premerge.sh's `head:` line;
   - the premerge.sh output.

   Report only after "GUI backend" is completed/success on that exact SHA.
   Done when the message is sent and premerge.sh exited 0, or the message names what blocks it.
6. **Hold.** The coordinator merges; the PR's own session leaves the merge button alone. Act again only on a new CI or review event, a conflict, or a coordinator request.

## Merge gate

The coordinator merges a PR when all of these hold on the exact head SHA:

- every check run is success or skipped;
- GitHub reports the PR mergeable;
- no file **overlaps** what master changed since the PR's merge-base. Docs-only overlap (`docs/superpowers/OPEN-ITEMS.md`, `docs/superpowers/plans/*`, release notes) is fine when the merge is clean, and so is a file identical on both sides (a cherry-pick master already has);
- a PR that adds or changes a backend route or chat tool passes the **semantic tests** (`test_write_surface_lock_policy.py`, `test_chat_tools_handler_dependencies.py`, `test_chat_tools_argshape.py`) on a trial merge with current master.

`scripts/coord/premerge.sh <pr>` checks the last two: it fetches master and the PR head, prints the head SHA, merge-base and how far behind the PR is, tests for conflicts, classifies each overlapping file, and runs the semantic tests on a trial merge when the PR touches routers or chat tools. It exits non-zero when the PR is blocked.

## Merging master into a PR

Merge master into a PR only for one of these:

- GitHub reports a conflict;
- premerge.sh reports a **code** overlap;
- the coordinator asks.

"Master moved" alone leaves a PR as it is: a green PR stays green, and a merge push restarts the ~2 h CI. Merge only while no CI runs on the head, then return to step 3.

## CI failures

- **Root-cause** each failure before pushing; reproduce it locally when the repo's tooling allows.
- **Re-run** a failed job at most once, and only when it died before any test ran or the failure is a known flake. A second failure is real.
- **Keep every test as strict as it is**: fix the code or the test's genuine bug, never skip, loosen or quarantine a test.

## Questions

Send each question to the coordinator with `send_message`, as 2–4 options, the recommended one first with its reason. The owner reads times in CET/CEST, so give times in that zone.
