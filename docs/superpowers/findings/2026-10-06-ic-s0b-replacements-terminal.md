# IC S0b, D9, D10 gate: findings

**Date:** 2026-10-06. **Branch head:** b2e7364 (`claude/energy-tool-features-research-fdixs0`; master at 8020e64
merged in). **Plan:** `docs/superpowers/plans/2026-10-06-ic-s0b-replacements-terminal.md` (plan PASS round 2, code
PASS round 2, §6).

## What landed

- **S0b.** The finance adapter reads every owner asset's investment through `asset_schema.access.upfront_parts`.
  `AssetFinance.parts` / `AssetPart`, `effective_parts`, a parts ↔ `overnight_cost` consistency check and
  `scale_capex` (one way to scale every purchase; the GS tornado, C5, uses it). A figure back-calculated from
  `capital_cost` is not established (`upfront_only_from_capital_cost`, owner decision 2026-10-06); a typed 0 stays an
  established 0. `cod_by_asset` defaults from `build_year` (`cod_from_build_year`; 0, NaN, non-integer or outside
  1900..2200 is absent).
- **D9.** `FinanceInputs.replacement_rule = "part_lifetimes"`: each part is re-bought in its last service year
  (`cod_year + k·L − 1`, k·L < the axis), GS's timing. One schedule (`finance.replacements.schedule`) feeds the cash,
  the tax vintages, the LCOS and the lifetime check; generated lines keep `source="replacement_capex"` with
  `source_id="<asset>:<part>"`.
- **D10.** `TerminalValueRule(method="remaining_life_annuity")`: GS's `annuity_pv` salvage per part; the LCOS nets
  the asset's own terms under it; the report carries a `terminal_value` block with the per-part terms; the xlsx names
  the method.
- Facade pins, `types.ts` and the finance editor (both options), QA scenario M (6 checks), docs (U1 landing §3,
  coordinating C2 now names `remaining_life_annuity`, IC spec §6.1).

## Evidence

- **The identity (GS `BY_CONSTRUCTION`).** Financial close one year before COD, `capex_phasing = [1]`,
  `analysis_years = H`, no escalation, no tax, WACC = the LP rate: PV(capex + replacements − TV) equals the PV of
  the LP annuities through `run_case` on 400 cases (H 1–40, part lifetimes 1 … 40, rates 0–15 %), worst relative
  error 9.7e-14 (review round 1 P1, re-run round 2).
- **GS salvage parity.** 630 cases from GS's own formulas (`proforma.py:432-436, 455-463`): inverter, PV and storage
  salvage to 1e-12; IC's replacement years equal GS's `range(L, H, L)`.
- **Gate on b2e7364.** Backend 9,512 tests, 0 failed (one run reached 9,494 before the session's run-time limit; the
  last 18 re-run and passed); all 31 QA drivers pass (`qa_investment_case.py` 251/251: the 245 baseline plus
  scenario M); vitest 3,279 tests in 279 files; tsc clean.

## Known limits (disclosed, not defects)

1. The identity is exact for whole-year lifetimes only; a non-whole lifetime is rounded half up and flagged
   `part_lifetime_rounded` (GS refuses it).
2. Under `fixed`, a single-part asset's last `replacement_capex` entry is valued as a full re-purchase by the
   remaining-life terminal value (stated in the report's terminal basis).
3. A capital-cost-only asset with no discount rate anywhere reads `overnight_cost_missing` without the
   `upfront_only_from_capital_cost` reason (the accessor cannot back-calculate without a rate).
4. `finance_case_hash` now includes the parts, so a stored report reads stale once after deploy (provenance only).
5. An infinite and a missing part lifetime hash alike (`_canon` maps non-finite floats to None); open,
   non-binding.

## For U2 (guided study)

- Compile writes the battery as two typed parts (C1) and uses `replacement_rule="part_lifetimes"`,
  `TerminalValueRule("remaining_life_annuity")`, financial close one year before COD, `capex_phasing = [1]` and
  `analysis_years` = the storage lifetime; capex bounds go through `scale_capex`.
