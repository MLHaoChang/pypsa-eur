# PLEXOS import: mapping plan

Status: done (slices merged: 1 #1603, 2a #1665, 2b #1713, 3a #1728, 3b #1745; slice 4 dropped by PLEXOS-SCOPE-1)
Owner decisions: IMPORTS-1, IMPORT-FLOW-1, HOLISTIC-1, RAW-NAMES-1, PR-SIZE-1, PLEXOS-SCOPE-1

## What a PLEXOS model file is

A PLEXOS input file is one XML document, `<MasterDataSet xmlns="http://tempuri.org/MasterDataSet.xsd">`.
It is a relational database dumped as flat tables. The ones we read:

| Table | Holds |
|---|---|
| `t_class`, `t_collection`, `t_property`, `t_unit` | the schema: class names, the collections between classes (System→Generators, Generator→Nodes, Line→Node From), property names and units |
| `t_object` | every object: name, class, category |
| `t_membership` | parent/child links per collection: generator→node, generator→fuel, line→node from/to, node→region/zone, model→scenarios |
| `t_data` | one property value per row (membership, property, value) |
| `t_band`, `t_date_from`, `t_date_to` | optional band number and validity dates per data row |
| `t_tag` | a data row tagged with an object: a **Scenario** (the row applies only when that scenario is on), a **Data File** (the value comes from that file), or a **Variable** (the value is scaled by that variable) |
| `t_text` | a file name or timeslice pattern on a data row |
| `t_memo_data` | free-text notes on a data row (we keep them as provenance) |
| `t_attribute_data` | per-object attributes such as Node latitude and longitude |
| `t_config` | `Version` and `Saved With` |

The `xlsx` export holds the same tables as sheets (`Objects`, `Memberships`, `Properties`, `Attributes`, …) with names instead of ids.

**Targeted version.** We read by class, collection and property **name**, never by numeric id, since ids change between versions. Tested on a file saved by PLEXOS 8.300. The format has been stable from 7.x to 10.x. The report records `t_config` `Version` and warns when it is below 7 or above 10.

## Mapping

Plain names in the report follow RAW-NAMES-1: "grid nodes", "lines", "generators", "batteries", "hydro reservoirs", "fuels".

| PLEXOS | Our network / sidecar | Notes |
|---|---|---|
| Node | `Bus` | `Voltage` → `v_nom`. There is no default voltage. A node without `Voltage` is listed in `report.assumed`, one entry per bus, as "voltage unknown". A line with reactance that touches such a bus is not converted: it goes to `report.unmapped` ("needs a node voltage to convert reactance"). Links do not need a voltage. Latitude/Longitude attributes → `x`/`y`. `Units` = 0 → the bus is kept but reported as out of service. |
| Node `Load` | `Load` on that bus | Static → `p_set`. Data-file-backed → `loads_t.p_set` over the snapshots. A file that is missing → its rows unmapped plus one warning per file, never a static fallback (GK A ruling A, slice 2b). |
| Region, Zone | sidecar `areas` (name, kind, member buses) | Region `VoLL` → `areas[].voll` and the project's load-shedding cost, if the project has one; otherwise sidecar only. |
| Line with `Reactance` | `Line` | `Max Flow` → `s_nom`, `Resistance`/`Reactance` (pu on 100 MVA) → ohms on `v_nom`. |
| Line without reactance (transport model) | `Link` | `Max Flow` → `p_nom`. `Min Flow` → `p_min_pu = Min Flow / Max Flow`, which is usually −1. `Wheeling Charge` → `marginal_cost`. |
| Transformer | `Transformer` | `Rating`, `Reactance`, `Resistance`. Slice 2b. If not built yet, listed in unmapped. |
| Generator | `Generator` | `Max Capacity` × `Units` → `p_nom`. `Min Stable Factor` (%) or `Min Stable Level` → `p_min_pu`. `Rating Factor` / `Rating` (%, often from a data file) → `generators_t.p_max_pu`. `Start Cost` → `start_up_cost`. `Commission Date` → `build_year`. `Retirement Date` → `lifetime`. `Max Ramp Up`/`Down` → `ramp_limit_up`/`down` (pu/min ×60). |
| Generator marginal cost | `marginal_cost` | (`Heat Rate` × fuel `Price`) + `VO&M Charge`. Heat rate in GJ/MWh, price in $/GJ, giving $/MWh. Currency is kept as written and recorded in `assumed` ("PLEXOS $ read as the project currency"). |
| Generator `Maintenance Rate`, `Forced Outage Rate`, `Mean Time to Repair` | sidecar `outages` (per generator) | Read by adequacy. Not part of `network.nc`. |
| Generator → Fuel | `Generator.carrier` = the fuel name | A generator with no fuel (wind, solar, hydro) gets its carrier from its category name. |
| Generator + `Head Storage` (hydro) | `StorageUnit` | `p_nom` from the generator. `max_hours` = storage `Max Volume` (GWh, energy hydro model) / `p_nom`. `Natural Inflow` → `storage_units_t.inflow`. `Initial Volume` → `state_of_charge_initial`. A storage in the "Level" or "Volume" hydro model (`t_config` "Hydro Model" ≠ Energy) → unmapped with reason. |
| Battery | `StorageUnit` | `Max Power` × `Units` → `p_nom`. `Capacity` (MWh) / `Max Power` → `max_hours`. `Charge Efficiency`/`Discharge Efficiency` (%) → `efficiency_store`/`efficiency_dispatch`. `Initial SoC` (%) → `state_of_charge_initial`. |
| Fuel | `Carrier`, plus sidecar `fuel_prices` | Static or data-file `Price` (a time-varying price → sidecar series). |
| Emission → Fuel `Production Rate` (kg/GJ) | `Carrier.co2_emissions` (t/MWh_th = kg/GJ × 0.0036) | Only for an emission named like CO2. Other emissions → sidecar `emissions`. |
| Model → Horizon | `network.snapshots` | `Date From`, `Step Type`/`Step Count`, `Chronological Date From/To`. Default: hourly over the horizon. `Periods per Day` ≠ 24 → resample, recorded in `assumed`. |
| Data File | time series | CSV layouts read: `Datetime,<name>…`; `Year,Month,Day,Period,<name>…`; `Month,Day,Period` and `Pattern`. The column chosen is the object name. Any other layout → warning plus unmapped row naming the file. |

Every source row lands in exactly one place: the network, a sidecar, `report.unmapped`, or `report.assumed`/`warnings` alongside a mapped value. A unit test counts rows in against rows out per table.

### Goes to `report.unmapped` (with reason)

- Constraint and Decision Variable objects, and every row of theirs ("generic constraint, no PyPSA equivalent yet"). Slice 4 may map simple "Max Capacity Built" and energy limits to `GlobalConstraint`.
- Reserve and the reserve memberships of its generators.
- Purchaser, Contract, Financial Contract, Market, Company, Physical Contract, and bids/offers (`Offer Price`, `Offer Quantity`, `Bid …`).
- Gas, water and heat classes (Gas Node, Gas Field, Water Node, Heat Node, Facility, Commodity, …).
- `Max Capacity Factor` (Day/Month/Year energy limits), `Max Energy`, `Min Energy`: these are energy constraints over a period, not a per-hour cap. Slice 4 option: map to per-period `e_sum_max`.
- Multi-band rows (band > 1). Band 1 is used, and each higher band is listed. Multi-band heat-rate curves are named as such.
- Variable-scaled rows whose Variable is not a plain profile (sampling, conditional, expression actions other than ×, ÷, +, −).
- Execution settings (LT Plan, MT/ST Schedule, PASA, Performance, Diagnostic, Report, Production, Transmission, Stochastic, List). Summarised as one line each, since they are settings rather than assets.
- Any class we do not know: one row per object, reason "class not supported".

## Scenarios, bands, dates, files

- **Scenarios.** A data row tagged with a Scenario applies only when that scenario is on. The user picks a **PLEXOS Model** in the import options (default: the only one, or the first by name, recorded in `assumed`). The scenarios that Model includes (Model→Scenarios memberships) are switched on. Untagged rows are the base. When an on scenario and the base both set a property, the scenario wins. When two on scenarios conflict, PLEXOS uses the `Scenario` priority; we use it too, and warn on a tie. Rows of scenarios that are off go to unmapped ("scenario X not selected"), grouped per scenario so the preview stays short. Open question S1 (below) is whether to also keep them as alternative variants.
- **Bands.** Band 1 only; see above.
- **Dates.** Rows with `Date From`/`Date To` become step series over the snapshots. A static property whose dated rows all fall outside the horizon → the undated row, plus a warning.
- **Data files and Variables.** Input is a `.zip` (XML plus the CSV folder) or the XML plus loose CSVs. PLEXOS stores Windows-relative paths (`csv_files\…`); we match them case-insensitively against the given files. A referenced file that is missing → its rows unmapped plus one warning per file; the static `t_data` value is a placeholder and is never used (GK A ruling A, slice 2b).

## Example models and fixtures

- **PLEXOS-World 2015 Gold V1.1**, Brinkerink & Deane, Harvard Dataverse, doi:10.7910/DVN/CBYXBY, **CC0** ("made available under a Creative Commons CC0 license"). It is 130 MB, 258 nodes and 30,658 generators, saved with PLEXOS 8.300. Fixture (slice 1): node EU-IRL and everything that touches it: 3 nodes, 2 lines, 63 generators, 5 batteries, 7 fuels, all 20 scenarios, 11 models and 3 data-file objects. Ireland has no hydro storage. `tests/gridspine/fixtures/plexos/make_fixture.py` documents the cut; the licence and the hand checks are in that folder's `README.md`. Slice 2 adds a hydro-storage node and a CSV trimmed to 48 hours, for the time series.
- A hand-written mini XML (`edge_cases.xml`, same schema) for the edge cases the World model lacks: reactance lines, bands, dated rows, several tags on one row, a constraint, a reserve and a transformer object, plus one broken row per reader guard.
- Prior art for cross-checking, not vendored: NREL `plexosdb` (BSD-3) and Open Energy Transition's `plexos-to-pypsa-converter`.

## Slices (PR-SIZE-1, GK A)

Engine cage (owner, option A): pypsa is imported only in `gridspine/ingest/plexos_import.py`. `plexos_xml.py` and the registry entry load without pypsa.

1. **XML reader** `gridspine/ingest/plexos_xml.py`: streaming parse of the tables into a name-resolved model (objects, memberships, property rows with scenario, band, date, file and variable tags). Pure, with no network. Tests: fixture counts per table, checked by hand. This PR also carries this plan.
2. **Network** `gridspine/ingest/plexos_import.py` `read_plexos(paths, options) -> ImportResult`: nodes, lines/links, generators, fuels/carriers, batteries, hydro storage, loads, horizon snapshots, data-file time series, plus the registry entry (`plexos`, "PLEXOS model", `.xml .zip`). Base rows only; tagged rows go to unmapped. Codes against the contract text until the core session lands `import_result.py`, then switches to it.
3. **Scenarios, dates, variables**: Model choice, scenario resolution and priority, dated rows, × and ÷ variable profiles.
4. **Optional, per owner**: `.xlsx` export reader, energy limits to `e_sum_max`, transformers, simple constraints.

## Parameters PLEXOS brings (for the expert catalogue, HOLISTIC-1)

Generator: capacity, unit count, minimum stable level, rating profile, heat rate, fuel price, variable O&M, start cost, commission/retirement year, ramp limits, maintenance rate, forced outage rate, mean time to repair. Battery: power, energy, charge/discharge efficiency, initial state of charge. Hydro: reservoir energy, natural inflow, initial level. Line: max/min flow, wheeling charge, reactance. Node: load profile, voltage, coordinates. Region: value of lost load. Fuel: price (static or series), CO2 rate.

## Owner scope (PLEXOS-SCOPE-1, 2026-11-17)

- Scenarios: the user picks one PLEXOS Model in the preview. Rows of other scenarios are listed as "not selected".
- Inputs: an XML file, or a .zip of the XML plus its CSV data files. A missing CSV gives a warning and the static value as fallback (superseded in slice 2b by GK A ruling A: unmapped, no fallback, because the static value is a placeholder). There is no xlsx reader in this package.
- Generic constraints, reserves, energy limits and market objects are reported as unmapped with a reason, plus follow-up issues. Slice 4 is dropped.

## Comments

- 2026-11-17, GK A (29), plan GO for slice 1 @24c8a52a, on these conditions. Where each is met:
  - P1 Hostile XML: any DTD is refused as kind `dtd` (billion laughs, external entity and external DTD tests), and elements are cleared as they stream. Deviation: `defusedxml` is not in the pixi `default` environment that `gridspine-tests` runs in (only `dev` and the GUI requirements have it), and adding it means editing `pixi.toml`/`pixi.lock`, which other PRs touch. So the reader uses `lxml` with `resolve_entities=False`, `load_dtd=False`, `no_network=True` and `huge_tree=False`, and refuses on the root's `docinfo` (internal DTD, external DTD or system URL) before any row is read.
  - P2 Scoped names: objects are keyed by (class, name), collections by (parent, child, name). Test: Node "X" and Generator "X".
  - P3 Fail closed: duplicate ids, dangling objects, unknown classes, collections and properties, collection mismatches, and qualifiers of missing rows go into `broken` with a reason, one test row each in `edge_cases.xml`. A missing required table is refused as `missing_table`, naming the table, with one parametrised test per table.
  - P4 Accounting: per table, in = resolved + broken + not_read. `not_read` is used only for tables the reader does not interpret (t_report, t_message, …), which are also named in `warnings`. Expected counts come from a raw lxml tag count and from `make_fixture.py`'s printout.
  - P5 Values: `abc`, `NaN` and `INF` give value None, with the raw text and a reason.
  - P6 Tags: every tag, with its `t_action` symbol, plus band, dates, texts and memo.
  - P7 Units: each row's unit comes from t_property → t_unit. A test rewrites the unit in the file and reads the new one back.
  - P8 Version: missing, below 7, above 10 and non-numeric versions warn, not refuse.
  - P9 Cage: a subprocess imports `plexos_xml` with `sys.modules['pypsa'] = None`.
  - Later-slice notes are carried into slice 2: zip traversal and size caps; Units = 0 means not built; lifetime = retirement − build year; the sign of Min Flow; VoLL stays in the sidecar.
- Memory: the full 130 MB World file reads in about 12 s with a peak of about 580 MB, because the raw row dicts are held until resolution. That is fine for slice 1; if GK wants it lower, the fix is two-pass streaming.
- 2026-11-17, GK A (29), plan GO for slices 2a and 2b (split and slice-1 condition answers accepted), added conditions:
  - Q1 Units drive conversion: convert from the t_unit slice 1 attaches; unknown unit (BTU/kWh, GW, kV vs V, % vs pu) converted with a test or unmapped with reason. Test one non-default unit.
  - Q2 Reactance base: system base from file when present, else 100 MVA with an assumed row. Hand-check one line.
  - Q3 Load never silently lost: Region Load with Node LPF distributed (sum exact) or unmapped. Test load in = mapped + unmapped.
  - Q4 One node per generator/battery; multi-node → unmapped. Line with from == to → unmapped. Tests.
  - Q5 Heat Rate band 1 only; Base+Incr, multi-fuel, Fuel Offtake → unmapped. Min Stable Factor vs Level precedence stated, tested, assumed row on disagreement.
  - Q6 Percentages/efficiencies in (0,100] before ÷100; else unmapped. p_nom ≤ 0 (not Units=0) → unmapped. Tests.
  - Q7 Unique names per component: refuse or deterministic suffix with assumed row naming both; never overwrite/truncate. Test.
  - Q8 Excel serial dates: epoch 1899-12-30, serial 60 handled; test known date + fractional serial. Horizon: leap year, non-hourly step, snapshot_weightings = step. Tests.
  - Q9 Accounting on both fixtures, per table, rows in = rows out, duplicates fail.
  - Q10 (2b) CSV match case-insensitive basename after \ → /. Short / gappy / duplicate timestamps → warning or unmapped, never 0-fill/ffill. NaN cells unreadable. Tests.
  - Q11 Targeted mutants per refusal and per conversion factor (×0.0036, ÷100, pu→Ω, Min Flow sign), -x each; survivors in PR body.
  - Session: <id> line in each PR body.
- 2026-11-17, core session (project-io) on where the sidecars live: outage rates go on the generators' own columns (`outage_rate_value`, `outage_rate_basis` = "FOR", `mttr_hours`), which adequacy reads through `occurrence.resolve_outage_params`; never `asset_health.json`. Areas go to `plexos_areas.json` and static fuel prices to `plexos_fuel_prices.json`; the core session adds both to `_BUNDLE_FILES`. Fuel prices are also folded into `marginal_cost`, each derived cost listed in `report.assumed` with the heat rate used.
- Slice 2a choices, for review:
  - Min Stable Factor / Level bind only with unit commitment, so they are not set as `p_min_pu` (that would make the unit must-run in an LP). The larger of the two is offered as a `Suggestion`; the rows are listed as unmapped with that reason.
  - A missing generator or battery `Units` takes the file's `t_property` default (GK A, 2026-11-17): 1 builds the unit (assumed row), 0 lists it as not built, and no default gives unmapped "unit count unknown". The same rule applies to a missing battery efficiency (the file's default, otherwise 100 % with an assumed row).
  - Min Stable (GK A ruling): one `Suggestion` per generator with the value in pu and MW; `p_min_pu` stays at its default; both tested.
  - Non-CO2 emission rates go to unmapped, not to an `emissions` sidecar, in 2a.
  - `Chrono Step Type` is not read: the step count is taken as days, with an assumed row, and the attribute is listed as unmapped.
  - Scenario rows are listed per object and scenario (full lists, per the contract), not as counts per scenario. A row tagged with an on and an off scenario counts as on.
  - Horizon: `Chrono Date From` (else `Date From`) × `Chrono Step Count` days × `Periods per Day` (default 24); weightings equal the step in hours. A horizon without a length gives one year, in `assumed`. Other horizon settings are unmapped.
  - Scenario-tagged rows are all unmapped in 2a: "not selected", or "is on in the chosen Model; applied in a later slice". Data-file, dated, variable and band > 1 rows likewise.
- 2026-11-17, GK A (30), plan GO for slice 2b with T1–T7 (the T8/T9 wording was not recorded; the GO is held as T1–T7 + A/B/C, GK A (31) #1713); answers A (1), B (1), C (2). Where each is met:
  - Deviation (ruling A): a referenced data file that is not supplied gives each of its rows unmapped "data file '<path>' not supplied" and one warning per file. There is never a static fallback, because the `t_data` value on a data-file row is a placeholder in real files (PLEXOS-World: Load 0, Rating Factor 100). This replaces "A file that is missing → warning, with the load's static value as fallback in `assumed`" in the mapping table and under "Data files and Variables".
  - B: a file step finer than the snapshots → unmapped, never resampled. C: `NAME,M1..M12` → unmapped "monthly layout not read in this slice".
  - T1: day/month order is decided per file (`plexos_csv._order`): a known Data File `Locale`, else a first field above 12 means day first and a second field above 12 month first; neither or both → the file is not read. A bare date is midnight. Snapshots without a single step are refused.
  - T2: `marginal_cost` series = heat rate (GJ/MWh, after 2a's BTU/kWh conversion) × fuel price series ($/GJ) + VO&M ($/MWh), with an assumed row naming the currency convention. Tested at two timestamps.
  - T3: Rating Factor series cells in [0, 100] are ÷100; one cell outside → the row is unmapped naming it; never clipped.
  - T4: storage Units from the file default (2a rule); Initial Volume through the same GWh/MWh factor and refused above Max Volume; `cyclic_state_of_charge` = False with an assumed row; an inflow series uses the same alignment rules.
  - T5: per-CSV caps `MAX_CSV_BYTES` (256 MiB, loose files and zip members; over it → unmapped + warning) and `MAX_CSV_ROWS`; only the needed columns are parsed (`usecols`); a header naming no needed object is not read further.
  - T6/T7: ledger tests on both fixtures with CSVs given; `plexos_csv` loads with `pypsa` blocked.
  - Data-file rows of properties not read as series (`Max Capacity`, `Max Capacity Factor Month`, …) are unmapped and their placeholder value is never used (`_Converter.take`).
- Slice 2b review (mp-code-review), kept on purpose: the `Month,Day,Period` and `Pattern` layouts in the mapping table are not read in 2b (listed as "layout not read") and move to slice 3 with the monthly layout. Two loose CSVs with one name, or a CSV both in the zip and given loose, still refuse the import: they are input mistakes the user fixes by re-uploading, unlike a missing file. The storage's `Hydro Model` is checked through `t_config` as the table says (≠ Energy → unit unmapped), on top of the volume unit.
- 2026-11-18, GK A (31), plan GO for slice 3a (scenarios + variables), S1–S6, split 3a / 3b (3b gets its own plan GO after 3a merges). Q-a (1): a Rating Factor above 100 % after its multiplier makes the row unmapped, never clipped. Q-b (1): the two `*.adjusted.csv` files trimmed to 48 h from the same CC0 archive. Conditions, where each is met (`tests/gridspine/test_plexos_scenarios.py`):
  - S1: an on scenario's row replaces the base row, which is unmapped "replaced by scenario X"; a row tagged with an on and an off scenario is on.
  - C1: a dated, band > 1 or timesliced scenario row is unmapped ("slice 3b") and never replaces a base row.
  - S2/C2: two on scenarios setting one property of one object → every row of it unmapped "scenarios X and Y both set P; none used", equal values included; one warning per scenario pair. No priority is read (the World file has none).
  - S3: Data File `Filename` rows resolve the same way, so World's Europe Model reads the adjusted files; a missing adjusted file is unmapped (ruling A), the base file is never a fallback.
  - S4/C3: actions × ÷ + -; any other action, a sampled variable (Sampling Method ≠ the file's default), any Variable property besides Profile and Sampling Method, a Variable without a Profile in the chosen Model, an unreadable profile file, a ÷ by 0 at any hour or a non-finite result → the whole row unmapped. A static row times a file profile becomes a series only for the series properties; otherwise unmapped. Several variables apply in tag order, with an assumed row. A Variable's rows count as network when a row it scales is imported (checked after the handlers), else unmapped "no imported row uses it".
  - S5/C4: variables apply before conversion (÷100, [0, 100] check, marginal cost).
  - S6/C5: ledger identity for each of the 11 World Models and the edge fixture. The 2b World tests keep the base files by switching the Europe Model's scenarios off (`_no_scenarios`).

- 2026-11-19, GK A (32), plan GO for slice 3b (dates, timeslices, CSV layouts). Answers Q1 (1), Q2 (1), Q3 (1), Q4 (1). Conditions, where each is met (`test_plexos_pattern.py`, `test_plexos_dates.py`, `test_plexos_timeseries.py`):
  - Q3: a dated or timesliced row that is filed or Variable-scaled, or an undated filed / Variable-scaled row in a group with dated or timesliced rows → the whole group unmapped ("… with a data-file or variable row: not read"). Q4: no scenario priority; ties keep failing closed.
  - P1: H is the hour of day 1–24 (H1 = 00:00–01:00); a snapshot is in the mask when every hour it spans matches; one covered only partly is refused (30 min and 3 h tests).
  - P2: W1 = Sunday, checked on 1 Jan 2017. P3: `,` joins items (OR within a symbol, AND across), `;` joins alternatives; `M1,W2;M3` test on a Jan Sunday, Jan Monday and Mar Sunday. Both readings are an assumed row on every timesliced import.
  - P4: the pattern is the text of the Timeslice's one undated, untagged, band-1 `Include` row; with no `Include` row, the name when it parses, with an assumed row. Several, dated, scenario-tagged or banded pattern rows → the Timeslice is unusable and every group it tags is unmapped. A Timeslice's rows count as network when a row it tags is imported.
  - D1: Date From inclusive at its timestamp; a Date From/To inside a snapshot → group unmapped. D2: a midnight Date To covers that day; a timed one up to and including the snapshot starting at it; an assumed row on every dated import.
  - D3: equal values on a shared snapshot are fine, different values fail closed (dated × dated and dated × timesliced). D4: band ≠ 1 is refused before grouping. D5: all dated rows outside the horizon → the undated row plus a warning; with no undated row → a gap, unmapped. Static "constant" is exact `==`.
  - C1: `Month,Day,Period` step = a day over its periods per day, equal to the snapshot step (finer and coarser refused); ragged days refused; 29 Feb rows used in leap years only. C2: non-finite cells in `NAME,M1..M12` and `Pattern` → that name unmapped.
  - L1: the World fixture's 0 dated / timesliced rows and 0 Timeslice objects is a test; the report and ledger of all 11 World Models are byte-identical to 3a's (compared with 3a's code in a worktree).

## Audit (2026-11-25)

- 2026-11-25 audit: checked merge log for the five PRs; gridspine/ingest has plexos_xml.py, plexos_import.py, plexos_csv.py, plexos_pattern.py.
