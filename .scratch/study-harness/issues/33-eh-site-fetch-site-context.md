<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 33 — eh_site: fetch_site_context in the inputs stage

Status: needs-triage
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

Design §9.2 lists `fetch_site_context` in stage 2 (inputs) "when a site is given", and §9.3
names it for eh_site. `eh_site@2` (issue 07 B3b) does not call it: the pilot runs offline on
the test fixture (L7). Add it to the inputs stage of a new profile version (L8: never change a
granted run's inputs), with a fixture for the offline smoke, and say which gate rule reads it.
