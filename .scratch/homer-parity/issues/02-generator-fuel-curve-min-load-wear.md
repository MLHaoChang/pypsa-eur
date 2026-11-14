<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Generator realism: fuel curve, minimum load and run-hour wear

Status: ready-for-agent
Model: Opus
Reviewer: GK B (GK A only if it touches the adequacy/IC engine)
Blocked by: none
Size: 02a + 02b, two PRs
Type: task
Gated: yes (GK B; GK A if it touches the IC/adequacy engine)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1)

## What

HOMER Pro models a genset's fuel use as a no-load intercept plus a slope per kWh, a minimum load ratio, and an O&M cost and lifetime per running hour. In GridSpine a minimum output setting (`p_min_pu`) exists, but a fuel curve with no-load fuel use was found only in the data-centre backup code (`services/adequacy/dc_backup.py` heat rate). Without it, off-grid diesel results read optimistic: a genset idling at low load burns too little fuel.

## Likely shape

Optional fuel-curve inputs on a generator (intercept and slope, or a two-point curve), a minimum load ratio, and per-run-hour O&M, used in the dispatch (linear or with a commitment option) and in the cost breakdown. Reuse the DC backup heat-rate inputs where they fit.

## Triage

Confirm what exists on master, decide linear versus unit commitment, name the files and the reviewer, and set the model tier.

## Comments

### Triage 2026-11-12

Master check (`23aa3bc8`): the pieces exist apart; the fuel curve and run-hour wear do not.
- Minimum load exists: `p_min_pu` on Generator (`models/schemas.py:358`) and Link (`:264`), asset schema `services/asset_schema/fields.py:215` ("Minimum output"), forms from the schema (`PropertiesPanel.tsx`).
- Unit commitment exists: `committable`, `min_up_time`, `min_down_time` (`schemas.py:375-381`), schema block `fields.py:141-170`, solver `solver/process_uc.py:79`, `solver/island.py:466-491`. This is the linear-versus-UC decision: **use the existing committable path**; no new MILP machinery.
- Fuel curve with no-load intercept: missing. `heat_rate_mwh_fuel_per_mwh_el` (`models/flex_archetypes.py:39`) is slope only, used by the DC backup burn (`services/adequacy/dc_backup.py:837`).
- Per-run-hour O&M or hour-based lifetime: missing. Only `fom_cost` EUR/MW/yr (`schemas.py:244,362`); finance lifetime is calendar years (`services/finance/case.py:159,186`); `vom_cost` is energy-based (`routers/results.py:2865`).
- Question for the implementer to check first, not a blocker: whether PyPSA's `stand_by_cost` is passed through the solve; if it is, the no-load fuel maps to it.

### Slices
- **02a** fuel curve (intercept or two-point; slope into `marginal_cost`, no-load into a committed-hour cost) + min load ratio exposed on generator forms, in dispatch and the cost breakdown. Opus.
- **02b** O&M per running hour and lifetime in running hours (replacement timing from simulated hours) in the finance engine. Opus. Blocked by: 02a.

### Acceptance
- [ ] 02a: a generator with no-load fuel 0.05 MWh/h and slope 0.25 MWh/MWh burns more at low load than the linear model on a fixture; fuel cost in the cost breakdown matches the hand calculation.
- [ ] 02a: the curve inputs appear in the Generator form only for a fuel generator; committable is required for the no-load term (stated in the form).
- [ ] 02a: default (no curve) = unchanged results.
- [ ] 02b: per-run-hour O&M adds cost = hours run x rate; the replacement year follows lifetime hours / hours run per year.
- [ ] Release-note fragment.

### Likely files
`models/schemas.py` (Generator), `services/asset_schema/fields.py`, `services/solver/process_uc.py`, `harness/calculations.py:208-244`, `services/finance/case.py`, `cashflow.py`, `routers/results.py:2836-2873`, `frontend/src/api/types.ts`.

### Tests
`gridspine` UC tests (`tests/gridspine/test_dispatch_producer.py` pattern), new `backend/tests/test_homer_fuel_curve.py`, finance tests beside `test_finance_cashflow.py`; `PropertiesPanel.generatorSchema.test.tsx`; mutants on the changed finance module.

### 02a plan verdict (GK B, 2026-11-13)

GO with conditions. Answers:
- Q1 (fuel-generator gate): the curve rows show on **every** generator, help "Fuel-burning generators only." GK B: an LP input never hides, and `reveal_when` cannot express "fuel carrier OR a curve is set", so option (b). Validation never depends on the carrier. This replaces the acceptance line "appear only for a fuel generator".
- Q2: `marginal_cost` is fully derived (slope × fuel price) while a curve is set.
- Owner HOMER-02A-DOCCAP: the assistant attrs-doc cap rises to 4600 for the three fuel fields.

Conditions met in 02a: H1 one derivation (`services/fuel_curve.py`), staging authoritative, the same guard on the single edit, bulk edit and pre-run check; H2 the curve inputs in `_network_input_fingerprint` only when present; H3 a direct edit of a derived cost refuses, a new curve over a non-zero cost says what it replaces, a curve beside a `marginal_cost` series refuses, derived writes are stamped customised; H4 the DC backup prices fuel off the LP and never writes `marginal_cost`; H5 Finite inputs, one test per refusal; H6 the fuel block reads solved values only (None otherwise), uneven and multi-period weightings; H7 off hours counted out, no-curve results byte-equal to master; H8 mutants in the PR body.
