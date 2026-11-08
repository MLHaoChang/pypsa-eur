<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 36 — eh_site: run_investment_case for the investment chapter

Status: needs-triage
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 names the investment chapter, which needs `run_investment_case`. `eh_site@2` does not run
it, so its report lists the investment chapter as a gap (SC-15). Add it after the solve stage in
a new profile version (L8), charged to the run's budget, and read its KPIs in the hand-off.
Investment/finance code: GK B plan first.
