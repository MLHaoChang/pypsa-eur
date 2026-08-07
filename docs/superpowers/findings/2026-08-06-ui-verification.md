# UI verification of the four 2026-08-05 myopic/Compare fixes

**Date:** 2026-08-06
**Scope:** independent verification of the four fixes shipped 2026-08-05,
driven against the backend package directly (`PyPSAService` + `run_simulation`
+ `validate_for_run`), not the packaged `.app`, and the existing Vitest suite
for the frontend-only fix.
**Verdict:** all four fixes verified. 5/5 backend checks PASS, 36/36 Vitest
checks PASS with no `it.fails`.

**Driver:** `pypsa-gui/backend/smoke/verify_myopic_ui.py`. Builds a 3-period
(2030/2035/2040) network with one extendable gas generator, a VOLL slack
generator, and 44% demand growth from 2030→2040, solves it through
`run_simulation` with `solve_strategy="myopic"`, and asserts against the live
in-memory network/context — the same objects the FastAPI routers read from.
Run directly (no server needed):

```
.pixi/envs/test/bin/python -u pypsa-gui/backend/smoke/verify_myopic_ui.py
```

Backend Python for all checks: `.pixi/envs/test/bin/python` (a bare `python`
resolves the wrong pixi env). Step 1 (starting a uvicorn server) from the
plan was skipped — it is optional scaffolding the driver script does not
need.

---

## Fix 1 — `e4b8d2f7`: report the true horizon cost for myopic runs

**Claim:** the status-bar objective for a myopic solve must equal the
Economics tab total. Before the fix they differed by −42.9% on a 3-period
system.

**What was run:** after the myopic solve completed (`status="ok"`,
`condition="optimal"`), the network was reinstalled into the active
`PyPSAService` context and both `_compute_run_objective(n, cfg)` (the
status-bar value) and `get_cost_breakdown()["total"]` (the Economics tab
value) were read from the same solved network.

**Observed:**

| surface | value |
|---|---:|
| status bar (`_compute_run_objective`) | 47,865,000 |
| Economics tab (`get_cost_breakdown()["total"]`) | 47,865,000 |

Exact match (relative difference 0, well inside the `1e-6` tolerance used by
the check).

**Verdict: PASS**

---

## Fix 2 — `b4dc11d6`: warn when myopic locks capacity after the first period

**Claim:** a myopic network with no per-period vintage bounds must produce a
`myopic_capacity_locked_after_first_period` warning from preflight, before
the solve runs.

**What was run:** `validate_for_run(n, cfg)` on the freshly built (unsolved)
network with `solve_strategy="myopic"`, `multi_investment_periods=True`,
`investment_periods=[2030, 2035, 2040]`, and no vintage bounds configured on
the `GAS` generator.

**Observed:** issue codes returned —
`['carrier_zero_co2', 'snapshot_weights_nyears_off', 'myopic_capacity_locked_after_first_period']`.
The target code is present. (The other two codes are expected side effects of
this smoke network's minimal setup — no CO2 pricing configured, and default
snapshot weightings not scaled to the investment-period year count — and are
unrelated to this fix.)

**Verdict: PASS**

---

## Fix 3 — `6d009a28`: make a myopic run's build periods visible after the solve

**Claim:** after a myopic solve, `n.meta["vintage_results"]` must carry an
entry per expanded asset so the Capacity Expansion "by period" chart is
non-empty.

**What was run:** after the solve, read
`n.meta["vintage_results"]["Generator"]["GAS"]["periods"]` and extracted each
entry's `build_year`. Also checked that the parent `GAS` row's own
`build_year` column (a separate, unrelated PyPSA attribute) was left
untouched at its default.

**Observed:**

- `vintage_results` build years recorded: `[2030]` — non-empty, so the
  per-period chart has data to render. (Only 2030 appears because GAS is
  capacity-optimal at the 2030 vintage size for this demand-growth profile —
  see the capacity-lock dynamic checked by fix 2; the mechanism produces an
  entry per *expanded* period, and none of 2035/2040 triggered expansion in
  this scenario.)
- `n.generators.at["GAS", "build_year"]` = `0.0` — untouched, confirming the
  fix records results in `vintage_results` metadata rather than mutating the
  parent asset's own `build_year` field.

**Verdict: PASS** (both sub-checks)

---

## Fix 4 — `97728ad5`: stop Compare showing absent curtailment as zero

**Claim:** frontend-only fix; verify by code-read plus the existing Vitest
suite — do not attempt to drive the React UI.

**What was run:**

```
cd pypsa-gui/frontend
export PATH="<repo>/.pixi/envs/default/bin:$PATH"
npx vitest run src/pages/CompareView.test.tsx --reporter=dot
```

**Observed:** `Test Files 1 passed (1)`, `Tests 36 passed (36)`, no
`it.fails` (only skipped) entries in the summary. A code comment at
`CompareView.test.tsx:331-337` confirms the old defect was a standing
`it.fails`: `CurtailmentTab` originally guarded with `!hasAnyA && !hasAnyB`
(AND — only trips when *both* sides are empty) while `EmissionsTab` used OR;
with only one side's curtailment null, the AND guard didn't fire and the
missing side's fields fell through to a literal `0`, producing a fabricated
`-100.00%` delta. `CompareView` now bails to a text banner on either side
missing, and the test at line 336
(`it('curtailment: a null side bails to a message, not a fabricated -100%', ...)`)
is a real (non-`.fails`) assertion.

Verified directly in the test file that no `it.fails`/`test.fails` remains
anywhere in `CompareView.test.tsx` (grep returned only the historical
comment, not a live `.fails` call).

**Verdict: PASS**

---

## Summary

| fix | commit | check | result |
|---|---|---|---|
| 1 | `e4b8d2f7` | status bar == Economics (47,865,000 == 47,865,000) | PASS |
| 2 | `b4dc11d6` | `myopic_capacity_locked_after_first_period` in preflight codes | PASS |
| 3 | `6d009a28` | `vintage_results` build years non-empty (`[2030]`); parent `build_year` untouched (`0.0`) | PASS |
| 4 | `97728ad5` | `CompareView.test.tsx`: 36/36 passed, no `it.fails` | PASS |

Backend driver: 5/5 checks passed, exit code 0. Frontend: 36/36 passed. All
four fixes behave correctly against a real solve / the existing suite; no
regressions or discrepancies found.
