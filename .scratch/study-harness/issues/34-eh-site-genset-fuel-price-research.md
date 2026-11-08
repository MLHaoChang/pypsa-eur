<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 34 — eh_site: genset fuel price as a research gap

Status: needs-triage
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 names the genset fuel price as researchable for eh_site. `eh_site@2` has no research gap
for it, so an unattended run keeps the template's fuel price. Add a research gap (cited source,
unit-checked, inside the envelope's research cost cap) in a new profile version (L8), with the
refusal when research is not allowed in the grant.
