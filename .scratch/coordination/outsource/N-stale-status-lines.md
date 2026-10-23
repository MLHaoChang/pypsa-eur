<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent — written 2026-10-23; docs only, no review gate beyond CI

# Outsourced task N: Fix stale `Status:` lines in `.scratch/`

Branch: `outsource/stale-status-lines` · one PR against `master` · PR body starts with `Outsourced task N`

## Read first

1. `AGENTS.md` (repo root), and `docs/agents/triage-labels.md` (the five status words) and `docs/agents/issue-tracker.md` (what `Status:` records).
2. A finished example of the format you write: `.scratch/chat-companion/issues/04d-retained-source-gaps.md` line 3 reads `Status: done (#327, 622149df)`. A multi-part one: `.scratch/datacenter-grid-readiness/issues/09-sixty-hz-throughout.md`.

## Goal in plain words

Many issue and spec files in `.scratch/` still say `ready-for-human`, `ready-for-agent`, `claimed` or `implementation in progress` for work that has merged. The coordinator and the sessions read these lines to decide what is open. Set each stale line to its true state and cite the merge PR and its 8-character merge commit. Change only the `Status:` line (and the stale `Progress:` line named below). Do not edit any other text in these files.

## First: get the full history

The checkout may be shallow.

```bash
git fetch --unshallow origin master 2>/dev/null || git fetch origin master
git log --first-parent origin/master --format='%h %s' | grep -F "<PR title or number>"
```

PR numbers in this repo collide with upstream PyPSA-Eur numbers (for example `(#311)` also matches `Merge pull request #311 from PyPSA/test-pd-1.4`). Always confirm the commit **subject** from the table, not only the number.

## The list (verified on master `abefdbd49`, 2026-10-23)

Replace line `Status:` in each file with the text in the last column. `sha` is the first 8 characters of the merge commit on `origin/master`.

| File (under `.scratch/`) | Now | Merged as (confirm by subject) | New `Status:` |
|---|---|---|---|
| `chat-companion/issues/01-truthful-state.md` | ready-for-human | #235 `Make companion text and work state truthful; specify remaining scopes` | `done (#235, 3175f4f9)` |
| `chat-companion/issues/02-canonical-outcomes.md` | ready-for-human | #241 `Show canonical companion answers with checked evidence (CP-02)` | `done (#241, ebbc66d4)` |
| `chat-companion/issues/03-source-navigation.md` | ready-for-human | #268 `Bind result views to source evidence and share load-flow calculations` | `done (#268, 5711a6fa)` |
| `chat-companion/issues/04-activity-needs-you.md` | ready-for-human | #309 `Add bounded assistant Activity and exact native job observation` | `done (#309, 07f90f23)` |
| `chat-companion/issues/04b-retained-job-observations.md` | ready-for-human | #311 `Retain bounded assistant job observations across worker loss` | `done (#311, 76835bb3)` |
| `chat-companion/issues/04c-retained-decision-observations.md` | ready-for-agent | #319 `Retain bounded chat decision observations for read-only inspection` | `done (#319, f0d2aa44)` |
| `chat-companion/issues/04f-native-completion-observation.md` | ready-for-agent | #351 `Retain bounded native job completion observations` | `done (#351, 31998501)` |
| `chat-companion/issues/05a-uncertain-case-reconciliation.md` | ready-for-agent | #352 `Reconcile uncertain experiment submissions with fenced native receipts` | `done (#352, e1adb0db)` |
| `harness/issues/221-report-job-state.md` | ready-for-human | #221 `Fix report worker state and pin chat recovery/cost regressions` | `done (#221, 0f4b9d19)`; also delete its `Progress:` line (l.7), which says "PR #221 not merged" |
| `live-copilot/issues/01b-exact-project-resolution.md` | implementation in progress (...) | #391 `Merge pull request #391 from MLHaoChang/claude/copilot-target-resolution-b` | `done (#391, 14a49dda)` |
| `live-copilot/issues/10-focus-and-recency-resolution.md` | implementation in progress (...) | #402 `Merge pull request #402 from MLHaoChang/claude/copilot-reference-ladder` | `done (#402, 16055419)` |
| `live-copilot/issues/15-project-asset-summary.md` | implementation in progress (...) | #404 `Merge pull request #404 from MLHaoChang/claude/copilot-asset-summary` | `done (#404, b176a76e)` |
| `campus-ic-extra-owner-assets/issues/02-investment-tab-shows-campus-equipment.md` | ready-for-agent | #647 `feat(ic): investment case shows the campus equipment and unmet needs (campus-IC 02+05)` | `done (#647, f3de776c)` |
| `campus-ic-extra-owner-assets/issues/03-defaults-pack-with-cited-campus-costs.md` | ready-for-agent | part A #648 (`campus-IC 03A`), part B #665 (`Campus IC 03 part B`) | `done (part A #648, 839868e8; part B #665, 30b313be)` |
| `campus-ic-extra-owner-assets/issues/04-campus-replacements.md` | ready-for-agent | #656 `feat(ic): campus study replacements in the investment case without double counting (campus-IC 04)` | `done (#656, 8b4bae14)` |
| `campus-ic-extra-owner-assets/issues/05-campus-unresolved-needs.md` | ready-for-agent | #647 (same PR as 02) | `done (#647, f3de776c)` |
| `datacenter-grid-readiness/issues/09-sixty-hz-throughout.md` | done (09a ...; 09b ... branch name) | #686 (09a), #692 (09b) | `done (09a #686, 1871073c; 09b #692, abefdbd4)` |
| `datacenter-grid-readiness/issues/10-grid-code-profile-sagc-schema.md` | done | #691 `DC 10a: grid-code profile fields for SAGC-type codes` | `done (#691, 60934d14)` |
| `ipp-renewables-bess/spec.md` | ready-for-agent (spec; ...) | spec #453; its issues 01-08 are all `done` | `done (spec #453, 90c7b8ff; issues 01-08 done)`. Keep the old sentence (owner answers, UX and gatekeeper notes) as the next line, starting `History:`. |
| `eh-microgrid-investment-case/spec.md` | ready-for-agent | #400 `Island Microgrid template: its own investment case (plain eh_microgrid)` | `done (#400, b6891c0b)` |
| `live-voice/issues/02-harness-bridge.md` | claimed | no PR; first slice 2026-10-11, no session since | `ready-for-agent` |
| `live-voice/issues/07-loadflow-summary-and-navigation.md` | claimed | same | `ready-for-agent` |
| `live-voice/issues/08-application-coverage.md` | claimed | same | `ready-for-agent` |
| `live-voice/issues/09-durable-conversation.md` | claimed | same | `ready-for-agent` |
| `live-voice/issues/11-nonblocking-monitoring.md` | claimed | same | `ready-for-agent` |

Notes on the rows:
- **live-voice.** `claimed` means a session holds the issue. These five were claimed on 11 Oct (first slices merged that day) and have no session. `.scratch/live-voice/CODEX-HANDOFF.md` (the table at l.103-108) lists what is left for each. Do not edit that file.
- **`chat-companion/issues/04`** keeps its `Progress:` line; the work it lists as incomplete is not all in 04b-04f. Do not touch it.
- **`money-year/issues/01-templates-case-year-and-repin.md`: leave `needs-triage`.** The relabel (#688, `719f8b01`) merged, but this issue is the follow-up: master still has `TEMPLATE_PACK_VERSION = "2026-10-22"` (`project_templates/eh_templates.py:102`), `AMMONIA_PACK_VERSION = "2026-10-22"` and `currency_year: 2020` (`eh_templates.py:414, 746`). Only add a line under `Status:`: `Progress: the relabelled pack 2026-10-25 merged (#688, 719f8b01); the repin and the case-year move are not done.`
- `datacenter-grid-readiness/issues/15-reactive-capability-curve-deadband.md` was touched by #691 and is not in this list. Check it by the rule below.

## The rule for any other stale line you find

Run `grep -rn "^Status:" .scratch --include=*.md` and look at each line that says `claimed`, `implementation in progress`, `ready-for-human`, `ready-for-agent` or has a `Progress:` line.

1. Find the evidence on `origin/master`: `git log --first-parent origin/master --format='%h %s' | grep -iF "<issue title or slug>"`, then `git show --stat <merge>` and check that the PR changed the files the issue names.
2. Change the line only if a merged PR does what the issue's "Done when" (or equivalent) asks. Cite `done (#N, sha8)`.
3. If the PR did part of it, write `Progress:` with what merged and what did not. Keep the status word.
4. If you cannot find a merged PR, leave the line and list the file in your PR body under "Could not verify". **Never guess.**
5. A file with no `Status:` line, or any text other than the Status and Progress lines, stays as it is.

## Release note

None. This is a docs-only change under `.scratch/`. `.github/workflows/release-notes-guard.yaml` only fails a PR that edits `doc/release_notes.md`; it does not require a fragment. `AGENTS.md` asks for a fragment when a change affects release notes; a status-line fix does not. Do not add one, and do not touch `doc/release_notes.md`.

## Files it may touch

Only the files in the table, and any other `.scratch/**/issues/*.md` or `spec.md` you find by the rule, and only their `Status:` and `Progress:` lines.

## Files it must NOT touch

Anything outside `.scratch/`. `.scratch/live-voice/CODEX-HANDOFF.md`, `.scratch/**/outsource/*.md` (the briefs), and `docs/agents/*`. Any code, test or config.

## Review

CI only. The coordinator checks the PR with `scripts/coord/premerge.sh <pr>`. No gatekeeper or UX review.

## Repo rules

- Branch `outsource/stale-status-lines` from current `master`. One PR. Never force-push. Do not merge it; the coordinator merges.
- PR body starts with `Outsourced task N`, then follows `.github/pull_request_template.md`. Mark it AI-generated. List "Changed", "Left as is, with the reason" and "Could not verify".
- Commands: `git diff --stat` must show only `.scratch/` files, and `git diff -U0` only `Status:`, `Progress:` and `History:` lines.

## Done when

- [ ] Every row of the table is applied, or the PR body says why not.
- [ ] Every `done (...)` cites a PR and a commit you confirmed on `origin/master` by subject.
- [ ] No line outside `Status:`, `Progress:` and the one `History:` line changed.
- [ ] The "Could not verify" list is in the PR body.
