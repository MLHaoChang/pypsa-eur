# SAM "Single Owner" oracle — provenance (IC P4)

Plan: `docs/superpowers/plans/2026-09-30-edge-investment-case-p4.md` § Fixtures and oracles.

**Generator:** `generate_sam_cases.py` (this folder), run in a developer venv with
**PySAM 7.1.1.post1** (`nrel-pysam`, BSD-3-Clause — `SAM_LICENSE`), SSC version 306.
PySAM is not an app dependency and the script is not run in CI; `test_sam_fixtures.py`
pins the script's and the profile's sha256 recorded in every case (`provenance`), so a
changed generator without regenerated fixtures fails.

**Files** (all **self-authored inputs, tool-generated outputs**):

| File | What |
|---|---|
| `gen_profile.csv` | the one synthetic 8,760-hour generation profile (kW): a clear-sky PV shape × a seasonal factor × a seeded daily cloud factor (`gen_profile()`, seed 1, 100 MW) |
| `s1.json` | all-equity, SL-20 federal and state, 21 % + 7 %, PPA 10 ¢/kWh +1 %/yr, O&M per kW-yr and fixed at inflation 2.5 % + escalation 1 %, degradation 0.5 %/yr, 25 years |
| `s1b.json` | S1 solving the PPA price for an 11 % after-tax IRR in year 20 (`ppa_soln_mode=0`) |
| `s2.json` | DSCR-sculpted debt (1.3, 18 yrs, 7 %, fee 2.75 %, DSRA 6 months, reserve interest 1.75 %), MACRS-5 90 % + SL-20 10 %, federal bonus 100 % on MACRS-5, state bonus 0 |
| `s2c.json` | S2 with `dscr_limit_debt_fraction` on, `dscr_maximum_debt_fraction` 60 % — the sculpted debt capped at 0.6 · TIC · (1 + fee), its service scaled pro rata (WP4.2b) |
| `s3.json` | gearing 60 % of TIC (fee 0, DSRA 0), 15 yrs, 6 %, a one-year moratorium inside the tenor; federal ITC 30 % (federal basis reduced, state not); MACRS-5 100 %; salvage 10 % |
| `s3f.json` | S3 with a 2.75 % closing fee — sizes the recorded gearing-with-fee deviation |

Each JSON: `provenance` (versions, date, shas, licence), `sam_inputs` (the **full**
`Singleowner` input export; 8,760-long arrays replaced by `{sha256_16, len}` — the
profile is `gen_profile.csv`, the others are SAM defaults), `outputs.scalars`,
`outputs.arrays` (every `cf_*` array of `analysis_period + 1` entries), `deviations`.

**Zeroed SAM defaults** (not modelled in P4 — plan "Zeroed defaults"): property tax,
insurance, working-capital / receivables / equipment reserves, reserve interest (except
S2), construction financing cost, debt closing and other financing costs, closing fee
and DSRA (except where a case sets them), moratorium (except S3/S3f), salvage (except
S3/S3f), PTC, ITC (except S3/S3f), capacity payments, land lease, the default
depreciation mix and bonus, TOD factors (all 1). `en_electricity_rates=1` (needed by
`ppa_soln_mode=0` standalone; harmless with generation ≥ 0).

**Units (SAM):** energy kWh; `ppa_price_input` $/kWh; `ppa_price`, `lcoe_nom`,
`lcoe_real` ¢/kWh; IRRs in %; money in $. `sam_case.py` converts to P4 units
(currency, MWh, currency/MWh, fractions).
