# Generic defaults pack

In-tree defaults the guided study seeds its assumptions ledger from (plan "one investment engine,
two faces" §3 rule 4). The pack holds:

- technology costs from technology-data v0.14.0 (DEA), in 2020 EUR;
- finance defaults;
- two illustrative tariffs;
- two synthetic load profiles.

The loader is `loader.py`: `load_defaults_pack(version=None)`. The flat export price helper is
`services/library/export_series.py`.

## Spec decision 20

IC spec decision 20 says "no external data curated in-tree". **This pack amends that decision
for itself only** (owner decision, 2026-10-05). Every other tariff, price series or market data
set still arrives through the import schemas (spec §11.4) and lives in the org Library.

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

The hash is a sha256 over the canonical JSON of the **parsed** content: every value row,
finance, tariffs and profile metadata. For each load profile it covers the sha256 of the
profile's file bytes, after normalising line endings to LF. The hash does not cover the file
location.

`load_defaults_pack()` with no argument loads the latest version.

## Units

Every value row is transcribed in the catalogue's own unit, in `original_value` and
`original_unit`. The loader converts it to the pack unit through the closed `UNIT_CONVERSIONS`
table, and the row keeps both the original value and the conversion factor. The conversions
are:

| From | To | Factor |
|---|---|---|
| EUR/kW | EUR/MW | ×1000 |
| EUR/kWh | EUR/MWh | ×1000 |
| %/year | share/year (the asset schema's `fom_share`) | ×0.01 |

Costs use the asset schema's part vocabulary (S0):

- the battery has a `power` part, priced per MW, which is the inverter;
- the battery has an `energy` part, priced per MWh, which is the storage block;
- PV is one `investment` part, priced per MW.

A field the catalogue does not carry is None, never 0. For example, technology-data books the
battery's FOM on the inverter only, so the energy part has no FOM value.

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
  - stated assumptions, such as the sizing-limit multiple.
- Catalogue rows (DEA, PyPSA-Eur) are `false`.
- A range marked `assumed` (±30 %) is flagged on the range itself, not through this flag.

`DefaultsPack.assumption_rows()` lists every row with its provenance and the pack's id,
version and hash. It is the input for the report's assumptions appendix.

## Tariffs are copied, never referenced

`pack_tariff(id)` returns a deep copy of the tariff. Its `Tariff.pack_hash` is set to
`generic_defaults@<version>:sha256:<hash>`; `parse_pack_stamp` reads that string back.

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
