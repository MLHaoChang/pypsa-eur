# PLEXOS import: mapping plan

Status: ready-for-agent (slice 1 plan GO from GK A (29); scope settled by PLEXOS-SCOPE-1)
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
| Node `Load` | `Load` on that bus | Static → `p_set`. Data-file-backed → `loads_t.p_set` over the snapshots. A file that is missing → warning, with the load's static value as fallback in `assumed`. |
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
- **Data files and Variables.** Input is a `.zip` (XML plus the CSV folder) or the XML plus loose CSVs. PLEXOS stores Windows-relative paths (`csv_files\…`); we match them case-insensitively against the given files. A referenced file that is missing → warning; the property falls back to its static `t_data` value, recorded in `assumed`.

## Example models and fixtures

- **PLEXOS-World 2015 Gold V1.1**, Brinkerink & Deane, Harvard Dataverse, doi:10.7910/DVN/CBYXBY, **CC0** ("made available under a Creative Commons CC0 license"). It is 130 MB, 258 nodes and 30,658 generators, saved with PLEXOS 8.300. Fixture: one country cut out of it (a few nodes, lines, ~30 generators, a battery, a hydro storage, three scenarios, one data file trimmed to 48 hours). `tests/gridspine/fixtures/plexos/make_fixture.py` documents how it was cut, and a `LICENCE.md` sits next to it.
- A hand-written mini XML (same schema) for the edge cases the World model lacks: reactance lines, bands, dated rows, a constraint, a reserve, a transformer.
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
- Inputs: an XML file, or a .zip of the XML plus its CSV data files. A missing CSV gives a warning and the static value as fallback. There is no xlsx reader in this package.
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
