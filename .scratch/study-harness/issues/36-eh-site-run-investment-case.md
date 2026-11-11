<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 36 — eh_site: run_investment_case for the investment chapter

Status: ready-for-agent (Opus; GK B plan first; finance code)
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 names the investment chapter, which needs `run_investment_case`. `eh_site@2` does not run
it, so its report lists the investment chapter as a gap (SC-15). Add it after the solve stage in
a new profile version (L8), charged to the run's budget, and read its KPIs in the hand-off.
Investment/finance code: GK B plan first.

## Comments

### 2026-11-11: triage

Checked on master: `eh_site@2` lists the investment chapter as a gap (SC-15) and does not run `run_investment_case`. Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: `pypsa-gui/backend/services/run_profiles.py` (a new `eh_site@3` profile, L8), `harness/run_eh.py`, `services/run_gates.py`, `tests/test_harness_run_eh_site_v2.py` and a sibling for v3. Reviewer: GK B (plan first). Also `services/finance/` for the runner; the plan goes to GK B before code.

Acceptance:
- [ ] A new profile version runs `run_investment_case` after the solve stage and charges it to the run's budget.
- [ ] The hand-off reads the case's KPIs; the report no longer lists the investment chapter as a gap when the case ran, and still does when it did not.
- [ ] A refused or failed case leaves the gap with its reason, never a missing chapter.
- [ ] Earlier versions are byte-equal (L8); the investment case's own results are unchanged (a pin).
