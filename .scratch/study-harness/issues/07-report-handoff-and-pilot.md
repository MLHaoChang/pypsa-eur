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
