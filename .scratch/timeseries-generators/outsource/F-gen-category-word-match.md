<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

Status: draft — needs gatekeeper triage (issue 11 is needs-triage; adequacy reads this function)

# Outsourced task F: "hydro" and "hydrogen" generators stop filing under demand response

Branch: `outsource/gen-category-word-match` · one PR against `master` · PR body starts with `Outsourced task F`

## Read first

1. `AGENTS.md` (repo root). Release notes and PR rules are there.
2. `.scratch/timeseries-generators/issues/11-gen-category-word-match.md`, the issue.
3. `pypsa-gui/backend/services/profile_shapes.py`, lines 217-238: the three keyword sets and `_gen_category`.
4. `pypsa-gui/backend/services/adequacy/eh_readiness.py`, lines 92-146: `_NOT_EQUIPMENT_KW` and `outage_scan`, the adequacy reader.
5. `pypsa-gui/backend/tests/test_energy_hub_tagging.py`, `test_readiness_outage_missing_skips_slack_sink_and_dump_generators` (line ~620). Your readiness test follows its style.

## Goal in plain words

`_gen_category(carrier)` puts a generator in one of four buckets: `'dr'`, `'renewable'`, `'conventional'` or `'other'`. It tests the demand-response keywords first, and it tests them as substrings. The keyword `'dr'` is found inside "hy**dr**o" and "hy**dr**ogen", so those generators land in demand response. Their renewable or conventional keywords are never checked.

Who reads the bucket:

- `routers/network_profiles.py:350` (`get_generator_profiles`) sends it to the frontend as `category`. The Time Series page picks the tab from it, and Properties picks the editors from it (`frontend/src/components/series/useSeriesFields.ts::generatorSeriesEditors`). So a hydro unit sits on the Demand Response tab with no Availability or Price editor.
- `routers/network_profiles.py:419` (`download_generator_profile_template`): which generators go in a per-category template file.
- `services/adequacy/eh_readiness.py:140` (`outage_scan`): a Generator is "thermal-like" only when the bucket is `'conventional'`. `eh_readiness` (the Site card, `/api/results/eh_readiness`) and `eh_setup.suggest_eh_setup` (line 296) list thermal units that have no outage data.
- `routers/network.py:76-85` and `routers/network_time_axis.py:63-72` only re-export the names. `tests/test_network_facade_surface.py:64-67` pins `_RENEWABLE_KW`, `_CONVENTIONAL_KW`, `_DR_KW` and `_gen_category` to `services.profile_shapes`, so keep those four names.

The frontend has no copy of the rule. It only reads `category` from the server. `components/CarrierSelect.tsx` and `layout/properties/cardKit.tsx` group carriers for the carrier picker with their own rule; that is a different feature, has no demand-response group, and is out of scope.

Fix: match the demand-response keywords as whole words, and stop a "hydrogen" word from counting as "hydro". Leave the renewable and conventional substring tests otherwise as they are (gatekeeper decision G1 below).

### The word rule (exact)

- Insert a boundary between a lowercase letter and the uppercase letter after it (camelCase: `DemandResponse` → `Demand Response`, `dsmLoad` → `dsm Load`).
- Lowercase. A word is a maximal run of the letters `a-z`. Everything else is a boundary: `_`, `-`, space, tab, `.`, `/`, `(`, `)`, digits and any other character. So `dr_industrial`, `DR-1`, `DR load` and `DR2` all contain the word `dr`; `drain`, `hydro`, `hydrogen` and `hydraulic` do not.
- A keyword is split into words the same way. It matches when its words appear consecutively in the carrier's words. So `demand_response` matches `demand_response`, `demand response`, `demand-response` and `DemandResponse`.
- Demand-response keywords: today's `_DR_KW` plus three explicit word forms that the substring rule used to catch, so they keep their bucket: `'flexible'`, `'flexibility'`, `'curtailment'`. (`'curtailment'` is the carrier PyPSA-Eur's `curtailment_mode` gives its generators, `scripts/solve_network.py:539`.)
- Renewable: the same substring test as today, run on the lowercased carrier with every word that starts with `hydrogen` blanked out first. So `hydropower`, `hydroelectric` and `HydroPlant` stay renewable, and `hydrogen` is not renewable.
- Conventional: unchanged substring test.
- Order unchanged: dr, then renewable, then conventional, else other.

### Carriers checked

I ran the old rule and this rule over every carrier we ship or write:

- Generator carriers built by every template in `project_templates/` (`_build.py` builders plus `eh_templates.BUILDERS`): `gas coal oil diesel grid import solar wind thermal`, `NH3 import`, `NH3 shortfall`.
- `services/carrier_catalog.py` (mirrors `frontend/src/utils/carrierCatalog.ts`).
- The palette defaults in `frontend/src/layout/paletteData.ts`.
- PyPSA-Eur electricity carriers. The Belgium template is a PyPSA-Eur network: `onwind`, `offwind-ac/-dc/-float`, `solar-hsat`, `ror`, `CCGT`, `OCGT`, `nuclear`, `lignite`, `biomass`, `geothermal`, `load`, `curtailment`, `hydro`, `PHS`.
- Every Generator carrier in `pypsa-gui/backend/tests`, for example `gas_unrated`, `diesel_unrated`, `load_sink`, `spillage`, `heat-dump`, `load_shedding`, `hydrogen`.

| Carrier | Old | New | Where it comes from |
|---|---|---|---|
| `hydro`, `Hydro`, `hydro-reservoir` | dr | renewable | catalogue, PyPSA-Eur, palette (`psh`) |
| `hydropower`, `hydroelectric`, `HydroPlant` | dr | renewable | user-coined |
| `hydrogen`, `hydrogen supply`, `H2 (hydrogen)` | dr | other | tests, user-coined |
| `solar_hydrogen` | dr | renewable | user-coined |
| `hydrogen CHP`, `hydrogen_gas_turbine`, `drill_diesel` | dr | conventional | user-coined (see G2) |
| `drain`, `dryer`, `hydraulic` | dr | other | user-coined |
| `demand response`, `demand-response` | other | dr | user-coined (see G4) |

Every other carrier listed above keeps its bucket. Of the shipped ones, only `hydro` moves.

### Stored data

Nothing stored goes stale:

- `/generators/profiles` and `/generators/template` compute the category on each request.
- User series are keyed by (component, attribute, name), not by category.
- `eh_readiness` is computed live. `services/flow_service.py:~460-484` caches it in process memory, keyed by network revision, and a restart clears that cache.
- An adequacy run reads outage data through `resolve_outage_params`, not through the category. `outage_scan` only feeds the readiness warnings and the setup suggestions.

A user's existing hydro profile stays attached to the generator; it now appears on the Renewable tab.

## User-facing outcome

- On the Time Series page, a `hydro` generator sits on the Renewable tab, and Properties shows a Price series editor for it.
- A `hydrogen` generator sits on the Conventional tab ('other' is listed there, `TimeSeriesManager.tsx:1169`), and Properties shows the Availability and Price editors for it.
- `dr`, `dsm`, `dsr`, `flex`, `interruptible`, `curtail` and `demand_response` generators stay on the Demand Response tab.
- **No new tabs, buttons, labels, categories or copy.** The four category strings are unchanged.

## Files to touch

- `pypsa-gui/backend/services/profile_shapes.py`:
  - Add `'flexible', 'flexibility', 'curtailment'` to `_DR_KW` (line 229).
  - Add one small private helper that returns a carrier's words (stdlib `re`).
  - Rewrite `_gen_category` (lines 232-238) to the rule above, and update its docstring to say the demand-response keywords match whole words.
  - Leave `_RENEWABLE_KW` and `_CONVENTIONAL_KW` (lines 217, 225) unchanged.
  - Keep the signature `_gen_category(carrier: str) -> str` and the four return values.
- `pypsa-gui/backend/tests/test_gen_category_words.py`: new file, tests below.
- `doc/release-notes.d/outsource-gen-category-word-match.md`: the release-note fragment.

## Tests (new file `tests/test_gen_category_words.py`; each fails on master)

1. `test_hydro_files_under_renewable`: parametrised over `hydro`, `Hydro`, `hydro-reservoir`, `hydropower`, `HydroPlant`. Each gives `'renewable'`. On master all give `'dr'`.
2. `test_hydrogen_is_not_demand_response`: `hydrogen`, `hydrogen supply` and `H2 (hydrogen)` give `'other'`; `hydrogen CHP` gives `'conventional'`. On master all give `'dr'`.
3. `test_demand_response_keywords_match_as_words`: at least one case per keyword, and each gives `'dr'`:
   - `dr`, `DR-1`, `dr_industrial`, `DR load`;
   - `dsm`, `industrial DSM`, `dsmLoad`;
   - `dsr`;
   - `flex`, `Flex Load`, `flexible_load`, `flexibility`;
   - `interruptible`;
   - `curtail`, `curtailment`;
   - `demand_response`, `demand response`, `demand-response`, `DemandResponse`.

   It fails on master for `demand response` and `demand-response`.
4. `test_dr_inside_a_word_is_not_demand_response`: `drain`, `dryer` and `hydraulic` give `'other'` (on master: `'dr'`).
5. `test_shipped_carriers_keep_their_category`: one table holding every carrier in "Carriers checked" with its expected bucket from the "New" column; carriers not in the change table keep their old bucket. It fails on master only because of `hydro`. This guards against collateral moves.
6. `test_generator_profiles_report_the_word_category` (endpoint, `client` + `install_network` from `tests/conftest.py`):
   - build a network with Generators whose carriers are `hydro`, `hydrogen`, `dsm` and `gas`;
   - call `GET /api/network/generators/profiles`;
   - assert the categories `renewable`, `other`, `dr` and `conventional`.
7. `test_readiness_thermal_scan_follows_the_word_category`, modelled on `test_readiness_outage_missing_skips_slack_sink_and_dump_generators`:
   - on `_feeder_hub()`, add a `hydrogen CHP` Generator and a `hydro_unit` Generator with carrier `hydrogen`, both with NaN `outage_rate_value` and `mttr_hours`;
   - assert `hydrogen CHP` is in `eh_readiness(...)["outage_units"]["missing"]` and `hydro_unit` is not.
   - On master `hydrogen CHP` is `'dr'`, so it is missing from the list.
   - If the gatekeeper answers G2 the other way, drop this test and add a case to test 2 instead.

Existing tests must pass unchanged, especially:

- `tests/test_energy_hub_tagging.py`: the readiness tests use `diesel_unrated`, `load_sink` and `spillage`, and none of them move;
- `tests/test_eh_setup_suggest.py` (`gas_unrated`);
- `tests/test_network_facade_surface.py`;
- `tests/test_network_profiles_surface.py`.

Commands (from the repo root; install once with `pixi install --frozen -e default -e test`):

```bash
pixi run gui-tests tests/test_gen_category_words.py
pixi run gui-tests tests/test_energy_hub_tagging.py tests/test_eh_setup_suggest.py tests/test_network_facade_surface.py tests/test_network_profiles_surface.py
pixi run gui-tests   # full backend suite before opening the PR
```

`gui-tests` runs `python -m pytest` in `pypsa-gui/backend`, so paths are relative to that folder. There is no frontend change and so no frontend test.

## Out of scope / don't touch

- Any frontend file, including `TimeSeriesManager.tsx`, `useSeriesFields.ts`, `PropertiesPanel.tsx`, `CarrierSelect.tsx` and `cardKit.tsx`. No UI change beyond the category value the server returns.
- `services/adequacy/eh_readiness.py` and `eh_setup.py`. `_NOT_EQUIPMENT_KW` is also a substring test, but it is not this task.
- `routers/network_profiles.py` (`_must_run_floor`, `_mc_baseline` and the solar/wind shape choice are substring tests too), `routers/network.py` and `routers/network_time_axis.py`.
- `services/results/carrier_kpis.py`. Its own `_RENEWABLE_KW` counts `hydrogen` as renewable; that is a separate issue.
- Campus files and `harness/catalogue.py`.
- The renewable and conventional substring rules beyond the `hydrogen` exclusion (G1).

## Gatekeeper decisions (answer before handing out)

- **G1, how far the word rule reaches.**
  - (a) *Recommended:* demand-response keywords as whole words, plus "hydrogen is not hydro"; renewable and conventional stay substrings. Only the table above moves, and no shipped carrier moves except `hydro`.
  - (b) Whole words for all three families, adding `onwind` and `offwind`. This also fixes `oil` in "boiler", `ror` in "error" and "mirror", and `gas` in "syngas". But it moves `hardcoal`, `biodiesel`, `natgas`, `fueloil`, `gasoil`, `gasifier`, `solarpv`, `hydropower` and `spillage` to `'other'`, and conventional→other silently drops units from the adequacy outage scan.
  - (c) An explicit carrier list, the issue's alternative. Users coin carriers freely, so a list would send every unlisted name to `'other'`.
- **G2, dr→conventional.** A user-coined carrier whose only demand-response hit was a false `dr` and that contains a conventional keyword (`hydrogen CHP`, `hydrogen_gas_turbine`, `drill_diesel`) becomes `'conventional'`. It then appears in the readiness and setup "missing outage data" lists. *Recommended:* accept, since these are real units that can break.
- **G3, where hydrogen goes.** *Recommended:* `'other'`, which needs no UI change. A class of its own would need a new tab, so it is out of scope.
- **G4, `demand response` and `demand-response` move other→dr.** *Recommended:* accept; it is the spelling the keyword means.

## Repo rules

- Branch `outsource/gen-category-word-match` from current `master`. Open exactly one PR. Never force-push. Do not merge it; our coordinator merges.
- PR body starts with the line `Outsourced task F`, then follows `.github/pull_request_template.md`. Mark the body as AI-generated.
- Add one release-note fragment, `doc/release-notes.d/outsource-gen-category-word-match.md`, containing one bullet in this form:

  ```
  * Fix(pypsa-gui): Generators with a "hydro" or "hydrogen" carrier no longer file under demand response; the Time Series page and Properties treat hydro as renewable and hydrogen as other.
  ```

  Never edit `doc/release_notes.md`. See `doc/release-notes.d/README.md`.
- Tests go in the same PR. Do not skip, loosen or delete any existing test.
- Do not commit credentials or `.env` files.

## Done when

- [ ] The seven new tests pass, and each of them fails when run against master's `profile_shapes.py`.
- [ ] The four existing test files named above pass unchanged.
- [ ] The full `pixi run gui-tests` is green locally, or the PR lists each failure that also fails on `master`.
- [ ] The diff touches only the three files listed under "Files to touch".
- [ ] `_RENEWABLE_KW`, `_CONVENTIONAL_KW`, `_DR_KW` and `_gen_category` are still importable from `services.profile_shapes`.
- [ ] One PR on `outsource/gen-category-word-match`, with a body starting `Outsourced task F`.
