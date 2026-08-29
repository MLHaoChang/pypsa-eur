# Sequential MC Adequacy Engine — Implementation Spec (Phase 6)

**Status:** binding contract for implementation workers. **v1.1** — amendments from
the engine-core worker's flagged deviations are marked **[v1.1]**. **v1.2** —
adjudications of the ELCC and MC-benchmark workers' flags, marked **[v1.2]**; binding
on the endpoint/panel workers. The companion plan
(`plans/2026-08-28-fmea-phase6-sequential-mc.md`, v2.1) carries the WHY and the review
record; this document carries the exact WHAT. Where they disagree, this spec wins and
the disagreement is a finding to raise, not to silently resolve.

Workers implement AGAINST this spec. Deviations require a recorded reason in the
commit message. No decision below is re-derivable during implementation.

---

## 1. Scope

- Single-area (copper plate), electrical-only. One weather realisation (the modelled
  horizon's profiles). Independent unit outages. DSR not modelled as a resource.
- Engine id `"mc"`, fidelity `"sequential_mc"`. No per-mode € ranking.
- The MC **never mutates the network**. All inputs are snapshotted under the
  PyPSAService lock exactly once, into plain arrays; computation is lock-free.

## 2. Module `services/adequacy/mc.py`

### 2.1 Input snapshot

```python
@dataclass(frozen=True)
class StorageSpec:
    name: str
    p_nom_mw: float          # p_nom_opt when finite & >0, else p_nom (CoptUnit rule)
    e_nom_mwh: float         # max_hours * p_nom_mw
    eff_store: float         # efficiency_store, default 1.0
    eff_dispatch: float      # efficiency_dispatch, default 1.0

@dataclass(frozen=True)
class MCInputs:
    units: tuple            # CoptUnit list from fleet_and_residual, VERBATIM
    residual: np.ndarray    # float64 (H,) — load minus must-take, from fleet_and_residual
    weights: np.ndarray     # float64 (H,) — same source
    periods: tuple          # ((label, start_idx, end_idx_exclusive), ...) — contiguous
                            # blocks of the (possibly MultiIndex) snapshot axis;
                            # single-period networks carry one block labelled "ALL"
    storage: tuple          # (StorageSpec, ...)
    nyears: float           # horizon_years(n) — shared helper
    vre_profiles: dict      # {name: np.ndarray (H,)} — ONLY for names requested via
                            # snapshot_inputs(n, vre_assets=[...]); the asset's
                            # must-take contribution (profile × capacity) so ELCC can
                            # un-net it. Empty by default.
```

`snapshot_inputs(n, *, vre_assets=()) -> MCInputs`:
- calls `fleet_and_residual(n)` (units/residual/weights identical to the COPT — the
  provable-membership invariant).
- storage rows: `n.storage_units` at electrical buses, excluding slack carriers/names
  via `slack.py`'s `is_slack_carrier` / name-prefix tests applied to the storage frame
  (no storage slack mask exists; DSR slacks are Generators and never reach here).
- period blocks: from the snapshot MultiIndex level 0 in order; assert contiguity.
- Everything copied (`np.ascontiguousarray`) — no live references escape the lock.

### 2.2 Transition math (exact, no silent coercion)

For a unit with `q ∈ (0,1)`:
- `mttr = max(mttr_hours, 1.0)`; flooring logs a warning naming the unit.
- `mttf = mttr * (1 - q) / q`. If `mttf < 1.0` → `ValueError` ("implied MTTF < 1 h —
  inconsistent pair", surfaces as 422 at the route). Never clamp.
- `p_fail = 1/mttf`, `p_repair = 1/mttr` (both now guaranteed ≤ 1).
- Stationary identity to preserve (asserted in tests): `p_fail/(p_fail+p_repair) == q`
  exactly in exact arithmetic given the floor.
- `q == 0` → the unit is deterministically up (no RNG consumed — see 2.3!). `q >= 1`
  rejected by the occurrence validator upstream; assert here anyway.

Sojourns are geometric. `P(run = 1) = 1/mttr` per outage — persistence is a statement
about the MEAN run, not a floor. Semi-Markov/minimum-duration is a recorded non-goal.

### 2.3 Sampling — the CRN stream contract (load-bearing)

**Every unit owns its own RNG substream, keyed by its position in the FULL fleet:**
`children = rng.spawn(len(units))`, `children[i]` is unit *i*'s stream **whether or not
unit i is excluded**. A `q == 0` unit still occupies its slot (stream never advanced —
that is fine: identity is positional, consumption is per-stream, so other units'
draws are unaffected either way).

`sample_capacity(units, H, draws, seed, *, exclude=frozenset(), periods=None) -> np.ndarray (draws, H) float32`:
- **[v1.1]** `periods` (block boundaries) lives HERE, not in `simulate`: per-block
  stationary restarts must happen on each unit's OWN substream, or per-period
  re-initialisation would need per-block seeds and destroy the CRN bit-identity.
  Default `None` = one block.
- accumulator (draws, H) float32, zero-initialised;
- for each unit i **in the full fleet**: generate its (draws, H) availability path
  from `children[i]` (state vector (draws,) bool, hour loop, initial state stationary);
  if `i ∉ exclude`, add `capacity_mw * state` into the accumulator; **if `i ∈ exclude`,
  generate and discard** — the draws must be consumed identically so every other
  unit's path is bit-identical across exclusion sets.
- Never materialise a (draws, H, units) cube.
- Bit-identity guarantee (tested): for any `exclude`, each included unit's contribution
  is bitwise identical to the full-fleet run at the same seed.

### 2.4 Simulation

`simulate(inputs, *, draws, seed, exclude=frozenset(), extra_firm_mw=0.0,
storage_enabled=True, exclude_storage=frozenset(), initial_soc_frac=1.0)
-> per-draw arrays (lole_h, eue_mwh) float64`

Per period block, per draw (vectorised across draws; hour loop; storage loop inner):
1. At block start: outage states re-drawn from stationary; SoC := `initial_soc_frac`.
   **Nothing carries across period boundaries.**
2. `deficit[d] = residual[h] − capacity[d,h] − extra_firm_mw`
3. `deficit > 0`: discharge storage in **descending remaining-energy order**:
   `give = min(p_nom, soc·eff_dispatch, deficit)`; `soc −= give/eff_dispatch`.
4. `deficit < 0` (surplus `s = −deficit`): charge in **ascending remaining-energy
   order**: `take = min(p_nom, (e_nom − soc)/eff_store, s)`; `soc += take·eff_store`.
   (Sign conventions above are the spec; tests pin them with hand arithmetic.)
5. Residual deficit after storage: `lole_h[d] += w[h]·(deficit > SHORTFALL_TOL)`,
   `eue[d] += w[h]·max(deficit, 0)`. `SHORTFALL_TOL = 1e-6` MW.

Weights scale ACCOUNTING only; MTTR/sojourns/SoC evolve in modelled hours.

### 2.5 Aggregation & convergence

`mc_adequacy(inputs, *, draws=500, seed=0, cov_target=0.05, max_draws=2000,
batch=250, **sim_kwargs) -> dict`  (**[v1.1]** `sim_kwargs` forwarded to the
simulation — `exclude` / `extra_firm_mw` / `exclude_storage` / `initial_soc_frac` —
so ELCC can aggregate without re-implementing the loop):
- run batches until `CoV(mean LOLE) ≤ cov_target` or `max_draws`;
- outputs: `lole_hours` (mean), `lole_ci` = (max(0, m−1.96·s/√n), m+1.96·s/√n),
  `eue_mwh` + `eue_ci` likewise, `by_period` (means per block), `n_samples`,
  `converged: bool`, `time_basis`/`horizon_years` via the shared helpers,
  `resolution_floor_h = min(positive weight)/n_samples` (**[v1.2]** — the original
  `1/(n·nyears)` was per-YEAR against the per-HORIZON `lole_hours`; on sub-year
  horizons it inflated the floor by 8760/H and made ELCC's refusal fire too eagerly;
  found by the ELCC worker, fixed in mc.py with a bitten regression test) reported
  always — **[v1.1]** the KEY is
  always present but the value is `None` when `nyears ≤ 0` (no finite floor exists;
  `inf` does not serialise); the panel renders "unknown", never 0 — and
  `warning = MC_WARNING_V1`.
- `MC_WARNING_V1` (module constant, one string, three clauses): single weather
  realisation; independent unit outages (no common-mode); DSR excluded as a resource.

## 3. Module `services/adequacy/elcc.py`

`elcc_for_asset(inputs, kind, name, *, seed, draws, tol_mw=None) -> dict`
- kinds: `"generator"` (exclude its unit index), `"storage_unit"`
  (exclude from dispatch), `"vre"` (residual += `inputs.vre_profiles[name]`; KeyError
  → the route's 404).
- **[v1.1] CRN requires FIXED draw counts for candidate evaluations.** The adaptive
  batching in `mc_adequacy` is CRN-hostile: two Δ evaluations stopping at different
  `n_samples` draw from different sets, and `LOLE_reduced(Δ)` stops being the monotone
  step function the predicate assumes. Every candidate-Δ evaluation therefore runs
  `mc_adequacy(draws=N, max_draws=N, seed=seed, ...)` (fixed N = the baseline's final
  `n_samples`); only the baseline may use the adaptive path. **[v1.2] Correction from
  the ELCC worker (ratified):** a single batch of N is NOT batch-sequence-identical to
  an adaptive run that reached N in several batches (batch k consumes the k-th spawned
  child of SeedSequence(seed)). Candidates therefore REPLAY the baseline's batch
  sequence (same draws/batch, `max_draws=N`, `cov_target=-1` so no early stop) — bit-
  identical to the baseline AND to each other, which is what the predicate needs.
- **[v1.1] `kind="vre"` is REJECTED for any name present in `inputs.units`** (an
  occurrence-bearing generator was never netted into the residual; `residual +=
  profile` would double-count it). 422 at the route with a message naming the unit.
- baseline = `mc_adequacy` on full inputs at (seed, draws). If
  `baseline lole ≤ resolution floor` → `{"status": "unidentifiable", "reason": ...}`.
- Predicate: **smallest Δ with LOLE_reduced(Δ) ≤ LOLE_baseline** (LOLE_reduced is a
  monotone non-increasing step function of Δ under CRN — equality is ill-posed).
- Bracket `[0, nameplate]`; if unmet at nameplate → `{"status": "not_bracketed"}`
  (never extrapolate beyond nameplate; exceedance rejected in v1).
- `tol_mw = max(0.5, 0.001·nameplate)` default. All evaluations same seed (CRN).
- Row (**[v1.2]** as delivered — nine keys, always all present): `{kind, name,
  nameplate_mw, elcc_mw, elcc_share, status: "ok"|"unidentifiable"|"not_bracketed",
  reason (None iff ok), baseline_lole_h, baseline_lole_ci}`. Route mapping: KeyError →
  404 (unknown asset), ValueError → 422 (bad kind / vre double-count / bad tol).
  **[v1.2] Known properties:** `not_bracketed` is provably unreachable for the three
  v1 kinds (a full-nameplate firm block dominates any removed asset on identical
  draws — kept as a CRN tripwire); `kind="vre"` nameplate is `max(profile)` (the peak
  must-take contribution) — conservative when the profile never reaches 1.0; the
  endpoint MAY later pass installed capacity to widen it.
- `MAX_ELCC_ASSETS = 10`.

## 4. API (routers/results.py)

- `GET /results/mc` → 204 before any run; else the stored payload (no thread field).
- `POST /results/mc` body `{draws?, seed?, cov_target?, elcc_assets?: [{kind, name}]}`
  → 409 if solve/sweep/frontier/mc running (and mc registered in THEIR guards);
  422 if `fleet_and_residual` yields zero occurrence-bearing units ("nothing to
  sample"); 422 on inconsistent unit pairs (the 2.2 ValueError); VoLL NOT required.
  Worker thread, `_state["mc"]` = `{status, result, error, started_at, thread}`.
- Payload: `{engine: "mc", fidelity: "sequential_mc", metrics: {...per 2.5},
  elcc: [...] | [], warning}` — a **sibling payload** like the COPT endpoint;
  `AdequacyReport`'s "one shape" docstring is amended to say so (recorded decision:
  no report bloat for engine-local studies).
- `models/adequacy.py`: `Engine` += `"mc"`; `MetricsBlock` += `lole_ci`, `eue_ci`
  (tuple|None); legacy `confidence_interval` docstring: "deprecated alias of lole_ci".
  One comment on `TargetBlock.basis` reserving `"mc_lole"` for Phase 7.
- `test_golden_coverage.ROUTE_SURFACES` += get_mc/post_mc.

## 5. Frontend

- `api/simulation.ts`: `getMc` (204→null), `startMc(body?)`.
- `McPanel.tsx` (FrontierPanel pattern): run/poll; blocked button NAMES the blocker;
  CI rendered as ranges (`9.1–9.8 h`, `n=…` beside) — never `±`; all-clear case
  renders `< {floor} h`; the standing warning; the ELCC table (status rows render
  reasons); non-additivity note.
- Cross-engine comparison table (same file or `EngineComparison.tsx`): rows
  lp_proxy / copt / mc; columns metric, value(+CI), fidelity, storage-aware?,
  DSR-aware?, foresight, time-basis; header tooltips state ENS↔EUE and
  shed-hours↔LOLE alignment; COPT storage cell is a structural dash.
- Mounted in **both** LostLoadTab branches. IA decision comment in the header
  (stay on Lost load; split condition verbatim from the plan).
- Literal hex colours in any SVG (`var(--…)` does not resolve there).

## 6. Test contract (file: `tests/test_adequacy_mc.py`, `tests/test_adequacy_elcc.py`)

Every ★ test must be demonstrated RED before implementation and must FAIL against the
named broken variant (bite check), with the variant documented in the test docstring.

- ★ T-trans: transition math exact; MTTR floor at 1 h keeps stationary `q`; implied
  MTTF < 1 h raises. Bite: remove the floor.
- ★ T6: hour-0 availability ≈ 1−q (CI). Bite: init all-up.
- ★ T3: mean run ≈ MTTR AND `P(run=1) ≈ 1/mttr` (CI). Bite: iid Bernoulli swap.
- ★ T-CRN: bit-identity of every included unit's contribution under any `exclude`.
  Bite: joint (draws×units) sampling.
- ★ T1: q=0 fleet + storage → exact hand arithmetic (both efficiencies).
- ★ T1b: start empty, surplus precedes deficit → charge path exact.
- ★ T-period: two-block fixture where carrying SoC across the boundary would change
  EUE; assert it does not. Bite: remove the reset.
- ★ T4: ∞-duration ≡ +p_nom firm; zero-energy ≡ absent (identical draws).
- ★ T2: thermal-only MC LOLE within 99% CI of COPT exact value (docstring states
  persistence-blindness). Bite: broken-transition variant (p_fail = q).
- ★ T2b: Dunkelflaute+battery fixture — EUE(persistent) ≠ EUE(iid) at same
  stationary q. This is the metric-level persistence pin.
- ★ T-elcc: perfect unit ELCC == capacity (tol) under CRN — bite: re-seed per
  evaluation; declining credit; battery long/short fixtures (short at SoC 0.5);
  unidentifiable; non-additivity on one fixture.
- Weights test: doubling weights doubles LOLE/EUE, changes no dynamics.
- Every stochastic test: fixed seed, draw counts sized so the assertion is stable.

## 7. Benchmarks (`tests/test_adequacy_benchmarks.py`, `@pytest.mark.slow`)

- Fixtures under `tests/benchmark_data/`: `rts79_units.csv` (32 units: name, MW, FOR,
  MTTR), `rts79_load.py` (**[v1.1]** delivered name; the plan's `rts79_load_model.py`
  is superseded) (the 1979 percentage tables + reconstruction:
  Monday-start, week 1 = winter, 8736 h) — provenance header: source, retrieval date,
  second-source cross-check. `basis="FOR"` pinned. RBTS likewise.
- Order: COPT vs published FIRST; then MC vs published: inside 95% CI AND half-width
  ≤ 5% of published. **[v1.2] Adjudications from the delivered gate:** (a) the RBTS
  width criterion is a MEASURED miss at 5000 draws (11.6% — event-rate, not engine;
  verified reachable at ~27k draws where it hits 4.6%) and is soft-asserted with the
  shortfall printed, RTS-79's stays hard; (b) the "MC mean within the COPT tolerance
  band" belt-and-braces is statistically unpurchasable at this budget (±1% on the mean
  needs ~10⁵–10⁶ draws) and is ratified as
  `|mean − published| ≤ 1%·published + 95% half-width`; (c) seeded anchors assert only
  at BENCH_DRAWS=5000 (a different budget is a different estimator), skip loudly. Budget: `BENCH_DRAWS` env, default 5000 (independent of
  MAX_DRAWS); print empirical CoV. After first green, pin the seeded value.
- If tables cannot be sourced in-environment: the task records the gap loudly in the
  PR body; the gate stays required and unmet (blocks the "done" claim, not the branch).

## 8. Performance targets

500 draws × 168 h × ≤10 units: < 5 s. 1000 × 8760 × 32 units: low tens of seconds.
A miss is a finding to report, not to hide.
