<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — Harness profile: eh_microgrid and eh_microgrid_staged

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK A for the island rules; GK B for the harness parts)
Model tier: opus
Owner decisions: SH-PILOT-1
Design: [design.md](../design.md) §9.3 "eh_microgrid"

## What to build

1. Profiles `eh_microgrid@1` and `eh_microgrid_staged@1`: scope `_MICROGRID`, archetype `off_grid`; `SINGLE_YEAR=True` handled at intake (end year after start year).
2. Island facts by the verdicts of the scorecard's island rows (`sustained_h` from `gridspine/schema/island.py`, frequency judge), critical feeder served; no SCR rule on an island.
3. Check the `campus_*` island tools on a microgrid project; where they do not fit, a native action over the island LP; record what was found in Comments.
4. Diesel-fleet IC baseline: the engine gives it (#302, owner M2e (a): `pypsa-gui/backend/services/results/baseline.py:4`, `finance_case.py:1376`; tested in `tests/test_eh_microgrid_case.py:97`), and [issue 20](20-microgrid-diesel-ic-baseline.md) landed its facts (`services/ic_counterfactual.py`). Two investment rules, both `warn`: `ic.counterfactual` `eq` `established`, and `ic.baseline_keeps` `nonempty` (the owned existing assets the case's baseline keeps; `not_established` naming each `MG_EXISTING` asset it drops). Both are evaluated once the case reads `established` (a baseline that sheds at VoLL or relaxes the island rows stays established, its caveats quoted by `counterfactual_text`), and `not_evaluated` with the fact's reasons otherwise; the run is never blocked by them (GK A).

## Acceptance criteria

- [ ] A run on each template reaches hand-off with island survival as a gate result.
- [ ] A seeded undersized battery fails survival; L2 resizes within bounds or ends `needs_user`.
- [ ] No SCR rule appears in the gate for an `off_grid` archetype.
- [ ] On `off_grid`, the rows built from the PoC's S_k'' (TDD, rapid voltage change, flicker, unbalance, motor start) are excluded with the reason (test); the gate is the island rows.
- [ ] The LOLE rule names its h/yr basis.
- [ ] The investment rules `ic.counterfactual` (`eq established`) and `ic.baseline_keeps` (`nonempty`), both `warn`, are evaluated on a finished case on each template, and `not_evaluated` with the fact's reasons while either reads `not_established` or `not_run` (issue 20).

## Likely files

`pypsa-gui/backend/services/run_profiles.py`, `pypsa-gui/backend/services/run_gates.py`, `pypsa-gui/backend/services/run_actions.py`, `pypsa-gui/backend/harness/workflows/profiles/eh_microgrid/`, `pypsa-gui/backend/tests/test_run_profile_microgrid.py` (new)

## Comments

### Fit check of the `campus_*` island tools (item 3), run on a project from each template

- Both templates make a capacity-expansion project, off-grid from creation, with an island sidecar (scenario `seamless`, `sustained_h` 24, `hospital_load` critical).
- `campus_run_island_study` fits `eh_microgrid` (5–8 s). It refuses `eh_microgrid_staged`: "island_study_multi_period_unsupported". So the scorecard's `ride_through:seamless` row stays unknown on the staged template, and the gate never passes a `ride_through:` row anyway.
- No stored fact says the sustained island was held: it exists only as LP rows. The staged solve's island facts are not saved; the single-year solve has the rows off.
- The critical load's unserved energy is explicit only in the reliability study's off-grid stress (`critical_unserved_mwh` for the `subsea_tie` contingency).
- The frequency judge needs a drafted campus file and a full campus run (about 2 min single-year, 7 min staged). Both shipped templates fail it: [issue 40](40-microgrid-templates-fail-frequency-screen.md).
- Fuel autonomy is not checked: the gensets draw no modelled fuel.
- The `campus_*` tools are chat tools; a profile reaches the island only through a native action.

So the survival evidence is a native check over the island LP (`services/adequacy/island_survival.py`): the plan as built, every size fixed, the island rows of the project's own solve added, per investment period.

### Plan verdicts (2026-11-10)

GK A (21) GO: Q1 fixed-design island LP; Q2 the frequency rule warns; Q3 harmonic distortion is excluded too; Q4 critical feeder from the reliability study's off-grid stress; Q5 a `warn` rule `mg.fuel.autonomy`, `not_evaluated` with the reason; conditions I1–I9.

GK B (44) GO for the harness side, conditions B1–B14: the survival action solves a private copy inside `solving_context` and counts in the run's solve budget (B1, B2); it is a registry action only (B3); "current" compares the network revision and the island sidecar digest (B4); a missing solved size is no evidence (B5); the critical-feeder and frequency facts carry the same currency check (B6); an unreadable record is no evidence (B7); goals show value, unit and source (B8); a campus draft is a native write with its own card (B9); the profile's EH study step and tunables (B10, B11); the `ic.baseline_keeps` words list every reader (B12); `planning` intake tests (B13); the guard tests at the trial merge (B14).

### Slices

- 12a (GK A): the off-grid exclusion on the scorecard, the island survival check and the tie and critical-load predicates. Engine only.
- 12b (GK B): the native action `mg_island_survival`, the `mg.*` gate facts and their words.
- 12c (GK B): the two profiles, their stage instructions, the `ic.baseline_keeps` reason words and the profile tests.

