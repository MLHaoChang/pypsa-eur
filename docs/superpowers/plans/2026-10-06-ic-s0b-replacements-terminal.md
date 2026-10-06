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
  columns; otherwise one part `investment` from a typed, **non-zero** `overnight_cost`; otherwise one part
  back-calculated from `capital_cost` (`derived_from_capital_cost=True`, only when a discount rate is passed or set on
  the asset); otherwise None (`access.py:36-74`). A typed `overnight_cost` of 0 falls through to `capital_cost`
  (`access.py:61`). `number()` keeps an infinite lifetime (`derive.py:113-121`); PyPSA's defaults are
  `lifetime = inf` and `build_year = 0`.
- `finance_case._assets` (line ~993) today reads only the typed `overnight_cost` × capacity, so a two-part battery
  (no `overnight_cost`, rule C1) reads `overnight_cost_missing` — the gap U2 is blocked on.
- A single-part `capital_cost` typed by a user often includes fixed O&M (PyPSA-Eur's `capital_cost` does), so a
  back-calculation from it overstates the capex. S0's derived composite `capital_cost` excludes FOM
  (`derive.py`), but a legacy single-part one cannot be told apart.
- `fin.replacement_capex: list[(year, asset, amount)]` is read in four places: `cashflow.py:219` (cash),
  `engine.py:438-444` `_replacement_vintages` (tax depreciation vintages, the asset's class, `tax.py:255-258`),
  `lcos.py:150` (storage LCOS), `timeline.py:94-103` (the `asset_lifetime_short` check). `debt.py:229` reads only
  `op.replacement`. `AssetFinance.overnight_cost` is read for the initial capex, ITC, the tax shares and the LCOS
  share (`cashflow.py:203-207`, `incentives.py:97-99`, `engine.py:203-208`, `lcos.py:92,148`); 27 call sites build
  `AssetFinance` positionally. The report's replacement line carries `source="replacement_capex"`, which the chat
  tools classify on (`chat_tools.py:2414`, `:2548`). `tests/test_finance_types_ts_parity.py` pins every
  `FinanceInputs` field in `frontend/src/api/types.ts`. Replacements are escalated by the `capex` class from `base_year`, have no
  contingency, and must lie on the operating axis.
- The terminal value sits at the last operating year, inside EBITDA (`cashflow.py:233`): SAM's salvage treatment.
- GS's salvage (`services/study/proforma.py` on `claude/edge-tool-ux-research-n0n2l6`): for each part, the
  purchase still alive at the horizon is valued at
  `cost × annuity(asset_rate, life) × annuity_pv_factor(rate, remaining)`, with
  `annuity_pv_factor(r, y) = (1 − (1+r)^−y)/r` (y when r = 0, 0 when y ≤ 0), `remaining = last_buy + life − horizon`,
  `asset_rate` = the asset's own `discount_rate`, else the LP rate, for PV; GS uses the LP rate unconditionally for
  the inverter (`proforma.py:435` vs `:453-463`) — the same number when the battery has no rate of its own, and IC's
  rule (the asset's rate, else the LP's) is the one the LP's own annuity uses (`periodized_costs.py:210`). GS's
  timing: t = 0 is the capex year, t = 1..H the operating years, H = the storage lifetime (`proforma.py:360`); a part
  is re-bought at **t = k·L for k·L < H** (`proforma.py:362`, `:496`), i.e. in the last service year of the previous
  purchase. GS refuses a non-whole lifetime (`proforma.py:182-190`) and returns no salvage for a non-finite life
  (`salvage_not_computed`, `:455-456`). GS's LCOS nets the inverter salvage (`proforma.py:536-542`). With this rule
  the case NPV equals the LP objective saving times the annuity factor — the identity GS's `BY_CONSTRUCTION`
  findings test.

## 3. Conventions (new, numbered on from the U1 follow-up)

- **S1. One accessor.** Capex comes only from `upfront_parts`. A part with `derived_from_capital_cost=True` is
  **not established** (`overnight_cost_missing:<asset>` with the reason `upfront_only_from_capital_cost:<asset>`):
  the IC rule C12 (typed, or not established) is kept, because a back-calculated figure can include FOM (owner
  decision 2026-10-06). The adapter passes `discount_rate=cfg.discount_rate` so the derivation is detected and
  flagged rather than read as None. A typed `overnight_cost` of exactly 0 stays an established part of 0 (today's
  behaviour; the adapter reads it before calling the accessor).
- **S2. Parts on the case.** `AssetFinance` gains `parts: tuple[AssetPart, ...]` (additive, default `()`, appended
  **after `carrier`** so positional construction keeps working):
  `AssetPart(name, overnight_cost, lifetime_years, fom_share)` with `overnight_cost` = `upfront_per_unit` × the
  asset's capacity (`p_nom_opt`, else `p_nom`). `AssetFinance.overnight_cost` stays the sum of the parts (None if any
  part is None); `lifetime_years` stays the asset's `lifetime` column (the longest part for a composite, as
  `derive_composite` writes it). An infinite lifetime maps to `lifetime_years = None` on the asset (today's `_fin`
  rule) and to `math.inf` on a part (kept: S5 never replaces it, S6 cannot value it). A hand case (SAM fixtures)
  with no parts behaves exactly as today.
  - **One source of truth (review B4, B5).** Everything that reads parts reads `effective_parts(a)`: `a.parts`, or,
    when it is empty, one part `investment` = `(a.overnight_cost, a.lifetime_years, None)`. `AssetFinance.__post_init__`
    refuses (`ValueError`) parts whose sum differs from `overnight_cost` by more than 1e-9 relative (None if any part
    is None). Capex is scaled through one facade helper, `finance.case.scale_capex(case, f)`, which scales the parts
    and `overnight_cost` together (the GS tornado, C5, must use it; `dataclasses.replace(overnight_cost=…)` on an
    asset with parts is refused by the check).
- **S3. COD from `build_year`.** An owner asset missing from `cod_by_asset` takes 1 January of its `build_year`,
  flagged `cod_from_build_year:<asset>`; with neither it is `cod_missing` as today. A `build_year` that is 0, NaN,
  negative or not an integer (PyPSA's default is 0) is absent (review B2). A typed entry wins. One COD for
  all assets stays the rule (`cod_mismatch` otherwise; staged is P5).
- **S4. One replacement schedule.** A new pure function `finance.replacements.schedule(case, tl) ->
  tuple[Replacement, ...]` (`Replacement(year, asset, part, amount, source)`, unescalated) is the only reader of the
  replacement inputs; the four call sites above read it. `source` is `"fixed"` for a `replacement_capex` entry, or
  `"part_lifetimes"`.
- **S5. `part_lifetimes` (D9).** `FinanceInputs.replacement_rule: Literal["fixed", "part_lifetimes"] = "fixed"`.
  Under `part_lifetimes`, for each owner asset and part with a finite lifetime `L` and an established
  `overnight_cost`, a replacement in the **last service year of the previous purchase**, `cod_year + k·L − 1`, for
  every k ≥ 1 with k·L < `analysis_years` (GS's t = k·L with financial close one year before COD; review B1), amount =
  the part's `overnight_cost` (the current upfront cost, no contingency, escalated by `capex` like any
  replacement). A part with no lifetime is `part_lifetime_missing:<asset>:<part>` (capex not established). A
  `replacement_capex` entry for an asset under `part_lifetimes` is refused `replacement_rule_conflict:<asset>`
  (double counting); entries for other assets still apply. A non-integer `L` rounds each year to the nearest whole
  year, half up (`floor(k·L + 0.5)`), disclosed `part_lifetime_rounded:<asset>:<part>`. An infinite `L` is never
  replaced. The `asset_lifetime_short` check counts a `part_lifetimes` asset as replaced only when every one of its
  effective parts (S2) is either at least the axis long or has a finite lifetime (so an asset with no parts and a
  short lifetime is replaced through its one effective part, never silently skipped). `schedule()` needs the
  `Timeline`: `build_timeline` builds it first, then runs the check. Under `fixed`, a part with L < the axis and no
  entry for its asset is flagged `part_not_replaced:<asset>:<part>` (a battery inverter at 10 y in a 25-y case).
- **S6. `remaining_life_annuity` (D10).** `TerminalValueRule.method` gains `"remaining_life_annuity"` (`value`
  unused, must be None). At the last operating year, `TV = Σ over assets and parts of base × annuity(r_a, L) ×
  annuity_pv_factor(r, remaining)`:
  - `L` = the part lifetime; `start` = the first service year of the part's last purchase (`cod_year` for the
    initial purchase, a replacement's year + 1); `remaining = start + L − (cod_year + analysis_years)`, with the
    **unrounded** `k·L` (`cod_year + k·L` for the k-th replacement), so it matches GS's `last_buy + L − H` exactly;
    a part with `remaining ≤ 0` adds 0. An infinite or missing `L` is `terminal_part_unknown` (GS's
    `salvage_not_computed`).
  - `base` = what that purchase cost: the part's `overnight_cost` for the initial purchase (no contingency), the
    escalated amount for a replacement.
  - `r_a` = the asset's own `discount_rate` (`case.lp_basis.asset_discount_rates`), else `case.lp_basis.discount_rate`;
    `r` = `case.lp_basis.discount_rate` (GS uses the LP rate; the report discloses that this is the LP basis, not the
    WACC). `annuity(r, L) = r / (1 − (1+r)^−L)` (1/L at r = 0).
  - Not established (`terminal_value_missing` with a reason) when: no `lp_basis` or its rate is None
    (`terminal_needs_lp_rate`), a part has no lifetime or no cost (`terminal_part_unknown:<asset>:<part>`), or under
    `replacement_rule="fixed"` an asset with more than one part has `replacement_capex` entries (a fixed entry names
    an asset, not a part: `terminal_needs_part_lifetimes:<asset>`).
  - Placement and tax: the same as `fixed` (last operating year, inside EBITDA; the counterfactual's own TV is never
    used, `engine.py:154-160`; CFADS excludes it, `debt.py:229`). The report names the method and lists the
    per-part terms under a new payload key `terminal_value` in `report.py::_project_payload`. The report notes that a
    replacement's base is its escalated (nominal) cost, annuitised at the LP rate.
  - **LCOS.** Under `remaining_life_annuity` the storage LCOS nets the asset's own per-part terms from its capex PV
    (as GS's LCOS does, `proforma.py:536-542`), stated in `LCOS_BASIS`; under the other methods it is unchanged.
- **S7. Facade.** `AssetFinance.parts`, `AssetPart`, `effective_parts`, `scale_capex`,
  `FinanceInputs.replacement_rule` (in the `MODEL_FIELDS` pin, `test_engine_facade_frozen.py:117-123`), the new
  `TerminalValueRule` method and `finance.replacements.schedule` join the frozen facade (U1 landing plan §3; the
  coordinating plan's C2 is amended to name `remaining_life_annuity` instead of `fixed`).
- **S8. Frontend (review B6).** `types.ts` gains `replacement_rule?: 'fixed' | 'part_lifetimes'` and the method
  `'remaining_life_annuity'`; `FinanceInputsEditor.tsx` offers both. Unconditional: the TS parity test requires it.
- **S9. Report sources.** A generated replacement keeps `source="replacement_capex"` (the chat tools classify on it)
  with the part in `source_id` (`<asset>:<part>`).

## 4. Work packages, tests first

### WP-A: S0b (S1–S3)
Tests (`tests/test_finance_case_upfront_parts.py`):
- A two-part battery written through `asset_schema.derive.apply_parts` (stored units: power 200,000 EUR/MW · 10 y,
  energy 150,000 EUR/MWh · 4 h · 15 y; p_nom_opt 2 MW): `AssetFinance` has two parts, overnight 400,000 + 1,200,000
  EUR, total 1.6 MEUR; no `overnight_cost_missing`.
- A typed `overnight_cost` of 0 stays an established 0; a `build_year` of 0 (PyPSA's default) with no
  `cod_by_asset` entry is `cod_missing`, never a crash (`test_finance_case_adapter.py:666` keeps passing).
- `__post_init__` refuses parts that disagree with `overnight_cost`; `scale_capex(case, 1.1)` scales both.
- A single-part generator with a typed `overnight_cost`: one part `investment`, total unchanged from today.
- An asset whose only cost is `capital_cost`: `overnight_cost_missing:<a>` and
  `upfront_only_from_capital_cost:<a>`.
- COD: `build_year` 2030 and no `cod_by_asset` entry → COD 2030-01-01 with `cod_from_build_year`; a typed entry
  wins; neither → `cod_missing`; two assets with different `build_year` → `cod_mismatch`.
- The existing finance suites and `qa_investment_case.py` pass unchanged (single-part numbers are the same).

### WP-B: D9 (S4, S5)
Tests (`tests/test_finance_replacements.py`):
- `schedule()` on the battery above, 25-year axis from COD 2030, `part_lifetimes`: power part at 2039 and 2049,
  energy at 2044 (the last service years), each at its part cost; under `fixed` it returns exactly
  `replacement_capex`.
- An asset with no parts and a 10-year lifetime on a 25-year axis under `part_lifetimes`: replaced through its one
  effective part (2039, 2049), never skipped; an infinite part lifetime is never replaced.
- Cash: the replacement row of the cash flow equals the schedule escalated by `capex` (hand numbers).
- The tax vintages and the LCOS read the same schedule (the battery LCOS with `part_lifetimes` equals the LCOS with
  the same entries typed as `replacement_capex`).
- Refusals and flags: `replacement_rule_conflict`, `part_lifetime_missing`, `part_lifetime_rounded`; the
  `asset_lifetime_short` check passes for a `part_lifetimes` asset.

### WP-C: D10 (S6)
Tests (`tests/test_finance_terminal_remaining_life.py`):
- GS parity, rebuilt from hand values (GS's solved fixture is not on master): battery inverter 10 y in a 25-y
  horizon → the purchase serving years 21–30 has 5 years left; PV 30 y in 25 y; values = cost × crf × annuity_pv_factor
  with the seed ledger's numbers, to 1e-9 relative.
- The identity: on a flat case with zero escalation, no tax, WACC = the LP rate, financial close one year before COD,
  `capex_phasing = [1.0]` and `analysis_years = H` (the U2 compile rule), PV(capex + replacements − TV) equals the PV
  of the LP annuities exactly (the review's hand check: 2,199,084.18 on the battery at 7 %, H = 25), so NPV(case) −
  NPV(baseline) is the annual saving × the annuity factor (GS's `BY_CONSTRUCTION`).
- Infinite lifetime → `terminal_part_unknown`; the LCOS nets the part terms under this method only.
- Not established: no LP rate, a part without lifetime, the fixed-rule multi-part case; `value` set → refused by the
  model.
- The report's terminal section lists the per-part terms; the xlsx carries the method.

### WP-D: facade, frontend, docs
- Facade pins (S7, incl. `MODEL_FIELDS`); `types.ts` and the finance editor (S8) with vitest and tsc; the U1 landing
  plan §3, the facade test's docstring reference and the coordinating plan's C2 updated; the IC spec's terminal-value
  list gains the method.

## 5. Process

Per WP: tests first, the implementation, an independent reviewer until PASS, each round recorded in §6. Then the
gate: the full backend suite, the QA drivers (`qa_investment_case.py` must stay at its 245 checks plus any added),
vitest and tsc if the frontend's finance editor gains the two options, a findings note, an independent assessor.
One PR, owner-merged.

## 6. Review record

- **Plan round 1 (2603916): PASS WITH CONDITIONS.** B1 replacements one year late against GS (the identity fails by
  −48,518.67 EUR on the battery at 7 %); B2 PyPSA's `build_year = 0` crashes the COD default; B3 infinite lifetimes
  undefined; B4 parts-less assets unreplaced under `part_lifetimes`; B5 two sources of capex truth (the tornado would
  scale only the first purchase); B6 the TS parity test needs `types.ts`; B7 a typed 0 would stop being established.
  All taken into S1–S9 and the tests, plus: the LCOS nets the part terms under the new method, half-up rounding,
  `part_not_replaced` under `fixed`, `parts` after `carrier`, `source="replacement_capex"` kept, a named payload key.
  The derived-from-`capital_cost` question went to the owner: not established (S1).
