<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent — written 2026-10-28; part 1 is docs only (CI), part 2 is one harness line (GK B reviews)

# Outsourced task S: Stale `Status:` lines, pack 2, and the pilot scorer's policy version

Branch: `outsource/stale-status-pack-2` · one PR against `master` · PR body starts with `Outsourced task S`

Every pointer below was checked on `master` at `c86f572f` (2026-10-28).

## Read first

1. `AGENTS.md` (repo root), `docs/agents/triage-labels.md` (the five status words) and `docs/agents/issue-tracker.md` (what `Status:` records).
2. The first pack, `.scratch/coordination/outsource/N-stale-status-lines.md` (merged as #703). Its section "The rule for any other stale line you find" is the rule for this task too.
3. For part 2: `pypsa-gui/backend/harness/README.md` ("The contract") and `docs/agents/review-gk.md`. The gatekeeper reviews that part.

## Goal in plain words

1. More issue files in `.scratch/` still say `ready-for-agent` for work that has merged. The coordinator and the sessions read these lines to decide what is open. Set each stale line to its true state and cite the merge PR and its 8-character merge commit. Where only part of the work merged, keep the status word and add a `Progress:` line under it that says what merged and what did not. Change only `Status:` and `Progress:` lines.
2. The pilot scorer checks an approved answer's policy version against a literal `"1"`, while the records module owns the version as a constant. When the version moves, the scorer would silently mark every good answer `unsupported_policy`. Make it read the constant.

## First: get the full history

The checkout may be shallow.

```bash
git fetch --unshallow origin master 2>/dev/null || git fetch origin master
git log --first-parent origin/master --format='%h %s' | grep -F "#<N> from"
```

PR numbers collide with upstream PyPSA-Eur numbers (`#844 from PyPSA/cm` and `#844 from MLHaoChang/claude/site3d-sc02a` are both on `master`). Always confirm the subject: our merges read `Merge pull request #N from MLHaoChang/<branch>`.

## Part 1: the list

Replace the `Status:` line in each file with the text in the last column, or add the `Progress:` line directly under `Status:` where the column says so. If a file already has a `Progress:` line, replace it.

**Before each edit**, run `git log -1 --format=%h origin/master -- <file>`. If the file changed on `master` after `c86f572f`, read the new `Status:` line again and apply the rule below instead of the table text.

| File (under `.scratch/`) | Now | Merged as (confirm by subject) | New text |
|---|---|---|---|
| `live-copilot/issues/17-sensitivity-create-and-run.md` | `ready-for-agent inside LC-05, after LC-12` | #793 `from MLHaoChang/claude/lc17-sensitivity-chat`. Done when (l.44-47): OJ-27 and OJ-28 in Tier A with one failed case of three, no second solve on restart, LC-D19. `tests/test_sensitivity_chat.py` l.117 (OJ-27, restart reuses the solve, LC-D19 at l.123) and l.140 (OJ-28, one failed of three). | `Status: done (#793, 21cef975)` |
| `live-copilot/issues/19-run-lifecycle-and-failure.md` | `ready-for-agent inside LC-05, beside LC-12 (built on ...)` | #802 `from MLHaoChang/claude/lc19-run-lifecycle`. Done when: OJ-20, OJ-31-34 in Tier A, an infeasible fixture, a requeued failed job. `tests/test_lc19_run_lifecycle_journeys.py` (OJ-20 l.38, OJ-31 l.67, OJ-32 l.104, OJ-33 infeasible l.274) and `tests/test_lc19_queue_retry_journey.py` (OJ-34 l.83). | `Status: done (#802, 6b1b8689)` |
| `site-scene-generation/issues/01-prototype-and-contracts.md` | `ready-for-agent — N milestone (...); B and P stay pending ...` | #794 `from MLHaoChang/claude/site3d-sc00n` ("Site 3D SC-00 N: native prototype, contract closures and solve-queue kind guard"); its record is `.scratch/site-scene-generation/sc-00-n/record.md`. The issue says each milestone is done separately (l.37) and B and P need engines that are not available. | `Status: N done (#794, f0f67080); B and P stay pending until a supported Blender and photo engine are available (owner decision SITE3D-START-1)` |
| `live-voice/issues/08-application-coverage.md` | `ready-for-agent` | Not complete. Done when (l.18-20) asks that every family has a real journey assigned to issue 05 **and passing**, and that the full-coverage claim is settled. #800 (`from MLHaoChang/claude/lv08-bcampus-grid-codes`, `5426cca8`, today) closed the last 31 needs-tool rows; the issue's own last comment says "Slice C (behavioral journeys on issue 05) remains; the full-coverage claim stays `not_established`". | Keep `Status: ready-for-agent`. Add: `Progress: slice A route parity (#760, 340191e3) and slice B typed tools (#765, #766, #767, #768, #769; B-campus #800, 5426cca8) merged, 0 needs-tool routes; slice C (the issue-05 behavioural journeys) and the full-coverage claim are not done.` |
| `timeseries-generators/issues/12-ts-chrome-plain-words.md` (Status on l.3; this file has no SPDX header, do not add one) | `ready-for-agent (triage 2026-10-28: ...)` | #845 `from MLHaoChang/claude/ts-chrome-plain-words`. Acceptance (l.42-45): `pages/TimeSeriesManager.plainwords.test.tsx` asserts no raw name on any tab or sub-tab; `tests/test_validation_gen_costs.py` (new last test) pins the Guided item that opens its generator. Confirm the upload-pane wording in `TimeSeriesManager.tsx` before you write `done`; if it still shows a raw name, write a `Progress:` line instead. | `Status: done (#845, 44e4449b)` |
| `study-harness/issues/01-run-skeleton-eh-site.md` | `ready-for-agent` | #835 `from MLHaoChang/claude/study-harness-01-run-skeleton`. Each acceptance line (l.28-38) has a test in `tests/test_harness_run_skeleton.py` (l.63, 87, 101, 114, 126, 189, 210, 228, 245, 288, 355, 384). Match them one by one before you write `done`. | `Status: done (#835, 76324e61)` |
| `campus-ic-extra-owner-assets/spec.md` (Status on l.3) | `ready-for-agent` | Its five issues are all closed: 01 `resolved`, 02-05 `done` (set by task N). | `Status: done (issues 01–05 done; last merge #665, 30b313be)` |
| `renewable-profiles/issues/05-tuning-panel.md` | `ready-for-agent (outsourced tasks G-a then G-b, ...)` | Backend: #694 `from MLHaoChang/outsource/profile-tuning-backend`. Frontend task G-b (`outsource/profile-tuning-frontend`) has no merge on `master`. | Keep `Status:`. Add: `Progress: the backend merged as task G-a (#694, 7234bfbd); the frontend (task G-b) is not merged.` |
| `renewable-profiles/issues/07-before-after-compare.md` | same | same | same `Progress:` line |
| `renewable-profiles/issues/12-representative-weeks.md` | `ready-for-agent (RP-Q5 = A: ...)` | same | same `Progress:` line |
| `live-copilot/issues/14-navigation-reach.md` | `ready-for-agent; small, independent PRs` | Items 1, 2, 3 and 6: #700 `from MLHaoChang/outsource/navigation-reach`. Items 4 and 5 are task R (briefed 2026-10-28). | Keep `Status:`. Add: `Progress: items 1, 2, 3 and 6 merged as task I (#700, 6cbdc8cb); items 4 and 5 are task R (outsource/navigation-reach-2).` |
| `site-scene-generation/issues/03-bindings-and-placement.md` | `in-progress — split in two PRs (...): SC-02a backend ... on claude/site3d-sc02a, then SC-02b UI` | SC-02a: #844 `from MLHaoChang/claude/site3d-sc02a`. SC-02b has no merge. A session is building SC-02b; if the file changed after `c86f572f` (see above), leave it and list it under "Left as is". | Keep `Status:`. Add: `Progress: SC-02a (backend) merged (#844, c86f572f); SC-02b (UI) is not merged.` |

Notes on the rows:

- **Check by the rule, no text given:** `renewable-profiles/issues/04-timeseries-manager-integration.md`. Its `Progress:` line (l.7) says "no weather bar ... per-row Generate", but #623 (`from MLHaoChang/claude/rp-04b-weather-bar`, `bd17b5e3`, "RP-04b weather bar, per-row Generate, wind and solar in Generate defaults for all") merged after it. Also #616 (RP-04a, `4a9e1823`) and #624 (RP-04c, `98b6bdaf`). Compare the issue's bullet list (l.13-20) and acceptance (l.22-25) with `master`, then rewrite only the `Progress:` line to name what merged and what is still missing ("Set coordinates", "Tune…", the time-basis choice: check each). Keep `ready-for-agent`.
- **`live-voice/issues/13-lv08-bcampus-limit-dispatch-cards.md` stays `ready-for-agent`.** It is the follow-up #800 opened: `tests/test_edit_card.py:46-48` still lists the three tools in `_NO_ADAPTER_YET`.
- **`project-workflow/issues/UX-flow-expert-audit.md` already has a correct `Progress:` line.** Leave it.
- **Not issue files, out of scope here:** `site-scene-generation/HANDOFF.md`, `sc-01/record.md` and `sc-02/record.md` still say SC-01b or SC-02a is "in review" (#821 and #844 have merged). The rule covers `issues/*.md` and `spec.md` only; list them in the PR body under "Left as is".

### Files with a live session: do not touch

These have sessions or open PRs working on them today. Do not edit them, even if a line looks stale:

- `.scratch/grid-to-process/issues/09*`, `10*`, `18*`
- `.scratch/study-harness/issues/02*`, `19*`
- `.scratch/live-copilot/issues/03*`, `18*`
- `.scratch/site-scene-generation/issues/02*`
- `.scratch/timeseries-generators/issues/08*`
- `.scratch/assistant-assurance/issues/02*`

### The rule for any other stale line you find

Run brief N's rule again over the whole tree:

```bash
grep -rn "^Status:" .scratch --include=*.md
```

Look at each line that says `claimed`, `in-progress`, `in progress`, `ready-for-human` or `ready-for-agent`, or has a `Progress:` line, in `.scratch/**/issues/*.md` and `.scratch/**/spec.md`, and that is not in the list above or the "do not touch" list.

1. Find the evidence: `git log --first-parent origin/master --format='%h %s' | grep -iF "<slug or issue id>"`, then `git diff --stat <merge>^1 <merge>` (the first-parent diff, not `<merge>^2`, which also shows every master commit the branch merged in). Check that the PR changed the files the issue names.
2. Change the line only if a merged PR does what the issue's "Done when" or acceptance list asks. Write `done (#N, sha8)`.
3. If the PR did part of it, add a `Progress:` line saying what merged and what did not. Keep the status word.
4. If you cannot find a merged PR, leave the line and list the file under "Could not verify". **Never guess.**
5. A file with no `Status:` line, and any text other than the Status and Progress lines, stays as it is.

## Part 2: the pilot scorer reads `POLICY_VERSION`

`pypsa-gui/backend/harness/pilot_evaluation.py:298` reads:

```python
    if approved.get("version") != 1 or answer.get("version") != 1 or receipt.get("policy_version") != "1":
```

The version is owned by `pypsa-gui/backend/services/assurance_records.py:48`, `POLICY_VERSION = "1"`. The assurance loop writes that constant into every receipt (`harness/assurance.py:948`, `"policy_version": records.POLICY_VERSION`), and the records module checks it (`assurance_records.py:887`).

The change:

1. Add the import that `harness/assurance.py:23` already uses: `from services import assurance_records as records`. Put it with the module's other imports (`pilot_evaluation.py:4-12`). Use the module, not `from ... import POLICY_VERSION`, so a test can patch the constant.
2. In l.298, replace `receipt.get("policy_version") != "1"` with `receipt.get("policy_version") != records.POLICY_VERSION`. Leave the two `version != 1` checks; they are the answer and approval schema versions, not the policy version.

Change nothing else in `pilot_evaluation.py`.

**Import check.** `assurance_records` imports `harness.durable` and `services.pypsa_service` at module level (l.16-19). After the change, run each of these. If one fails with an import cycle, move the import into the function that holds l.298 instead, and say so in the PR body:

```bash
cd pypsa-gui/backend
python -c "import harness.pilot_evaluation"
python -c "import harness.reliability_cases"
python tools/run_pilot_evaluation.py --help
```

**Test.** Add one test to `pypsa-gui/backend/tests/test_pilot_evaluation_scoring.py`, which already builds a passing observation with `observation(case)` (l.14-34; `receipt` is one dict shared by `answer` and `approved`):

```python
def test_policy_version_follows_the_records_module(monkeypatch):
    from services import assurance_records
    case = GOLDEN_CASES[0]
    frames, answer = observation(case)
    assert "unsupported_policy" not in score(case, frames, answer)["failures"]
    monkeypatch.setattr(assurance_records, "POLICY_VERSION", "2")
    assert "unsupported_policy" in score(case, frames, answer)["failures"]
    answer["receipt"]["policy_version"] = "2"
    assert "unsupported_policy" not in score(case, frames, answer)["failures"]
```

On `master` the last assertion fails (the literal `"1"` rejects `"2"`). Show that red run in the PR body, then the green one. Do not change `observation()` or any existing test.

Part 2 adds no user-visible behaviour. No release-note fragment: `.github/workflows/release-notes-guard.yaml` only fails a PR that edits `doc/release_notes.md`.

## Files it may touch

- The `Status:` and `Progress:` lines of the files in the table, and of any other `.scratch/**/issues/*.md` or `.scratch/**/spec.md` found by the rule.
- `pypsa-gui/backend/harness/pilot_evaluation.py`: the one import and l.298 only.
- `pypsa-gui/backend/tests/test_pilot_evaluation_scoring.py`: the one new test only.

## Files it must NOT touch

- The "do not touch" files above.
- `.scratch/**/outsource/*.md` (the briefs), `.scratch/live-voice/CODEX-HANDOFF.md`, records and handoff files that are not `issues/*.md` or `spec.md`.
- `docs/agents/*`, `doc/release_notes.md`, `doc/release-notes.d/`.
- Any other code, test or config, including `services/assurance_records.py`.

## Tests

```bash
pixi install --frozen -e default -e test   # once
pixi run gui-tests tests/test_pilot_evaluation_scoring.py tests/test_pilot_evaluation.py tests/test_checked_answer_pilot.py
pixi run gui-tests   # the full backend suite before you open the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so test paths are relative to that folder. Make no paid model calls; `test_live_pilot_case` stays skipped without its flag.

## Review

- Part 1: CI only. The coordinator checks it with `scripts/coord/premerge.sh <pr>`.
- Part 2: gatekeeper B reviews `harness/pilot_evaluation.py` and the new test against `docs/agents/review-gk.md`.

## Repo rules

- Branch `outsource/stale-status-pack-2` from current `master`. One PR. Never force-push. Do not merge it; the coordinator merges.
- PR body starts with `Outsourced task S`, then follows `.github/pull_request_template.md`. Mark it AI-generated. List "Changed", "Left as is, with the reason" and "Could not verify", and give the red-then-green output of the part 2 test.
- `git diff --stat` shows only `.scratch/` files plus the two backend files. `git diff -U0 -- .scratch` shows only `Status:` and `Progress:` lines.

## Done when

- [ ] Every row of the table is applied, or the PR body says why not.
- [ ] Every `done (...)` and `Progress:` cites a PR and a commit you confirmed on `origin/master` by subject.
- [ ] No line outside `Status:` and `Progress:` changed under `.scratch/`; no "do not touch" file changed.
- [ ] `pilot_evaluation.py` compares against `records.POLICY_VERSION`; the new test failed on `master` and passes; the three pilot test files and the full `gui-tests` are green.
- [ ] The "Could not verify" list is in the PR body.
