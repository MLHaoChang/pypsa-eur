<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 44 — The report's gates table prints raw result-gate rule ids

Status: needs-triage
Type: bug (UX)
Gated: yes (GK B, UX)
Parent: [12-profile-microgrid.md](12-profile-microgrid.md) (UX (19) on #1409)

## What

The report's gates table (backend: `harness/run_handoff.py`, `services/reports/harness_chapter.py`) prints raw result-gate rule ids such as `mg.fuel.autonomy` and `dc.readiness.*` in user-visible text. There are 37 distinct facts: 18 have plan-card sentences in the frontend `runWords.ts` `FACT_WORDS`, the 19 `dc.readiness.*` rows have only `run_profiles` row sentences, and none has a short noun for a table cell.

#1409 kept this out and shows the count wording "N checks not evaluated (see the gates table)" instead.

## Likely fix

A backend word map (rule id → short noun and sentence), with a test enumerated from `run_profiles.PROFILES` so every rule of every profile has words, and the gates table reading it.
