# gridspine Increment 2 Implementation Plan — Snapshot Selection over 8760 h

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rank a full synthetic year of UC dispatch on a renewables-augmented 39-bus system and select the top-k stress snapshots (min inertia, max IBR share, peak load, max import), each exportable to a load-consistent .raw — retiring increment 1's hour-19-only guard.

**Architecture:** Extends `gridspine/` in place. New: `templates/` (H parameters as YAML — the assumptions-ledger mechanism starts here), synthetic year profiles, a RES-augmented fixture (`case39_res`, pandapower `sgen`), rolling-horizon UC, a per-snapshot loads artifact (the missing half of the stage-1→2 contract), `ranking/`. Stage boundaries stay validated artifacts; engine cage unchanged (+ `templates/` and `ranking/` import NO engine).

**Tech Stack:** as increment 1 (pypsa 1.1.2, pandapower 3.1.2 via pypi, highspy, pandas; + pyyaml already in env).

**Spec:** `docs/superpowers/specs/2026-08-27-gridspine-design.md`
**Prior art:** increment 1 (commits 59e322cc..b2797dfb); its ledger rulings are binding: canonical-ID allowlist `[A-Za-z0-9_-]` fullmatch; branch-flow comparison lands THIS increment (task 8); never `pp.from_json/from_pickle` on client files.

## Global Constraints

- ALL commands via `pixi run …`; test gate `pixi run gridspine-tests`; TDD Evidence (RED+GREEN actual output) per task report; mutation check where a task names one.
- Execute in a dedicated worktree; path-limited commits with `git commit --dry-run -- <paths>` untracked check.
- Engine cage: `pypsa` only under `producers/`; `pandapower` only under `ingest/`, `static/`, `handoff/`; `schema/`, `ranking/`, `templates/`, `readback/` import neither.
- Canonical IDs: names cross boundaries; `[A-Za-z0-9_-]` fullmatch enforced by `_check_series` (RES names `W_BUS_xx`/`S_BUS_xx` comply).
- **Runtime discipline: no test solves more than 336 h.** Every year-scale function takes an `hours` parameter; tests exercise 168–336 h; the full 8760 h runs only via the CLI. A test that takes >120 s is a defect.
- Determinism: profiles are closed-form (no RNG). Same inputs → identical artifacts.
- All synthetic data (profiles, RES siting/capacity, H values) is ledgered: every constant carries a source tag `measured|datasheet|assumed` or a LEDGER entry.
- Model routing: **[Opus]** implement / **[Opus, Fable review]** master reviews line-by-line / **[FABLE]** Fable implements.

---

### Task 1: Dynamic parameter templates — H values as YAML **[Opus, Fable review]**

**Files:**
- Create: `gridspine/templates/__init__.py` (empty), `gridspine/templates/unit_params.py`, `gridspine/templates/data/case39_units.yaml`
- Test: `tests/gridspine/test_unit_params.py`

**Interfaces:**
- Produces: `load_unit_params(path=None) -> pd.DataFrame` indexed by `unit_id`, columns `h_s` (float, inertia constant, s), `mbase_mva` (float), `source` (str ∈ {measured, datasheet, assumed}), `include_in_inertia` (bool). Default path = the packaged case39 YAML. Raises `ContractError` on: unknown source tag, missing column, non-positive h_s/mbase for rows with `include_in_inertia: true`, duplicate unit ids.
- Inertia convention consumed by Task 7: online contribution of a unit = `h_s * mbase_mva` (MW·s); `include_in_inertia: false` excludes it (the slack models an import, not a machine).

- [ ] **Step 1: Write the YAML** — `gridspine/templates/data/case39_units.yaml`:

```yaml
# IEEE 39-bus (New England) classic dynamic data, H on 100 MVA system base
# (Athay et al. 1979 / standard case39 dynamic set). Tag: datasheet.
# SLK_BUS_31 models grid import: excluded from pocket inertia (assumed).
units:
  G_BUS_30:   {h_s: 42.0,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_32:   {h_s: 35.8,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_33:   {h_s: 38.6,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_34:   {h_s: 26.0,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_35:   {h_s: 34.8,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_36:   {h_s: 26.4,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_37:   {h_s: 24.3,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_38:   {h_s: 34.5,  mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  G_BUS_39:   {h_s: 500.0, mbase_mva: 100.0, source: datasheet, include_in_inertia: true}
  SLK_BUS_31: {h_s: 30.3,  mbase_mva: 100.0, source: datasheet, include_in_inertia: false}
```
NOTE: probe `load_case39()` gen names first (Step 2 below) — if the pandapower gen set differs from these ten (e.g. no G_BUS_39, or a gen at 31), adjust the YAML keys to the actual canonical names and record the mapping in the report. The H VALUES stay; only key names move.

- [ ] **Step 2: Probe actual unit ids**

Run: `pixi run python -c "from gridspine.ingest.pandapower_source import load_case39, registry_from_net; print(list(registry_from_net(load_case39()).index))"`
Adjust YAML keys to match exactly; report the list.

- [ ] **Step 3: Write the failing tests** — `tests/gridspine/test_unit_params.py`:

```python
import pytest

from gridspine.schema.contracts import ContractError
from gridspine.templates.unit_params import load_unit_params


def test_default_load_covers_case39_units():
    from gridspine.ingest.pandapower_source import load_case39, registry_from_net
    params = load_unit_params()
    reg = registry_from_net(load_case39())
    assert set(reg.index) == set(params.index)


def test_columns_and_source_tags():
    params = load_unit_params()
    assert {"h_s", "mbase_mva", "source", "include_in_inertia"} <= set(params.columns)
    assert params["source"].isin(["measured", "datasheet", "assumed"]).all()
    assert (~params.loc[params.index.str.startswith("SLK_"), "include_in_inertia"]).all()


def test_bad_source_tag_rejected(tmp_path):
    bad = tmp_path / "u.yaml"
    bad.write_text("units:\n  G_X: {h_s: 5.0, mbase_mva: 100.0, source: guessed, include_in_inertia: true}\n")
    with pytest.raises(ContractError, match="source"):
        load_unit_params(bad)


def test_nonpositive_h_rejected(tmp_path):
    bad = tmp_path / "u.yaml"
    bad.write_text("units:\n  G_X: {h_s: 0.0, mbase_mva: 100.0, source: assumed, include_in_inertia: true}\n")
    with pytest.raises(ContractError, match="h_s"):
        load_unit_params(bad)
```

- [ ] **Step 4: RED** — `pixi run gridspine-tests` → ModuleNotFoundError
- [ ] **Step 5: Implement** — `gridspine/templates/unit_params.py`:

```python
"""Dynamic-parameter templates as data. Every value is tagged with its
provenance — this file format IS the assumptions ledger's unit section."""
from pathlib import Path

import pandas as pd
import yaml

from gridspine.schema.contracts import ContractError

_DEFAULT = Path(__file__).parent / "data" / "case39_units.yaml"
_SOURCES = frozenset({"measured", "datasheet", "assumed"})
_REQUIRED = ("h_s", "mbase_mva", "source", "include_in_inertia")


def load_unit_params(path=None) -> pd.DataFrame:
    raw = yaml.safe_load(Path(path or _DEFAULT).read_text())
    units = raw.get("units")
    if not isinstance(units, dict) or not units:
        raise ContractError("unit params YAML has no 'units' mapping")
    df = pd.DataFrame.from_dict(units, orient="index")
    df.index.name = "unit_id"
    missing = [c for c in _REQUIRED if c not in df.columns or df[c].isna().any()]
    if missing:
        raise ContractError(f"unit params missing/null columns: {missing}")
    bad_src = df.loc[~df["source"].isin(_SOURCES), "source"]
    if len(bad_src):
        raise ContractError(f"unknown source tags {sorted(set(bad_src))}; allowed {sorted(_SOURCES)}")
    counted = df[df["include_in_inertia"].astype(bool)]
    if ((counted["h_s"] <= 0) | (counted["mbase_mva"] <= 0)).any():
        raise ContractError("h_s and mbase_mva must be positive for inertia-counted units")
    if df.index.duplicated().any():
        raise ContractError(f"duplicate unit ids: {sorted(df.index[df.index.duplicated()])}")
    return df
```
(`yaml.safe_load` only — never full `yaml.load`.)

- [ ] **Step 6: GREEN** — full gate. **Step 7: Commit** (path-limited, dry-run check — the YAML is a NEW file in a NEW directory, verify it's listed).

---

### Task 2: Synthetic year profiles **[Opus]**

**Files:**
- Create: `gridspine/ingest/synthetic_profiles.py`
- Test: `tests/gridspine/test_synthetic_profiles.py`

**Interfaces:**
- Produces (all return `pd.Series` of length `hours`, values in [0, 1.05], deterministic closed-form, no RNG):
  - `year_load_shape(hours=8760)` — inc-1 `LOAD_SHAPE` daily pattern (import it from producers? NO — engine cage: copy the 24 values into this module as `DAILY_SHAPE` with a comment naming the duplication deliberate; `producers/` keeps its own) × weekly factor (Sat/Sun ×0.85) × seasonal factor `1 + 0.12*cos(2π(day-15)/365)`, normalised so max == 1.0.
  - `wind_cf(hours=8760)` — capacity factor: `0.35 + 0.25*sin(2πh/72 + 1.3) + 0.15*cos(2πday/365)`, clipped to [0.02, 0.95].
  - `solar_cf(hours=8760)` — zero outside hour-of-day 6..18; inside: `sin(π(hod-6)/12)^1.5 × (0.75 + 0.25*cos(2π(day-172)/365))`, clipped ≥0.
- LEDGER strings for the driver (module constant `PROFILE_LEDGER: list[str]`) naming all three as synthetic/assumed.

- [ ] **Step 1: failing tests** — length/hours param respected (168 and 8760); ranges (load ∈ (0,1], max==1.0 exactly; wind ∈ [0.02,0.95]; solar==0 at hod 3, >0 at hod 12); determinism (two calls identical); weekend factor visible (mean of Saturday hours < mean of Wednesday hours for same week); solar seasonal (day-172 noon > day-350 noon).
- [ ] **Step 2: RED. Step 3: implement per formulas above (numpy+pandas only). Step 4: GREEN. Step 5: commit.**

---

### Task 3: RES-augmented fixture `case39_res` **[Opus]**

**Files:**
- Modify: `gridspine/ingest/pandapower_source.py` (add `load_case39_res`, extend `registry_from_net`)
- Test: `tests/gridspine/test_case39_res.py`

**Interfaces:**
- Produces: `load_case39_res() -> net` = `load_case39()` plus pandapower **sgen** rows (PQ injections): wind `W_BUS_33` 600 MW, `W_BUS_35` 600 MW, `W_BUS_37` 600 MW; solar `S_BUS_34` 500 MW, `S_BUS_36` 500 MW (column `p_mw` = installed MW, `q_mvar=0`, `in_service=True`, canonical `name`). Siting/size are LEDGER assumptions (module constant `RES_LEDGER`). Names pass the charset allowlist. `validate_canonical` runs over buses + gens + ext_grid + sgen names.
- `registry_from_net(net)` gains sgen rows with `kind='res'` (existing gen/ext_grid rows unchanged — increment-1 callers see identical output for vanilla case39; assert that).

- [ ] **Step 1: failing tests** — 5 sgens with expected names/buses/MW; registry has 15 rows for case39_res (10 + 5) and `kind` counts {gen:9, ext_grid:1, res:5}; vanilla `load_case39()` registry UNCHANGED (10 rows, no res) — regression guard; `pp.runpp(load_case39_res())` converges (sgens at installed MW may over-inject: set sgen `p_mw` scaled to 30% for the LF smoke, or assert convergence with sgens `in_service=False`… simplest honest form: LF with all sgen at 0.3× installed converges; comment why).
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: drop the sgen loop → registry-count test fails. Step 5: commit.**

---

### Task 4: Producer v2 — profiles in, rolling-horizon UC **[Opus, Fable review]**

**Files:**
- Modify: `gridspine/producers/pypsa_nodal.py`
- Test: `tests/gridspine/test_producer_year.py`

**Interfaces:**
- `to_pypsa(net, snapshots=24, load_shape=None, res_cf=None)` — backward compatible: `load_shape=None` → inc-1 behavior (LOAD_SHAPE, 24 h) so ALL existing tests pass untouched. With `load_shape` (Series len==snapshots): loads use it. `res_cf`: dict `{unit_id: Series}` for sgen-derived RES units — added as pypsa Generators, `committable=False`, `marginal_cost=0.5`, `p_nom=installed`, `p_max_pu=cf series` (curtailable). RES units read from `net.sgen`.
- `run_uc_rolling(n, window=168, overlap=24) -> n` — solves committable UC over `n.snapshots` in windows: solve `[t0, t0+window)`, fix/carry unit status at the seam by re-solving with `min_up/down` continuity honoured via warm overlap (statuses inside the final `overlap` hours of each window are RE-SOLVED in the next window; only pre-overlap hours are frozen). Freezing = write the solved `status` into `n.generators_t.status` progressively; final `to_dispatch_table` reads the assembled result. Non-optimal window → RuntimeError naming the window. `window % 24 == 0` and `overlap < window` enforced (ContractError).
- Existing `run_uc` stays (small horizons, tests).

- [ ] **Step 1: failing tests** (168 h max solves, one 336 h rolling):
  - `to_pypsa(load_case39(), 24)` unchanged output vs inc-1 expectations (bus set, 10 gens) — compat guard.
  - `to_pypsa(load_case39_res(), 168, load_shape=year_load_shape(168), res_cf={...from wind_cf/solar_cf...})` → 15 generators, RES non-committable with p_max_pu set.
  - `run_uc_rolling(n_336h, window=168, overlap=24)` → dispatch table validates; energy balance each hour <1%; at least one committable unit OFF in some hour (RES-rich valley) AND at least one hour with ALL committable on (peak) — the year has real commitment texture.
  - Seam correctness: unit status at hours `window-overlap-1` and `window-overlap` obey min_up/min_down across the boundary (assert no 1-hour up/down violation across all units/hours: vectorised check of run lengths ≥2 except at series ends).
  - `run_uc_rolling` bad args (window=100, overlap≥window) → ContractError.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN (runtime: 336 h rolling on case39_res must finish <90 s; if slower, reduce test to 168+overlap and note). Mutation: break seam carry (freeze nothing) → min-up/down test fails. Step 5: commit.**

---

### Task 5: Loads artifact + hour-consistent LF — retire the hour-19 guard **[Opus, Fable review]**

**Files:**
- Modify: `gridspine/schema/dispatch.py` (add loads-table contract), `gridspine/static/loadflow.py` (apply loads + RES), `gridspine/drivers/planning.py` (guard retirement), `gridspine/producers/pypsa_nodal.py` (emit loads table)
- Test: `tests/gridspine/test_loads_artifact.py` (+ edits to `test_vertical_slice.py` guard test)

**Interfaces:**
- `validate_loads(df) -> df` in `schema/dispatch.py`: columns `bus` (str), `hour` (int), `p_mw` (float ≥0, finite), `q_mvar` (float, finite); unique (bus, hour); same pre-coercion discipline as `validate_dispatch`.
- `to_loads_table(n) -> DataFrame` in producers: per-bus per-hour set loads from `n.loads_t.p_set` (bus = load's bus name; q_mvar scaled from the pandapower net's native Q/P ratio per bus — constant power factor, LEDGER note).
- `apply_snapshot(net, dispatch, loads, hour, registry) -> None` in `static/loadflow.py`: NEW function — sets `net.load` p/q by bus for the hour (validated loads table), sets committable gens via existing `apply_dispatch` logic, sets `net.sgen` p_mw for `kind=='res'` rows from the dispatch table. `apply_dispatch` stays for compat but gains a deprecation comment pointing here.
- Driver: `run_39bus_slice` keeps signature; the `hour != 19` ContractError is REPLACED by the loads-artifact path — any hour is now valid because loads track the hour. Manifest `load_consistency` becomes `"per-snapshot loads artifact (increment 2)"`. The guard test flips: `hour=8` now RUNS and converges, and a new assertion checks `sum(net.load.p_mw)` after `apply_snapshot(hour=8)` ≈ `year/daily shape factor × base` (loads genuinely moved).

- [ ] **Step 1: failing tests** — validator rejections (dup bus-hour, negative p, inf q); `to_loads_table` hour-0 sums match `loads_t.p_set.iloc[0]`; `apply_snapshot(hour=8)` on case39: `net.load.p_mw` sum ≈ shape(8)×base within 0.1%, LF converges, slack |p| < 5% of load (the inc-1 silent-import defect, now asserted dead); driver `hour=8` end-to-end converges with hour-8 loads in the raw (adapt the inc-1 stage-order test's technique).
- [ ] **Step 2: RED (hour=8 currently raises ContractError — that IS the red). Step 3: implement. Step 4: GREEN, full gate. Mutation: skip load-setting in apply_snapshot → slack-bound test fails. Step 5: commit.**

---

### Task 6: RAW writer — sgen machines **[FABLE]**

**Files:**
- Modify: `gridspine/handoff/raw_writer.py`
- Test: extend `tests/gridspine/test_raw_writer.py`

**Interfaces:**
- `write_raw` emits `net.sgen` rows as GENERATOR records (same 20-field record; MBASE = installed `p_mw` (assumed = MVA, LEDGER note), PG = current `p_mw`, STAT from `in_service`, WPF/wind fields left default — v33 wind-machine WMOD/WPF fields are optional; document the choice). Bus IDE: a bus whose ONLY machine is an sgen becomes IDE=2. `_IdCounter` covers sgen+gen sharing a bus (distinct IDs).
- Latitude as increment 1: v33 correctness beats this brief; tests are the contract; document deviations.

- [ ] **Step 1: failing tests** — toy net + one sgen: generator-section record count includes it, PG matches, distinct machine ID when co-located with a gen, IDE=2 for sgen-only bus; case39_res: 15 machine records; offline sgen STAT=0.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + regression (all inc-1 raw tests untouched and green). Step 5: commit.**

---

### Task 7: Ranking — metrics and selection **[Opus, Fable review]**

**Files:**
- Create: `gridspine/ranking/__init__.py` (empty), `gridspine/ranking/metrics.py`, `gridspine/ranking/select.py`
- Test: `tests/gridspine/test_ranking.py`

**Interfaces:**
- `snapshot_metrics(dispatch, loads, unit_params, registry) -> DataFrame` indexed by hour, columns: `load_mw` (Σ loads), `import_mw` (Σ p of `kind=='ext_grid'` units), `inertia_mws` (Σ `h_s*mbase_mva` over units with status==1 AND `include_in_inertia` — RES units: not in unit_params → contribute 0, join with how='left', fillna h=0, LEDGER), `ibr_share` (Σ res p / Σ all p, ∈[0,1], 0/0→0). Pure pandas — NO engine imports (`ranking/` is cage-free like `schema/`).
- `select_snapshots(metrics, k=5) -> DataFrame` — union of: k lowest `inertia_mws`, k highest `ibr_share`, k highest `load_mw`, k highest `import_mw`; columns `hour`, `reasons` (list[str] — a hour selected by several criteria lists all), sorted by hour; `validate_selection` companion (non-empty, hours ⊆ metrics index, reasons non-empty) in the same module raising ContractError.
- These are pipeline-IP modules: docstrings state the metric definitions precisely (the report appendix quotes them).

- [ ] **Step 1: failing tests** — hand-built 4-hour dispatch/loads/params fixture where every metric is hand-computable: assert exact values (inertia drops when a unit's status→0; ibr_share 0 when no res rows; import equals slack p). Selection: k=1 on the fixture picks the known extreme hours; a hour extreme in two metrics appears once with 2 reasons; k > len(metrics) degrades gracefully (all hours).
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: flip inertia to count status==0 → exact-value test fails. Step 5: commit.**

---

### Task 8: Branch-flow comparison (increment-1 debt) **[Opus]**

**Files:**
- Modify: `gridspine/readback/pf_compare.py`, `gridspine/static/loadflow.py` (LFResult gains `branch_flow`), runbook README
- Test: extend `tests/gridspine/test_pf_compare.py`, `tests/gridspine/test_loadflow.py`

**Interfaces:**
- `LFResult` gains `branch_flow: pd.DataFrame` (index `(from_bus, to_bus, ckt)` as columns or MultiIndex — pick columns `from_bus,to_bus,ckt,p_from_mw,q_from_mvar,loading_percent`, default empty) filled by `run_lf` from `res_line`/`res_trafo` (+ CKT ids matching the raw writer's `_IdCounter` convention — read the writer's keying and reproduce it; cross-module consistency test REQUIRED: write a raw, parse its branch records' (I,J,CKT), assert the LF branch_flow keys map 1:1 through the bus-number dict).
- `compare_branch_flows(lf, pf_csv, p_tol=0.01, q_tol_mvar=5.0) -> DataFrame` — per-branch, keyed on (from_bus,to_bus,ckt); relative P within 1% (of |p| or 1 MW floor to avoid div-by-~0), Q absolute within tol; `ok` column; ContractError on key-set mismatch / missing columns / non-converged (reuse the converged guard pattern). Runbook: `case39_h<hour>_branches.csv` section updated from "captured, compared in increment 2" to the live contract.

- [ ] **Step 1: failing tests** — synthetic LFResult + CSV within/outside tolerance; key mismatch rejected; the writer↔LF ckt-consistency test; runbook wording updated (grep test optional — assert README no longer claims comparison is future).
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN. Step 5: commit.**

---

### Task 9: Driver v2 — year study CLI **[Opus, Fable review]**

**Files:**
- Create: `gridspine/drivers/year_study.py`
- Test: `tests/gridspine/test_year_study.py`

**Interfaces:**
- `run_year_study(outdir, hours=8760, k=5, window=168, overlap=24) -> StudyResult` — dataclass `StudyResult(selected: DataFrame, artifacts: dict, lf_results: dict[int, LFResult])`. Chain: `load_case39_res` → `to_pypsa(..., year_load_shape(hours), res_cf from wind/solar cf)` → `run_uc_rolling` → dispatch + loads artifacts (CSV) → `snapshot_metrics` (+ `metrics.csv`) → `select_snapshots` (+ `selected.csv` with reasons) → per selected hour: `apply_snapshot` → `run_lf` → `lf_<hour>_bus.csv` + `case39_h<hour>.raw` (f_hz=60). Non-convergent selected hour: recorded in `selected.csv` (`converged` column), maximal-severity note, run continues (spec: a result, not a crash). Manifest: stages, ledgers (PROFILE_LEDGER + RES_LEDGER + unit-params source counts + inc-1 LEDGER entries), `load_consistency: per-snapshot`.
- CLI: `pixi run python -m gridspine.drivers.year_study --out <dir> [--hours 8760] [--k 5]`.
- StageError artifacts on failure per stage (inc-1 pattern).

- [ ] **Step 1: failing tests** (hours=336, k=2 — runtime cap): artifacts exist; selected.csv non-empty with reasons; every selected hour's raw exists and carries that hour's load level (stage-order technique from inc 1: RAW load records vs loads.csv at the hour); metrics length == hours; manifest ledger includes profile + RES + unit-params entries.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN, full gate. Step 5: run CLI once at 336 h, keep artifacts for inspection; do NOT run 8760 in the task (report the command for Hao). Step 6: commit.**

---

## Parallelism map

- Tasks 1, 2 independent after start → one message, two dispatches.
- Task 3 needs nothing from 1–2 (fixture only) → may join the first wave (three lanes).
- Task 4 needs 2+3; Task 5 needs 4 (loads table emit) but its schema/static halves need only 2+3 — keep sequential 4→5 (same files touched: pypsa_nodal).
- Task 6 needs 3; Task 7 needs 1 (unit_params) + dispatch-table shape (inc 1); Task 8 needs nothing new (inc-1 LFResult) — 6, 7, 8 run parallel with 4→5 chain.
- Task 9 is the barrier: needs all.

## After increment 2

8760 h run by Hao (CLI, minutes-to-hours) → selected snapshots → PowerFactory validation of one selected min-inertia hour (same runbook flow, now any hour). Increment 3 per spec: N-1/N-2 screening + severity metric joins the ranking.
