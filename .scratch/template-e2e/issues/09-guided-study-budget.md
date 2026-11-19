<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09 — Guided cannot choose the study size; the staged concentrator study runs 15+ minutes

Status: ready-for-agent
Owner: unassigned
Size: S (a) or S (b)
Gated: UX (a); GK A (b)
Likely files: pypsa-gui/frontend/src/pages/hubDesign/shared/GoalStudyRun.tsx (the progress line and Abort); pypsa-gui/frontend/src/pages/results/EhReferenceDesignPanel.tsx (the only `budget_solves` field, 1 to 120); pypsa-gui/backend/services/run_profiles.py (`eh_study` profile, `budget_solves: 2`)
Journey fit: set up the question (section 8) → run (section 9) → wait → results. The wait is where a user needs to know how big the study is, and the PR-CI journeys need a small one.
Expected-fail id: `budget` (template-journeys.mjs; PR #1629).

## What

When a user starts the study in Guided (section 9), the page says "Studying… up to N calculation steps" and offers only Abort. There is no way to see the size before the run or to choose a smaller one. The same size can be set only in Expert, in the reference-design panel (1 to 120). For most templates the default is small. For the Mining Concentrator (staged growth) it is not: 26,280 hourly snapshots over three periods and up to 30 steps, and the study had not finished after 15 minutes, with the user watching a progress line and an Abort button.

Run times measured by the short journeys (local container, default settings):

| Template | Run |
|---|---|
| eh_datacenter | 25 s |
| eh_datacenter_staged | 58 s |
| eh_site (hospital) | 63 s |
| eh_microgrid | 12 s |
| eh_microgrid_staged | 90 s |
| eh_h2_hub | 25 s |
| eh_h2_hub_staged | 53 s |
| eh_ammonia_staged | 178 s |
| eh_concentrator_staged | not done after 15 min |

Consequence for CI: PR CI cannot wait for the concentrator verdict, so its short journey starts the run, checks the progress line and aborts it. The full journey on the schedule waits up to 50 minutes.

## Candidate fixes

**a) A study-size line in Guided section 9** (recommended first: it also serves the user). Show the study size as an editable line labelled "calculation steps" next to "Run study", using the existing study-size value. GAP03-ADD-1 applies: Guided edits existing values.
- Journey steps once built: section 9 has a field labelled "calculation steps" (the `budget` predicate); set it to 2; run; the progress line reads "of 2 calculation steps" (the `budget` predicate); for the concentrator the verdict arrives within the short budget; the progress line reads "of 2 calculation steps". The concentrator short journey then waits for the verdict instead of aborting.

**b) A smaller default study size for the staged concentrator.** One line in the template's run profile, but it changes the study results the template produces, so GK A reviews it.
- Journey steps once built: the concentrator's short journey waits for the verdict within 300 s; the abort branch (`slow: true`) is removed.

Both can ship; (a) alone leaves the concentrator slow by default.

## Acceptance

- A Guided user can see and change the study size before a run, or the concentrator's default finishes within the short-journey budget.
- The `budget` marker in `template-journeys.mjs` flips to a normal assertion and, for (b), the `slow` branch goes.

## Triage (2026-11-19, master 9f850d36)

Checked by code reading only; the app was not re-run. Still true: `GoalStudyRun.tsx` shows the size only in the progress line, and `run_profiles.py` `eh_study` still has `budget_solves: 2` with no Guided field. Scope: fix (a) only (UX gate). Fix (b) changes template results and is not triaged here; file it separately if the owner wants it. Blocked by: none.
