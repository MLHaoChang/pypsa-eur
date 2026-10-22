# Generic defaults pack

In-tree defaults the guided study seeds its assumptions ledger from (plan "one investment engine,
two faces" §3 rule 4). The pack holds:

- technology costs from technology-data v0.14.0 (DEA), in 2020 EUR;
- from version 2026-10-07, the campus electrical equipment costs (transformers, cables, capacitor
  banks, shunt reactors, STATCOMs, switchgear), transcribed from
  `gridspine/templates/data/campus_assets.yaml`, in 2026 EUR, all illustrative placeholders;
- from version 2026-10-23, those campus rows take cited reference costs in 2025 EUR (Ei's
  *Normvärdeslista elnät 2024-2027*, the Danish Energy Agency's *Technology Data for Energy
  Transport*; `docs/superpowers/research/2026-10-12-campus-asset-reference-costs.md`), with each
  row's source and URL; a value no source covers stays `assumed`;
- from version 2026-10-13, the Energy Hub template technologies (IC S3 seeds): a gas genset (OCGT),
  a diesel genset (diesel engine farm), onshore wind, a fuel cell (LT-PEMFC) and an electrolyser
  (AEC), from technology-data v0.14.0 in 2020 EUR, one `investment` part per MW each;
- from version 2026-10-20, the ammonia technologies the `eh_ammonia_staged` template prices from: the
  Haber-Bosch loop (per MW of NH3 output), the liquid NH3 tank and the type 1 H2 tank, from
  technology-data v0.14.0 in 2020 EUR, one `investment` part each;
- finance defaults;
- regional cost overlays (see [Regional versions](#regional-versions));
- two illustrative tariffs;
- two synthetic load profiles.

The loader is `loader.py`: `from services.library.defaults_pack.loader import load_defaults_pack`. The package `__init__` re-exports nothing. The flat export price helper is
`services/library/export_series.py`.

## Typed cost units (GS-2b)

From version 2026-10-22 (EU) and 2026-10-22+<region> every `values.csv` row carries three typed
columns after `basis`/before `source_technology` (`money_year` replaces `currency_year`):

| Column | Values | Meaning |
|---|---|---|
| `capacity_basis` | `output_electric`, `output_electric_ac`, `output_electric_dc`, `input_electric`, `output_product`, `storage_energy`, `not_applicable` | What the row's capacity measures (the vocabulary of GS-EU's `CapacityBasisCode`; a test keeps them equal). `basis` stays the unit (`per_MW`, `per_MWh`, ...) and must agree: `per_MWh` means `storage_energy`; `lump`, `per_km`, `per_bay` mean `not_applicable`. |
| `includes_grid_connection` | `true`, `false`, `unknown` | Whether the overnight cost already includes the grid connection (the project prices that separately). Never empty. |
| `money_year` | year | The money year of a money row (with `currency`). |

The loader refuses a typed row without them, rows of one technology part that disagree, a
`capacity_basis` that does not fit the row's `basis`, a regional row whose `capacity_basis` differs
from the EU row's, and a typed file with a `currency_year` column. EU DEA rows are `unknown` (the
technology-data v0.14.0 text does not state it per row); campus equipment is `false`.

**Legacy versions.** `LEGACY_VERSIONS` (2026-10-05, -07, -13, -20 and the three 2026-10-21
regional versions) shipped before the typed units and are never edited. They load for provenance
(`pack.typed` is False, `capacity_basis` None); `price_from_pack` on them raises `LegacyPackVersion`,
which names the typed successor. A test pins the set: a new version must be typed.

**Pricing.** `pricing.SCALING_RULES` is the one table, component x capacity basis, to a scale or a
refusal (`CapacityBasisMismatch`, nothing written). A Link's `p_nom` is its `bus0` flow: an
`output_electric*` row is scaled by the efficiency of the electricity bus it delivers to, an
`input_electric` row is not (and needs an electricity `bus0`), an `output_product` row by the
efficiency of the product's bus (`PRODUCT_CARRIER`). A Generator or StorageUnit takes
`output_electric*` at 1; a Store takes `storage_energy` at 1; everything else is refused. The
return value carries `scale`, `currency`, `money_year`, `capacity_basis`,
`includes_grid_connection` and `connection_fee_review` (true unless the row says `false`); the asset
provenance of `overnight_cost` carries the last three. `commercial.grid_connection_double_count`
warns on a row that includes the connection when the project also charges a capacity fee.

## Spec decision 20

IC spec decision 20 says "no external data curated in-tree". **This pack amends that decision
for itself only** (owner decision, 2026-10-05). Every other tariff, price series or market data
set still arrives through the import schemas (spec §11.4) and lives in the org Library.

**Sanctioned exception (owner decision G2, 2026-10-21; GS-2).** Three researched `open_verified`
cost sources may also be transcribed in-tree, as region-tagged versions of this pack (see
[Regional versions](#regional-versions)): Canada Energy Regulator Energy Future 2026
(`cost_canada_cer_ef2026`, Open Government Licence - Canada), UK DESNZ Electricity Generation
Costs 2025 (`cost_uk_desnz_2025`, Open Government Licence v3.0) and the EIA capital-cost reference
plants (`cost_eia_reference_plants`, EIA reuse policy: use with acknowledgment). The exception names
these three sources only. Every other source needs its own terms review and an explicit owner
clearance first. Each regional manifest records the clearance as `owner_clearance: 2026-10-21,
coordinator-relayed (decision G2)`, the licence, its terms URL, the source file and its sha256.

## Versions

One version is one directory, `versions/<YYYY-MM-DD>/`:

| File | What it holds |
|---|---|
| `manifest.json` | The pack id, the version, what it was seeded from, and each load profile with its file, sha256, flags, source and unit. |
| `values.csv` | One row per technology value. |
| `finance.yaml` | The finance defaults. |
| `tariffs.json` | The IC `Tariff` payloads, each with a `meta` block beside it. |
| `load_profiles/*.csv` | The synthetic shapes. |

To change any content:

1. **Add a new version directory.** Never edit a version that has shipped. Old versions stay,
   so a project that copied a tariff from an old version can still be traced back to it.
2. **Pin the new hash** in `tests/fixtures/defaults_pack/pack_hashes.json`, beside the old pins.
   `test_every_version_is_pinned_and_its_hash_matches` fails until the new version is pinned.
   It also fails if a pinned version's content changes.

The hash is a sha256 over the canonical JSON of the **raw files** (`canonical_payload`):

- `values.csv` rows as strings, header first;
- `finance.yaml` and `tariffs.json` as loaded, without `_comment`;
- the manifest without `files`;
- each load profile file's sha256, after normalising line endings to LF.

No model's dump enters the hash, so a model change (for example a new default field on
`TariffItem`) does not re-hash a shipped version. Validation is a separate step. The file
location does not enter the hash.

`load_defaults_pack()` with no argument loads the latest version.

## Regional versions

A regional version is a directory `versions/<YYYY-MM-DD>+<region>/` (today `2026-10-21+ca_federal`,
`+uk`, `+us_federal`). It is an **overlay** on one EU version, not a full pack:

| File | What it holds |
|---|---|
| `manifest.json` | `region`, `currency`, `base_version` and `base_hash` (the EU version it falls back to and that version's hash; a mismatch is refused), each source (id, publisher, URL, file, sha256, licence, terms URL, `owner_clearance`) and the sha256 of `source_cells.csv`. |
| `values.csv` | Only the rows the source supports, with the EU row's key, technology, part, basis and parameter, in the region's own currency (`CAD/MW`, `GBP/MW`, `USD/MW`). Each row's `source` is `<source_id>: <table or cell>`. |
| `source_cells.csv` | The source figures behind each row, with their location in the file. |

Rules:

- **Explicit selection.** `load_defaults_pack(region="ca_federal")` and
  `price_from_pack(..., version="2026-10-21", region="ca_federal")` take the region as an argument;
  nothing infers it from a project. `available_versions()` never lists a regional directory, so
  `load_defaults_pack()` stays the latest EU version and EU projects and pins are unchanged. How a
  project picks its region is a later wiring decision.
- **No fallback across currencies.** A regional row is read as the case's real money, so an EU row in
  2020 EUR would be taken for CAD. A money row the region does not carry is not substituted:
  `pack.value(key)` and `price_from_pack` raise `MissingRegionalRow(key, region, currency)`.
- **Currency-less rows may fall back.** A share, lifetime or efficiency the source does not state
  (the EIA study has no lifetimes; DESNZ and CER state no usable OCGT efficiency basis) comes from
  the EU row, for a part the region prices, and `pack.region_info.row_source[key]` is `eu_fallback`
  (`regional` otherwise).
- **Units.** Conversions stay in the closed `UNIT_CONVERSIONS` table, per currency; none changes
  currency. Mixed money years within one currency stay allowed.
- **Hash.** The regional hash covers the overlay manifest, `values.csv` and the base version's
  hash. It is pinned under `generic_defaults_regional` in `tests/fixtures/defaults_pack/regional_pack_hashes.json`.
- **A new version, never an edit**, as for the EU versions.

What each region carries (technologies: `solar-utility`, `onwind`, `gas-genset`; each with overnight,
`fom_share` and, where the source states one, lifetime):

| Region | Source and cell | Money |
|---|---|---|
| `ca_federal` | CER EF2026 cost data electricity CSV: Province/Territory ON, Scenario CM, Year 2030 | CAD, Dollar Year 2025. Overnight excludes the separate Connection Costs field; FOM includes property and municipal tax, insurance and land. |
| `uk` | DESNZ Annex A, sheet "Technical and Cost Assumptions", 2030 commissioning, Medium. Overnight is pre-development + construction (no infrastructure); `fom_share` is (fixed O&M + insurance) / overnight | GBP, real 2024. Overnight = pre-development + construction; the Infrastructure component is left out because it includes the grid connection, which a project prices separately (the connection share cannot be separated from it). Connection and use of system charges are left out of `fom_share`. |
| `us_federal` | EIA AEO2025 capital cost study (SL-018001 Rev A), Table 1-2 cases 16 (PV, 150 MWac), 13 (onshore wind, 200 MW) and 4 (H-class simple cycle, 419 MW) | USD, 2023 price levels, EPC boundary, no financing cost. The study's capital cost includes generic interconnections (a one-mile transmission line and gas lateral, owner's costs); their share cannot be separated from Table 1-2. |

Regional packs carry technology rows only: the finance defaults (discount rate and the rest) stay the EU base's until a regional finance source is cleared, and `price_from_pack` records the base pack as the discount rate's source. A regional `price_from_pack` returns the region's `currency` and `annualised_cost_per_mw_yr` (no `_eur_` key), and records `currency` on its money fields and `fallback: eu_fallback` on a field taken from the EU row.

Not mapped (no clear key): battery power and energy parts (the sources price a whole battery system
per kW, not the inverter and the storage block), the diesel genset (a diesel engine farm; CER and
DESNZ give turbines or gas engines), electrolysis, fuel cell, ammonia and tanks, rooftop PV, the campus
electrical equipment. Asking a regional pack for any of their money rows raises `MissingRegionalRow`.
The gas genset's efficiency is not transcribed either (heat rates are higher-heating-value or
unstated), so it comes from the EU row, flagged `eu_fallback`. Mapping caveat: the gas genset key is
the EU's small OCGT; the regional rows are the sources' simple-cycle turbines (CER simple cycle, DESNZ
OCGT 300 MW at 30 % load factor, EIA 419 MW H-class), which are larger plants.

## Units

Every value row is transcribed in the catalogue's own unit, in `original_value` and
`original_unit`. The loader converts it to the pack unit through the closed `UNIT_CONVERSIONS`
table, and the row keeps both the original value and the conversion factor. The conversions
are:

| From | To | Factor |
|---|---|---|
| EUR/kW | EUR/MW | ×1000 |
| EUR/kWh | EUR/MWh | ×1000 |
| EUR/kW_NH3 | EUR/MW | ×1000 (technology-data's ammonia-output kW) |
| EUR/MWh_NH3 | EUR/MWh | ×1 (technology-data's ammonia tank, LHV) |
| %/year | share/year (the asset schema's `fom_share`) | ×0.01 |
| EUR | EUR/unit (basis `lump`) | ×1 |
| EUR/km | EUR/km (basis `per_km`) | ×1 |
| EUR/bay | EUR/bay (basis `per_bay`) | ×1 |
| share/year | share/year (a campus `opex_frac`) | ×1 |
| USD/kW, CAD/kW, GBP/kW | USD/MW, CAD/MW, GBP/MW (regional packs) | ×1000 |

Each row carries its own money year in `currency` and `money_year` (`currency_year` in the legacy versions): 2020 for the catalogue rows
(technology-data v0.14.0), the campus library's `price_year` for the campus equipment rows (2026 up
to 2026-10-22, 2025 from 2026-10-23).
The finance defaults and the seed tariffs are in 2020 EUR. No row is converted with a price index,
so a caller that mixes rows of both years converts them itself.

In an investment case the engine reads these costs as real prices in the case's base-year money
(a 2020 row in a case with base year 2027 is used as real 2027 EUR): held flat to the base year,
then escalated from it at the case's rates. Reports and template ledgers say so ("costs in real
2027 EUR (2020 price data)"). It is a convention, not a conversion: no price index is applied.

Costs use the asset schema's part vocabulary (S0):

- the battery has a `power` part, priced per MW, which is the inverter;
- the battery has an `energy` part, priced per MWh, which is the storage block;
- PV is one `investment` part, priced per MW.
- Each campus equipment entry is one `investment` part (technology `<kind>.<id lowercased>`, e.g.
  `transformer.tr_132_33_40`), priced per unit (`lump`: transformers, capacitor banks, reactors,
  STATCOMs), per km (`per_km`: cables) or per bay (`per_bay`: switchgear), with its lifetime and
  `fom_share` (the campus `opex_frac`). Electrical ratings stay in the campus YAML; the manifest
  pins that file's sha256 (`seeded_from.campus_equipment`), and a changed YAML needs a new pack
  version: the parity test compares the YAML with the latest version that pins its sha256.

A field the catalogue does not carry is None, never 0. For example, technology-data books the
battery's FOM on the inverter only, so the energy part's `fom_share` row has no value, status
`not_available` and the note `fom_booked_on_power_part`.

Tariff rates are stored in the IC units: per kWh and per kW-month. Each item's `source_rows`
keep the seed price in EUR/MWh, EUR/MW/month or EUR/month. The loader checks each rate against
`TARIFF_CONVERSIONS`.

A derived value names a formula from the closed `DERIVED_FORMULAS` registry. Today the only
one is `square`: the battery's round-trip efficiency is the inverter efficiency squared. The
loader refuses an unknown formula id. The guided ledger should call `derive()` so that each
formula has one definition.

## The `illustrative` flag

Every row carries a source, a year and an `illustrative` flag.

- `illustrative = true` means the figure is not taken from a cited source; it was chosen at a
  plausible order of magnitude. This applies to:
  - the two seed tariffs;
  - the synthetic load profiles;
  - stated assumptions, such as the sizing-limit multiple;
  - every campus equipment row up to 2026-10-22 (`source = "assumed (gridspine campus_assets.yaml placeholder)"`);
  - from 2026-10-23, a campus row whose value no source covers (`source = "assumed (gridspine campus_assets.yaml)"`).
- Catalogue rows (DEA, PyPSA-Eur) and the cited campus rows from 2026-10-23 are `false`.
- A range marked `assumed` (±30 %) is flagged on the range itself, not through this flag.

`DefaultsPack.assumption_rows()` lists every row with its provenance and the pack's id,
version and hash. It is the input for the report's assumptions appendix.

## Tariffs are copied, never referenced

`pack_tariff(id)` returns a deep copy of the tariff. Its `Tariff.pack_hash` is set to
`generic_defaults@<version>:sha256:<hash>`; `parse_pack_stamp` reads that string back.

The copy is made from the tariff as the file holds it, so editing a loaded pack's objects
cannot yield a stamped, altered tariff. `tariff_is_unchanged(tariff)` says whether a tariff is
still the pack's as shipped (everything but `pack_hash` equal, and a stamp, when present,
naming this pack version); the guided ledger uses it to
mark a row `customised`.

Write the copy inline into `CommercialConfig.import_tariff` and leave `import_tariff_ref` as
`None`. A ref would be resolved in the org Library, which does not hold pack tariffs, and would
fail with `library_ref_stale`. This is owner decision R3.

Each tariff is mapped onto IC items as follows:

| Seed component | IC item |
|---|---|
| Energy bands | `energy`, with TOU periods |
| Network charge | an `energy` item with id `network:energy` (plan §4: network charges are items with ids `network:*`) |
| Demand charge | `demand`, `per_kw_month`, billed on the monthly peak import |
| Fixed charge | `fixed`, `per_month` |
| Export compensation | not an IC tariff item; see below |

Export compensation is stored in `meta.export.price_per_mwh`. Pass that price to
`put_flat_export_series`, which mints a flat Library series for `export_price_ref`.

The two tariffs have these jurisdictions:

- `de_industrial_illustrative` is `DE`.
- `tou_reference_illustrative` is `generic`. It is deliberately free of any jurisdiction, so it
  joins no tax pack.

Both tariffs have `valid_from` 2020-01-01, the money year of the seed, and an open `valid_to`.

## Load profiles

`load_profile_series(id, index, annual_mwh=..., weights=...)` returns MW. The values are
scaled so that the energy, `sum(MW x w)`, is `annual_mwh`:

- `w` is `weights`, the hours each row stands for: pass the network's snapshot weightings
  for representative periods;
- without `weights`, `w` is the index's step in hours, so a 15-minute index works as is. An
  index whose `freq` is not a fixed length (for example `MS`) needs `weights`.

`annual_mwh` is per year, so each investment period is scaled on its own and every period
carries `annual_mwh`. The periods are level 0 of a snapshot MultiIndex, passed as `index` or
as the index of `weights`. On a plain index, a new period starts only where the first
period's timestamps repeat exactly (one weather year reused in every period). Any other
plain index whose timestamps are not increasing is refused with a `ValueError`.

The factors are read on the index's own clock. Pass the site clock: with
`commercial.timezone` set the snapshots are UTC-naive, so convert them first.
