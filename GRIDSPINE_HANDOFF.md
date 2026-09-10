# gridspine — cloud continuation handoff

**Written:** 2026-09-01 · **Owner:** Hao (Hitachi Energy Power Consulting)
**Purpose of this file:** everything a fresh session needs to finish increment 2. It assumes zero prior context.

`gridspine/` is a headless pipeline package living inside the pypsa-eur fork. It chains open-source engines upstream of commercial dynamics tools: **PyPSA** (capacity expansion + unit commitment) → **pandapower** (AC load flow, later N-1/short circuit) → snapshot ranking → a **PSS/E `.raw` handoff** that PowerFactory imports. Own IP is the canonical schema, the ranking, the handoff contract, the parameter templates and the assumptions ledger; the engines are off-the-shelf.

---

## 1. Where the code is

| Branch | Tip | Contents |
|---|---|---|
| `feature/local-app-impl` | `2c4a2e00` | Increment 1 **merged** + the design spec + both plans |
| `gridspine-inc2` | `17e0dc8f` | Increment 2, tasks 1–8 (branches off `2c4a2e00`) |
| `master` | — | This handoff document only; **no gridspine code** |

Both work branches are pushed to `origin` (`github.com/MLHaoChang/pypsa-eur`). `upstream` is the public PyPSA project — never push there.

```bash
git fetch origin
git checkout gridspine-inc2      # the branch to continue on
```

Design spec and plans (on `feature/local-app-impl`, inherited by `gridspine-inc2`):

- `docs/superpowers/specs/2026-08-27-gridspine-design.md` — architecture and the decisions behind it
- `docs/superpowers/plans/2026-08-28-gridspine-increment-1.md`
- `docs/superpowers/plans/2026-08-31-gridspine-increment-2.md` — **Task 9's spec is section "Task 9" of this file**

---

## 2. Status

**Increment 1 — complete and merged.** 39-bus vertical slice: case39 ingest → PyPSA nodal UC → AC load flow → `.raw` v33 export → PowerFactory comparison harness.

**Increment 2 — 8 of 9 tasks complete.** All eight landed with TDD evidence, an independent review, and fix rounds where review found defects.

| # | Task | Commit(s) | State |
|---|---|---|---|
| 1 | Dynamic parameter templates (H values as YAML) | `8a9d441a` | done |
| 2 | Synthetic year profiles (load / wind / solar) | `46067e75` | done |
| 3 | RES-augmented fixture `case39_res` | `160ee711` | done |
| 4 | Producer v2 — profiles in, rolling-horizon UC | `806a86de` | done |
| 5 | Loads artifact + hour-consistent LF (retires the hour-19 guard) | `17e0dc8f` | done |
| 6 | RAW writer emits sgen (RES) machine records | `cf01ad25`, `5b251b63` | done |
| 7 | Ranking — snapshot metrics and top-k selection | `18bc8911` | done |
| 8 | Branch-flow read-back and comparison | `33461338`, `28ccb19b` | done |
| **9** | **Year-study driver + CLI** | — | **NOT STARTED — this is the work** |

**Gate, verified 2026-09-01 on `17e0dc8f`:**

```
pixi run gridspine-tests
233 passed, 2 skipped, 43 warnings in 97.70s
```

Both skips are expected and documented, not failures:

- `test_raw_writer.py:80` — `pandapower.converter.from_psse` does not exist in pandapower 3.1.2, so the `.raw` round-trip cannot run. PowerFactory is the real oracle.
- `test_vertical_slice.py:66` — the PowerFactory fixture has not been exported yet (see §7).

The suite takes ~100 s because Task 4's rolling-UC fixtures solve real MILPs. That is normal, not a hang.

---

## 3. Environment

Everything runs through **pixi**; a bare `python`/`pytest` resolves the wrong environment and silently proves nothing.

```bash
pixi install                 # first run in a fresh clone; takes several minutes
pixi run gridspine-tests     # the gate: python -m pytest tests/gridspine -v
```

- **pandapower is pinned `==3.1.2` under `[pypi-dependencies]`, not conda.** Every conda-forge 3.x build declares `scipy <1.14`, unsatisfiable against this repo's `scipy>=1.16.3` (the recipe mistranslated `~=1.13`). The exact pin also keeps macOS and Windows on one version — a range resolves to 3.2.2 on osx/linux but 3.1.2 on win-64.
- `ruff` is **not** installed in this environment. Do not claim a lint pass.
- Reports for each task live in `.superpowers/sdd/2026-08-31-gridspine-increment-2/` **in the local worktree only** — that directory is gitignored, so it does not travel with the branch. This document replaces it.

---

## 4. The API as it stands

Everything below is landed and tested. Task 9 consumes it and adds nothing to it.

### Stage boundaries (`gridspine/schema/`)

```python
ContractError(ValueError)                      # contracts.py — every validator raises this

validate_dispatch(df) -> DataFrame             # dispatch.py
#   columns: unit_id, hour, p_mw, q_mvar, status
#   guards run BEFORE dtype coercion: status exactly 0/1, hour integral,
#   unit_id non-null, p/q finite, unique (unit_id, hour), status 0 => p_mw ~ 0

validate_loads(df) -> DataFrame                # dispatch.py
#   columns: bus, hour, p_mw, q_mvar — same pre-coercion discipline,
#   unique (bus, hour), p_mw >= 0 (q_mvar signed: capacitive buses exist)

validate_canonical(buses, unit_names) -> None  # network.py
unit_registry(gen_names, gen_buses, ext_names, ext_buses) -> DataFrame
MAX_NAME_LEN = 12                              # PSS/E v33 NAME field width
#   names must fullmatch [A-Za-z0-9_-]; see the security note in §6

StageError(stage, element_ids, cause).write(outdir) -> Path   # errors.py
```

### Ingest (`gridspine/ingest/`) — may import pandapower

```python
load_case39()      -> net    # canonical names BUS_01..BUS_39, G_BUS_xx, SLK_BUS_31
load_case39_res()  -> net    # + 5 sgen: W_BUS_33/35/37 @600 MW, S_BUS_34/36 @500 MW
registry_from_net(net) -> DataFrame   # index unit_id; columns bus, kind in {gen, ext_grid, res}
RES_LEDGER                            # the siting/capacity assumptions, for the manifest

year_load_shape(hours=8760) -> Series   # synthetic_profiles.py — deterministic, closed form
wind_cf(hours=8760)        -> Series
solar_cf(hours=8760)       -> Series
PROFILE_LEDGER                          # for the manifest
```

### Templates (`gridspine/templates/`) — no engine imports

```python
load_unit_params(path=None) -> DataFrame
#   index unit_id; columns h_s, mbase_mva, source in {measured, datasheet, assumed},
#   include_in_inertia. Default file: templates/data/case39_units.yaml
```

### Producers (`gridspine/producers/`) — the only module allowed to import pypsa

```python
to_pypsa(net, snapshots=24, load_shape=None, res_cf=None) -> pypsa.Network
#   load_shape=None keeps increment-1 behaviour byte for byte.
#   res_cf maps EVERY sgen canonical name to a per-hour capacity factor;
#   a missing key raises, and so does a key naming no sgen.
#   Profiles align POSITIONALLY (the index is discarded, never reindexed onto).

run_uc(n) -> n                                        # exact solve, short horizons
run_uc_rolling(n, window=168, overlap=24, mip_rel_gap=0.01) -> n
to_dispatch_table(n) -> DataFrame                     # validated
to_loads_table(n, net) -> DataFrame                   # validated — TWO arguments, see §6
```

### Static (`gridspine/static/`) — may import pandapower

```python
@dataclass LFResult:
    converged: bool
    bus: DataFrame           # index bus name; vm_pu, va_degree
    branch_loading: DataFrame
    slack_p_mw: float
    branch_flow: DataFrame   # from_bus, to_bus, ckt, p_from_mw, q_from_mvar, loading_percent

apply_snapshot(net, dispatch, loads, hour, registry) -> None   # THE one to call
apply_dispatch(net, table, hour, registry) -> None             # deprecated; generators only
run_lf(net) -> LFResult                                        # non-convergence is a RESULT
```

### Ranking (`gridspine/ranking/`) — no engine imports

```python
snapshot_metrics(dispatch, loads, unit_params, registry) -> DataFrame
#   index hour; columns load_mw, import_mw, inertia_mws,
#   inertia_excl_equiv_mws, ibr_share

select_snapshots(metrics, k=5) -> DataFrame       # columns hour, reasons (list[str])
validate_selection(selection, metrics) -> DataFrame
CRITERIA  # min_inertia_excl_equiv_mws, max_ibr_share, max_load_mw, max_import_mw
```

### Handoff and read-back

```python
write_raw(net, path, title="gridspine export", f_hz=50.0) -> dict[bus_name, bus_number]
compare_lf(lf, pf_csv, vm_tol=0.01, va_tol_deg=0.5) -> DataFrame
compare_branch_flows(lf, pf_csv, p_tol=0.01, q_tol_mvar=5.0) -> DataFrame
run_39bus_slice(outdir, hour=19) -> SliceResult                # drivers/planning.py
```

**Engine cage** (enforced by tests, do not breach): `pypsa` only under `producers/`; `pandapower` only under `ingest/`, `static/`, `handoff/`. `schema/`, `ranking/`, `templates/`, `readback/` import neither — that is what lets a client recompute the metrics from the CSVs without the simulation stack.

---

## 5. Task 9 — the remaining work

Create `gridspine/drivers/year_study.py` and `tests/gridspine/test_year_study.py`. Touch nothing else.

### Interface

```python
@dataclass StudyResult:
    selected: DataFrame            # the selection table, plus a `converged` column
    artifacts: dict                # name -> Path
    lf_results: dict[int, LFResult]

run_year_study(outdir, hours=8760, k=5, window=168, overlap=24) -> StudyResult
```

CLI: `pixi run python -m gridspine.drivers.year_study --out <dir> [--hours 8760] [--k 5] [--window 168] [--overlap 24]`

### The chain

1. `load_case39_res()` → `registry_from_net`
2. `to_pypsa(net, hours, load_shape=year_load_shape(hours), res_cf=…)` — build `res_cf` by prefix: `wind_cf(hours)` for names starting `W_`, `solar_cf(hours)` for `S_`
3. `to_loads_table(n, net)` → write `loads.csv`
4. `run_uc_rolling(n, window, overlap)` → `to_dispatch_table` → write `dispatch.csv`
5. `snapshot_metrics(dispatch, loads, load_unit_params(), registry)` → write `metrics.csv`
6. `select_snapshots(metrics, k)` → `validate_selection` → write `selected.csv` (with `reasons` and `converged`)
7. Per selected hour: `apply_snapshot` → `run_lf` → write `lf_<hour>_bus.csv` and `case39_h<hour>.raw` (**`f_hz=60.0`** — case39 is a 60 Hz system)
8. Write `manifest.json`: stages, the hour list, and the assembled ledger

The manifest ledger must include `planning.LEDGER`, `PROFILE_LEDGER`, a `RES_LEDGER` summary, and unit-parameter provenance counts (e.g. "unit H params: 10 datasheet, 0 measured, 0 assumed"). That ledger is the report appendix — it is the reason a client can tell a measurement from an assumption.

A selected hour whose load flow does not converge is **recorded** (`converged=False`) and the run continues. Non-convergence is a result, not a crash. Stage failures write `StageError` artifacts, following `drivers/planning.py`.

### Constraints that bind the tests

- **Runtime.** Tests run `hours=336, k=2, window=168, overlap=24`, with **one module-scoped rolling-solve fixture reused across every test**. Budget under 120 s for the file. The 8760-hour run is CLI-only — do not put it in a test; report the command for Hao instead.
- `select_snapshots` returns **between k and 4k rows, never exactly k** — read `len()`.
- Do **not** assert a year-wide slack bound. At peak hours the slack legitimately imports ~4.5 % of load; the 5 % bound in `test_loads_artifact.py` holds at hour 8 only. A year-wide assertion must net out the `ext_grid` dispatch first.
- Curtailed RES rows carry `status=0, p_mw=0`, indistinguishable from offline. Per-hour figures come from `metrics`, never from counting statuses.
- **Mutation check required**: skip `apply_snapshot` before a selected hour's `.raw` and show which test fails. If none does, the test is blind and needs strengthening — this has already happened twice in this project (§6).

---

## 6. Rulings and traps — read before writing code

Each of these was paid for once. They are binding unless deliberately revisited.

**Decisions**

1. **The detailed grid is canonical; PyPSA projects onto it.** Names cross every stage boundary (`PyPSA bus == pandapower bus == .raw NAME == PowerFactory name`); positional indexes never do. Nodal resolution makes the mapping the identity.
2. **The stage-1→2 contract is a dispatch table keyed by unit id, not a PyPSA network.** That is what makes PyPSA a plugin: the connection-study variant supplies the same table from client snapshots.
3. **`to_loads_table(n, net)` takes two arguments.** PyPSA solves a real-power problem and carries no reactive load, so Q must come from the pandapower net. Q is derived at constant power factor from each bus's native Q/P — a ledgered assumption.
4. **Ranking sorts on `inertia_excl_equiv_mws`, not `inertia_mws`.** `G_BUS_39` (h = 500 s) is the aggregated interconnection equivalent, not a power station; its ~50 000 MW·s is a near-constant floor that mutes the commitment signal the metric exists to surface. Both columns are reported: quote `inertia_mws`, rank on the other.
5. **`mip_rel_gap=0.01` on the rolling solve is mandatory, not tuning.** Measured on case39_res: exact solves take 6 s at 24 h, 32 s at 48 h, 488 s at 72 h, and a 168 h window never finishes. The gap is an *optimality* tolerance — min up/down times, `p_min_pu` and the energy balance stay hard at any gap, so the seam contract is untouched.
6. **The increment-1 `.raw` writer deviates from the naive form in 12 places**, each verified against the v33 field layout: real tap ratios (case39's are 1.006–1.07 and dropping them alone would blow the 1 % PowerFactory gate), the tap-adjusted impedance base for LV-tapped transformers, the 17-field transformer line 3 including CNXA1, unique (I,ID) and (I,J,CKT) counters, `STAT` from `in_service`, IDE=4 for isolated buses, and fixed-Q (QT=QB=QG) for inverter machines so PowerFactory does not read them as voltage controllers.

**Traps that produced silent wrong answers**

7. **HiGHS returns 0.9999999 for a committed unit.** PyPSA measures the trailing commitment run with `status.cumsum() == 1, 2, 3…`, which compares unequal — so the run measures as *zero hours* and the seam constraint vanishes silently, while `astype(bool)` still reads 1e-9 as "up". Frozen statuses are rounded to exact 0/1 in `_rounded_status`. Do not remove it.
8. **A test can pass for the wrong reason, twice over.** The commitment test was satisfied by the always-zero non-committable slack until it was restricted to committable units. The seam test showed zero violations with the seam carry deliberately removed, because a 2-seam fixture let the next window re-choose the same units — it needed a 13-seam fixture (336 h / window 48 / overlap 24) to bite. Mutate the code and confirm the test fails; a green negative test proves nothing.
9. **`validate_dispatch` cannot catch every corruption.** A status-inference bug on non-committable units gets its `p_mw` zeroed *before* validation, so the table validates cleanly while real dispatched energy vanishes. Only `test_non_committable_status_is_inferred_from_output` catches it. Do not treat the validator as the safety net for that class.
10. **Increment 1's driver refused every hour but 19, and the reasoning behind parking that was wrong.** The parking note claimed non-peak hours "fail loudly"; a probe showed hours 8–18 *converge*, with the slack silently importing up to 933.6 MW of phantom residual — plausible, hour-mislabelled artifacts. Task 5 fixed the cause (the loads artifact) rather than the symptom. The lesson generalises: check that a failure mode actually fails.
11. **A safety substitution can invalidate the probe.** A `--dry-run` instead of the real command, a stub instead of the service — each is chosen to avoid consequences, so it never gets listed among the things that might explain the result. Vary one factor at a time.

**Security (from an adversarial pass over increment 1)**

12. Canonical IDs are restricted to `[A-Za-z0-9_-]` via `str.fullmatch` at the validator, because the `.raw` writer emits names unescaped — a name containing a quote, comma or newline forged records in a probe. Note `re.match(..., "$")` is **fail-open**: `$` matches before a trailing newline, so `"B1\n"` passes. Use `fullmatch`.
13. **Never call `pp.from_json` or `pp.from_pickle` on a client-supplied file.** Both are arbitrary-import / RCE gadgets: `from_json` imports the module and class named in the JSON. When increment 2+ accepts client grids, that needs a sanitising trust boundary first.

---

## 7. The pending human step — PowerFactory validation

This is the last piece of increment 1's definition of done, and it needs Hao, not an agent.

The exported `.raw` for the peak hour is at `results/gridspine_slice/case39_dispatch.raw` (regenerate any time with `pixi run python -m gridspine.drivers.planning --out results/gridspine_slice`).

Per `tests/gridspine/fixtures/powerfactory/README.md`: import it, run a Newton-Raphson load flow, and export **two** CSVs in the same session —

- `case39_h19.csv` — `bus_name,vm_pu,va_degree`
- `case39_h19_branches.csv` — `from_bus,to_bus,ckt,p_from_mw,q_from_mvar,loading_percent`

Drop both into `tests/gridspine/fixtures/powerfactory/` and re-run the gate; `test_powerfactory_gate` arms itself and asserts agreement per element (< 1 % on voltage magnitude, < 0.5° on angle). Increment 2's Task 8 added the branch-flow comparison, so both files are now checked automatically.

Until that fixture exists the converters are verified against pandapower and internal consistency only. **That is the one claim in this project not yet backed by an independent oracle.**

---

## 8. After Task 9

1. Hao runs the full year: `pixi run python -m gridspine.drivers.year_study --out results/gridspine_year --hours 8760 --k 5`. Expect minutes to hours — 52 rolling windows at the 1 % gap.
2. Validate one *selected* min-inertia hour in PowerFactory, same runbook flow. That is the interesting comparison: increment 1 validated the peak hour, which is the easy one.
3. **Increment 3** (per the design spec): N-1 full and N-2 screened contingency analysis via `lightsim2grid` with a DC-LODF prune, IEC 60909 short circuit, and an N-1 severity metric joining the ranking.

**A trap already identified for increment 3:** `apply_snapshot` maps a curtailed RES unit to `in_service=False`. That is correct for load flow — a zero-injection PQ element and an absent one are the same node equation — but **wrong for short circuit**. A curtailed inverter is still energised, still synchronised, and still contributes fault current; `in_service=False` deletes it from the fault calculation and understates the contribution at exactly the buses the study is about. Increment 3 needs its own status → element mapping. The warning is in the `_apply_res` docstring.

Two smaller items worth folding in when convenient: the RES sites all share one `wind_cf` series, so wind is perfectly correlated across the three farms (understates fleet variability); and `templates/data/*.yaml` has no package-data entry, so a wheel build would break the default path while every test still passes.

---

## 9. How this work has been run

Each task was implemented by a subagent against a written brief, then reviewed by an independent agent that read the diff and the report without trusting either. Reviews returned "needs fixes" five times; every finding was either fixed or ruled on explicitly. Reports carry the failing-test output before the fix and the passing output after, plus a mutation check where the test's own sensitivity was in doubt.

That process is why §6 exists — most of those entries are things a review or a mutation caught, not things anyone knew in advance. Continuing in the same style is recommended: write the test first, break the code to confirm the test sees it, and treat a green suite as "nothing I already asserted broke" rather than "the change is right".
