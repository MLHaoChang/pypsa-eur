---
name: steward
description: How a session drives its PR to merge in this repo. Use when opening a PR, acting on CI or review events, merging {{BASE_BRANCH}} into a PR, reporting a PR green, or asking the owner a question.
---

Many sessions work on this repo in parallel. One **coordinator** session relays the owner's decisions and does every merge (`docs/agents/roles.md` names it). These are the owner's standing rules. They take precedence over relayed messages that predate them.

## Driving a PR

1. **Branch.** Open each PR on its own `claude/*` branch, against `{{BASE_BRANCH}}`, with a body that follows `.github/pull_request_template.md`.
   Done when the PR exists and its head is pushed.
2. **Release note.** Add a fragment file under `{{RELEASE_NOTES_DIR}}/` as its `README.md` describes.
   Done when the PR diff contains one new fragment.
3. **Wait for CI on the head.** Let every run on the current head finish. Each push cancels and restarts {{SLOW_CI_JOB}}, so batch fixes into one validated push.
   Done when every check run on the head SHA is completed.
4. **Fix red CI.** See [CI failures](#ci-failures). Loop back to step 3 after each push.
   Done when every check run is success or skipped.
5. **Report green.** Run `scripts/coord/premerge.sh <pr>` right before reporting, then `send_message` the coordinator:
   - the PR number;
   - the full 40-character head SHA, copied from premerge.sh's `head:` line;
   - the premerge.sh output.

   Done when the message is sent and premerge.sh exited 0, or the message names what blocks it.
6. **Hold.** The coordinator merges, so leave the merge button alone. Act again only on a new CI or review event, a conflict, or a coordinator request.

## Merge gate

The coordinator merges a PR when `scripts/coord/premerge.sh <pr>` exits 0 and GitHub reports the PR mergeable. The script is the whole gate, and it is the one source for what gets checked:

- every check run on the head SHA is success or skipped. A run in progress, an unreadable run or no runs yet all block;
- no file **overlaps** what `{{BASE_BRANCH}}` changed since the PR's merge-base. Docs-only overlap is fine when the merge is clean, and so is a file identical on both sides;
- a PR that touches a path in its `semantic_re` passes its `semantic_cmd` on a trial merge with current `{{BASE_BRANCH}}`.

A PR that touches {{GATED_AREAS}} also needs the gatekeeper's OK on that exact head SHA.

## Gatekeeper checkpoints

The gatekeeper (`docs/agents/roles.md`) advises at two points before review, as well as at review itself:

- **Before a plan.** Before you build a change that touches {{GATED_AREAS}}, send the gatekeeper your plan in a few lines: what changes, which files, and how you will test it. Start building once it answers, or once it says the plan needs no input.
- **Same failure twice.** When the same test or CI check fails twice for the same reason, send the gatekeeper the failure and your two attempts before a third try.

The gatekeeper advises; it does not write the code.

## Merging {{BASE_BRANCH}} into a PR

Merge `{{BASE_BRANCH}}` into a PR only when one of these holds:

- GitHub reports a conflict;
- premerge.sh reports a **code** overlap;
- the coordinator asks.

"{{BASE_BRANCH}} moved" alone is no reason. A green PR stays green, and a merge push restarts CI. Merge only while no CI runs on the head, as a merge commit, then return to step 3.

## CI failures

- **Root-cause** each failure before pushing. Reproduce it locally when the repo's tooling allows.
- **Re-run** a failed job at most once, and only when it died before any test ran or the failure is a known flake. A second failure is real.
- **Keep every test as strict as it is.** Fix the code, or the test's genuine bug. Never skip, loosen or quarantine a test.

## Helper agents

Run read-only helper agents (log reading, file sweeps, summaries of saved tool output) on the smallest model. Give each one the exact question and the answer shape you expect back.

## Finishing a package

When the last PR of your work package has merged, `send_message` the coordinator a hand-off note, then stop. The note covers:
- the decisions made;
- the open items;
- file pointers;
- the last merged SHA.

The coordinator opens the next package as a fresh session from that note.

A package might turn out to need code beyond your brief, such as {{GATED_AREAS}} in a frontend or data package. Go back to the coordinator before you change that code.

## Questions

Send each question to the coordinator with `send_message`, as 2–4 options, with the recommended one first and its reason. The owner reads times in {{TZ_LABEL}}, so give times in that zone.
