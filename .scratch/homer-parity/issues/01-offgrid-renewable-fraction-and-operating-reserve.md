<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Off-grid constraints: minimum renewable fraction and operating reserve

Status: ready-for-agent
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
