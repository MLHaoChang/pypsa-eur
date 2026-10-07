# IC G1, G2: campus equipment in the defaults pack and in the investment case

**Date:** 2026-10-07. **Session:** the IC session. **Base:** master at 546f2cb (S0b/D9/D10, #90, merged).
**Agreed shapes:** `docs/superpowers/plans/2026-10-05-ic-u1-engine-landing.md` §8 (owner-approved 2026-10-07, sent to
the gridspine campus session). This plan implements them; it does not change them.

## 1. Scope

| WP | What |
|---|---|
| **G1** | A new defaults-pack version `2026-10-07` = 2026-10-05 unchanged plus the campus equipment cost rows; the loader gains the bases `lump` and `per_bay` |
| **G2** | `ExtraOwnerAsset` and `build_finance_case(..., extra_assets=())`: campus-chosen equipment as owner capex in the same case |

Out of scope: the investment-case route reading the latest campus study (default B in §8; who builds it is the
campus session's answer), the campus mapping from `_investment` rows (campus side, §8), staged builds (P5).

## 2. Facts (master 546f2cb)

- **Pack layout.** One version is one directory `services/library/defaults_pack/versions/<YYYY-MM-DD>/` (manifest,
  `values.csv`, `finance.yaml`, `tariffs.json`, `load_profiles/`), hash-pinned in
  `tests/fixtures/defaults_pack/pack_hashes.json` (D2). `load_defaults_pack()` with no argument loads the latest
  version (`loader.py:1093`); no production caller passes no argument today (only the README documents it).
- **Bases are closed.** `PackValue.basis` and `CostPart.basis` are `Literal["per_MW", "per_MWh", "per_km"]`
  (`loader.py:194, 226`); `_BASIS_UNIT` maps each to its overnight unit (`per_km → EUR/km`, `loader.py:165`);
  `_cost_parts` refuses a part whose overnight unit disagrees with its basis (`loader.py:835-842`).
  `UNIT_CONVERSIONS` is a closed map of (catalogue unit, pack unit) → factor (`loader.py:146-154`).
- **Campus library** (`gridspine/templates/data/campus_assets.yaml`, merged with #83): 81 entries in six lists
  (`transformers`, `cables`, `capacitor_banks`, `shunt_reactors`, `statcoms`, `switchgear`). Every entry has an `id`,
  a capex (`capex_eur`, or `capex_eur_per_km` for a cable; switchgear's `capex_eur` is per bay, the YAML's header
  and one entry's note say so), `opex_frac` (fixed opex per year as a share of capex) and `lifetime_a`; every number
  is `{value, source: assumed}`. The library has `currency: EUR`, `price_year: 2026`, `discount_rate: 0.07`.
- **Finance side.** `AssetFinance(name, component, overnight_cost, lifetime_years, carrier, parts)`, `AssetPart`,
  `effective_parts`, `scale_capex`, the `__post_init__` consistency check (`services/finance/case.py`);
  `build_finance_case(n, cfg, fin, *, result_df, lost_load=None, owner=None)` (`services/results/finance_case.py`);
  `_cod` (one COD for all owner assets; `cod_from_build_year`); the counterfactual is built from the network without
  the owner's investable assets (C13); `finance_case_hash` walks the case's dataclass fields.
  `asset_schema.access.UpfrontPart(name, upfront_per_unit, lifetime, fom_share, derived_from_capital_cost=False)`.
- **FOM.** For network assets, fixed O&M reaches the case through the LP's cost rows (the ledger), not from
  `fom_share`; an extra asset has no LP row, so its FOM must be added by the adapter.

## 3. Conventions

- **G-1. A new version, never an edit.** `versions/2026-10-07/` copies every 2026-10-05 file byte for byte except
  `manifest.json` (version, a `campus_equipment` entry in `seeded_from`) and `values.csv` (the 2026-10-05 rows
  unchanged, then the campus rows). Both versions are hash-pinned; 2026-10-05 keeps its pin. The latest version
  becomes 2026-10-07; every existing test that pins 2026-10-05 names it explicitly.
- **G-2. Bases.** `basis` gains `"lump"` (overnight unit `EUR/unit`) and `"per_bay"` (`EUR/bay`); `per_km` already
  exists (`EUR/km`). `UNIT_CONVERSIONS` gains `("EUR", "EUR/unit")`, `("EUR/km", "EUR/km")`, `("EUR/bay", "EUR/bay")`
  and `("share/year", "share/year")`, each 1.0.
- **G-3. Rows.** Per campus entry, `technology = "<kind>.<id lowercased>"` (`kind` ∈ transformer, cable,
  capacitor_bank, shunt_reactor, statcom, switchgear; e.g. `transformer.tr_132_33_40`), one part `investment`, three
  rows: `overnight` (the capex), `lifetime` (`lifetime_a`), `fom_share` (`opex_frac`). `source = "assumed (gridspine
  campus_assets.yaml placeholder)"`, `source_year = 2026`, `currency = EUR`, `currency_year = 2026`,
  `price_basis = real`, `illustrative = true`, `source_technology` = the campus `id`, `note` = the YAML's note where
  it has one. Transcribed by a one-off script whose output is checked in (the script is not shipped); a test reads
  the YAML and asserts every entry's three numbers equal the pack's, so the two cannot drift silently.
- **G-4. `ExtraOwnerAsset`** (in `services/finance/case.py`, frozen facade):
  `(name, kind, basis, quantity, parts: tuple[UpfrontPart, ...], build_year, source, source_hash)`; validated in
  `__post_init__`: `kind` in the six, `basis` in `lump | per_km | per_bay`, `quantity > 0` and finite, at least one
  part, `build_year` a plausible year (the S0b bounds 1900..2200), `name` non-empty.
- **G-5. Into the case.** `build_finance_case(..., extra_assets=())`; each extra asset becomes one `AssetFinance`
  appended after the network assets:
  - `component = "campus:<kind>"`, `carrier = None` (no incentive), `lifetime_years` = the longest finite part life
    (None if none);
  - `parts` = one `AssetPart` per `UpfrontPart` with `overnight_cost = upfront_per_unit × quantity` and the part's
    lifetime and `fom_share`; `overnight_cost` = their sum (the S0b check holds by construction);
  - a part with `derived_from_capital_cost=True` is refused `extra_asset_derived_upfront:<name>` (S1: a
    capital-cost-derived upfront is not established; here it is a caller error, so it refuses).
- **G-6. Fixed O&M.** The adapter adds, per extra asset, a fixed-opex template line of `Σ fom_share × overnight_cost`
  per operating year, payer the owner, `source = "extra_asset_fom"`, `source_id = <name>`, escalated by the `opex`
  class like any fixed opex. A part with `fom_share = None` reads `fom_share_missing:<name>:<part>` (opex not
  established for that line).
- **G-7. COD.** One COD rule for all owner assets (S0b `_cod`): an extra asset's COD is `fin.cod_by_asset[name]`, else
  1 January of `build_year` (`cod_from_build_year:<name>`). A COD different from the network assets' COD is
  `cod_mismatch` when earlier and `extra_asset_staged_build:<name>` when later (until P5); never moved.
- **G-8. Counterfactual.** Extra assets never enter the counterfactual (default A): the counterfactual is built from
  the network as today; the extra assets are only on the project side.
- **G-9. Refusals.** A name equal to an owner-owned network component, or duplicated among the extras, is refused
  `extra_asset_duplicates_network_asset:<name>` / `extra_asset_duplicate:<name>`.
- **G-10. Report and hash.** The report gains `extra_assets` (per asset: name, kind, basis, quantity, overnight,
  parts, build year, source, source_hash) under its own heading; the xlsx About sheet lists them; the case hash covers
  them (a field of the case). Replacement lines and terminal-value terms of extra assets carry their names, as for any
  part (S9).
- **G-11. Facade.** `ExtraOwnerAsset` and the new `build_finance_case` keyword join the frozen facade (signature and
  field pins; U1 landing §3 updated).

## 4. Work packages, tests first

### WP-G1 (G-1 … G-3)
Tests (`tests/test_library_defaults_pack_campus.py`):
- Both versions load; 2026-10-05's pin is unchanged; 2026-10-07 has its own pin; every 2026-10-05 row is present
  unchanged in 2026-10-07.
- `cost_parts("transformer.tr_132_33_40")` is one `investment` part, basis `lump`, overnight 1,800,000 EUR/unit,
  lifetime 40, `fom_share` 0.015; a cable is `per_km` (CB_33_AL95: 90,000 EUR/km), switchgear is `per_bay`
  (SG_132_31p5: 450,000 EUR/bay).
- The YAML ↔ pack parity test over all 81 entries; every campus row is `illustrative` with the stated source.
- The loader refuses a `lump` part whose overnight unit is not `EUR/unit` (the existing basis/unit rule extended).
- The existing pack tests pass, naming 2026-10-05 where they pin it.

### WP-G2 (G-4 … G-11)
Tests (`tests/test_finance_case_extra_assets.py`):
- A solved edge network with an owner battery plus two extra assets (a transformer, lump, quantity 1; a cable,
  per_km, quantity 2.5 km): the case has both as `AssetFinance` with the right overnight (1.8 MEUR; 2.5 × 90,000),
  components `campus:transformer` / `campus:cable`, no carrier.
- Capex: the project capex row includes them (× (1 + contingency)); the counterfactual is unchanged with and without
  extras (G-8).
- FOM: the `extra_asset_fom` line equals Σ fom_share × overnight per year, escalated by `opex`.
- Replacements and TV under `part_lifetimes` + `remaining_life_annuity` include the extra parts (a 25-y capacitor
  bank on a 30-y axis is replaced in its last service year; its remaining life is valued).
- COD: `build_year` = the network COD year → accepted with `cod_from_build_year`; earlier → `cod_mismatch`; later →
  `extra_asset_staged_build`.
- Refusals: derived upfront, duplicate names, a name equal to a network owner asset, an invalid kind/basis/quantity.
- Report `extra_assets` block and the xlsx row; the case hash changes when an extra asset changes.
- Facade pins.

## 5. Process

Per WP: tests first, the implementation, an independent reviewer until PASS, each round in §6. Then the gate: the
full backend suite, the QA drivers (a `qa_investment_case.py` scenario N for extra assets), vitest and tsc (no
frontend change is planned; they guard the types parity), a findings note, an independent assessor. One PR,
owner-merged.

## 6. Review record

(empty)
