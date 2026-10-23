<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: ready-for-agent — GK triage 2026-10-23: G1 (a), G2 include, G3 follow-up, G4 accept

# Outsourced task H: Hydrogen stops counting as renewable in the results carrier KPIs

Branch: `outsource/carrier-kpis-hydrogen` · one PR against `master` · PR body starts with `Outsourced task H`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. `.scratch/timeseries-generators/issues/13-carrier-kpis-hydrogen.md`, the issue.
3. `.scratch/timeseries-generators/outsource/F-gen-category-word-match.md`, the sibling task. It fixed the same bug in the Time Series category (merged as PR #676).
4. `pypsa-gui/backend/services/profile_shapes.py`, lines 219-258: `_RENEWABLE_KW`, `_carrier_words` and `_gen_category`. Lines 247-254 are F's renewable branch, which blanks hydrogen words before the substring test. You copy that mask.
5. `pypsa-gui/backend/services/results/carrier_kpis.py`, lines 148-191: `CURTAILMENT_SOURCES`, the local `_RENEWABLE_KW`, `_is_renewable_carrier` and the row loop that reads it.
6. `pypsa-gui/backend/services/results/curtailment.py`, lines 29-146 (`curtailment_frame`). Lines 114-146 hold the `RENEW_KEYWORDS` filter and the `curtailment_cost > 0` ("has_subsidy") rule.
7. `pypsa-gui/backend/tests/test_gen_category_words.py`, F's tests. Your tests follow their style.

## Goal in plain words

Two results surfaces decide whether a Generator's unused capacity counts as curtailment. Each does it with a substring test over renewable keywords. `hydro` is found inside "**hydro**gen", so both treat a hydrogen Generator as renewable and report its unused capacity as curtailed energy.

**Load Flow's Carrier KPIs** come from `carrier_kpis.compute_carrier_kpis`, which builds one row per (component, carrier). A Generator row gets a curtailment figure only when `_is_renewable_carrier(carrier)` is true; thermal plant otherwise reports its unused headroom as "curtailment".

The carrier string this check sees is the key from `n.statistics.*(groupby="carrier")`. In PyPSA 1.1.2 that key is the carrier's `nice_name` when one is set, and the raw carrier id otherwise. That matters in practice. A Generator a user creates with carrier `H2` gets `nice_name` "Hydrogen" from `services/carrier_catalog.py` (through `ensure_carrier`). Its row is keyed `Hydrogen`, and today it gets a curtailment figure.

**The Curtailment tab and the views built on it** come from `curtailment.curtailment_frame`. It keeps one column per Generator for which either holds:

- its raw carrier id (`n.generators.carrier`, lowercased at line 128) contains one of `RENEW_KEYWORDS` (line 129);
- its `curtailment_cost > 0` (the `has_subsidy` rule, lines 131-137).

A `hydrogen` Generator therefore gets a curtailment column. An `H2` Generator does not, because this check reads the raw id, not the nice name.

I reproduced both on master without a solve. A 4-snapshot network has 10 MW Generators, each dispatching 4 MW, with carriers `hydrogen`, `H2` (nice name `Hydrogen`), `solar`, `hydro` and `gas`. Results:

- Carrier KPIs: the `hydrogen`, `Hydrogen`, `solar` and `hydro` rows each report 24.0 MWh, 60.0 % curtailed; `gas` reports 0.
- `curtailment_frame`: columns for the `hydrogen`, `solar` and `hydro` Generators (24.0 MWh each over the 4 h); none for `H2` or `gas`. A second `hydrogen` Generator with `curtailment_cost = 5` also gets a column.

Who reads the result:

- `carrier_kpis._RENEWABLE_KW` / `_is_renewable_carrier` have no other reader; they only gate `curtailment_mwh` / `curtailment_pct` on Generator rows. Nothing in this module computes a renewable share.
  - `routers/results.py:663-689` (`get_carrier_kpis`, `GET /api/results/carrier_kpis`) returns the payload unchanged.
  - `frontend/src/api/simulation.ts:1371-1385` → `frontend/src/pages/results/LoadFlow.tsx`. The "Carrier KPIs" table shows the Curtailment % column, with the warning colour above 50 % (line 637). The CSV export (`results_carrier_kpis.csv`, lines 595-605) writes `curtailment_MWh` and `curtailment_pct`.
  - `services/chat_tools.py:602`: the chat assistant's results tool with `carrier_kpis` calls `get_carrier_kpis`. `harness/catalogue.py:124` only lists the name.
- `curtailment.curtailment_frame` feeds:
  - `compute_curtailment` → `GET /api/results/curtailment` (`routers/results.py:899-907`). Its frontend readers are `pages/results/Curtailment.tsx`, `Dispatch.tsx` and `AggregatedOverview.tsx`, and the chat tool's `curtailment` maps to `get_curtailment`.
  - `curtailment_by_cause` → `GET /api/results/curtailment/by_cause` (`routers/results.py:910-924`) → `pages/results/CurtailmentByCause.tsx`.
  - `services/results/load_shape.py:88-95` (`_excess`), the curtailed-renewable MW per snapshot.
- Tests that read these payloads include `tests/test_results_seam.py`, `test_results_facade_surface.py`, `test_compare_cross_surface.py`, `test_snapshots_freq_weightings.py`, `test_curtailment_by_cause.py`, `test_eh_ipp.py` and `test_results_range.py`. The golden fixture's Generators are `gas`, `solar` and `diesel`, and the IPP fixtures use solar/wind, so none of these moves.

Not this task:

- `services/results/load_shape.py:51` (`RENEWABLE`) matches exact labels, so it has no hydrogen bug.
- `services/compare/curtailment.py` selects by `p_max_pu` profile, not by keyword.

Fix: blank every word that starts with `hydrogen` before each of the two substring tests. Use F's mask, and keep each file's own keyword set. This applies one rule in one place, so Load Flow and the Curtailment tab agree on hydrogen, and both agree with the Time Series page.

### The rule (exact)

- The mask is F's (`profile_shapes.py:248-253`):
  - insert a boundary between a lowercase letter and the uppercase letter after it (`HydrogenImport` → `Hydrogen Import`);
  - a word is a maximal run of ASCII letters;
  - a word is a hydrogen word when its lowercase form starts with `hydrogen`;
  - replace each hydrogen word with a space, keep every other character, then lowercase.
- Carrier KPIs: `_is_renewable_carrier(c)` is true when an entry of `carrier_kpis._RENEWABLE_KW` is a substring of the masked carrier. The keyword tuple stays as it is: `wind solar ror hydro geothermal wave tidal pv biomass biogas run-of-river`. Do not merge it with `profile_shapes._RENEWABLE_KW`.
- Curtailment tab: `is_renew` is true when an entry of `curtailment.RENEW_KEYWORDS` is a substring of the masked carrier. The keyword tuple stays as it is: `wind solar pv ror geothermal offwind onwind hydro biomass wave tidal rooftop`.
  - Mask the raw carrier string, **before** lowercasing. The camelCase split needs the case: lowercased `HydrogenSolar` would be one hydrogen word and lose its `solar`.
  - The `has_subsidy` rule is unchanged, so a hydrogen Generator with `curtailment_cost > 0` still gets a column.
- So `hydrogen`, `Hydrogen`, `Hydrogen Storage`, `HydrogenFuelCell` and `hydrogen CHP` are not renewable in either file. `hydro`, `hydropower`, `HydroPlant`, `Run-of-River` and `solar_hydrogen` still are.
- Demand-response words are not consulted (G1 (a)). `flex solar`, `solar_dr`, `dsm wind` and `wind_curtail` stay renewable here, although `_gen_category` puts them in `'dr'`.
- Unchanged:
  - in `carrier_kpis.py`: `CURTAILMENT_SOURCES`, the Link rows (always get a figure), StorageUnit and Store rows (never get one), the percentage formula, the row keys and the storage revenue overlay;
  - in `curtailment.py`: everything outside the `is_renew` line, including the vintage masking, `compute_curtailment`, `curtailment_by_cause` and the payload shapes.

Reuse: `profile_shapes` exposes the word helper `_carrier_words`, but not the hydrogen mask on its own, and `profile_shapes.py` is out of scope. Do not build the masked string by joining `_carrier_words(...)`. That drops the hyphens, so `Run-of-River` (the catalogue nice name of `ror`) would lose its curtailment figure.

Copy F's mask expression once, as a module-level `_without_hydrogen_words(carrier: str) -> str` in `carrier_kpis.py`, and cite `profile_shapes._gen_category` in its docstring. `curtailment.py` imports that helper from `services.results.carrier_kpis`. There is no import cycle: `carrier_kpis` imports only `period_utils` and `load_frames`. Test 3 pins the copy to F's.

### Carriers checked

I ran master's rule and the new rule over every carrier we ship or write. For Carrier KPIs, both raw ids and nice names are covered, because the statistics key can be either. The Curtailment tab sees raw ids only.

- Generator carriers built by every template in `project_templates/` (`_build.build_3bus`, `build_ieee14`, `eh_templates.BUILDERS`): `gas coal oil diesel grid solar wind`, `NH3 import`, `NH3 shortfall`. No template sets a `nice_name` on these.
- `services/carrier_catalog.py`: every id and every `nice_name` (it mirrors `frontend/src/utils/carrierCatalog.ts`).
- Palette defaults in `frontend/src/layout/paletteData.ts` that create Generators: `thermal` → `gas`, `renewable` → `wind`. The `hydro` (PSH) and `H2` items create StorageUnits, Stores or Links, which neither check sees.
- PyPSA-Eur electricity carriers and the `plotting.nice_names` in `config/plotting.default.yaml`, which `scripts/add_electricity.py:227-233` writes onto `n.carriers`: `onwind`, `offwind-ac/-dc/-float`, `solar`, `solar-hsat`, `ror`, `hydro`, `CCGT`, `OCGT`, `nuclear`, `lignite`, `biomass`, `geothermal`, `oil`, `coal`, `load`, `curtailment`, `PHS`, and their nice names (`Onshore Wind`, `Solar`, `Run of River`, `Reservoir & Dam`, `Load Shedding`, `Hydrogen Storage`, ...).
- Every Generator carrier in `pypsa-gui/backend/tests`, for example `gas`, `grid`, `H2`, `hydrogen`, `load_shedding`, `product_shortfall`, `solar thermal`, `solar PV`, `Solar`, `Wind`, `GAS`.

| Carrier | Master (KPIs / Curtailment tab) | New (both) | Where it comes from |
|---|---|---|---|
| `Hydrogen` | renewable / renewable | not renewable | catalogue `nice_name` of `H2`; any `H2` Generator a user adds in the editor (KPIs key) |
| `hydrogen` | renewable / renewable | not renewable | tests |
| `Hydrogen Storage` | renewable / renewable | not renewable | PyPSA-Eur nice name of `H2` (a Store there, so no Generator row in practice) |
| `HydrogenFuelCell`, `hydrogen supply`, `H2 (hydrogen)`, `HydrogenImport` | renewable / renewable | not renewable | user-coined |
| `hydrogen CHP` | renewable / renewable | not renewable | user-coined (thermal; now no headroom-as-curtailment) |
| `H2`, `H2 fuel cell` | not / not | not renewable | catalogue, tests |
| `solar_hydrogen` | renewable / renewable | renewable | user-coined (its `solar` still counts) |
| `hydro`, `Hydro`, `hydropower`, `HydroPlant`, `hydro-reservoir`, `Hydro Reservoir`, `Pumped Hydro`, `Pumped Hydro Storage` | renewable / renewable | renewable | catalogue, PyPSA-Eur, user-coined |
| `Run-of-River` | renewable / renewable | renewable | catalogue nice name of `ror` |
| `Run of River`, `Reservoir & Dam` | not / not | not renewable | PyPSA-Eur nice names of `ror` and `hydro` (already missed; G3 follow-up) |
| `flex solar`, `solar_dr`, `Solar DR`, `dsm wind`, `wind_curtail` | renewable / renewable | renewable | user-coined (`_gen_category` says `'dr'`; G1 (a) keeps them) |

Every other carrier listed above keeps its value in both files. Of the shipped carriers, only `Hydrogen` (catalogue nice name) and `Hydrogen Storage` (PyPSA-Eur nice name) move, and of the test carriers only `hydrogen`.

### Stored data

Nothing stored goes stale:

- `/api/results/carrier_kpis`, `/api/results/curtailment` and `/curtailment/by_cause` compute on each request from the solved network.
- No study, scenario or compare snapshot stores these payloads. `services/compare/curtailment.py` uses its own `p_max_pu` rule.
- CSVs the user already exported keep the old figures. That is expected.

## User-facing outcome

- In Load Flow's Carrier KPIs table, a Generator whose carrier (or carrier nice name) is a hydrogen word shows 0 curtailment MWh and 0.0 % curtailment, as a gas row does. Its unused capacity no longer shows in the warning colour. The CSV export and the chat assistant's numbers match.
- On the Curtailment tab, the Dispatch and Overview curtailment series and Curtailment by cause, such a Generator no longer appears, unless it has `curtailment_cost > 0`.
- Hydro, run-of-river, solar, wind and every other renewable carrier keep their curtailment figure on every surface.
- **No new columns, labels, keys or copy.** The payload shapes are unchanged.

## Files to touch

- `pypsa-gui/backend/services/results/carrier_kpis.py`:
  - Add a module-level `_without_hydrogen_words(carrier: str) -> str` (F's mask; stdlib `re`, imported at module top).
  - Move `_RENEWABLE_KW` (lines 162-165) to module level, with the same tuple and the same name.
  - Move `_is_renewable_carrier` (lines 167-169) to module level as `_is_renewable_carrier(name: str) -> bool`, using the mask. Delete the two nested definitions. Line 179 keeps calling `_is_renewable_carrier(carrier)`.
  - Add one sentence to the comment block at lines 156-160 saying that hydrogen words do not count as renewable.
  - Change nothing else in the file.
- `pypsa-gui/backend/services/results/curtailment.py`:
  - In `curtailment_frame`, line 128-129: compute `is_renew` on `_without_hydrogen_words(<raw carrier>)`, imported from `services.results.carrier_kpis`.
  - Keep `RENEW_KEYWORDS` and the `has_subsidy` rule exactly as they are. Change nothing else.
- `pypsa-gui/backend/tests/test_carrier_kpis_hydrogen.py`: new file, tests below.
- `doc/release-notes.d/outsource-carrier-kpis-hydrogen.md`: the release-note fragment.

## Tests (new file `tests/test_carrier_kpis_hydrogen.py`; each fails on master)

On master `_is_renewable_carrier` is nested and so cannot be imported, and tests 1-3 fail on that alone. Each also asserts a value that master's rule gets wrong, so it would fail even if the helper were importable.

1. `test_hydrogen_carriers_are_not_renewable`: parametrised over `hydrogen`, `Hydrogen`, `Hydrogen Storage`, `HydrogenFuelCell`, `hydrogen supply`, `H2 (hydrogen)`, `HydrogenImport` and `hydrogen CHP`. Each gives `False` from `_is_renewable_carrier`; master's substring rule gives `True` for every one.
2. `test_carrier_table_keeps_its_renewable_answer`: one table holding every carrier in "Carriers checked", with its expected value from the "New" column. Carriers not in the change table keep master's value. It includes at least:
   - `onwind`, `offwind-float`, `solar-hsat`, `Run-of-River`, `Hydro Reservoir`, `hydro`, `hydropower`, `solar_hydrogen` → `True`;
   - `gas`, `CCGT`, `H2`, `H2 Fuel Cell`, `load`, `curtailment`, `Run of River`, `Reservoir & Dam` → `False`.

   It fails on master because of the `Hydrogen`/`hydrogen` rows. It guards against collateral moves.
3. `test_renewable_answer_matches_time_series_category`:
   - parametrised over `H2`, `hydrogen`, `Hydrogen`, `Hydrogen Storage`, `HydrogenFuelCell`, `H2 fuel cell`, `hydro`, `Hydro`, `ror`, `Run of River`, `onwind`, `solar`, `biomass` and `gas`;
   - for each carrier that `_gen_category` (from `services.profile_shapes`) does not put in `'dr'`, assert `_is_renewable_carrier(c) == (_gen_category(c) == 'renewable')`. None of these 14 is `'dr'` today; I checked.
   - Also assert that `flex solar`, `solar_dr`, `dsm wind` and `wind_curtail` give `True` from `_is_renewable_carrier`. `_gen_category` puts these in `'dr'`, and G1 (a) keeps them renewable here.
   - On master it fails for `hydrogen`, `Hydrogen`, `Hydrogen Storage` and `HydrogenFuelCell`, where `_gen_category` says `'other'` and carrier KPIs say renewable.
4. `test_hydrogen_generator_row_has_no_curtailment` (Carrier KPIs, no solve). Build the network:
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
5. `test_curtailment_tab_agrees_with_carrier_kpis_on_hydrogen`. Build test 4's network without the `H2` Generator, and name the Generators `h2_unit` (carrier `hydrogen`), `hydro_unit` (carrier `hydro`) and `gas_unit` (carrier `gas`).
   - `services.results.curtailment.curtailment_frame(n)`:
     - has a `hydro_unit` column summing to `pytest.approx(24.0)`;
     - has no `h2_unit` column and no `gas_unit` column;
     - `compute_curtailment(n, None, None)["columns"]` likewise has `hydro_unit` and not `h2_unit`.
   - `compute_carrier_kpis`: the `hydrogen` row shows `curtailment_mwh == 0.0`; the `hydro` row shows `pytest.approx(24.0)`.
   - Then set `n.generators["curtailment_cost"] = 0.0` and `n.generators.loc["h2_unit", "curtailment_cost"] = 5.0`. Assert that `curtailment_frame(n)` now has an `h2_unit` column summing to `pytest.approx(24.0)`; the `has_subsidy` rule still applies.
   - On master `h2_unit` is a column of the frame (24.0 MWh), so the absence assertion fails. I checked every claim here against master's `curtailment_frame`:
     - with only `p` set and no `generators_t.p_max_pu`, `p_max = p_nom_opt`;
     - the vintage block is a no-op without vintages;
     - the `curtailment_cost` column, when present, is read per Generator.

Existing tests must pass unchanged, especially:

- `tests/test_results_seam.py`;
- `tests/test_results_facade_surface.py`;
- `tests/test_compare_cross_surface.py`;
- `tests/test_snapshots_freq_weightings.py`;
- `tests/test_curtailment_by_cause.py`;
- `tests/test_eh_ipp.py`;
- `tests/test_results_range.py`;
- `tests/test_gen_category_words.py` (F's tests; you do not touch `profile_shapes.py`).

Commands (from the repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_carrier_kpis_hydrogen.py
pixi run gui-tests tests/test_results_seam.py tests/test_results_facade_surface.py tests/test_compare_cross_surface.py tests/test_snapshots_freq_weightings.py tests/test_curtailment_by_cause.py tests/test_eh_ipp.py tests/test_results_range.py tests/test_gen_category_words.py
pixi run gui-tests   # full backend suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so paths are relative to that folder. There is no frontend change and so no frontend test.

## Out of scope / don't touch

- `services/profile_shapes.py`. Import `_gen_category` from it in test 3 only; do not edit it or move the mask into it.
- `harness/catalogue.py`.
- Any frontend file, including `LoadFlow.tsx`, `Curtailment.tsx`, `Dispatch.tsx`, `simulation.ts`, `paletteData.ts` and `carrierCatalog.ts`.
- `services/adequacy/*`.
- `routers/network_profiles.py` and `routers/results.py`.
- `smoke/check_bundle.py`.
- Defaults packs, `gridspine/` and campus files.
- `services/results/load_shape.py` and `services/compare/curtailment.py`.
- Harmonising the two keyword sets. `curtailment.RENEW_KEYWORDS` has `offwind`, `onwind` and `rooftop`; `carrier_kpis._RENEWABLE_KW` has `biogas` and `run-of-river`. That stays a follow-up.
- Nice-name blind spots such as `Run of River` and `Reservoir & Dam` (G3 follow-up).
- Any change to which keywords count as renewable, beyond the hydrogen exclusion.

## Gatekeeper decisions (2026-10-23)

- **G1, how the rule is shared with F: (a).** Copy only F's hydrogen mask, once, into `carrier_kpis._without_hydrogen_words`. Keep each file's keyword set as it is; do not merge it with `profile_shapes`'. Test 3 pins the copy to `_gen_category` on every carrier that `_gen_category` does not put in `'dr'`, and it keeps `flex solar`, `solar_dr`, `dsm wind` and `wind_curtail` renewable in carrier KPIs.
- **G2, the Curtailment tab: include.** `services/results/curtailment.py` gets the same mask in H, so Load Flow agrees with the Curtailment tab. `RENEW_KEYWORDS` is not harmonised with `_RENEWABLE_KW` (follow-up). A hydrogen Generator with `curtailment_cost > 0` still counts through the existing `has_subsidy` rule. Test 5 covers this.
- **G3, nice names: follow-up.** PyPSA-Eur's `Run of River` and `Reservoir & Dam` nice names get no Carrier KPIs curtailment figure, before and after H. This is logged in issue 13's follow-ups; H does not touch it.
- **G4, hydrogen rows show 0: accept.** 0 is the branch thermal Generators already take, so a hydrogen row reads like a gas row. No payload or frontend change.

Follow-ups (not H):

- Nice-name blind spots `Run of River` / `Reservoir & Dam` (G3).
- Harmonising `curtailment.RENEW_KEYWORDS` with `carrier_kpis._RENEWABLE_KW`.
- A shared renewable helper in `profile_shapes.py`.

## Repo rules

- Branch `outsource/carrier-kpis-hydrogen` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body starts with the line `Outsourced task H`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated.
- Add one release-note fragment, `doc/release-notes.d/outsource-carrier-kpis-hydrogen.md`, containing one bullet in this form:

  ```
  * Fix(pypsa-gui): Hydrogen generators no longer count as renewable in the results: Load Flow's carrier KPIs and the Curtailment tab no longer report their unused capacity as curtailment.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files.

## Done when

- [ ] The five new tests pass, and each of them fails when run against master's `carrier_kpis.py` and `curtailment.py`.
- [ ] The eight existing test files named above pass unchanged.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the four files listed under "Files to touch".
- [ ] `compute_carrier_kpis`, `curtailment_frame`, `compute_curtailment` and `curtailment_by_cause` keep their signatures and payload keys. `_RENEWABLE_KW`, `_is_renewable_carrier` and `_without_hydrogen_words` are importable from `services.results.carrier_kpis`, and `RENEW_KEYWORDS` is unchanged.
- [ ] One PR on `outsource/carrier-kpis-hydrogen`, with a body starting `Outsourced task H`.
