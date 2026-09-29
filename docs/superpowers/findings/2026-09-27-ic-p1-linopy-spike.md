# IC P1 WP1.5a-0 — linopy new-variable spike

Date: 2026-09-27 · Plan: `docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md` WP1.5a-0 ·
Script: `2026-09-27-ic-p1-linopy-spike.py` (runs against the 15-min edge fixture shifted to 2030-01-28 so the
week spans January and February). Stack: PyPSA 1.1.2, linopy 0.8.0, HiGHS.

## Verdict: GO for WP1.5a (no fallback to auxiliary Links needed)

| Question | Result |
|---|---|
| `n.model.add_variables(lower=0, name="ic_peak_import", coords=[months])` inside `extra_functionality` | Works. `Link-p` dims are `('snapshot', 'name')`; the per-snapshot month map is an `xr.DataArray` over `snapshot`, and `peak.sel(month=that)` broadcasts the monthly variable onto snapshots. |
| Per-snapshot `p_import[t] − P_peak[m(t)] ≤ 0` | Works (`add_constraints(..., name="ic_peak_import_def")`). |
| `n.model.objective += rate · Σ P_peak` | `n.objective` includes the term: 1,018,886.7396 = energy cost + 12,000 × (35.5 + 35.5), matching to 1e-10. |
| `assign_solution` / `assign_duals` on a dash-less name | INFO only: "could not be mapped to the network component because it does not include the symbol '-'" and "shadow-prices … ic_peak_import_def were not assigned". No error, no warning. |
| Solution readable post-solve | `n.model.variables["ic_peak_import"].solution` → per-month 35.5 MW = the monthly max of `links_t.p0["import"]`. |
| Composition with `_wrap_with_objective_scale` (scale 1e-3), through `run_simulation` | Objective 1,018,886.7396896 vs 1,018,886.7395897 at scale 1 (2e-15 rel.): the commercial wrapper runs before the scale wrapper, the scale multiplies the whole objective, and `_rescale_results_for_objective` divides `n.objective` back once. No double scaling. |
| Behaviour | The BESS shaves the import peak from 45 MW (no demand charge) to 35.5 MW. |

## Consequences for WP1.5a–WP1.5c
1. Naming convention stands (spec §5.1): new variables and constraints are dash-less with an `ic_` prefix.
2. `n.model` (and so the variable's solution) is a solve-time object. Peak values the report and
   `cost_breakdown` need after a reload must be copied into `last_commercial_terms` at the end of the solve
   (spec §5.1 reload rule); rows are recomputed from those persisted values, never from `n.model`.
3. Duals of `ic_*` constraints are not assigned to the network; if a demand-charge shadow price is wanted it
   must be read from `n.model.constraints[...].dual` in the same post-solve hook.
4. Windowed dispatch (rolling / myopic) calls `extra_functionality` once per window; each window re-creates the
   variables (spec §5.2 running-max lower bound is P6 scope). WP1.5a must key the variables to the months
   present in the window's snapshots.
