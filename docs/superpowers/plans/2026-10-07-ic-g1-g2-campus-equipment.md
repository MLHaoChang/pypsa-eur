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
  (`loader.py:194, 226`); `_BASIS_UNIT` maps each to its overnight unit (`per_km → EUR/km`, `loader.py:164`);
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

- **G-1. A new version, never an edit.** `versions/2026-10-07/` copies every 2026-10-05 file except:
  `manifest.json` (version; a `campus_equipment` entry in `seeded_from` with the campus YAML's path and **sha256**);
  `values.csv` (the 2026-10-05 rows unchanged, then the campus rows); `finance.yaml` (only its comments and
  `currency_year_source`: rows now state their own money year, review B4); and any "2026-10-05" in the copied
  `tariffs.json` / `finance.yaml` comments. Both versions are hash-pinned; 2026-10-05 keeps its pin; the latest
  version becomes 2026-10-07. Two existing tests change with it (review B3): the new manifest is listed in
  `smoke/check_bundle.py` (`test_library_export_series.py:217-218` requires every version's manifest there), and
  `test_energy_hub_class_c_authoring.py`'s fake root gains the 2026-10-07 manifest. The README states that each
  row carries its own `currency_year` (2020 for the catalogue rows, 2026 for the campus rows) and gains the new
  units.
- **G-2. Bases.** `basis` gains `"lump"` (overnight unit `EUR/unit`) and `"per_bay"` (`EUR/bay`); `per_km` already
  exists (`EUR/km`, `loader.py:164`). `UNIT_CONVERSIONS` gains `("EUR", "EUR/unit")`, `("EUR/km", "EUR/km")`, `("EUR/bay", "EUR/bay")`
  and `("share/year", "share/year")`, each 1.0.
- **G-3. Rows.** Per campus entry, `technology = "<kind>.<id lowercased>"` (`kind` ∈ transformer, cable,
  capacitor_bank, shunt_reactor, statcom, switchgear; e.g. `transformer.tr_132_33_40`), one part `investment`, three
  rows: `overnight` (the capex), `lifetime` (`lifetime_a`), `fom_share` (`opex_frac`). `source = "assumed (gridspine
  campus_assets.yaml placeholder)"`, `source_year = 2026`, `currency = EUR`, `currency_year = 2026`,
  `price_basis = real`, `illustrative = true`, `source_technology` = the campus `id`, `note` = the YAML's note where
  it has one; a `label` per row; the three rows share `basis` and `source_technology` (`loader.py:835-838`);
  domains `[0, inf)` overnight, `[1, inf)` lifetime, `[0, 1]` fom_share. The key rule `<kind>.<id lowercased>` is
  this plan's (§8's `transformer.132_33.40mva` was an example); the campus session is told. Transcribed by a one-off
  script whose output is checked in (not shipped). A parity test checks the 2026-10-07 rows against the YAML **as
  pinned by the manifest's sha256**: if the campus YAML changes, the test says "a price change needs a new pack
  version" instead of failing on a number (review note 8).
- **G-4. `ExtraOwnerAsset`** (in `services/finance/case.py`, frozen facade):
  `(name, kind, basis, quantity, parts: tuple[UpfrontPart, ...], build_year, source, source_hash)`.
  - **No solver import (review B1).** `asset_schema.access` imports `services.solver.periodized_costs`, and the IC
    tripwire forbids the finance package from loading the solve stack
    (`test_investment_case_tripwires.py:191-210`). `case.py` names `UpfrontPart` only under `TYPE_CHECKING` and
    reads the parts by attribute (`name`, `upfront_per_unit`, `lifetime`, `fom_share`,
    `derived_from_capital_cost`), so an `asset_schema` `UpfrontPart` is accepted as is and `asset_schema` (another
    session's package) is not edited. A part must be a dataclass (so `_canon` hashes its fields, not its repr) with
    those attributes; otherwise `extra_asset_invalid:<name>:parts`, never a bare `AttributeError`.
  - `__post_init__` raises `ValueError` naming a code: `extra_asset_invalid:<name>:<field>` when `name` is empty,
    `kind` is not one of the six, `basis` not `lump | per_km | per_bay`, `quantity` not finite and > 0 (and not a
    whole number for `lump` / `per_bay`), there is no part, `build_year` is outside 1900..2200, `source_hash` is not
    16 hex characters, or a part's lifetime is None, NaN, infinite or below 1 year, its `upfront_per_unit` is not
    finite or below 0, or its `fom_share` is outside [0, 1] (review B5; §8: every part needs a finite typed
    lifetime).
- **G-5. Into the case.** `build_finance_case(..., extra_assets=())`; each extra asset becomes one `AssetFinance`
  appended after the network assets:
  - `component = "campus:<kind>"`, `carrier = None`, `lifetime_years` = the longest part lifetime (all finite by
    G-4);
  - `parts` = one `AssetPart` per `UpfrontPart` with `overnight_cost = upfront_per_unit × quantity` and the part's
    lifetime and `fom_share`; `overnight_cost` = their sum (the S0b check holds by construction);
  - a part with `derived_from_capital_cost=True` is refused `extra_asset_derived_upfront:<name>` (S1: a
    capital-cost-derived upfront is not established; here it is a caller error, so it refuses).
  - **Money year (review B4).** Upfront costs are in the case's `fin.currency` and `fin.currency_year`; the caller
    converts. The report block states it; the route builder must refuse or convert when the campus library's
    `currency` / `price_year` differ from the case's.
  - **Incentives (review B2).** `build_incentives` excludes extra assets before its carrier checks (they would read
    `asset_carrier_missing`, or enter an ITC/grant basis when no `asset_classes` are set), disclosed
    `incentive_excludes_extra_asset:<name>`.
  - **Tax (review B8).** An extra asset's depreciation class comes from `fin.depreciation_class_by_asset[name]`; with
    none, post-tax reads `tax_input_missing:depreciation_class:<name>` (the existing rule, no default class).
  - **Lifetime under `replacement_rule="fixed"`.** The existing `asset_lifetime_short` rule applies (a 20-y STATCOM on
    a 25-y axis is refused unless a `replacement_capex` entry names it); under `part_lifetimes` it is replaced.
  - **Field.** `FinanceCase.extra_assets: tuple[ExtraOwnerAsset, ...] = ()`, appended last; `_canon` omits exactly
    `FinanceCase.extra_assets == ()` (no generic empty-tuple rule, which would re-hash every case through `flags`,
    `templates`, `parts`), so a case without extras keeps its S0b hash.
- **G-6. Fixed O&M (review B6).** Network FOM reaches the case from `n.statistics.fom` as a ledger line
  (`value_flows.py:105`); an extra asset has none, so the adapter adds one `TemplateLine` (`services/finance/case.py`)
  per extra asset and template: `TemplateLine(key=f"extra_asset_fom:{name}", stream="fom", amount=−Σ fom_share ×
  overnight_cost, esc_class="opex", source="extra_asset_fom", source_id=name, money_year=base_year, period=k)`.
  `stream="fom"` because the report turns it into a `CashflowLine` whose `value_stream` is a closed set
  (`models/finance.py:335-342`; `fom` maps to `opex`, `cashflow.py:38`). It is appended **after** `_scale`, so an annualised template does
  not multiply it (`finance_case.py:1478`), and it never enters the counterfactual. (`fom_share` is never None, G-4.)
- **G-7. COD (review B7, §8 compares years).** The case COD comes from the network owner assets as today
  (`_cod`). An extra asset with a typed `fin.cod_by_asset[name]` must equal the case COD (`cod_mismatch`
  otherwise, `timeline.py:78-84`). Without one it is compared **by year**: `build_year` = the case COD's year is
  accepted at the case COD (`cod_from_build_year:<name>`); an earlier year is `cod_mismatch`; a later one is
  `extra_asset_staged_build:<name>` (until P5). An extra is taken at the case COD within its build year (stated in
  the `cod_from_build_year` flag and the report block); a later year is never moved. Extras are compared by year where
  network assets are compared by date, as §8 words it. The check runs after `_cod`.
- **G-8. Counterfactual.** Extra assets never enter the counterfactual (default A): the counterfactual is built from
  the network as today; the extra assets are only on the project side.
- **G-9. Refusals.** A name equal to an owner-owned network component, or duplicated among the extras, is refused
  `extra_asset_duplicates_network_asset:<name>` / `extra_asset_duplicate:<name>`. A `transformer` or `cable` extra
  when the owner also owns a network Transformer or Line (investable, `finance_case.py:163-164`) is flagged
  `extra_asset_may_double_count:<name>` (not refused: names differ, the overlap is a judgement).
- **G-10. Report and hash.** The report payload gains `extra_assets` (per asset: name, kind, basis, quantity, the
  overnight cost **read from `case.assets`** so `scale_capex` is reflected, parts, build year, source, source_hash,
  money-year statement); the xlsx About sheet lists them. There is no frontend change, so the "own heading" exists in
  the payload and the xlsx only; readers use `.get` (old payloads lack the key). The case hash covers them.
  Replacement lines and terminal-value terms of extra assets carry their names (S9). The terminal basis text says the
  extra assets' terms are valued at the case's LP basis but were never charged by the LP; GS's by-construction
  identity does not hold with extras.
- **G-11. Facade.** `ExtraOwnerAsset` and the new `build_finance_case` keyword join the frozen facade (signature and
  field pins; U1 landing §3 updated).
- **G-12. For U2 and the route.** "Latest" now carries 81 more technologies on `lump` / `per_km` / `per_bay`: U2
  filters by basis, and looks a stamped tariff's pack up by its stamp version (`tariff_is_unchanged` against the latest
  pack returns False for a 2026-10-05 stamp, `loader.py:463-470`). The future campus route must put the extra assets
  and their `source_hash` into the assumptions digest (`investment_case_runner.py:118-129`), or a changed campus study
  will not mark a report stale.

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
- Campus money rows carry `currency_year` 2026; catalogue rows keep 2020.
- The existing pack tests pass, including `check_bundle` listing both manifests and the class-C authoring fake root.

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
- COD with a network COD of 2027-07-01: `build_year` 2027 → accepted at 2027-07-01 (`cod_from_build_year`); 2026 →
  `cod_mismatch`; 2028 → `extra_asset_staged_build`; a typed entry ≠ the case COD → `cod_mismatch`.
- FOM with an annualised template and a two-period network: one unscaled line per period; none in the counterfactual.
- Incentives: with and without `asset_classes`, the extras are excluded (flagged) and the network battery's incentive
  is unchanged. Tax: missing class → `tax_input_missing:depreciation_class:<name>`; set → depreciated.
- Under `fixed`: a 20-y STATCOM on a 25-y axis → `asset_lifetime_short`; with a `replacement_capex` entry → accepted.
- Refusals and validation: each `extra_asset_invalid:<name>:<field>`, derived upfront, duplicate names, a name equal to
  a network owner asset; `extra_asset_may_double_count` with an owned network Line.
- Importing `services.finance` still does not load `services.solver` (the tripwire); an `ExtraOwnerAsset` built from a
  real `asset_schema.access.UpfrontPart` works (the test may import `access`); a non-dataclass part or one missing a
  field is `extra_asset_invalid:<name>:parts`.
- Hash stability: one fixture case's `finance_case_hash` pinned to its value on master 546f2cb.
- Report `extra_assets` block and the xlsx row; the case hash changes when an extra asset changes.
- Facade pins.

## 5. Process

Per WP: tests first, the implementation, an independent reviewer until PASS, each round in §6. Then the gate: the
full backend suite, the QA drivers (a `qa_investment_case.py` scenario N for extra assets), vitest and tsc (no
frontend change is planned; they guard the types parity), a findings note, an independent assessor. One PR,
owner-merged.

## 6. Review record

- **Plan round 1 (71fa630): PASS WITH CONDITIONS.** §2 facts confirmed (81 entries: 29 transformers, 12 cables,
  9 capacitor banks, 9 reactors, 9 STATCOMs, 13 switchgear; unique ids; the hand numbers). B1 `UpfrontPart` import
  loads the solve stack (tripwire); B2 `carrier=None` is not "no incentive"; B3 two existing tests need the new
  manifest; B4 mixed money years unstated; B5 part lifetimes unvalidated; B6 the FOM line unspecified (closed
  streams, `_scale`); B7 COD compared by date, §8 by year; B8 tax class for extras. All taken into G-1 … G-12 and the
  tests, plus notes: an explicit `extra_assets` field hashed only when non-empty, the key rule recorded, a
  may-double-count flag, overnight read from `case.assets`, the terminal basis text, validation codes, row domains,
  the sha256-pinned parity test, U2 and route notes.
- **Plan round 2 (9b4cce4): PASS WITH CONDITIONS.** B1 sound (the tripwire's runtime and AST checks pass; the facade
  pins compare `str(signature)` and never evaluate annotations); G-7 consistent with `_cod`; the G-6 placement right;
  the targeted `_canon` omission hash-stable. C1 G-6 named `CashflowLine` for `TemplateLine`. Taken, with the notes:
  parts validated as dataclasses with the attributes, a real-`UpfrontPart` test, the COD wording, a targeted `_canon`
  rule, a hash pinned to master.
- **Code round 1 (153a8ab): PASS.** The transcription exact on all 81 entries (the reviewer's own script); 2026-10-05
  byte-identical and its pin unchanged; the new pin equals the loader hash. End to end through `run_case`: capex with
  contingency (1.15 × 2,115,000), the FOM line per year with escalation and the 2027-07-01 COD, the capacitor bank's
  replacement and every extra's terminal term by hand, the counterfactual identical, the battery LCOS unchanged, tax
  missing vs set, the may-double-count flag with an owned Transformer. The hash without extras recomputed on a
  546f2cb archive (64e7a34307ea1af3). The four readings accepted. Taken after it: the scaled part cost in the payload,
  the COD sentence when a typed date overrides `build_year`, `asset_finance()` pinned, `:` refused in names. Noted
  for U2: `scale_capex` does not move an extra's FOM line (as for network FOM).
