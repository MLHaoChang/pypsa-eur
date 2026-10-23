<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: needs-triage — awaiting GK decisions

# Outsourced task H: Hydrogen stops counting as renewable in the results carrier KPIs

Branch: `outsource/carrier-kpis-hydrogen` · one PR against `master` · PR body starts with `Outsourced task H`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. `.scratch/timeseries-generators/issues/13-carrier-kpis-hydrogen.md`, the issue.
3. `.scratch/timeseries-generators/outsource/F-gen-category-word-match.md`, the sibling task. It fixed the same bug in the Time Series category (merged as PR #676).
4. `pypsa-gui/backend/services/profile_shapes.py`, lines 219-258: `_RENEWABLE_KW`, `_carrier_words` and `_gen_category`. Lines 247-254 are F's renewable branch, which blanks hydrogen words before the substring test. Your rule copies that branch.
5. `pypsa-gui/backend/services/results/carrier_kpis.py`, lines 148-191: `CURTAILMENT_SOURCES`, the local `_RENEWABLE_KW`, `_is_renewable_carrier` and the row loop that reads it.
6. `pypsa-gui/backend/tests/test_gen_category_words.py`, F's tests. Your tests follow their style.

## Goal in plain words

`compute_carrier_kpis` builds one row per (component, carrier) for the Load Flow results page. A Generator row gets a curtailment figure only when `_is_renewable_carrier(carrier)` is true. Thermal plant otherwise reports its unused headroom as "curtailment". The test is a substring test over `_RENEWABLE_KW`, so `hydro` is found inside "**hydro**gen". A hydrogen Generator is therefore treated as renewable: its unused capacity shows as curtailed energy. That figure is often above 50 %, and then the cell is marked as a warning.

The carrier string the test sees is the key from `n.statistics.*(groupby="carrier")`. In PyPSA 1.1.2 that key is the carrier's `nice_name` when one is set, and the raw carrier id otherwise. That matters in practice. A Generator a user creates with carrier `H2` gets `nice_name` "Hydrogen" from `services/carrier_catalog.py` (through `ensure_carrier`). Its row is keyed `Hydrogen`, and today it gets a curtailment figure.

I reproduced the bug without a solve. A 4-snapshot network has five 10 MW Generators, each dispatching 4 MW. Their carriers are `hydrogen`, `H2` (nice name `Hydrogen`), `solar`, `hydro` and `gas`. On master, the `hydrogen`, `Hydrogen`, `solar` and `hydro` rows each report 24.0 MWh, 60.0 % curtailed; `gas` reports 0.

`_RENEWABLE_KW` and `_is_renewable_carrier` have no other reader. Nothing in this module computes a renewable share. These read the curtailment figures they gate:

- `routers/results.py:663-689` (`get_carrier_kpis`, `GET /api/results/carrier_kpis`) returns the payload unchanged.
- `frontend/src/api/simulation.ts:1371-1385` (`getCarrierKpis`) → `frontend/src/pages/results/LoadFlow.tsx`. The "Carrier KPIs" table shows the Curtailment % column, with the warning colour above 50 % (line 637). The CSV export (`results_carrier_kpis.csv`, lines 595-605) writes `curtailment_MWh` and `curtailment_pct`.
- `services/chat_tools.py:602`: the chat assistant's results tool with `carrier_kpis` calls `routers.results.get_carrier_kpis`, so the assistant quotes the same numbers. `harness/catalogue.py:124` only lists the name.
- Tests that read the payload: `tests/test_results_seam.py::test_carrier_kpis` (router equals service), `tests/test_compare_cross_surface.py::test_dispatch_agrees_with_carrier_kpis` (energy only), `tests/test_snapshots_freq_weightings.py` (solar energy, `live_solve`), `tests/test_results_facade_surface.py`. The golden fixture's Generators are `gas`, `solar` and `diesel`, so none of these moves.

The similar rules elsewhere are not this task:

- `services/results/curtailment.py:120-129` (`RENEW_KEYWORDS`, Curtailment tab) has the same `hydro` substring. See G2.
- `services/results/load_shape.py:51` (`RENEWABLE`) matches exact labels, so it has no hydrogen bug.
- `services/compare/curtailment.py` selects by `p_max_pu` profile, not by keyword.

Fix: a carrier is renewable when a renewable keyword is a substring of the lowercased carrier, after every word that starts with `hydrogen` is blanked. This is exactly F's renewable branch in `_gen_category`, so the Load Flow table and the Time Series page agree on what counts as renewable. Everything else stays as it is.

### The rule (exact)

- Keywords: today's tuple, unchanged: `wind solar ror hydro geothermal wave tidal pv biomass biogas run-of-river`.
- Words are found as F finds them:
  - insert a boundary between a lowercase letter and the uppercase letter after it (`HydrogenImport` → `Hydrogen Import`);
  - a word is a maximal run of ASCII letters;
  - a word is a hydrogen word when its lowercase form starts with `hydrogen`.
- Blank each hydrogen word in place (replace it with a space), keep every other character, lowercase, then run today's substring test. So `hydrogen`, `Hydrogen`, `Hydrogen Storage`, `hydrogen CHP` and `HydrogenImport` are not renewable. `hydro`, `hydropower`, `HydroPlant`, `Run-of-River` and `solar_hydrogen` still are.
- `_is_renewable_carrier(c)` must equal `_gen_category(c) == 'renewable'` for every carrier that contains no demand-response word. (`_gen_category` tests demand-response words first; see G1.)
- Unchanged: `CURTAILMENT_SOURCES`, the Link rows (always get a figure), StorageUnit and Store rows (never get one), the percentage formula, the row keys, and the storage revenue overlay.

Reuse: `profile_shapes` exposes the word helper `_carrier_words`, but not the hydrogen mask on its own, and `profile_shapes.py` is out of scope. Do not build the masked string by joining `_carrier_words(...)`. That drops the hyphens, so `Run-of-River` (the catalogue nice name of `ror`) would lose its curtailment figure. Copy F's four-line mask expression (`profile_shapes.py:248-253`) into the new helper and cite it in a comment. Test 3 pins the two copies together.

### Carriers checked

I ran master's rule and this rule over every carrier we ship or write. Both raw ids and nice names are covered, because the statistics key can be either.

- Generator carriers built by every template in `project_templates/` (`_build.build_3bus`, `build_ieee14`, `eh_templates.BUILDERS`): `gas coal oil diesel grid solar wind`, `NH3 import`, `NH3 shortfall`. No template sets a `nice_name` on these.
- `services/carrier_catalog.py`: every id and every `nice_name` (it mirrors `frontend/src/utils/carrierCatalog.ts`).
- Palette defaults in `frontend/src/layout/paletteData.ts` that create Generators: `thermal` → `gas`, `renewable` → `wind`. The `hydro` (PSH) and `H2` items create StorageUnits, Stores or Links, which this test never sees.
- PyPSA-Eur electricity carriers and the `plotting.nice_names` in `config/plotting.default.yaml`, which `scripts/add_electricity.py:227-233` writes onto `n.carriers`: `onwind`, `offwind-ac/-dc/-float`, `solar`, `solar-hsat`, `ror`, `hydro`, `CCGT`, `OCGT`, `nuclear`, `lignite`, `biomass`, `geothermal`, `oil`, `coal`, `load`, `curtailment`, `PHS`, and their nice names (`Onshore Wind`, `Solar`, `Run of River`, `Reservoir & Dam`, `Load Shedding`, `Hydrogen Storage`, ...).
- Every Generator carrier in `pypsa-gui/backend/tests`, for example `gas`, `grid`, `H2`, `hydrogen`, `load_shedding`, `product_shortfall`, `solar thermal`, `solar PV`, `Solar`, `Wind`, `GAS`.

| Carrier (statistics key) | Master | New | Where it comes from |
|---|---|---|---|
| `Hydrogen` | renewable | not renewable | catalogue `nice_name` of `H2`; any `H2` Generator a user adds in the editor |
| `hydrogen` | renewable | not renewable | tests |
| `Hydrogen Storage` | renewable | not renewable | PyPSA-Eur nice name of `H2` (a Store there, so no Generator row in practice) |
| `hydrogen supply`, `H2 (hydrogen)`, `HydrogenImport` | renewable | not renewable | user-coined |
| `hydrogen CHP` | renewable | not renewable | user-coined (thermal; now no headroom-as-curtailment) |
| `solar_hydrogen` | renewable | renewable | user-coined (its `solar` still counts) |
| `hydro`, `hydropower`, `HydroPlant`, `hydro-reservoir`, `Hydro Reservoir`, `Pumped Hydro`, `Pumped Hydro Storage` | renewable | renewable | catalogue, PyPSA-Eur, user-coined |
| `Run-of-River` | renewable | renewable | catalogue nice name of `ror` |
| `Run of River`, `Reservoir & Dam` | not renewable | not renewable | PyPSA-Eur nice names of `ror` and `hydro` (already missed; see G3) |
| `flex solar`, `solar_dr`, `Solar DR`, `dsm wind`, `wind_curtail` | renewable | renewable | user-coined (would move under G1 (b)) |

Every other carrier listed above keeps its value. Of the shipped carriers, only `Hydrogen` (catalogue nice name) and `Hydrogen Storage` (PyPSA-Eur nice name) move, and of the test carriers only `hydrogen`.

### Stored data

Nothing stored goes stale:

- `/api/results/carrier_kpis` computes on each request from the solved network.
- No study, scenario or compare snapshot stores carrier KPI rows. `curtailment_pct` appears nowhere else in the backend.
- CSVs the user already exported keep the old figure. That is expected.

## User-facing outcome

- In Load Flow's Carrier KPIs table, a Generator whose carrier (or carrier nice name) is a hydrogen word shows 0 curtailment MWh and 0.0 % curtailment. It no longer shows its unused capacity in the warning colour. The CSV export and the chat assistant's `carrier_kpis` numbers match.
- Hydro, run-of-river, solar, wind and every other renewable carrier keep their curtailment figure.
- **No new columns, labels, keys or copy.** The payload shape is unchanged.

## Files to touch

- `pypsa-gui/backend/services/results/carrier_kpis.py`:
  - Move `_RENEWABLE_KW` (lines 162-165) to module level, with the same tuple and the same name.
  - Move `_is_renewable_carrier` (lines 167-169) to module level as `_is_renewable_carrier(name: str) -> bool`, and give it the rule above (stdlib `re`, imported at module top). Its docstring names F's branch as the source of the mask.
  - Delete the two nested definitions. Line 179 keeps calling `_is_renewable_carrier(carrier)`.
  - Update the comment block at lines 156-160 with one sentence saying that hydrogen words do not count as renewable.
  - Change nothing else in the file.
- `pypsa-gui/backend/tests/test_carrier_kpis_hydrogen.py`: new file, tests below.
- `doc/release-notes.d/outsource-carrier-kpis-hydrogen.md`: the release-note fragment.

## Tests (new file `tests/test_carrier_kpis_hydrogen.py`; each fails on master)

On master `_is_renewable_carrier` is nested and so cannot be imported, and tests 1-3 fail on that alone. Each also asserts a value that master's rule gets wrong, so it would fail even if the helper were importable.

1. `test_hydrogen_carriers_are_not_renewable`: parametrised over `hydrogen`, `Hydrogen`, `Hydrogen Storage`, `hydrogen supply`, `H2 (hydrogen)`, `HydrogenImport` and `hydrogen CHP`. Each gives `False`; master's substring rule gives `True` for every one.
2. `test_carrier_table_keeps_its_renewable_answer`: one table holding every carrier in "Carriers checked", with its expected value from the "New" column. Carriers not in the change table keep master's value. It includes at least:
   - `onwind`, `offwind-float`, `solar-hsat`, `Run-of-River`, `Hydro Reservoir`, `hydro`, `hydropower`, `solar_hydrogen` → `True`;
   - `gas`, `CCGT`, `H2`, `H2 Fuel Cell`, `load`, `curtailment`, `Run of River`, `Reservoir & Dam` → `False`.

   It fails on master because of the `Hydrogen`/`hydrogen` rows. It guards against collateral moves.
3. `test_renewable_answer_matches_time_series_category`: for every carrier in test 2's table that has no demand-response word, `_is_renewable_carrier(c) == (_gen_category(c) == 'renewable')`. `_gen_category` comes from `services.profile_shapes`. This pins the copied mask to F's. On master it fails for `hydrogen` and `Hydrogen`.
4. `test_hydrogen_generator_row_has_no_curtailment` (service seam, no solve). Build the network:
   - `n.set_snapshots(pd.date_range("2025-01-01", periods=4, freq="h"))` and one Bus;
   - `n.add("Carrier", "H2", nice_name="Hydrogen")`;
   - five 10 MW Generators with carriers `hydrogen`, `H2`, `solar`, `hydro` and `gas`;
   - `n.generators["p_nom_opt"] = 10.0`;
   - `n.generators_t.p` = 4.0 everywhere;
   - one Load.

   Call `compute_carrier_kpis(n, result_df=plain)`, with `plain` as in `tests/test_snapshots_freq_weightings.py:120`. Assert:
   - the `hydrogen` and `Hydrogen` Generator rows have `curtailment_mwh == 0.0` and `curtailment_pct == 0.0`, and still `energy_mwh == 16.0`;
   - the `solar` and `hydro` rows have `curtailment_mwh == pytest.approx(24.0)` and `curtailment_pct == pytest.approx(60.0)`;
   - the `gas` row has 0.

   On master the `hydrogen` and `Hydrogen` rows report 24.0 MWh and 60.0 %. I verified both numbers against master.

Existing tests must pass unchanged, especially:

- `tests/test_results_seam.py` (`test_carrier_kpis`);
- `tests/test_results_facade_surface.py`;
- `tests/test_compare_cross_surface.py`;
- `tests/test_snapshots_freq_weightings.py`;
- `tests/test_gen_category_words.py` (F's tests; you do not touch `profile_shapes.py`).

Commands (from the repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_carrier_kpis_hydrogen.py
pixi run gui-tests tests/test_results_seam.py tests/test_results_facade_surface.py tests/test_compare_cross_surface.py tests/test_snapshots_freq_weightings.py tests/test_gen_category_words.py
pixi run gui-tests   # full backend suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so paths are relative to that folder. There is no frontend change and so no frontend test.

## Out of scope / don't touch

- `services/profile_shapes.py`. Import `_gen_category` from it in test 3 only; do not edit it or move the mask into it.
- `harness/catalogue.py`.
- Any frontend file, including `LoadFlow.tsx`, `simulation.ts`, `paletteData.ts` and `carrierCatalog.ts`.
- `services/adequacy/*`.
- `routers/network_profiles.py` and `routers/results.py`.
- `smoke/check_bundle.py`.
- Defaults packs, `gridspine/` and campus files.
- `services/results/curtailment.py` (`RENEW_KEYWORDS`, same bug; G2), `services/results/load_shape.py` and `services/compare/curtailment.py`.
- Nice-name blind spots such as `Run of River` and `Reservoir & Dam` (G3).
- Any change to which keywords count as renewable, beyond the hydrogen exclusion.

## Gatekeeper decisions (answer before handing out)

- **G1, how the rule is shared with F.**
  - (a) *Recommended:* copy F's hydrogen mask into a module-level `_is_renewable_carrier` in `carrier_kpis.py`, and pin it to `_gen_category` with test 3. This needs no edit to `profile_shapes.py` (out of scope) and moves only hydrogen words.
  - (b) Delegate: `_is_renewable_carrier(c) = _gen_category(c) == 'renewable'`. Nothing is copied. But demand-response words win first, so `flex solar`, `solar_dr`, `Solar DR`, `dsm wind` and `wind_curtail` lose their curtailment figure, and results would depend on a Time Series helper.
  - (c) Extract a shared `_is_renewable(carrier)` into `profile_shapes.py` and use it in both places. This is the cleanest, but it edits an out-of-scope file; it would be a follow-up after H.
- **G2, the Curtailment tab.** `services/results/curtailment.py:120-129` has its own `RENEW_KEYWORDS` with the same `hydro` substring. The Curtailment and Dispatch tabs therefore still list a hydrogen Generator's spill, while Load Flow's table (after H) does not. The comment at `carrier_kpis.py:156-160` says these were meant to agree.
  - (a) *Recommended:* leave it out of H and file a follow-up issue. Its list differs (it has `onwind`, `offwind` and `rooftop`, and lacks `biogas` and `run-of-river`), and it feeds three surfaces that need their own carrier table.
  - (b) Fold it into H with the same mask.
- **G3, nice names.** The statistics key is the `nice_name`, so PyPSA-Eur's `Run of River` and `Reservoir & Dam` get no curtailment figure, today and after H.
  - (a) *Recommended:* out of scope; note it in the follow-up issue.
  - (b) In H, also test the raw carrier id behind the nice name (`n.carriers` lookup).
- **G4, hydrogen rows show 0 rather than blank.** Non-renewable Generator rows already show `0.0` curtailment, so hydrogen rows do too.
  - *Recommended:* accept. A blank or "—" cell would change the payload and the frontend, which is out of scope.

## Repo rules

- Branch `outsource/carrier-kpis-hydrogen` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body starts with the line `Outsourced task H`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated.
- Add one release-note fragment, `doc/release-notes.d/outsource-carrier-kpis-hydrogen.md`, containing one bullet in this form:

  ```
  * Fix(pypsa-gui): Hydrogen generators no longer count as renewable in the Load Flow carrier KPIs, so their unused capacity is no longer reported as curtailment.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files.

## Done when

- [ ] The four new tests pass, and each of them fails when run against master's `carrier_kpis.py`.
- [ ] The five existing test files named above pass unchanged.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the three files listed under "Files to touch".
- [ ] `compute_carrier_kpis` keeps its signature and its row keys; `_RENEWABLE_KW` and `_is_renewable_carrier` are importable from `services.results.carrier_kpis`.
- [ ] One PR on `outsource/carrier-kpis-hydrogen`, with a body starting `Outsourced task H`.
