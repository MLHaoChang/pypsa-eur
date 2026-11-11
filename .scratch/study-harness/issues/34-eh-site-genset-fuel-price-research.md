<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 34 — eh_site: genset fuel price as a research gap

Status: ready-for-agent (Opus; GK B plan first)
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 names the genset fuel price as researchable for eh_site. `eh_site@2` has no research gap
for it, so an unattended run keeps the template's fuel price. Add a research gap (cited source,
unit-checked, inside the envelope's research cost cap) in a new profile version (L8), with the
refusal when research is not allowed in the grant.

## Comments

### 2026-11-11: triage

Checked on master: no research gap for the genset fuel price in `eh_site@2` (`existing.gensets.fuel` is an enum only). Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: `pypsa-gui/backend/services/run_profiles.py` (a new `eh_site@3` profile, L8), `harness/run_eh.py`, `services/run_gates.py`, `tests/test_harness_run_eh_site_v2.py` and a sibling for v3. Reviewer: GK B (plan first).

Acceptance:
- [ ] `eh_site@3` (or the version issue 33 adds, if it merges first) carries a fuel-price research gap: a cited source, the unit checked, the value inside the envelope's research cost cap.
- [ ] A grant that does not allow research refuses the gap with a typed reason and keeps the template's fuel price, shown in the report.
- [ ] A price in the wrong unit is refused, with a test.
- [ ] Earlier profile versions are byte-equal (L8).
