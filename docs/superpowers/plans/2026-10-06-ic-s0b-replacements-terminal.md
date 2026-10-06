# IC S0b, D9, D10: the finance read of the asset schema, part replacements, remaining-life terminal value

**Date:** 2026-10-06. **Session:** the IC session. **Base:** master at d6cb42b (S0 #78, U1 #81 and its
follow-up #85 merged).
**Decisions this implements:** D8, D9 and D10 of `docs/superpowers/plans/2026-10-05-ic-u1-engine-landing.md` §2,
and S0b of the coordinating plan `docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md` §10
(compile rules C1 and C2 of its §4). U2 (the guided study rewire) depends on these.

## 1. Scope

| WP | What | Decision |
|---|---|---|
| **S0b-1** | `results/finance_case._assets` reads every owner asset's investment through `asset_schema.access.upfront_parts`; `AssetFinance` carries the parts | D8, C1 |
| **S0b-2** | `cod_by_asset` defaults from the asset's `build_year` | D8 |
| **D9** | `FinanceInputs.replacement_rule = "part_lifetimes"`: each part is replaced at the end of its lifetime, at its current upfront cost | D9 |
| **D10** | `TerminalValueRule(method="remaining_life_annuity")`: GS's `annuity_pv` salvage, computed by the engine | D10, C2 |

Out of scope: staged builds (P5), the campus extra-asset hook (G2, after agreement with the campus session),
the campus defaults-pack rows (G1), and any LP change.

## 2. Facts this plan rests on (master d6cb42b)

- `asset_schema.access.upfront_parts(n, component_class, name, *, discount_rate=None) -> list[UpfrontPart] | None`.
  `UpfrontPart(name, upfront_per_unit, lifetime, fom_share, derived_from_capital_cost=False)`; `upfront_per_unit` is
  per unit of the asset's sizing variable (EUR/MW for a battery: the energy part is already scaled by `max_hours`).
  For a composite class (today only the StorageUnit battery: parts `power` and `energy`) it reads the custom part
  columns; otherwise one part `investment` from the typed `overnight_cost`; otherwise one part back-calculated from
  `capital_cost` (`derived_from_capital_cost=True`); otherwise None.
- `finance_case._assets` (line ~993) today reads only the typed `overnight_cost` × capacity, so a two-part battery
  (no `overnight_cost`, rule C1) reads `overnight_cost_missing` — the gap U2 is blocked on.
- A single-part `capital_cost` typed by a user often includes fixed O&M (PyPSA-Eur's `capital_cost` does), so a
  back-calculation from it overstates the capex. S0's derived composite `capital_cost` excludes FOM
  (`derive.py`), but a legacy single-part one cannot be told apart.
- `fin.replacement_capex: list[(year, asset, amount)]` is read in four places: `cashflow.py:219` (cash),
  `engine.py:_replacement_vintages` (tax depreciation vintages), `lcos.py:150` (storage LCOS), `timeline.py:94`
  (the `asset_lifetime_short` check). Replacements are escalated by the `capex` class from `base_year`, have no
  contingency, and must lie on the operating axis.
- The terminal value sits at the last operating year, inside EBITDA (`cashflow.py:233`): SAM's salvage treatment.
- GS's salvage (`services/study/proforma.py` on `claude/edge-tool-ux-research-n0n2l6`): for each part, the
  purchase still alive at the horizon is valued at
  `cost × annuity(asset_rate, life) × annuity_pv_factor(rate, remaining)`, with
  `annuity_pv_factor(r, y) = (1 − (1+r)^−y)/r` (y when r = 0, 0 when y ≤ 0), `remaining = last_buy + life − horizon`,
  `asset_rate` = the asset's own `discount_rate`, else the LP rate. With it, the case NPV equals the LP objective
  saving times the annuity factor — the identity GS's `BY_CONSTRUCTION` findings test.

## 3. Conventions (new, numbered on from the U1 follow-up)

- **S1. One accessor.** Capex comes only from `upfront_parts`. A part with `derived_from_capital_cost=True` is
  **not established** (`overnight_cost_missing:<asset>` with the reason `upfront_only_from_capital_cost:<asset>`):
  the IC rule C12 (typed, or not established) is kept, because a back-calculated figure can include FOM.
- **S2. Parts on the case.** `AssetFinance` gains `parts: tuple[AssetPart, ...]` (additive, default `()`):
  `AssetPart(name, overnight_cost, lifetime_years, fom_share)` with `overnight_cost` = `upfront_per_unit` × the
  asset's capacity (`p_nom_opt`, else `p_nom`). `AssetFinance.overnight_cost` stays the sum of the parts (None if any
  part is None); `lifetime_years` stays the asset's `lifetime` column (the longest part for a composite, as
  `derive_composite` writes it). A hand case (SAM fixtures) with no parts behaves exactly as today.
- **S3. COD from `build_year`.** An owner asset missing from `cod_by_asset` takes 1 January of its `build_year`,
  flagged `cod_from_build_year:<asset>`; with neither it is `cod_missing` as today. A typed entry wins. One COD for
  all assets stays the rule (`cod_mismatch` otherwise; staged is P5).
- **S4. One replacement schedule.** A new pure function `finance.replacements.schedule(case, tl) ->
  tuple[Replacement, ...]` (`Replacement(year, asset, part, amount, source)`, unescalated) is the only reader of the
  replacement inputs; the four call sites above read it. `source` is `"fixed"` for a `replacement_capex` entry, or
  `"part_lifetimes"`.
- **S5. `part_lifetimes` (D9).** `FinanceInputs.replacement_rule: Literal["fixed", "part_lifetimes"] = "fixed"`.
  Under `part_lifetimes`, for each owner asset and part with a finite lifetime `L` and an established
  `overnight_cost`, a replacement at `cod_year + k·L` for every k ≥ 1 with the year on the operating axis, amount =
  the part's `overnight_cost` (the current upfront cost, no contingency, escalated by `capex` like any
  replacement). A part with no lifetime is `part_lifetime_missing:<asset>:<part>` (capex not established). A
  `replacement_capex` entry for an asset under `part_lifetimes` is refused `replacement_rule_conflict:<asset>`
  (double counting); entries for other assets still apply. A non-integer `L` rounds each year to the nearest whole
  year (`k·L`), disclosed `part_lifetime_rounded:<asset>:<part>`. The `asset_lifetime_short` check counts a
  `part_lifetimes` asset as replaced.
- **S6. `remaining_life_annuity` (D10).** `TerminalValueRule.method` gains `"remaining_life_annuity"` (`value`
  unused, must be None). At the last operating year, `TV = Σ over assets and parts of base × annuity(r_a, L) ×
  annuity_pv_factor(r, remaining)`:
  - `L` = the part lifetime; `last_buy` = the last purchase year of the part on or before the last operating year
    (the COD, or the latest replacement of that part); `remaining = last_buy + L − (cod_year + analysis_years)`;
    a part with `remaining ≤ 0` adds 0.
  - `base` = what that purchase cost: the part's `overnight_cost` for the initial purchase (no contingency), the
    escalated amount for a replacement.
  - `r_a` = the asset's own `discount_rate` (`case.lp_basis.asset_discount_rates`), else `case.lp_basis.discount_rate`;
    `r` = `case.lp_basis.discount_rate` (GS uses the LP rate; the report discloses that this is the LP basis, not the
    WACC). `annuity(r, L) = r / (1 − (1+r)^−L)` (1/L at r = 0).
  - Not established (`terminal_value_missing` with a reason) when: no `lp_basis` or its rate is None
    (`terminal_needs_lp_rate`), a part has no lifetime or no cost (`terminal_part_unknown:<asset>:<part>`), or under
    `replacement_rule="fixed"` an asset with more than one part has `replacement_capex` entries (a fixed entry names
    an asset, not a part: `terminal_needs_part_lifetimes:<asset>`).
  - Placement and tax: the same as `fixed` (last operating year, inside EBITDA). The report's terminal row names the
    method and lists the per-part terms.
- **S7. Facade.** `AssetFinance.parts`, `AssetPart`, `FinanceInputs.replacement_rule`, the new
  `TerminalValueRule` method and `finance.replacements.schedule` join the frozen facade
  (`tests/test_engine_facade_frozen.py`, plan §3 of the U1 landing file).

## 4. Work packages, tests first

### WP-A: S0b (S1–S3)
Tests (`tests/test_finance_case_upfront_parts.py`):
- A two-part battery (power 200 EUR/kW · 10 y, energy 150 EUR/kWh · 4 h · 15 y, p_nom_opt 2 MW): `AssetFinance`
  has two parts, overnight 400,000 + 1,200,000 EUR, total 1.6 MEUR; no `overnight_cost_missing`.
- A single-part generator with a typed `overnight_cost`: one part `investment`, total unchanged from today.
- An asset whose only cost is `capital_cost`: `overnight_cost_missing:<a>` and
  `upfront_only_from_capital_cost:<a>`.
- COD: `build_year` 2030 and no `cod_by_asset` entry → COD 2030-01-01 with `cod_from_build_year`; a typed entry
  wins; neither → `cod_missing`; two assets with different `build_year` → `cod_mismatch`.
- The existing finance suites and `qa_investment_case.py` pass unchanged (single-part numbers are the same).

### WP-B: D9 (S4, S5)
Tests (`tests/test_finance_replacements.py`):
- `schedule()` on the battery above, 25-year axis from COD 2030, `part_lifetimes`: power part at 2040 and 2050,
  energy at 2045, each at its part cost; under `fixed` it returns exactly `replacement_capex`.
- Cash: the replacement row of the cash flow equals the schedule escalated by `capex` (hand numbers).
- The tax vintages and the LCOS read the same schedule (the battery LCOS with `part_lifetimes` equals the LCOS with
  the same entries typed as `replacement_capex`).
- Refusals and flags: `replacement_rule_conflict`, `part_lifetime_missing`, `part_lifetime_rounded`; the
  `asset_lifetime_short` check passes for a `part_lifetimes` asset.

### WP-C: D10 (S6)
Tests (`tests/test_finance_terminal_remaining_life.py`):
- GS parity: the two cases in GS's proforma tests (battery inverter 10 y in a 25-y horizon → the year-20 inverter
  has 5 years left; PV 30 y in 25 y), reproduced through `remaining_life_annuity` to 1e-9 relative.
- The identity: on a flat case with zero escalation, no tax and WACC = the LP rate, NPV(case) − NPV(baseline) equals
  the annual saving × the annuity factor when every part's capex is valued this way (the GS `BY_CONSTRUCTION`
  check, built from hand cash flows).
- Not established: no LP rate, a part without lifetime, the fixed-rule multi-part case; `value` set → refused by the
  model.
- The report's terminal section lists the per-part terms; the xlsx carries the method.

### WP-D: facade, docs
- Facade pins (S7); the U1 landing plan §3 gains the new names; the IC spec's terminal-value list gains the method.

## 5. Process

Per WP: tests first, the implementation, an independent reviewer until PASS, each round recorded in §6. Then the
gate: the full backend suite, the QA drivers (`qa_investment_case.py` must stay at its 245 checks plus any added),
vitest and tsc if the frontend's finance editor gains the two options, a findings note, an independent assessor.
One PR, owner-merged.

## 6. Review record

(empty)
