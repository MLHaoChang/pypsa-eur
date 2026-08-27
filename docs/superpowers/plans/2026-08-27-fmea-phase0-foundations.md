# FMEA Phase 0 — Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the data-model and metric foundations for the solution-FMEA / reliability-target feature, per the design at `docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md` (v4, §11 Phase 0). Five tasks: centralise the slack-carrier test, add outage-rate attributes with a basis label and validator, pin the canonical lost-load number, add the shed-hours metric, and stub the `AdequacyReport` contract. **Nothing in this phase is user-visible**; every deliverable is backend surface + tests that Phases 1–2 build on.

**Architecture:** All new backend code lives in a new `pypsa-gui/backend/services/adequacy/` package (verified free of collisions; do NOT touch `services/failure_taxonomy.py`, which classifies *solver* failures, not asset failures). Frontend work in this phase is limited to the outage-rate property fields (Task 2). Read spec §§4.4, 5.1, 5.4, 6.3 before starting — the design decisions are there, not re-argued here.

**Tech Stack:** Python 3.13 / FastAPI / PyPSA 1.x / pandas backend; React + TypeScript frontend; pixi for environments.

## Global Constraints

- **Branch:** all work on `claude/solution-fmea-integration-0mx5lc`. Re-run `git branch --show-current` before every commit.
- **Stage files explicitly by path.** NEVER `git add -A`, `git add .`, or `git commit -a`. NEVER `git checkout`, `git stash` (of others' files), `git reset`, or `git clean`.
- **Backend tests run in the `test` pixi env only:** `pixi run gui-tests` from the repo root, or `.pixi/envs/test/bin/python -m pytest` from `pypsa-gui/backend`. A bare `pytest` resolves the wrong env.
- **Do not pass `-q` to pytest** (`pytest.ini` already sets it; doubling suppresses the summary).
- **Node comes from pixi:** `export PATH="<repo>/.pixi/envs/default/bin:$PATH"`.
- **Test-first:** every behavioural task writes its failing test before the change. A regression test that passes on unfixed code is worthless — run it against the pre-change tree once to see it fail.
- **Two known-environmental failures:** `test_compare_invariants.py::test_lost_load_cost_is_energy_times_voll` and `::test_per_bus_and_per_carrier_lost_load_agree_with_the_total` fail in containers whose pandas version trips the restricted unpickler's allowlist. If they fail identically before your change, they are not yours. (Task 0 may fix this as a side effect — see below.)

### Task 0 (gate): dependency on the bugfix branch

Phase 0's custom attributes depend on the `_merge_partial_update` fix (partial PUTs silently reset custom columns) from `claude/fix-lost-load-cost-and-custom-attr-drop` (commit `8e2f98d`).

- [x] Check whether the fix is present: `grep -n "known_defaults" pypsa-gui/backend/routers/network.py` — a hit inside `_merge_partial_update` means it landed.
- [ ] If absent: preferred path is that the bugfix PR merges to master first (PR opened: https://github.com/MLHaoChang/pypsa-eur/pull/4 — absent on this branch until it lands), then `git merge origin/master` into this branch. If it has not merged and Phase 0 must proceed, `git merge origin/claude/fix-lost-load-cost-and-custom-attr-drop` into this branch (a merge, not cherry-pick — it no-ops once master carries it).
- [ ] Do NOT reimplement the fix inline; Task 2's partial-PUT test will simply fail until the merge is done, which is the intended forcing function.

---

### Task 1: `SLACK_CARRIERS` — centralise the slack test before anything else touches it

**Why first:** spec §4.4 — the Phase 1 `demand_response` tier is only safe if every site that special-cases the VOLL slack tests *membership in a set* rather than equality with one string. Do the mechanical refactor while there is still exactly one slack carrier, so behaviour-preservation is trivially checkable.

**Files:**
- Create: `pypsa-gui/backend/services/adequacy/__init__.py` (empty), `pypsa-gui/backend/services/adequacy/slack.py`
- Modify: `pypsa-gui/backend/services/ac_pf_service.py`, `pypsa-gui/backend/services/solver_service.py`, `pypsa-gui/backend/routers/results.py`
- Test: `pypsa-gui/backend/tests/test_adequacy_slack.py` (new)

**The module.** `slack.py` exports:

```python
SLACK_CARRIERS: frozenset[str] = frozenset({"load_shedding", "voll_slack"})
SLACK_NAME_PREFIXES: tuple[str, ...] = ("__voll_", "voll_slack_")
VOLL_SLACK_PREFIX = "__voll_"          # the prefix the CURRENT builds create with

def slack_generator_mask(generators: pd.DataFrame) -> pd.Series: ...
def is_slack_carrier(carrier: object) -> bool: ...
```

`slack_generator_mask` is lifted from the existing defence-in-depth mask at `services/ac_pf_service.py:142-158` (carrier OR name-prefix, legacy `voll_slack`/`voll_slack_` spellings included — keep that docstring's rationale). It is the reference implementation; move it, don't fork it.

**Code sites to convert** (equality/prefix tests → the shared constants/helpers):

| Site | Today | Change |
|---|---|---|
| `services/ac_pf_service.py:142-158` | inline mask | delegate to `slack_generator_mask` |
| `services/solver_service.py:4432,4445` | hard-coded `f"__voll_{bus}"` / `carrier="load_shedding"` at creation | build name from `VOLL_SLACK_PREFIX`; carrier stays the literal `"load_shedding"` (creation defines the convention; add a comment pointing at `slack.py`) |
| `services/solver_service.py:4468,4470` | strip/capture by `"__voll_"` prefix | use `VOLL_SLACK_PREFIX` |
| `services/solver_service.py:2452-2453` | `if carrier == "load_shedding":` in the cost decomposition | `is_slack_carrier(carrier)` — note for Phase 1: when `demand_response` exists it must NOT land in `voll_shed_*`; the helper centralises where that split will happen |
| `routers/results.py:2648-2650, 2709, 2727-2747` | price-drivers slack detection | detect via `slack_generator_mask` / `is_slack_carrier`; the *diagnosis tag string* `"load_shedding"` in the payload is API surface — do NOT rename it |

**Prose-only sites** (update the comment/docstring to reference `services/adequacy/slack.py`; no code change): `services/pypsa_service.py:84,907`, `services/project_context.py:126`, `routers/network.py:80`, `services/asset_results/service.py:29`. Frontend hits (`api/simulation.ts:335,343`, `SolverSettings.tsx:1652,1675`, `LoadFlow.tsx:1647,1688`, `Prices.tsx:471,512`) are diagnosis-tag legend text and type unions, not carrier filtering — leave them.

**Steps:**

- [x] **Write the pinning test first** (`test_adequacy_slack.py`): build a generators DataFrame containing a `__voll_X` row, a legacy `voll_slack_Y` row, a `carrier="voll_slack"` row, and normal generators; assert `slack_generator_mask` selects exactly the three slack rows. Add a source-level guard: grep the backend `services/` + `routers/` tree (excluding `tests/` and `adequacy/slack.py`) for `== "load_shedding"` / `.startswith("__voll_")` and assert zero code hits — this is the test that keeps the centralisation from regressing. (Model it on the repo's other source-grepping pin tests; match on code lines, not comments.)
- [x] Create `slack.py`; move the ac_pf mask logic into it; convert the table's code sites.
- [x] Run the affected suites: `test_lost_load_and_custom_attrs.py` (if Task 0 merged), the compare suite, and any `price_drivers` tests. Zero behaviour change expected — identical pass/fail to the pre-change tree.
- [x] Commit: `refactor(gui): centralise the VOLL-slack test behind SLACK_CARRIERS`.

---

### Task 2: outage-rate attributes with a labelled basis

**Design:** spec §5.4. Three attributes on five components: `outage_rate_value: float | None`, `outage_rate_basis: "FOR" | "EFORd" | None`, `mttr_hours: float | None`. `None`/NaN means "unset → fall back to the per-carrier default library". **Never silently convert between FOR and EFORd.**

**Files:**
- Create: `pypsa-gui/backend/services/adequacy/occurrence.py`
- Modify: `pypsa-gui/backend/models/schemas.py` (the five create schemas: `GeneratorCreate:56`, `StorageUnitCreate:100`, `StoreCreate:134`, `LinkCreate:184`, `LineCreate:213` — line numbers approximate, locate by class name), `pypsa-gui/backend/services/validation_service.py`
- Frontend: `frontend/src/api/types.ts`, `frontend/src/layout/PropertiesPanel.tsx`, `frontend/src/utils/propertyDocs.ts`
- Test: `pypsa-gui/backend/tests/test_adequacy_occurrence.py` (new)

**Precedent:** `curtailment_cost` (`models/schemas.py:161-163`) is the exact pattern — a custom non-PyPSA column read straight off the DataFrame. PyPSA 1.x accepts custom columns and round-trips them through netCDF for free. The traps: (a) Pydantic `extra="ignore"` silently drops undeclared fields, so all five schemas must declare all three fields; (b) custom columns get no `fillna(default)` on import, so **NaN must mean unset** everywhere they are read; (c) partial PUTs preserve them only with the Task 0 fix in place.

**`occurrence.py`:**

- `CARRIER_DEFAULTS: dict[str, OutageParams]` — per-carrier `(rate, basis, mttr_hours)` seeded from published class averages (NERC GADS / RTS-GMLC for thermal; ENTSO-E ERAA assumptions for others). Every entry carries a `source` string. Missing carrier → no default → asset excluded from occurrence-based analysis with a warning, never a guessed number.
- `resolve_outage_params(n, component) -> pd.DataFrame` — effective per-asset params: asset value where set, else carrier default; column `basis_source ∈ {"asset", "carrier_default", "missing"}`.
- `validate_outage_params(df) -> list[Warning]` — the consistency validator: implied `MTTF = MTTR·(1−FOR)/FOR`; warn when implied events/yr is implausible (e.g. > 20/yr for a thermal unit — the spec's example: FOR 0.10 + MTTR 24 h ⇒ 36.5 outages/yr), when MTTR ≤ 0, or when rate ∉ [0, 1).

**Steps:**

- [x] **Tests first:** (1) create a generator with all three fields via the API schema → present in `n.generators`; (2) netCDF export→import round-trip preserves them and NaN stays NaN; (3) partial PUT omitting them preserves them (requires Task 0); (4) `resolve_outage_params` fallback order asset → carrier default → missing; (5) validator flags the FOR 0.10/MTTR 24 h pair and passes FOR 0.05/MTTR 72 h.
- [x] Declare the three fields on the five create schemas, `curtailment_cost`-style (with the same "custom GUI column" comment pattern).
- [x] Implement `occurrence.py`; wire `validate_outage_params` into `validation_service.py` as **warnings** (never blocking — a solve without outage data is still a valid solve).
- [x] Frontend, per component (Generator, StorageUnit, Store, Link, Line — the 6 touch points each documented in spec §11): `types.ts` interface fields; `PropertiesPanel.tsx` save payload (~:163), `toFS` allowlist (~:209 — omission means the form never loads the value), read-only row (~:257), inputs (`NumInput` for value/MTTR, a select for basis) (~:399); `propertyDocs.ts` tooltips including the FOR-vs-EFORd caveat.
- [x] Run backend + frontend (`vitest`) suites; commit: `feat(gui): outage-rate attributes with labelled basis + defaults library`.

---

### Task 3: pin the canonical lost-load number

**The defect** (spec §6.3): two divergent computations. `solver_service.py:4477` computes `lost_load_cost_eur = total_mwh * voll` **unweighted** (in-code comment: "Assumes hourly snapshots"); `solver_service.py:2452-2458` computes the same quantity **snapshot-weighted** per period. Under tsam representative snapshots (`services/time_aggregation_service.py`) they disagree — and the Phase 1 ENS cap constrains the weighted integral, so an unweighted headline would not mean what the target means.

**Decision to implement:** the canonical totals are **snapshot-weighted** (they are what matches the objective and the physical MWh). The capture keeps `lost_load_t` as *unweighted per-snapshot MW* (it is a power series; consumers weight it), but `lost_load_total_mwh` and `lost_load_cost_eur` become weighted.

**Files:**
- Modify: `pypsa-gui/backend/services/solver_service.py` (the `_capture_and_remove_slacks` closure, ~:4458-4501)
- Audit (read, adjust only if they re-derive totals): `routers/results.py` `/results/lost_load` (~:2923), `routers/compare.py` `_compute_lost_load_summary` and the economics lost-load block (both already weight `lost_load_t` themselves — verify they use the capture *totals* only for the `voll = cost/mwh` ratio, which is invariant to consistent weighting)
- Test: extend `pypsa-gui/backend/tests/test_lost_load_and_custom_attrs.py`

**Steps:**

- [x] **Failing test first:** build a capture path with non-unit `snapshot_weightings` (e.g. weights of 3.0) and assert `lost_load_total_mwh` equals the weighted integral (it currently returns the unweighted sum → fails).
- [x] Weight the totals in the capture closure; update its docstring and the capture-format comment in `routers/compare.py` (~:2340).
- [x] Audit the two consumers; add one cross-surface assertion: capture `lost_load_cost_eur` == the economics summary's lost-load total (same network, non-unit weights).
- [x] Commit: `fix(gui): make the lost-load capture totals snapshot-weighted`.

---

### Task 4: the shed-hours metric

**Why:** spec §5.1 — with a binding energy cap, achieved ENS ≈ the cap by construction; shed-hours is the reported number that still carries information. **No shed-hours/LOLE metric exists anywhere in the backend today** (verified by grep).

**Files:**
- Create: `pypsa-gui/backend/services/adequacy/metrics.py`
- Modify: `pypsa-gui/backend/routers/results.py` (`/results/lost_load` payload), `pypsa-gui/backend/routers/compare.py` (`_compute_lost_load_summary`), `pypsa-gui/backend/models/schemas.py` (`LostLoadComparison`)
- Test: `pypsa-gui/backend/tests/test_adequacy_metrics.py` (new)

**Definition** (write it in the docstring, it is the contract): `shed_hours(ll_df, weights, threshold_mw=1e-3)` = the sum of snapshot weightings over snapshots where **total electrical shed power** exceeds the threshold. Electrical = columns whose bus carrier is electrical (reuse the bus-carrier lookup pattern from `compare.py`'s lost-load block). Per-period split mirrors the existing `_per_period_groupby` treatment. The threshold exists because LP solutions carry ~1e-9 numerical dust; it must be an argument, not a buried constant.

**Steps:**

- [x] **Tests first:** synthetic `ll_df` cases — zero frame → 0 h; one snapshot shedding with weight 3.0 → 3.0 h; sub-threshold dust → 0 h; multi-period index → correct per-period split.
- [x] Implement `metrics.py`; surface `shed_hours` (total + by_period) in the `/results/lost_load` payload and `LostLoadComparison`. Additive schema change only — existing fields untouched, so existing consumers are unaffected.
- [x] Commit: `feat(gui): shed-hours metric on the lost-load surfaces`.

---

### Task 5: the `AdequacyReport` contract stub

**Why now:** spec §10 — the contract is what keeps Phases 1–5 (and the optional PRAS/Antares engines) plugging into one shape. Stubbing it in Phase 0 with serialization tests means later phases fill fields rather than negotiate shape.

**Files:**
- Create: `pypsa-gui/backend/models/adequacy.py`
- Test: `pypsa-gui/backend/tests/test_adequacy_contract.py` (new)

**Steps:**

- [ ] Transcribe spec §10 into Pydantic models: `AdequacyReport`, `TargetBlock` (with `binding: Literal["system_cap","zone_cap","voll"]` and `zone_field_populated: bool`), `MetricsBlock` (with `time_basis`), `CostBlock` (with `excludes_shed_cost: Literal[True]` — a literal, so a consumer can never receive a report where it is false), `InputsBlock` (with `outage_rate_bases`), `EnergyBlock` (`involuntary_mwh` / `demand_response_mwh`), `FailureModeResult`, `TradeoffPoint`. Every field gets a docstring; the spec's rationale comments come along.
- [ ] Tests: round-trip `model_dump_json` → `model_validate_json`; assert `excludes_shed_cost` rejects `False`; assert an empty-but-valid minimal report constructs (what Phase 1 will emit before Phase 2 adds `per_mode`).
- [ ] No endpoint, no wiring — models + tests only.
- [ ] Commit: `feat(gui): AdequacyReport contract models`.

---

## Done criteria for the phase

- `pixi run gui-tests` green (modulo the two documented environmental failures, which must be identical to the pre-phase baseline).
- The source-grep guard from Task 1 passes: no stray `== "load_shedding"` / `"__voll_"` code sites outside `services/adequacy/slack.py` and the creation site.
- A generator created with outage attributes survives: netCDF round-trip, partial PUT, and `resolve_outage_params` resolution.
- `/results/lost_load` reports weighted totals + shed-hours on a non-unit-weighted network.
- `models/adequacy.py` importable and serializable.
- Nothing user-visible changed except the three new property-panel fields.
