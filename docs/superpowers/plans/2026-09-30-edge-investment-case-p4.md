# Edge Investment Case — Phase 4: finance engine, single owner (plan v0.1)

**Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` §4.2 / §4.2a (finance contracts),
§4.3 (report), §6.1–6.6 (finance engine), §11 (packs), §12 (API, chat, UI), §13 (P4 row), §15 (oracles,
provenance), decisions 1, 10, 14. **Outline:** P0–P1 plan, "P4 Finance engine (single owner)".
**Carried in:** P0 gate findings 5 (numeric defaults on `DebtTranche` fees, `itc_recapture_years`,
`escalation`) and 7 (finance must not inherit the solve stack through `physical_quantities`); P2 pins
(contract prices are indexed to the modelled year — "P4 escalates from the modelled year onward, never
both"; settlement covers one period-year — "P4 applies period years"; tenor, escalation and tax are P4's);
P3 pins (the ledger → `CashflowLine` mapping, WP3.1; overnight capex to the owner from `asset_owners`;
`tax_levy` lines are deductible opex, never corporate tax; `basis="annuity"` / `"model_only"` lines are not
converted; a `None` line makes that participant's returns `not_established`); P3 follow-ups filed as "P4
hygiene" (the capacity fee's shape checked at the PUT; `replacesInline`'s load-time re-check; the DSR slack
cash-flow decision from P2). **Branch:** `claude/energy-tool-features-research-fdixs0`.

**Process (owner's instruction, spec §13):** this plan → review loop until PASS → per WP: TDD, then an
implementation-review loop until PASS → Phase 4 e2e QA gate (a `qa_investment_case.py` driver discovered
by `run_qa_drivers.py`, frontend vitest + `tsc`, full backend suite, findings note, independent assessor)
before P5.

---

## What P4 delivers

**Money over time for a single owner.** From a solved, billed and ledgered site (P1–P3), the finance engine
builds an annual cashflow from financial close to the end of the analysis period: capex phased over
construction with interest during construction, the operating year replicated with escalation,
degradation and contract indexation, replacement capex and a terminal value; debt sized by gearing or
sculpted to a DSCR target, with fees and a DSRA; corporate tax with depreciation and loss treatment from a
dated jurisdiction pack; incentives (ITC, PTC, grants) with dated eligibility; and the returns — project IRR
pre/post tax, equity IRR, NPV at WACC, payback, DSCR / LLCR / PLCR, a finance-consistent LCOE, and
**solve-for-PPA-price**. It is validated against **NREL SAM "Single Owner"** on three reference cases
generated with PySAM and committed with provenance, and it is reachable through a long-running study
(`POST/GET /results/investment_case`, abort, report, xlsx export), chat tools and the Investment tab.

**Not P4:** tax equity (P7); multi-participant returns beyond the single owner (the engine takes any
participant list, but P4's gate and UI are single-owner, spec decision / MVP-A); realistic dispatch (P6 — P4
values the perfect-foresight dispatch, `mode="pf"`); the scenario matrix and P50/P90 (P7); the full
`InvestmentCaseReport` assembler with narration (P7 WP7.4 — P4 fills the finance sections).

## Conventions pinned by this plan

**C1 — Where the arithmetic lives.** Pure functions on annual `numpy` arrays in `services/finance/`:
`timeline.py`, `cashflow.py`, `debt.py`, `tax.py`, `incentives.py`, `metrics.py`, `export_xlsx.py`,
`investment_case_runner.py` (the runner is the only stateful module). They take a plain **`FinanceCase`**
(frozen dataclass: the operating-year template, the assets, the owner, the resolved packs) and import
nothing from `services.results`, `services.solver*` or routers — the tripwire stays as it is. The one
adapter that reads a solved network — **`services/results/finance_case.py::build_finance_case(n, cfg, fin,
*, result_df)`** — lives in the results layer, which may import the solve stack. This closes **P0 gate
finding 7 by construction**: the finance package never imports `physical_quantities`; the adapter does and
hands plain numbers over. A tripwire test pins that `services/finance/**` imports none of
`services.results`, `services.solver`, `services.solver_service`, `routers`.

**C2 — The year axis.** Integer years. `y0` = the calendar year of `financial_close`; construction years run
`y0 … cod_year − 1` (at least one when `construction_months > 0`); operating years `cod_year …
cod_year + analysis_years − 1`. A new `FinanceInputs.analysis_years` (required, 1..60 — SAM's
`analysis_period`) bounds the axis; the spec's `max(cod + lifetime)` is the default the adapter proposes
from the assets' lifetimes, never an implicit truncation. One owner COD in P4 (`cod_by_asset` must agree
across the owner's assets or the case is refused: `cod_mismatch` — per-asset CODs are P5's).

**C3 — The operating-year template.** The ledger (P3) is per modelled period, **unweighted** (one
period-year). Flat network: one period, key `"_"` → every operating year starts from that template.
Multi-period network: operating year `y` uses the investment period `p` with `p ≤ y < next(p)` (years
before the first period use the first); an operating year after the last period uses the last. The
template's **base year** is the modelled year (the snapshots' majority year, P2's indexation basis;
`period` for multi-period): escalation runs from the base year, never from COD (P2 pin: "escalates from the
modelled year onward").

**C4 — Escalation, one rate per stream class, all nominal.** `FinanceInputs.escalation` keys are the four
spec classes `opex`, `fuel`, `tariff`, `ppa`, plus `export` (market price) and `capex` (replacement
capex) — nominal rates per year. The adapter maps each `ValueStreamKind` to a class (table in WP4.1);
contract lines use their **own** `indexation_pct_per_year` (P2 already indexed to the modelled year; P4
continues the same indexation forward — never adds the generic `ppa` rate on top); a contract without
indexation uses `ppa`. **A class with cashflows and no rate is `not_established`** (the section says which)
— never a silent 0 (closes P0 gate finding 5 for `escalation`). SAM's O&M convention
`(1+inflation)(1+real_escalation)−1` is how the oracle mapping converts; the engine takes nominal rates.

**C5 — Degradation.** Per asset, a constant annual rate (SAM's single-value `degradation`) or a per-year
list; year `k` of operation (k = 1 at COD) scales that asset's **generation-linked** cash by
`(1−d)^(k−1)` (SAM convention). Generation-linked = ledger lines whose `asset` is the asset (P3's export
split parts), and contract lines settled on the asset's output (PPA pay-as-produced / as-consumed volume).
The bill is not re-rated for degraded generation (the extra import it causes is disclosed:
`degradation_bill_effect_not_modelled`, a note, not a gate — P6/P7 re-dispatch territory).

**C6 — Capex and IDC.** Overnight capex per asset from the seam (`physical_quantities.components`:
`overnight_cost × p_nom_opt`, `None` → the capex section `not_established`, never the annuitised
`capital_cost`), × (1 + `contingency_share`) (`None` → `not_established`), phased over the construction
years by `capex_phasing` (one entry per construction year; a single `[1.0]` = all at `y0`), owned by the
asset's owner (`asset_owners`; single owner: all). Interest during construction accrues on the drawn debt
balance at the tranche rate (mid-year drawdown convention, stated) and is **capitalised** into the debt
(no cash interest before COD). Replacement capex `(year, asset, amount)` in real base-year money, escalated
by `capex`. SAM has no IDC mechanic (its `construction_financing_cost` is an input lump); the oracle cases
set it to 0 and IDC is checked by a hand oracle (WP4.2a).

**C7 — Tax.** A pack's corporate tax is an ordered list of **layers** `{name, rate, base: "taxable_income",
deductible_in_later_layers: bool}` — SAM's state-then-federal (state deductible from federal income), the
German KSt + SolZ + GewSt (GewSt not deductible, §4 Abs. 5b EStG; SolZ is 5.5 % of KSt), the US federal
layer with a state slot. **Loss treatment** is a case option `tax_losses: "offset_other_income" |
"carryforward"`: SAM Single Owner assumes the owner uses losses immediately (negative tax = a benefit) —
the oracle cases use `offset_other_income`; `carryforward` applies the pack's rule (period, share limit —
the German Mindestbesteuerung, the US 80 % NOL limit). **Levies** (`tariff_item_kind="tax_levy"`) are
deductible opex (P3 pin), never corporate tax. Interest deductibility caps (§163(j) 30 % of EBITDA,
Zinsschranke 30 % above a threshold) apply only when the pack defines them and only in `carryforward` mode
(disallowed interest carries forward).

**C8 — Debt and CFADS.** **CFADS is pre-tax: EBITDA − reserve funding + reserve releases** (SAM's
`cf_cash_for_ds`). The spec §6.3 sentence "post-tax, pre-financing (SAM's convention)" is wrong about SAM:
PySAM 7.1.1's Single Owner gives `cf_cash_for_ds = cf_ebitda − cf_funding_equip1` with federal and state
tax not deducted (checked on the default case: EBITDA 3,644,901, CFADS 2,702,918, the difference the
equipment-reserve funding exactly). WP4.0 corrects the spec with a dated errata line; the report prints the
definition. Sizing modes per tranche: `gearing` (share of total capital cost incl. capitalised fees and IDC,
SAM `debt_percent`), `amount`, and `dscr_target` sculpting (SAM `debt_option=1`: the debt is Σ over the
tenor of `CFADS_t / DSCR` discounted at the tranche rate; payment_t = `CFADS_t / DSCR`). Repayment shapes:
`annuity` (SAM `payment_option=0`), `level` principal (SAM `payment_option=1`), `dscr_target`. DSRA =
`dsra_months / 12` × next year's debt service, funded at close and adjusted yearly (SAM's reserve
convention), released at maturity. The circularity (fees and DSRA funding ← debt size ← CFADS) is solved by
fixed-point iteration, relative tolerance 1e-6, at most 50 iterations; non-convergence sets `debt`
`not_established` with the residual (spec §6.3).

**C9 — Returns.** IRR by `scipy.optimize.brentq` on NPV over a bracket found by scanning for a sign change;
no sign change → `None` + `irr_not_established:no_sign_change`; more than one sign change in the cash
series → still solved, flagged `irr_multiple_sign_changes`. NPV at `wacc_nominal` (`None` →
`npv_not_established`), end-of-year discounting with `y0` undiscounted (SAM convention). Payback: first year
cumulative undiscounted after-tax cash ≥ 0 (fractional by linear interpolation, stated). DSCR per year =
CFADS / debt service (years with debt service only); min, average; LLCR = PV(CFADS over the remaining loan
life, at the debt rate) / outstanding debt at COD; PLCR the same over the project life. Finance-consistent
LCOE = PV(all costs incl. financing and tax effects, SAM `lcoe_nom` definition) / PV(energy). Solve-for-PPA:
`brentq` on a contract's price (or SAM-mode a flat PPA price) to reach a target after-tax IRR in a target
year (SAM `ppa_soln_mode=0`, `flip_target_percent`, `flip_target_year`); bracket from 0 to a cap, no root →
`not_established`.

**C10 — The WACC gate (spec §6.6).** `wacc_vs_discount_rate_consistent` = `|wacc_nominal −
cfg.discount_rate| ≤ 1e-6` **and** `|inflation − cfg.inflation_rate| ≤ 1e-6`; `None` when either finance
value is `None`. The report states the annuity basis (nominal rate on real costs — `cfg.discount_rate` in
the LP annuity) and the PV basis (real, Fisher, only under `auto_discount_periods`) separately and names
the number the sizing used. The gate never feeds back into the LP (decision 10).

**C11 — Packs are data with sources.** Every rule a P4 pack adds is a `Rule(value, source)` whose source
names the statute or official publication (and section / table); dated cliffs are separate pack versions
(`valid_from` / `valid_to`, `load_pack(as_of=…)` — WP0.2 already supports this). The values listed in
WP4.3a/4.4 are the **expected content**; each lands only with its cited source, and the implementation
reviewer checks each against the source text. A rule the implementer cannot source is left absent
(`not_established` at lookup — WP0.2's semantics), never guessed.

**C12 — Unknown is `None` + a flag.** A `None` ledger line, overnight cost, escalation class, pack rule or
tranche input makes the section(s) that depend on it `not_established` with the reason; downstream
headlines stay `None`. A section never reports a number built on a substituted 0 (ADR-0001).

---

## Fixtures and oracles (bind the gate)

**The SAM oracle.** PySAM **7.1.1** (`nrel-pysam`, BSD-3-Clause) is not an app dependency: it is installed
only in a developer venv to (re)generate fixtures. `tests/fixtures/investment_case/sam/`:
- `generate_sam_cases.py` — builds each case from a committed hourly generation profile and a committed
  input dict, runs `Singleowner` standalone (`SystemOutput.gen`), writes `<case>.json` = `{provenance:
  {pysam_version, ssc_version, generated, script_sha, license}, sam_inputs: {…}, outputs: {scalars, cf_*
  arrays}}`. Not run in CI (no PySAM there); a test pins that each JSON's `script_sha` equals the
  committed script's hash (a changed generator without regenerated fixtures fails).
- `gen_profile.csv` — one synthetic 8,760-hour profile (a clear-sky PV shape with a seeded cloud factor;
  the seed and formula in the script), shared by the three cases.
- `PROVENANCE.md` + `SAM_LICENSE` (BSD-3 text) — like `oracles/` for REopt.

SAM features **off** in every case (not modelled in P4, stated as deviations in the findings): property tax,
insurance, working-capital / receivables / major-equipment reserves, construction financing cost, state
ITC, CBI / IBI / PBI, TOD revenue factors, capacity payments, salvage except in S3.

| Id | SAM set-up | What it checks (to tolerance T) |
|---|---|---|
| **S1** | all-equity (`debt_option=0`, `debt_percent=0`), SL-20 depreciation (100 %), federal 21 % + state 7 % layers, PPA $/MWh fixed with 1 %/yr escalation, O&M per kW-yr + fixed with inflation 2.5 %, degradation 0.5 %/yr, 25 years; **S1b** the same with `ppa_soln_mode=0` (target 11 % after-tax IRR in year 20) | revenue, O&M, EBITDA, depreciation, state/federal tax, after-tax cash per year; project IRR pre/post tax, NPV; S1b: the solved PPA price |
| **S2** | DSCR-sculpted debt (`debt_option=1`, DSCR 1.3, 18 yrs, 7 %, closing fee 2.75 %, DSRA 6 months), MACRS-5 90 % + SL-20 10 %, 100 % federal bonus on MACRS-5, same revenue side | debt size, debt service / interest / principal per year, DSRA balance, DSCR per year and min, CFADS per year, depreciation per class, taxes, equity after-tax cash, IRR, NPV |
| **S3** | gearing debt (`debt_option=0`, 60 %, standard amortisation, 15 yrs, 6 %), 30 % federal ITC with 50 % basis reduction, MACRS-5 100 %, no bonus, salvage 10 % at the end | debt schedule, ITC in year 1, reduced depreciable basis, terminal value, taxes, after-tax cash, IRR, NPV |

**Tolerance T** (pinned now, revisited only by a recorded deviation): per-year arrays |ours − SAM| ≤
max(1.0, 1e-6·|SAM|) in currency units; IRR ≤ 1e-5 absolute (0.001 pp); NPV, debt size, solved PPA price
≤ 1e-6 relative; DSCR ≤ 1e-6 absolute. A SAM convention the engine does not follow is a **recorded
deviation** (findings note, the case JSON's `deviations` list, a test asserting the deviation's size) — never
a loosened tolerance.

**Hand oracles** (self-authored, stdlib arithmetic beside the fixture, like P2's H1–H3):
- **F1 IDC:** two construction years, 60/40 phasing, one term loan drawn pro rata at 8 % — the capitalised
  IDC and the debt at COD by hand.
- **F2 DE pack:** a German single-owner case (KSt 15 % + SolZ 5.5 % + GewSt at Hebesatz 400 %, 20-year
  straight-line AfA, carryforward with the Mindestbesteuerung) — taxable income, tax per layer and loss
  carryforward by hand for 6 years including two loss years.
- **F3 US pack:** MACRS-7 with 100 % bonus and a §163(j)-capped interest deduction in carryforward mode, the
  NOL 80 % limit — by hand for 5 years.
- **F4 escalation / indexation:** a PPA with its own indexation, a tariff class and an export class, a base
  year two years before COD — every factor by hand.
- **F5 terminal value / replacement:** a replacement in year 10 and `book_value` / `multiple_of_ebitda`
  terminal values.

**The integration fixture.** The P1/P3 edge site (`build_edge_15min`, US tariff, PV + BESS, a firm connection
fee, an export price) under `single_owner`, solved, with a `FinanceInputs` → the `investment_case` study
end to end through the routes; the operating-year template equals the ledger's per-participant net to the
cent, and year-1 EBITDA = −(the owner's ledger net excluding capex lines) (the ledger ↔ finance seam).

---

## Dependency graph

```
WP4.0 foundations: SAM oracle fixtures, contract amendments, spec errata, tripwire
 ├─ WP4.1 timeline + operating cashflows (+ IRR/NPV core)      ← S1 revenue/opex/EBITDA
 │   ├─ WP4.3a tax + depreciation engine; eu_de, us_federal     ← S1 full parity, F2, F3
 │   │   ├─ WP4.4 incentives with dated rules (OBBBA)           ← S3 ITC
 │   │   └─ WP4.3b eu_nl, ca_federal packs
 │   └─ WP4.2a debt: amount / gearing, annuity / level, fees, IDC ← S3 debt, F1
 │       └─ WP4.2b DSCR sculpting fixed point + DSRA            ← S2
 │           └─ WP4.5 metrics, solve-for-PPA, WACC gate          ← S1b, all parity
 │               └─ WP4.6 adapter, runner, routes, persistence, chat, xlsx
 │                   └─ WP4.7 FE: finance inputs, results, export
```

WP4.3b is independent after WP4.3a and may land last (MVP-A needs only `eu_de` + `us_federal`).

**Per-WP invariants (all of P4).** The P1–P3 gates stay green (`qa_commercial_lp.py`,
`qa_billing_contracts.py`, `qa_value_flows.py` in each WP's verification). **P4 never changes the LP, a
committed commercial hash or the ledger** (decision 10: the finance layer is a post-processor). Unknown is
`None` + a flag (C12). Every new persisted field is backward compatible (P3 projects load unchanged) and
mirrored in `frontend/src/api/types.ts`. Tripwires (C1). The SAM parity test for every case the WP can
reach runs in its verification.

---

## WP4.0 Foundations

- **SAM oracle fixtures** as specified above: the generator, the profile, S1 / S1b / S2 / S3 JSON, provenance,
  license, the `script_sha` pin test, and a `test_sam_fixtures.py` that checks each JSON's shape (every
  compared array has `analysis_years + 1` entries; scalars present) — the parity tests land in the WPs that
  can reach them.
- **Spec errata** (a dated "Errata" block at the end of the spec, the text unchanged above it): §6.3 CFADS
  is pre-tax per SAM (C8); §6.6 "L641–698" → the current `assumptions.py` block (4b) lines; §16 risk 3's
  "SAM tolerance … left to the P7 plan" → pinned here for P4.
- **Contract amendments** (`models/finance.py`, backward compatible, `types.ts` mirrored):
  - `FinanceInputs.analysis_years: int | None = None` (required at run time — `None` → the run is refused
    `analysis_years_missing`, the adapter proposes a default); `tax_losses: Literal["offset_other_income",
    "carryforward"] | None = None`; `solve_ppa: SolvePpa | None` (`contract_id | None`, `target_irr`,
    `target_year`); `escalation` documented as the six C4 classes.
  - **P0 gate finding 5:** `DebtTranche.upfront_fee` / `commitment_fee` default → `None` (a tranche without
    a stated fee is refused at run time: `debt_fee_missing` — 0 must be typed); `TaxEquityStructure.
    itc_recapture_years` → `None` (P7's); escalation per C4.
  - `Provenance`: `source_id: str | None`, `contract_id: str | None`, `period: str | None` (the P3 pin
    needs drill-down to survive; `source` keeps the ledger `source`).
  - `DebtTranche`: `rate` may be a per-year list (SAM's single rate is the common case); `repayment_start`
    = COD (grace years move it).
  - `TaxPack`: `layers: list[TaxLayer]` (C7) — `corporate_rate` stays for the single-layer case and is
    derived (Σ with deductibility) for display.
- **Tripwire:** `services/finance/**` imports none of `services.results`, `services.solver`,
  `services.solver_service`, `routers` (C1); the pack modules stay leaf.
- Tests: amendments round-trip, old payloads validate, `types.ts` parity test extended, the tripwire, the
  fixture pin.

## WP4.1 Timeline and operating cashflows

- `timeline.py`: `Timeline(y0, construction_years, cod_year, operating_years, base_year)` from the case;
  `cod_mismatch`, `analysis_years_missing`, `financial_close_after_cod` refusals.
- `cashflow.py`:
  - the **stream-class map** `ValueStreamKind → escalation class` (energy_import, network_*, demand_charge,
    retail_fixed → `tariff`; energy_export, ancillary → `export`; ppa_settlement, cfd_settlement, lease,
    eaas_fee, dr_* → the contract's indexation, else `ppa`; fom, vom, other → `opex`; fuel → `fuel`;
    certificates → `tariff`; capex → `capex`; tax (levies) → `tariff`); unit test per kind;
  - **operating cash** per year and per (value_stream, tariff_item, asset, contract): template amount ×
    escalation factor from the base year × degradation factor (C5) for generation-linked lines;
  - **capex** per construction year (C6), replacement capex, **terminal value** (`none`, `fixed`,
    `book_value` = remaining tax basis at the end, `multiple_of_ebitda` × last-year EBITDA; SAM salvage =
    `fixed` at `salvage_percentage × installed cost`, taxed per the pack: SAM treats salvage as taxable
    income — mirrored);
  - **EBITDA** = operating revenue − operating costs (levies included as costs, C7);
  - `CashflowLine`s for every non-zero line with provenance (C1 amendments).
- `metrics.py` core: `npv(rate, cash)`, `irr(cash)` (C9) — used by the parity tests from here on.
- Tests: F4 escalation/indexation by hand; degradation factors; the class map; capex phasing and contingency
  (`None` → `not_established`); replacement; the three terminal value rules; **S1 parity: revenue, O&M,
  EBITDA per year** (the tax columns wait for WP4.3a); a multi-period template mapping (C3) on a two-period
  toy; a `None` ledger line → the owner's operating section `not_established`.

## WP4.2a Debt: sizing by amount or gearing, annuity / level, fees, IDC

- `debt.py`: tranche draw schedule over construction (pro rata with capex phasing), IDC capitalised (C6),
  upfront fee (share of the committed amount, paid at close, financed from sources), commitment fee on the
  undrawn balance during construction; repayment from COD (grace years deferring principal, interest paid);
  `annuity` and `level` shapes; several tranches (senior first; mezzanine subordinated in the waterfall —
  DSCR on senior service and on total service both reported).
- Sources and uses at COD: capex + IDC + fees + DSRA initial funding = debt + equity; equity = the balance
  (a negative equity is refused: `debt_exceeds_uses`).
- Tests: **F1** IDC by hand; annuity and level schedules against closed forms; fees; grace years; two
  tranches; **S3 debt schedule parity** (debt size, interest, principal per year).

## WP4.2b DSCR sculpting fixed point and DSRA

- Sculpting (C8): debt size = Σ_t CFADS_t / DSCR_target × (1+r)^−t over the tenor; payment_t = CFADS_t /
  DSCR_target; principal_t = payment_t − interest_t; the circular inputs (fees financed, DSRA funding) by
  fixed point (1e-6, ≤ 50) — non-convergence → `debt` `not_established` with the residual and the
  iteration count; the optional `dscr_maximum_debt_fraction` cap (SAM) as a tranche field `max_gearing`.
- DSRA: target = `dsra_months/12` × next year's debt service; funded at close, topped up / released
  yearly, released at maturity; reserve interest income at a stated `reserves_rate` (SAM
  `reserves_interest`; the oracle cases set it so SAM's interest-on-reserves matches or record a deviation).
- Tests: **S2 parity** (debt size, schedule, DSCR per year and min, DSRA balance, CFADS); a forced
  non-convergence (a pathological fee loop) → `not_established` with the residual; sculpting with a
  negative-CFADS year (payment 0, the year flagged).

## WP4.3a Tax and depreciation; `eu_de` and `us_federal` packs

- `tax.py`: depreciation schedules — straight-line (SAM's SL is **full-year from COD**; the oracle decides
  the first-year convention and the test pins it), declining balance with switch to SL, **MACRS** tables
  (half-year convention, IRS Pub. 946 Table A-1) and **bonus** (share of basis in year 1, remainder on the
  schedule); allocation of the depreciable basis across classes (SAM's `depr_alloc_*`); ITC basis reduction
  hook (WP4.4 fills it); tax layers (C7); loss treatment (C7); interest cap (C7).
- **`us_federal` pack** (valid from 2026-01-01; each value with its source): corporate 21 % (IRC §11(b));
  MACRS 5/7/15/20-year tables (Pub. 946 Table A-1); bonus depreciation 100 % for property acquired after
  2025-01-19 (the 2025 reconciliation act, "OBBBA"); NOL: indefinite carryforward limited to 80 % of taxable
  income (IRC §172(a)(2)); §163(j) 30 % of adjusted taxable income; a state layer slot (rate `None` until a
  state pack exists).
- **`eu_de` pack** (valid from 2026-01-01; each with its source): KSt 15 % (§23 KStG) with the enacted
  reduction path from 2028 as later pack versions; SolZ 5.5 % of KSt (§4 SolZG); GewSt = 3.5 % × Hebesatz
  (§11 GewStG; the Hebesatz is a **case input**, the pack holds the Messzahl — a German case without it is
  `not_established`); GewSt not deductible (§4 Abs. 5b EStG); AfA straight-line useful lives per asset class
  from the BMF AfA tables (PV, wind, battery storage — each cited; a class without a cited life is absent);
  the declining-balance option for movables where enacted (with its dates as pack versions); loss
  carryforward with the Mindestbesteuerung (§10d EStG: €1m + a share of the excess, the share dated);
  Zinsschranke (§4h EStG, 30 % of tax EBITDA above the €3m threshold).
- Pack hashes change: `pack_hashes.json` regenerated by the test's helper, the diff reviewed.
- Tests: **S1 full parity** (depreciation, state and federal tax, after-tax cash, IRR pre/post, NPV); **F2**
  and **F3** by hand; MACRS tables sum to 100 %; bonus; carryforward limits; the interest cap; a pack rule
  missing → `not_established` naming the rule; the dated KSt path picks the version by `as_of`.

## WP4.3b `eu_nl` and `ca_federal` packs

- `eu_nl`: VPB with its two brackets (Wet Vpb 1969 art. 22, the bracket threshold and rates dated);
  depreciation (straight-line, useful life per class, the residual-value rule where applicable); loss
  carryforward (the €1m + 50 % rule, dated); the energy-investment deduction (EIA) as an incentive slot for
  WP4.4 (not modelled unless sourced).
- `ca_federal`: general corporate rate (federal part, provincial slot); CCA classes 43.1 (30 %) and 43.2
  (50 %) declining balance with the half-year rule and the Accelerated Investment Incentive where dated;
  the Clean Technology ITC (30 %, dated) as a WP4.4 incentive rule.
- `test_finance_packs.py`: the registry becomes {eu_de, eu_nl, us_federal, ca_federal}; each pack's hash
  pinned; `eu_nl` / `ca_federal` no longer raise.
- Tests: a hand case per pack (tax per layer, depreciation per class for 5 years).

## WP4.4 Incentives with dated rules

- `incentives.py`: **ITC** (share of eligible basis, `itc_percent_maxvalue` cap, basis reduction share —
  SAM `itc_*_deprbas_*` = 50 % for the federal ITC — applied to the depreciable basis, in year 1);
  **PTC** ($/MWh × generation × (1+escalation)^(k−1), for a term of years, SAM `ptc_fed_*`); **grant**
  (amount or share, at COD; reduces basis if the rule says so); `accelerated_depreciation` (a pack
  schedule override); `cfd` / `capacity_payment` are contracts (§6.5 — revenue through the ledger, not
  here).
- **Eligibility** (`EligibilityRule`): begin-construction and placed-in-service tests against the case's
  dates; **phase-out** tables `[(date, share)]` read at the relevant date; the **FEOC flag** (`None` = not
  assessed → the incentive is `not_established` for a technology whose rule requires it; `True` → refused;
  `False` → eligible).
- **`us_federal` incentive data (as dated pack versions, each sourced):** the clean electricity ITC/PTC
  (IRC §48E / §45Y) with the 2025 act's termination for wind and solar (begin construction by 2026-07-04 or
  placed in service by 2027-12-31), the storage ITC runway and phase-down schedule, and the FEOC
  (prohibited foreign entity) restrictions for construction beginning after 2025-12-31 — encoded as data
  (spec §6.5: "OBBBA as data with dates, not code"). Values the implementer cannot cite are absent.
- Tests: **S3 ITC parity** (the credit, the reduced basis, the tax effect); PTC against a hand formula with
  escalation and term; eligibility by date on both sides of each cliff; phase-out shares; the FEOC flag's
  three states; a grant reducing basis.

## WP4.5 Metrics, solve-for-PPA, the WACC gate

- `metrics.py` (C9): project IRR pre / post tax, equity IRR (the owner), NPV at WACC, payback, DSCR
  min / avg (senior and total), LLCR, PLCR, finance-consistent LCOE (SAM `lcoe_nom` definition; `lcoe_real`
  alongside), **solve-for-PPA** (C9).
- The WACC gate (C10) and its report block.
- **All-case SAM parity** (S1, S1b, S2, S3) in one parametrised test, arrays and scalars to T; the recorded
  deviations asserted by size.
- Tests: IRR edge cases (no sign change, multiple, all negative); payback interpolation; LLCR / PLCR closed
  forms; solve-for-PPA with no root; the gate's four states (consistent, inconsistent rate, inconsistent
  inflation, `None`).

## WP4.6 Adapter, runner, routes, persistence, chat, xlsx

- **`services/results/finance_case.py::build_finance_case`** (C1): from the solved network, `cfg`, the
  value-flow ledger (`value_flows.value_flow_ledger`) and `FinanceInputs` — the owner's operating-year
  template per period (C3), the overnight capex per asset (C6, from `physical_quantities`), degradation
  links, the base year, the resolved packs. The P3 pinned mapping (a line → one `CashflowLine` per
  operating year; `annuity` / `model_only` not converted; `None` → not established).
- **Finance inputs storage:** `solver_config.finance` (a dict, `FinanceInputs` JSON), `GET/PUT
  /api/simulation/finance` with If-Match (the value-flows pattern), persisted with the project, bundle
  round trip; the P3 "hygiene" item — the connection capacity fee's shape (`fee_eur_per_mw_year`) checked
  at the solver-config PUT — lands here (same route family).
- **The study:** `services/finance/investment_case_runner.py::start_investment_case` (the `eh_study`
  pattern: thread, progress, `publish_study`, abort); `STUDY_KEYS` / `STUDY_LABELS` / `ABORTABLE_STUDIES`
  gain `investment_case`; `ProjectSolverState.investment_case`; routes `POST/GET /results/investment_case`,
  `POST /results/investment_case/abort`, `GET /results/investment_case/report` (`ic_report_http_payload`),
  `GET /results/investment_case/export.xlsx`; the study stores an `InvestmentCaseReport` with `project`,
  `debt`, `tax`, `participants`, `gates` filled and the other sections `skipped` (P5–P7) — the P4 part of
  the assembler lives in `services/finance/report.py::assemble_finance_sections`.
- **Registration:** route inventory, `ROUTE_SURFACES`, the facade and range tests, the swap-guard abort
  regex, `RESULT_STATE_KEYS` (already holds `investment_case_report`), the seam test case.
- **Chat:** `run_investment_case` (execution_long_running), `get_investment_case` (summary under the
  4,000-character cap; `detail="cashflows"` paged), `solve_ppa_price`, `explain_cashflow` (the lines that
  drive IRR / min DSCR: the largest contributions by stream and year) — the four guard tests, error kinds
  in `tool-error-kinds.json`; the name does not collide with the existing `explain_investment`.
- **xlsx export** (`export_xlsx.py`, openpyxl, the `asset_results` pattern): one sheet per section plus
  `CashflowLines` and an `About` sheet (packs and hashes, assumptions hash, the CFADS definition, the WACC
  gate); an **Excel round trip** test reads the workbook back and matches every number.
- Tests: the adapter on the integration fixture (template = the ledger's owner net to the cent; the seam
  year-1 EBITDA check); the routes (409 during a solve, 204 before a run, abort); persistence and bundle
  round trip; chat tools; xlsx round trip.

## WP4.7 Frontend: finance inputs, results, export

- In the Investment tab: a **Finance inputs** section (dates, analysis years, capex phasing and
  contingency, escalation by class, degradation by asset, tranches with sizing / shape / fees / DSRA, tax
  pack + Hebesatz / state slot + loss treatment, incentives, WACC / cost of equity / inflation, solve-for-PPA)
  saving through `PUT /simulation/finance` (the server judges; 422 mapped to fields, the WP3.7 pattern);
  a **Run** control with progress and abort; **Results**: headline returns, the WACC gate chip, the
  cashflow table by year and stream, the debt schedule with DSCR, the tax table; completeness chips per
  section; **Export** (xlsx).
- Tests: the form round-trips a `FinanceInputs` unchanged; a 422 lands at its field; the run / abort flow
  with mocked API; the results view with a fixture report (including `not_established` sections);
  `expectAllButtonsNamed`.

---

## Phase 4 e2e QA gate

- [ ] `backend/tests/qa_investment_case.py` (auto-discovered): S1, S1b, S2, S3 parity to T through the
  engine; F1–F5 by hand; the integration fixture through the routes (finance PUT → solve → value flows →
  `POST /results/investment_case` → report → xlsx round trip); a `None` ledger line and a missing
  escalation class give `not_established` (never a number); the P1–P3 drivers still pass.
- [ ] Frontend vitest and `tsc` green.
- [ ] Full backend `not slow`, all QA drivers green; findings note
  `docs/superpowers/findings/<date>-ic-p4-finance.md` (with every SAM deviation and its size); assessor
  verdict recorded here.
- [ ] ADR-0002: the live probe for P2–P4's chat changes run and recorded, or stated as owed.

## Scope boundaries (not P4)

- Tax equity structures (P7); per-asset CODs and archetype-specific cash streams (P5); realistic-dispatch
  revenue and the haircut (P6); scenario matrix, tornado, P50/P90 (P7); the full report assembler and
  narration (P7).
- SAM features left out (stated deviations): property tax, insurance, working-capital / receivables /
  equipment reserves, state ITC, CBI / IBI / PBI, TOD revenue factors, capacity payments in the finance
  module (they are contracts).
- State / provincial packs beyond empty slots (spec §14).

## Risks

1. **SAM conventions not in the SAM documentation** (first-year SL convention, DSRA timing, fee
   financing, reserve interest). Mitigation: the oracle decides; each convention is pinned by a test once
   matched, and a mismatch the engine chooses not to follow is a recorded deviation with its size.
2. **Pack content accuracy** (dated statutory values). Mitigation: C11 — every value cited, reviewer
   checks against the source, unsourced = absent; dated cliffs as pack versions.
3. **Multi-period templates** (C3) are a modelling approximation; the findings note states it and the
   integration fixture covers the flat case.
4. **Long-running study** reuse of the `eh_study` machinery (swap guard, abort regex, campaign gating):
   the registration checklist from the survey is a WP4.6 test list, not memory.

---

## Plan review

_pending_
