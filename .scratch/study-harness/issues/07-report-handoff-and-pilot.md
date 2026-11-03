<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Report and hand-off stages; eh_site pilot end-to-end

Status: ready-for-agent
Type: task
Blocked by: 05, 06
Gated: yes (GK B)
Model tier: opus
Owner decisions: SH-PILOT-1, SH-FOLD-1, LC-21 Q6 = A, AI-558
Absorbs: LC-21 (`.scratch/live-copilot/issues/21-network-study-report.md`) as stage 7; LC-23 slice 4 (hand-off close)
Design: [design.md](../design.md) §3 stages 7–8, §9.3 eh_site

## What to build

1. LC-21's network-study chapters in the existing evidence pipeline (its option A), plus the **harness chapter**: gates per stage, change ledger, source ledger, envelope grant and refusals, gaps.
2. Narrative from admitted facts only (≤12 per explanation); unsupported chapters listed as gaps (SC-15).
3. Hand-off: native KPI read-back from `profile.kpis`, the resume point, the notice, and "Revert the run's changes" as one LC-16 preview.
4. The full `eh_site@1` profile (design §9.3) and the pilot acceptance run.

## Acceptance criteria

- [ ] **Pilot**: from one prompt, an eh_site run in envelope mode goes intake → hand-off with the panel closed after approval, on the test fixture with the fake provider; the report has the harness chapter; the user gets one completion notice.
- [ ] The same run in step-by-step mode reaches the same hand-off with one card per write/run (COP-1).
- [ ] `POST /reports/generate` no longer answers `no_evidence` for a solved network-only project (LC-21).
- [ ] Every number in the narrative passes the number audit; an injected extra claim is withheld.
- [ ] Revert preview lists exactly the ledger rows.

## Likely files

`pypsa-gui/backend/services/reports/chapters.py`, `pypsa-gui/backend/services/reports/evidence.py`, `pypsa-gui/backend/harness/runner.py`, `pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/smoke/` (one pilot smoke), `pypsa-gui/backend/tests/test_harness_run_pilot.py` (new)

## Comments

## Folded from #970

Owner decision HARNESS-970-1 (3 Nov). Three items from #970 land here, all after the pilot works. None changes the acceptance criteria above.

1. **Trajectory evaluation (HH-05B).** The pilot acceptance run is one trajectory. HH-05B turns the pilot, and later each profile's run, into a reproducible suite: a closed case list with fixed fixtures, an independent native oracle per case, positive and refusal controls, and a report that counts unauthorized effects, wrong-project results and false native completion as release blockers (refusing everything must fail). Build it as `harness/trajectory_evaluation.py` plus an offline CLI under `tools/`, with the fake provider; no paid provider runs and no wall-clock thresholds in CI. Its first cases are the ones the folded issues need: 22's barrier cases, 23's survive-a-restart case, 24's kill-between-call-and-charge case. **HH-05A is skipped** (#970's baseline characterization pack); only the barrier test lives on, as issue 22's failing test.
2. **Report comments → revisions (HH-08A).** A user reads the hand-off report and comments ("shorten the grid section", "show the BESS duration in hours"). HH-08A keeps each comment as a durable item until it is addressed or withdrawn: edits are a closed set (report paragraph/bullet text, workbook text and number formats), they preserve the evidence references and disclosures, they never change a number, formula, chart range or source assumption, and an unsupported comment stays open with its reason. Revisions are new copies against the saved report's digest (a stale base is refused). Builds on the harness chapter and revert preview of this issue and on the reports service (`services/reports/`); recheck it against master, since #970 is pinned at #944. A revision that needs a model call goes through issue 24's reservation. Numeric repairs (#970's 08B and 08C) stay out.
3. **Per-request-part coverage at hand-off (HH-09A, reduced).** The brief records an objective but not which parts of the request were asked for. Add one check to the hand-off: each part of the approved request (deliverable, case, stated target) is listed as `covered`, `partly` or `gap`, from the native receipts and gates, and any `gap` is named in the report and the completion notice (the same treatment as SC-15's unsupported chapters). No IntentContract store, no decision packets and no second authority over acceptance; HH-09A's larger store is dropped with HH-09B.
