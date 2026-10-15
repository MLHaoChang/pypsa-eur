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

The coordinator merges a PR when `scripts/coord/premerge.sh <pr>` exits 0 and GitHub reports the PR mergeable. The script is the whole gate and the one source for what it checks:

- every check run on the head SHA is success or skipped (in progress, unreadable or none yet blocks);
- no file **overlaps** what master changed since the PR's merge-base. Docs-only overlap (`docs/superpowers/OPEN-ITEMS.md`, `docs/superpowers/plans/*`, release notes) is fine when the merge is clean, and so is a file identical on both sides (a cherry-pick master already has);
- a PR that touches a path in its `semantic_re` passes the `semantic_tests` it lists on a trial merge with current master, plus the assistant inventory check when it touches `harness/catalogue*`.

It prints the head SHA, merge-base and how far behind the PR is, then one section per check, and exits non-zero when the PR is blocked.

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

## Gatekeeper checkpoints

The gatekeeper (session_01Fz66v1SpJnjGHky1RbUuqC) advises at two points before review, as well as at review:

- **Before a plan.** Before you build a change that touches gridspine, investment/finance, adequacy or IC engine code, send the gatekeeper your plan in a few lines: what changes, which files, and how you will test it. Start building once it answers, or once it says the plan needs no input.
- **Same failure twice.** When the same test or CI check fails twice for the same reason, send the gatekeeper the failure and your two attempts before a third try.

The gatekeeper advises; it does not write the code.

## Helper agents

Run read-only helper agents (log reading, file sweeps, summaries of saved tool output) with `model: haiku`. Give each one the exact question and the answer shape you expect back.

## Finishing a package

When the last PR of your work package has merged, `send_message` the coordinator a hand-off note, then stop. The note covers:
- the decisions made;
- the open items;
- file pointers;
- the last merged SHA.

The coordinator opens the next package as a fresh session from that note.

A package that turns out to need code beyond your brief, such as gridspine or investment-engine code in a frontend or template-data package, goes back to the coordinator before you change that code.

## Questions

Send each question to the coordinator with `send_message`, as 2–4 options, the recommended one first with its reason. The owner reads times in CET/CEST, so give times in that zone.
