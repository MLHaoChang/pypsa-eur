<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Off-grid constraints: minimum renewable fraction and operating reserve

Status: done (#1424; 01a only, 01b operating reserve pending)
Model: Opus
Reviewer: GK B (GK A only if the adequacy/IC engine is touched)
Blocked by: none (40 is independent: it is about island frequency screens, not these constraints)
Size: 01a + 01b, two PRs, each well under 1,500 lines
Type: task
Gated: yes (GK B; GK A if it touches the IC/adequacy engine)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1)

## What

HOMER Pro lets an off-grid design require a minimum renewable fraction of served energy and an operating (spinning) reserve: dispatchable headroom held each hour as a share of load plus a share of the solar and wind output, so a cloud or a gust does not drop load. A keyword pass found neither constraint in GridSpine; `reserve_margin` in `services/adequacy/` is a capacity margin, not per-hour headroom.

## Likely shape

Two optional constraints on an energy-hub or microgrid study, with typed inputs (percent of load, percent of PV, percent of wind; minimum renewable fraction), shown in the results with the hours where the reserve binds. Check the microgrid profiles (SH-12c) and study-harness 40 (microgrid templates fail the frequency screen) for overlap.

## Triage

Confirm on master that neither constraint exists (also under other names), name the files and the reviewer, and set the model tier.

## Comments

### Triage 2026-11-12

Master check (2026-11-12, triage keyword + synonym pass, `23aa3bc8`): both constraints are missing; nothing exists under another name.
- Renewable fraction: no constraint and no KPI. `services/network_global_constraints.py:67-131` is generic global-constraint CRUD with no renewable type; `routers/results.py`, `services/finance/metrics.py` and `services/solver/*` hold none. Only curtailment penalty (`models/schemas.py:1341`, `:1410`) and VRE credit (`routers/results.py:1371`) are near it.
- Operating reserve: no LP rows. `services/solver/reserve_products.py` and `models/commercial.py:775-805` are FCR/aFRR revenue products, not headroom. `services/solver/island.py:2,35` has a UPS bridge reserve for islands. `reserve_margin` in `services/adequacy/` is a capacity margin. The `spinning` genset flag (`gridspine/schema/island.py:518`) is a fuel-cost rule needing `p_min_pu > 0`.
- The implementer re-greps before building; the pass was by keyword.

### Slices
- **01a** minimum renewable fraction: constraint plus a renewable-fraction KPI that shows even when the constraint is off.
- **01b** operating reserve: per-hour headroom = a% of load + b% of PV + c% of wind, held by dispatchable units (generators, storage discharge headroom), with the hours where it binds. Blocked by: 01a only for the shared input block in Solver Settings.

### Acceptance
- [ ] 01a: typed input `min_renewable_fraction` (0-1) in the solver config, validated; the LP adds one energy constraint over the solved periods; a case needing more renewables than are available reports infeasible with that constraint named.
- [ ] 01a: KPI `renewable_fraction` in results, equal to hand-computed renewable energy served / load on a 3-asset fixture.
- [ ] 01b: typed inputs pct of load, pct of PV, pct of wind; rows per snapshot; a fixture where a genset must run at an hour it otherwise would not.
- [ ] 01b: results list the snapshots where the reserve binds.
- [ ] Both default off: existing study results are byte-identical.
- [ ] Release-note fragment.

### Likely files
`pypsa-gui/backend/models/schemas.py` (`SolverConfig`, near `:712`), `services/solver/objective.py` / `solver/assumptions.py` (extra constraints), `services/solver_service.py`, `routers/results.py` (KPI), `frontend/src/pages/SolverSettings.tsx` and `api/types.ts`. The chat tool for solver settings follows `harness/README.md`.

### Tests
New `backend/tests/test_homer_renewable_fraction.py`, `test_homer_operating_reserve.py` (LP fixtures; follow `test_fca_load_scalers.py`); a `SolverSettings` vitest; `scripts/coord/mutants.py` on the changed solver module.

### 01a plan verdicts (2026-11-12)

GK B GO (per-period rows), with conditions; the PR body maps each to its test:
- R1 Off is byte-identical: `goal_free_config` (fingerprint, IC digest) equals master's for None and 0; 0.6 changes both.
- R2 One implementation: `services/solver/renewable_fraction.renewable_fraction(n)`; 03a imports it (or 01a imports 03a's if that lands first). Weighted energy, served = load − slack, served 0 → None, grid import gross.
- R3 Grid import is non-renewable as a Generator or as a Link from a bus fed by a non-renewable Generator, even an electrical bus; a Link fed through a Store/StorageUnit is not a source.
- R4 The row uses the scaled p_set and time-varying η (`links_t.efficiency`).
- R5 Slacks (VOLL, DSR, dc_curtailment) let the LP meet the target by shedding at the slack's price; documented, tested with and without a VOLL slack.
- R6 Served 0 → None with a reason; NaN/inf/out-of-range refused by the schema.
- R7 Every solver-settings path runs the same validator; chat inventory checks if the tool enumerates fields.
- R8 New route GET /results/renewable_fraction: route policy tests and route-parity check at the trial merge.
- R9 Infeasibility names the row per period and the max renewable supply; no row on an empty period.
- R10 One row per period on that period's weightings; a sum-over-all mutant fails.
- R11 Vitest: 80 → 0.8, empty → null, −5/150/text refused with no request.
- R12 mutants.py plus hand mutants (slack term, weighting, period filter, off-gate, import-link classification, 0/0 guard), each with its killing test.

UX (no objection to placement), conditions:
- U1 The hint never names `min_renewable_fraction`; the infeasible message opens with the label.
- U2 Label "Minimum renewable fraction (%)"; visible "0 means no minimum"; out-of-range gets an inline sentence, no silent clamp.
- U3 Plain-words definition.
- U4 Block heading "Off-grid constraints" with a muted line saying when it applies.
- U5 Shots: idle, value set, out-of-range, infeasible message; Expert (Guided only if reachable); light and dark.
- U6 The chat edit card shows the same label, with a drift test.
- R13 (GK B, later): fail closed now, count later. A dispatched Link from a fuel stock (a non-renewable Store, no Generator, no electrical inflow) into an electrical bus makes the KPI None with a reason, and the LP row warns. **Follow-up:** count its η·p0 as non-renewable. The same fail-closed rule covers non-renewable Generators that reach the site through an uncounted path (a chain, a multi-port Link, a second Link).
- 01a evidence (#1424): UX shots and the Playwright scripts that took them are in `.scratch/homer-parity/shots-01a/`: `{light,dark}-{idle,set,out-of-range}.png`, `light-page-context.png`, `guided-light-value-100.png`, `dark-infeasible-log.png`; scripts `shoot.js`, `shoot_guided.js`, `shoot_log.js`, `solve.js`, `peek.js`. The scripts read the dev admin password from disk and hold no credential.
