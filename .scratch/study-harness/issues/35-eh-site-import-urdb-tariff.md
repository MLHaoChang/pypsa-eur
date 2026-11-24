<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 35 — eh_site: import_urdb_tariff in the inputs stage

Status: done (eh_site@6, PR for claude/sh35-eh-site-urdb-tariff)
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
- [x] A new profile version imports a URDB tariff in the inputs stage, from a fixture response in the smoke (no network, L7).
- [x] The plan says, with GK B, whether it needs its own envelope gap kind; the decision is recorded in the file.
- [x] A tariff with a currency or year mismatch is refused as the stamp rules require (GLE-05).
- [x] Earlier versions are byte-equal (L8).

### 2026-11-24: GK B (71) plan GO

Decisions (builder session_01G7qCuoAtMdDEDdciFDqxvB, reviewer GK B (71)):

- Q1 (a): the intake cites an uploaded URDB JSON (`import_urdb_tariff` reads uploads only); no OpenEI fetch, no new egress (owner decision G4: URDB is a user-scoped import; SH44-EGRESS-1 covers @3's endpoints only).
- Q2 (a): **yes, its own envelope gap kind**, `urdb_upload`, for the `attach_tariff` target, with `import_urdb_tariff` as its prepare step. `library_tariff` keeps "L1 never creates or uploads one".
- Q3 (a): a currency mismatch (`binding.tariff_currency_mismatch`, unchanged) or modelled years outside the tariff's validity refuse the gap in the harness, at the grant and again at use; preflight's `commercial.tariff_out_of_validity` stays a warning.

Conditions C1-C10: the prepare step is admitted only on a `urdb_upload` gap and the attach only for its own ref (C1); the grant pins every `import_urdb_tariff` arg (C2); upload sha256 and converted-tariff hash are checked before any write (C3); one Library write per grant (C4); Library write rights (C5) and upload access (C6) are checked at the grant; both year ends, investment periods, open `valid_to`, and a currency change after the grant (C7); new error kinds only in a fragment (C8); @1-@5 PINNED from master (C9); fixture terms checked (C10).
