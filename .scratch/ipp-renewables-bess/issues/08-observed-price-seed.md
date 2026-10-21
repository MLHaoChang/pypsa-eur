# 08: Observed price seed (GS-4): SMARD DE-LU and Energinet DK1, 2024

Status: ready-for-human

**Resumed (owner PACE-2, 2026-10-21):** the pause was lifted. The PR opened after #515 and #518 merged. This ticket is built and awaits review.

## What

Owner Q-GS4 = A (2026-10-21): ship an attributed historical seed with the IPP example.

- **Series:** DE-LU 2024 hourly day-ahead prices from SMARD and DK1 2024 from Energinet, minted as `ipp_da_smard_2024` and `ipp_da_dk1_2024`. They sit beside `ipp_da_*` and never replace those series.
- **Default:** the shipped series fill nothing, so the template's default price stays illustrative.
- **Fetching:** a script fetches them once and the files are pinned by hash. Nothing is fetched at run time.
- **Attribution:** "Bundesnetzagentur | SMARD.de" and "Source: Energinet (www.energidataservice.dk)", both CC BY 4.0, plus a data-changed note wherever provenance shows.
- **Spec:** record that the owner's 2026-10-21 clearance supersedes Q8 for these two sources (spec §2.3).

## Gatekeeper plan (approved, option A, with three conditions)

The basis rule table refuses 2024-dated series against the 2030 finance block. So the series are re-dated onto the 2030 calendar: hour of year kept, 29 Feb dropped, UTC kept, so weekdays shift by one day. They are labelled as 2030 real money (assumed).

1. **Price–weather correlation:** say whether re-dating keeps price and weather together. It does not. The description and the ledger say "price–weather correlation not preserved: prices are 2024, the profiles are synthetic".
2. **Two ledger facts:**
   - the observed values with their attribution (`source: observed`);
   - a separate `assumed` row: "re-dated to 2030, taken as 2030 real money".
3. **First failing test:** the hour-for-hour re-dating identity (29 Feb dropped, UTC kept).

## Built on the branch

| File | What it holds |
|---|---|
| `project_templates/fetch_ipp_market_series.py` | GET-only fetch script; run once, writes `eh_ipp_hybrid/sources/*.csv` |
| `eh_ipp_hybrid/sources/smard_de_lu_2024.csv` | 8784 h, sha256 `97c56c7e…0ab8`: mean 78.51 EUR/MWh, 457 negative hours, min −135.45, max 936.28 |
| `eh_ipp_hybrid/sources/energinet_dk1_2024.csv` | 8784 h, sha256 `f90ea159…a37f`: mean 70.64 EUR/MWh, 375 negative hours |
| `eh_ipp.py` | `OBSERVED`, `observed_series` (re-date), `observed_stats`; `series_meta` entries (no targets); the two kinds of ledger row; docstring and study note |
| `eh_ipp_hybrid/price_series/{smard,dk1}_2024.csv` | the re-dated series |
| `meta.json`, `eh_template.json` | regenerated |
| `tests/test_eh_ipp_observed_prices.py` | re-date identity, hash pins and completeness, attribution and `basis_conflicts`, default stays illustrative, ledger rows, live: create mints all five → swap the export to SMARD → it binds → solve |
| `REUSE.toml` | CC-BY-4.0 annotations naming SMARD and Energinet as copyright holders |
| spec §2.3 | the Q8 supersession note |
| release note | `claude-ipp-gs4-observed-prices.md` |

`test_eh_ipp_observed_prices.py`, `test_eh_ipp.py` and `test_template_price_series.py` pass locally: 67 tests, live solve included.

## Done before the PR

1. Master was merged in after #510, #515 and #518. The sidecars regenerate byte-identical.
2. The wider regression passes: templates, project flow, ledger filing, site-card parity, bundle sidecars and pins, revenue streams, curtailment by cause.
3. E1 (session_01S7WJfjiHwyW6t83C5Pn37n) to be told about the new files and the re-dating, which bears on the SERIES_NOTES `currency_year`.
