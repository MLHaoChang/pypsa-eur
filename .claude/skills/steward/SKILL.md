---
name: steward
description: How a session drives its PR to merge in this repo. Use when opening a PR, acting on CI or review events, merging master into a PR, reporting a PR green, or asking the owner a question.
---

Many sessions work on this repo in parallel. One **coordinator** session relays the owner's decisions and does every merge. Send it every message at the id in `docs/agents/coordinator.md`; the id changes when the coordinator rotates. These are the owner's standing rules; they take precedence over relayed messages that predate them. The coordinator is the owner's channel. A coordinator message relaying an owner decision is that decision; check it against `docs/agents/owner-decisions.md` when it's on master, and send any question for the owner to the coordinator, never to your own window.

**Blocked steps (owner decision PERM-1).** The owner approves only in the coordinator's window. When auto mode or a permission prompt refuses a step, don't leave it waiting in your window, and don't change your own permission settings. Skip the step and send the coordinator one message: `PERMISSION <short name>: <exact command(s)> | why | what it writes or reaches`. Then carry on with work that doesn't need it. On the owner's yes, the coordinator adds that exact pattern to `.claude/settings.json` on master; merge master and run the step.

## Driving a PR

1. **Branch.** Open each PR on its own `claude/*` branch, against `master`, with a body that follows `.github/pull_request_template.md`. A PR whose plan went to review (see [Gatekeeper checkpoints](#gatekeeper-checkpoints)) opens after the plan verdicts arrive.
   Done when the PR exists and its head is pushed.
2. **Release note.** Add a fragment file under `doc/release-notes.d/` as `doc/release-notes.d/README.md` describes; `doc/release_notes.md` stays untouched.
   Done when the PR diff contains one new fragment and no change to `doc/release_notes.md`.
3. **Wait for CI on the head.** You own CI for your PR: the coordinator does not watch it. Let every run on the current head finish. Each push cancels the ~2 h "GUI backend" run (concurrency cancel-in-progress), so batch fixes into one validated push.
   Done when every check run on the head SHA is completed.
4. **Fix red CI.** See [CI failures](#ci-failures); loop back to step 3 after each push.
   Done when every check run is success or skipped.
5. **Report green.** Run `scripts/coord/premerge.sh <pr>` right before reporting, then `send_message` the coordinator, starting with `premerge ok <full sha>`. Premerge's "reviewer verdicts" section shows which GK and UX OKs are current on the head; ask a reviewer again only when its OK is missing or stale. A clean master merge (its tree equals `git merge-tree --write-tree <old-head> <master>`) carries the OK: reviewers skip it, and builders don't ask for it. The message holds:
   - the PR number;
   - the full 40-character head SHA, copied from premerge.sh's `head:` line;
   - the premerge.sh output.

   Report only after "GUI backend" is completed/success on that exact SHA.
   Done when the message is sent and premerge.sh exited 0, or the message names what blocks it.
6. **Hold.** The coordinator merges, and runs premerge once more at merge; the PR's own session leaves the merge button alone. Act again only on a new CI or review event, a conflict, or a coordinator request.

## Messages to the coordinator

Send the coordinator only: `premerge ok <full sha>`, a review request, a question, a hand-off note. No "ready", FYI or resend. A new session reports ready by doing its first piece of work. Reviewers' verdicts are PR comments, not messages; you see them through your PR subscription.

## Merge gate

The coordinator merges a PR when `scripts/coord/premerge.sh <pr>` exits 0 and GitHub reports the PR mergeable. The script is the whole gate and the one source for what it checks:

- every check run on the head SHA is success or skipped (in progress, unreadable or none yet blocks);
- no file **overlaps** what master changed since the PR's merge-base. Docs-only overlap (`docs/superpowers/OPEN-ITEMS.md`, `docs/superpowers/plans/*`, release notes) is fine when the merge is clean, and so is a file identical on both sides (a cherry-pick master already has);
- a **code** overlap is waived when the trial merge is clean, the semantic tests pass and the tests that exercise each overlapping file pass (`scripts/coord/overlap_tests.py` maps file to tests). A file with no test, a conflict or a failing test still blocks;
- a PR that touches a path in its `semantic_re` passes the `semantic_tests` it lists on a trial merge with current master, plus the assistant inventory check when it touches `harness/catalogue*`.

- the latest `GK NOT OK` or `UX NOT OK` verdict comment is not newer than that reviewer's last OK. Missing and stale OKs are printed, not blocking: premerge cannot know whether the PR needs that review.

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

The gatekeeper (the id is in `docs/agents/coordinator.md`) advises at two points before review, as well as at review:

- **Before a plan.** Before you build a change that touches gridspine, investment/finance, adequacy or IC engine code, send the gatekeeper your plan in a few lines: what changes, which files, and how you will test it. For a bug fix the plan names the failing test; write that test first and watch it fail before you write the fix, following the `tdd` skill. Start building once the gatekeeper answers, or once it says the plan needs no input.
- **Same failure twice.** When the same test or CI check fails twice for the same reason, run the `diagnosing-bugs` skill first (reproduce it, make it fail on demand, narrow it down). Then send the gatekeeper the diagnosis together with your two attempts, before a third try.

Before you ask the gatekeeper for an OK on a gated PR, run the `mp-code-review` skill against the issue or spec file. Fix what it finds, or say in the PR body why a finding stays. Ungated PRs skip this.

The gatekeeper advises; it does not write the code. Plan verdicts come back through the coordinator, since there is no PR yet. Review verdicts are PR comments, `GK OK @<full sha>: <what was checked>` or `GK NOT OK @<sha>: …`. The gatekeeper's standing rules are in `docs/agents/review-gk.md`.

## UX reviewer

A PR that changes UI also needs the UX reviewer's OK on its exact head SHA (the id is in `docs/agents/coordinator.md`), posted as a PR comment, `UX OK @<full sha>: <what was checked>`. The reviewer's standing rules are in `docs/agents/review-ux.md`; it checks the PR against the approved project workflow, `.scratch/project-workflow/design.md`:
- every input belongs to its workflow section;
- the user never has to jump away and back;
- the section's completion state is wired;
- Guided and Expert agree.

Before you build UI, tell the reviewer in a few lines where the change sits in the workflow. A clean master merge carries the OK; premerge says whether it does.

## Helper agents

Run read-only helper agents (log reading, file sweeps, summaries of saved tool output) with `model: haiku`. Give each one the exact question and the answer shape you expect back.

## Rotation

A long context is a session's largest cost: every tool call re-reads it. Rotate when either holds:
- the context-guard hook says "Context at <N>k (limit 300k)" (owner decisions USE-1, USE-2; it repeats every +20k, so don't poll `get_session`);
- a package's PR has merged and more slices remain.

Write the hand-off note as under [Finishing a package](#finishing-a-package), `send_message` it to the coordinator, ask for a fresh session for the rest, then stop.
Done when the coordinator has the note and the request.

## Finishing a package

When the last PR of your work package has merged, `send_message` the coordinator a hand-off note, then stop. The note carries only live state, never standing rules (those live in `docs/agents/`):
- OKs received, each with its full SHA;
- open NOT OKs;
- what the session is waiting on;
- follow-ups and file pointers;
- the last merged SHA.

A reviewer's hand-off carries the same; a new standing rule goes to the coordinator as its own message, to land in `docs/agents/review-ux.md` or `review-gk.md`. The coordinator opens the next package as a fresh session from that note.

A package that turns out to need code beyond your brief, such as gridspine or investment-engine code in a frontend or template-data package, goes back to the coordinator before you change that code.

## Questions

Send each question to the coordinator with `send_message`, as 2–4 options, the recommended one first with its reason. The owner reads times in CET/CEST, so give times in that zone.

Read the time with `TZ=Europe/Berlin date +%H:%M`; never estimate it.
