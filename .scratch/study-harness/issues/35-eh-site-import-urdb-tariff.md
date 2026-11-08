<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 35 — eh_site: import_urdb_tariff in the inputs stage

Status: needs-triage
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 lists `import_urdb_tariff`/`attach_tariff` as eh_site's tariff inputs. `eh_site@2` fills the
tariff only from a held Library tariff (`attach_tariff`, library_tariff gap). Add the URDB
import path in a new profile version (L8), offline-testable (fixture response; no network in the
smoke, L7), and decide with GK B whether it needs its own envelope gap kind.
