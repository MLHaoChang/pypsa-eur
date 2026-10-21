# Focused EU implementation contract

The owner requested parameter-first European research, reconciliation with the existing library and integration of additional usable data. Baseline: current master at the start of this package; see VALIDATION.json. PR #491 retains the earlier cross-region research and review unchanged.

## This bounded integration

Add an explicit, offline, optional European technology-reference reader under the existing `services/library/` package. Reuse native `PackValue` and `CostPart`. Add an attributed, hash-pinned subset of two current DEA workbooks under that same library. Research artifacts and the reproducible extraction helper live beside this contract.

Admit utility onshore wind, utility fixed-tilt PV, commercial/industrial rooftop PV, 100 MW AEC and 100 MW PEM electricity-to-hydrogen systems. Preserve available central projection years only. No interpolation, country cost calibration, automatic selection or project/default mutation. The reader requires an explicit technology and year and returns source/applicability information together with the native cost part.

Preserve the source cell, row label, central estimate column, workbook SHA-256 and publication/retrieval context. Wind is 2020 EUR; these updated PV sheets are 2025 EUR; renewable-fuel workbook assumptions are 2020 EUR. PV values use AC capacity consistently; electrolyser values use total electrical input. Convert million EUR/MW to EUR/MW, EUR/kW to EUR/MW and percentages to fractions. Derive wind/PV annual FOM share from the same-year, same-basis absolute FOM divided by investment. Keep the source numerator/denominator in the record. Lifetime is plant lifetime, not stack replacement or project horizon.

This is a reference reader and data addition, not a change to investment/finance, gridspine, adequacy or dispatch engine code. It creates no database, router, chatbot tool or remote-fetch permission. Current UI defaults and existing versioned packs remain unchanged. Normal project approvals still govern any future use.

## Admission exclusions

- SOEC lifetime values that are labelled years but resemble operating hours require source clarification. Do not repair the unit by guessing.
- H2 stack replacement, heat/water inputs, VOM, outages and auxiliary boundaries remain separate references; no claim that one cost/efficiency record models the full process.
- Wind total investment includes source-specific connection/land/decommissioning components; PV total investment includes source-specific balance-of-system/connection components. Adding independent versions of those costs may double count.
- Country tariffs need selected utility/customer/date/voltage and a bill fixture. Denmark also requires DKK/currency handling, validity rules and verified terms; no EUR relabelling.
- Commercial templates with restrictive terms are links only. No negotiated PPA quote, derivatives pricing service or unrestricted forward curve is supplied.

## Validation

1. Parse only the two exact, hash-pinned XLSX releases read-only with cached values; no macros, links or formulas execute. Assert exact selected sheet/header/year/estimate cells and workbook licence notices before extracting.
2. Validate every source number/domain, native model and money/capacity basis. Refuse unsupported years/technologies and changed data bytes.
3. Independent known-cell numeric tests: million/kilo scaling, absolute FOM reconstruction, percentage efficiency, original years and AC/input basis. Missing fields stay missing.
4. Test source attribution retention, independent return values, complete candidate selector inventory and immutable defaults-pack hashes.
5. Run the existing defaults/library suites appropriate to this additive package. Report remote CI separately.

## Subsequent integrations

Start with a reviewed Danish DataHub energy-charge subset and DE/NL final transport schedules. Then FR/ES/IT calendars and excess/reactive rules, followed by contract archetypes with permitted text/field rights. Each must pass its own rights, tariff arithmetic, temporal and native-field checks before being advertised as available. Research-ready does not mean importer-ready.
