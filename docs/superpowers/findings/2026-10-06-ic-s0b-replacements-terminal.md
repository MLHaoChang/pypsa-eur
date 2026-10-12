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

## Known limits (disclosed, not defects; 4, 6, 7 and 8 from the gate assessor)

1. The identity is exact for whole-year lifetimes only; a non-whole lifetime is rounded half up and flagged
   `part_lifetime_rounded` (GS refuses it).
2. Under `fixed`, a single-part asset's last `replacement_capex` entry is valued as a full re-purchase by the
   remaining-life terminal value (stated in the report's terminal basis).
3. A capital-cost-only asset with no discount rate anywhere reads `overnight_cost_missing` without the
   `upfront_only_from_capital_cost` reason (the accessor cannot back-calculate without a rate).
4. **Stored reports do not read stale after deploy.** `finance_case_hash` includes the parts, but it is provenance
   only: `investment_case_runner.staleness` compares the assumptions hash built in
   `routers/results.py::_ic_assumptions`. A report whose result would now change (a two-part battery that read
   `overnight_cost_missing`, a case refused `cod_missing` that now takes its COD from `build_year`, a battery with
   both a typed `overnight_cost` and part columns, where the parts now win) stays "current" until it is re-run.
5. An infinite and a missing part lifetime hash alike (`_canon` maps non-finite floats to None); open,
   non-binding.
6. **The staleness key does not cover the network's own investment columns** (the part columns, `lifetime`,
   `build_year`, `overnight_cost`). The gap predates this work for `overnight_cost` and `lifetime`; it widens here,
   since `build_year` now sets the COD. Follow-up: add the owner assets' investment columns as their own part of
   `assumptions_digest`.
7. **An infinite lifetime blocks the remaining-life terminal value.** Under `remaining_life_annuity`, an owner asset
   left at PyPSA's default `lifetime = inf` makes the terminal value, EBITDA and every project metric not established
   (`terminal_part_unknown:<asset>:<part>`, NPV None). GS instead reports an NPV without the uncomputed salvage
   (`npv_excludes_uncomputed_salvage`). IC's behaviour follows its no-substitution rule (C12), so it is not a
   defect, but the guided study must type every owner asset's lifetime.
8. The terminal value values the initial purchase without contingency, while the capex includes it (stated in the
   report's base basis); the identity holds with `contingency_share = 0`.

## For U2 (guided study)

- Compile writes the battery as two typed parts (C1) and uses `replacement_rule="part_lifetimes"`,
  `TerminalValueRule("remaining_life_annuity")`, financial close one year before COD, `capex_phasing = [1]`,
  `contingency_share = 0` and `analysis_years` = the storage lifetime.
- Every owner asset needs a finite, typed lifetime (limit 7): one left at `inf` makes the case's metrics not
  established, where GS's pro forma reported an NPV without that salvage.
- **Tornado (C5, amended).** CAPEX bounds go only through `finance.case.scale_capex(case, f)`:
  `dataclasses.replace(asset, overnight_cost=…)` on an asset with parts raises `ValueError`. RATE bounds must replace
  `case.lp_basis` (`discount_rate` and `asset_discount_rates`) together with `inputs.wacc_nominal`: the terminal value
  annuitises and discounts on the LP basis, so a WACC-only bound leaves the salvage unchanged (773,725.92 at both 7 %
  and 9 % in the gate probe), whereas GS's rate bound moves it (`findings.py:546-559`).
- The facade does not pin `TerminalTerm` or the report's `terminal_value` keys; pin them in U2 if compile reads the
  salvage from them.

## Gate assessor

**PASS WITH CONDITIONS (9565191).** The decisions are implemented and nothing in scope is missing; 664 backend tests,
`qa_investment_case.py` 251/251, tsc clean, vitest 144/144 in a quiet run (`ParticipantsDesigner` flakes under load,
untouched by this branch, passes alone 3/3). End-to-end probe through the real adapter and `run_case` (two-part
battery with its own rate 0.05, LP rate 0.06, PV 30 y, 25-y axis, capex escalation 2 %): parts 0.4 + 1.2 MEUR,
replacements 2039 / 2049 (power) and 2044 (energy), terminal value 317,888.07 + 642,578.92 + 543,134.80 =
1,503,601.79 EUR by hand, the report block and the LCOS netting correct. Conditions, all documentation: limit 4
corrected and the staleness gap added (limit 6); the infinite-lifetime behaviour (limit 7); C5 amended for
`scale_capex` and the LP-basis rate bounds. Taken in this note and in the coordinating plan.
