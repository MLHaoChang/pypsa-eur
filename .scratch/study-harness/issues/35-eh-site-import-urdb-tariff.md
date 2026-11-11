<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 35 — eh_site: import_urdb_tariff in the inputs stage

Status: ready-for-agent (Opus; GK B plan first)
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

§9.3 lists `import_urdb_tariff`/`attach_tariff` as eh_site's tariff inputs. `eh_site@2` fills the
tariff only from a held Library tariff (`attach_tariff`, library_tariff gap). Add the URDB
import path in a new profile version (L8), offline-testable (fixture response; no network in the
smoke, L7), and decide with GK B whether it needs its own envelope gap kind.

## Comments

### 2026-11-11: triage

Checked on master: `eh_site@2` fills the tariff only from a held Library tariff (`attach_tariff`); no URDB path in the profile. Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: `pypsa-gui/backend/services/run_profiles.py` (a new `eh_site@3` profile, L8), `harness/run_eh.py`, `services/run_gates.py`, `tests/test_harness_run_eh_site_v2.py` and a sibling for v3. Reviewer: GK B (plan first).

Acceptance:
- [ ] A new profile version imports a URDB tariff in the inputs stage, from a fixture response in the smoke (no network, L7).
- [ ] The plan says, with GK B, whether it needs its own envelope gap kind; the decision is recorded in the file.
- [ ] A tariff with a currency or year mismatch is refused as the stamp rules require (GLE-05).
- [ ] Earlier versions are byte-equal (L8).
