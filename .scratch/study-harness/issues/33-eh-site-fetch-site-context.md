<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 33 — eh_site: fetch_site_context in the inputs stage

Status: ready-for-agent (Opus; GK B plan first)
Type: task
Gated: yes (GK B)
Model tier: opus
Parent: [07-report-handoff-and-pilot.md](07-report-handoff-and-pilot.md) (GK B (33) P7)
Design: [design.md](../design.md) §9.2, §9.3 eh_site

Design §9.2 lists `fetch_site_context` in stage 2 (inputs) "when a site is given", and §9.3
names it for eh_site. `eh_site@2` (issue 07 B3b) does not call it: the pilot runs offline on
the test fixture (L7). Add it to the inputs stage of a new profile version (L8: never change a
granted run's inputs), with a fixture for the offline smoke, and say which gate rule reads it.

## Comments

### 2026-11-11: triage

Checked on master: `eh_site@2` is the newest eh_site profile in `run_profiles.PROFILES` and its inputs stage does not call `fetch_site_context`. Verdict: ready-for-agent.

- Blocked by: none (07 is done, #1257).
- Likely files: `pypsa-gui/backend/services/run_profiles.py` (a new `eh_site@3` profile, L8), `harness/run_eh.py`, `services/run_gates.py`, `tests/test_harness_run_eh_site_v2.py` and a sibling for v3. Reviewer: GK B (plan first).

Acceptance:
- [ ] A new profile version (`eh_site@3`) calls `fetch_site_context` in the inputs stage when the intake names a site; `eh_site@1` and `@2` stay byte-equal and a granted run keeps its profile version (L8).
- [ ] The offline smoke runs it from a fixture, with no network (L7).
- [ ] The plan names which gate rule reads the fetched context, and a test shows that rule firing on it.
- [ ] A site that cannot be fetched leaves the gap visible in the report, never a silent default.
