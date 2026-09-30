# Edge Investment Case — Phase 4: finance engine, single owner (plan v1.0)

**Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` §4.2 / §4.2a (finance contracts),
§4.3 (report), §6.1–6.6 (finance engine), §11 (packs), §12 (API, chat, UI), §13 (P4 row), §15 (oracles,
provenance), decisions 1, 10, 14. **Outline:** P0–P1 plan, "P4 Finance engine (single owner)".
**Carried in (each placed below — "Carry-in register"):** P0 gate findings 5 and 7; P2 pins (contract
prices indexed to the modelled year — "P4 escalates from the modelled year onward, never both"; settlement
covers the represented hours of one period-year — "P4 applies period years"; tenor, escalation and tax are
P4's; the DSR slack cost decision; `contracts_record` persistence; the windowed-tier residue); P3 pins (the
ledger → `CashflowLine` mapping, WP3.1; overnight capex to the owner from `asset_owners`; `tax_levy` lines
are deductible opex; `annuity` / `model_only` lines not converted; a `None` line → that participant's
returns `not_established`; per-asset export parts "→ P4 if the returns need them"); P3 "P4 hygiene" (the
capacity fee's shape at the PUT; `replacesInline`'s load-time re-check). **Branch:**
`claude/energy-tool-features-research-fdixs0`.

**Process (owner's instruction, spec §13):** this plan → review loop until PASS → per WP: TDD, then an
implementation-review loop until PASS → Phase 4 e2e QA gate (`qa_investment_case.py` discovered by
`run_qa_drivers.py`, frontend vitest + `tsc`, full backend suite, findings note, independent assessor)
before P5.

---

## What P4 delivers

**Money over time for a single owner.** From a solved, billed and ledgered site (P1–P3), the finance engine
builds an annual cashflow from financial close to the end of the analysis period: capex phased over
construction with interest during construction, the operating year replicated with escalation,
degradation and contract indexation and tenors, replacement capex and a terminal value; debt sized by
amount, gearing or a DSCR target, with fees and a DSRA; corporate tax with depreciation and loss treatment
from dated jurisdiction packs; incentives (ITC, PTC, grants) with dated eligibility; and the returns —
unlevered project IRR, equity IRR pre/post tax, NPV, payback, DSCR / LLCR / PLCR, a finance-consistent
LCOE, a **lifecycle-cost NPV**, and **solve-for-PPA-price**. Returns are on the **incremental** cash of the
investment against the site without it (C13). It is validated against **NREL SAM "Single Owner"** on three
reference cases generated with PySAM and committed with provenance, and it is reachable through a
long-running study (`POST/GET /results/investment_case`, abort, report, xlsx export), chat tools and the
Investment tab.

**Not P4:** tax equity (P7); multi-participant returns beyond the single owner (the engine takes any
participant list; P4's gate and UI are single-owner, MVP-A); realistic dispatch (P6 — P4 values the
perfect-foresight dispatch, `mode="pf"`); scenario matrix and P50/P90 (P7); the full report assembler with
narration (P7 WP7.4 — P4 fills the finance sections).

## Conventions pinned by this plan

**C1 — Where the arithmetic lives, and imports.** Pure functions on annual `numpy` arrays in
`services/finance/`: `timeline.py`, `cashflow.py`, `debt.py`, `tax.py`, `incentives.py`, `metrics.py`,
`export_xlsx.py`, `investment_case_runner.py`. They take a plain **`FinanceCase`** (frozen dataclasses: the
operating-year template, the counterfactual, the assets, the owner, the resolved pack rules) and import
nothing from `services.results`, `services.solver*` or routers. The one adapter that reads a solved network
— **`services/results/finance_case.py::build_finance_case(n, cfg, fin, *, result_df)`** — lives in the
results layer. The runner never imports it: **the router injects `build_case: Callable[[], FinanceCase]`**
(the `eh_study` "state is injected" pattern). This closes **P0 gate finding 7**: the tripwire's
`FORBIDDEN_PREFIXES` for `services/finance/**` gains `services.results`, and a **subprocess test** imports
every `services.finance.*` module and asserts `services.solver_service` is absent from `sys.modules`
(transitive, not only direct). `finance_case` joins `IC_RESULTS_MODULES`.

**C2 — The year axis and the case dates.** Integer years. `y0` = the calendar year of `financial_close`;
construction years `y0 … cod_year − 1` (none when COD is in `y0`); operating years `cod_year … cod_year +
analysis_years − 1`. `FinanceInputs.analysis_years` (1..60, SAM `analysis_period`) is **required**; the
adapter proposes the owner assets' shortest lifetime as the default, never an implicit truncation. One owner
COD in P4 (assets of the owner with different CODs → `cod_mismatch`; per-asset CODs are P5's). Case dates
drive eligibility and pack schedules: `financial_close`, `acquisition_date` (property acquired — bonus,
degressive AfA), `construction_start` (begin-construction tests), COD = placed in service. An asset whose
lifetime ends inside the analysis period needs a replacement entry for it or the case is refused
(`asset_lifetime_short:<asset>`); multi-period **staged builds** (an owner asset with `build_year` after the
first period) are refused in P4 (`staged_build_not_supported` — P5).

**C3 — The operating-year template must be a year.** The ledger (P3) is per modelled period, unweighted,
over the **represented hours** (P2 pin). The adapter checks Σ objective weights per period: within 0.5 % of
8,760 (8,784 in a leap year) → annual; otherwise the case is refused `template_not_annual:<hours>` unless
`FinanceInputs.annualise=True`, which scales every template line by `8760/hours` and flags
`template_annualised:<factor>` in the report (demand charges on a partial year are then an approximation,
stated). Flat network: one period, key `"_"`. Multi-period: operating year `y` uses period `p` with `p ≤ y <
next(p)` (years before the first use the first, after the last use the last). **Base year** = the modelled
year (snapshots' majority year; the period year for multi-period): escalation runs from the base year (P2
pin).

**C4 — Escalation, one nominal rate per stream class.** `FinanceInputs.escalation` keys: `opex`, `fuel`,
`tariff`, `ppa`, `export`, `capex` — nominal rates, factor for year `y` = `(1+r)^(y − base_year)`. Stream
classes per `ValueStreamKind` (table in WP4.1). Contract lines use their own `indexation_pct_per_year`
from the contract's own base (P2 already indexed to the modelled year; P4 continues forward — never the
generic `ppa` rate on top); a contract without indexation uses `ppa`. **A class with cashflows and no rate
is `not_established`** (closes P0 finding 5 for `escalation`). **SAM mapping:** SAM escalates O&M
additively, factor `(1 + inflation + escal)^(k−1)` with year 1 unescalated (review round 1 #6, PySAM:
ratios 1.035 / 1.071225 at i = 2.5 %, e = 1 %) — i.e. a nominal rate `i + e` from base year = COD year; the
oracle mapping sets `base_year = cod_year` and `opex = inflation + escal`. SAM's `ppa_escalation` is
already nominal.

**C5 — Degradation.** Per asset: a constant annual rate or a per-year list (`degradation_by_asset: dict[str,
float | list[float]]`; an owner generator without an entry is `not_established` — 0 must be typed).
Operating year `k` (k = 1 at COD) scales the asset's **generation-linked** cash by `(1−d)^(k−1)` (SAM,
verified). Generation-linked = the per-asset parts of the export split (**the adapter reads
`LedgerInputs.export_split` per `(component, asset)`** — the merged ledger lines carry no `asset`, review
#10) and contract lines settled on the asset's output (PPA pay-as-produced / as-consumed volume). **The bill
effect** of degraded behind-the-meter generation (more import) is **modelled to first order**: the avoided
import value the asset provides (counterfactual bill − actual bill, C13) is scaled by the asset's
generation share of the site's on-site supply and the same factor; the remainder of the bill is not
re-rated (demand charges are not re-optimised — stated as `degradation_bill_first_order`).

**C6 — Capex, IDC, tax basis.** Overnight capex per asset from the seam (`overnight_cost × p_nom_opt`,
`None` → capex `not_established`, never the annuitised `capital_cost`), × (1 + `contingency_share`)
(`None` → `not_established`), phased by `capex_phasing` (one entry per construction year), owned per
`asset_owners`. IDC accrues on the drawn balance at the tranche rate (mid-year drawdown, stated),
capitalised into the debt. **Tax basis:** depreciable basis and ITC base = installed capex incl.
contingency (SAM: `total_installed_cost`; review #2 — fees and DSRA are in neither); IDC is added to the
depreciable basis (real-world capitalisation; SAM has no IDC — oracle cases have none); financing fees are
treated per `financing_fee_tax: "amortised" | "not_deducted"` (a case option; SAM = `not_deducted`, the
oracle cases use it; `amortised` spreads the fee over the tenor as a deduction). Replacement capex `(year,
asset, amount)` in base-year money, escalated by `capex`, depreciated on the asset's schedule from its year.

**C7 — Tax.** A pack's corporate tax is an ordered list of **layers** — a pack rule `tax_layers` (the
schema lives in `packs/base.py`, not the unused pydantic `TaxPack`): `{name, rate | rate_schedule,
deductible_in_later_layers, depreciation_profile, loss_rules}`. Each layer has **its own depreciation
profile** (SAM: federal and state depreciation differ — review #7; US states commonly decouple from bonus)
and its own loss pool (German GewSt keeps its own §10a GewStG pool). Year-dependent rates (the KSt path,
the Mindestbesteuerung share) are **schedules inside one pack version** (C11). **Loss treatment:**
`tax_losses: "offset_other_income" | "carryforward"` (case input, required): SAM Single Owner = offset
(negative tax is a benefit) — the oracle cases; `carryforward` applies the layer's loss rules. Levies
(`tariff_item_kind="tax_levy"`) are deductible opex (P3 pin), stream `tax_levy`, never corporate tax.
Interest limits (§163(j), Zinsschranke, NL art. 15b) apply only in `carryforward` mode with their pack
rules and case inputs (C11 / WP4.3).

**C8 — Debt and CFADS.** **CFADS = EBITDA − major-equipment reserve funding** (SAM `cf_cash_for_ds`; the
equipment reserve is off in P4, so CFADS = EBITDA). **DSRA funding / releases and reserve interest are not
in CFADS** (review #3, PySAM: year-1 CFADS = EBITDA = 6,021,826.7 while the DSRA released 3,187.3); they sit
below: pre-tax equity cash = CFADS − debt service + reserve interest ± DSRA movement. Reserve interest is
taxable. Tranche sizing: `amount`; `gearing` with `gearing_base: "capex"` (installed capex incl.
contingency — SAM's `debt_percent` base when fee and DSRA are 0; review #2: with a fee SAM computes D =
g·TIC·(1+g·f), a one-step, not a fixed point — a recorded deviation, measured by the extra oracle case
S3f); `dscr_target` sculpting. **Sculpting is closed form** on pre-tax CFADS: D = Σ_t (CFADS_t / DSCR)
(1+r)^−t over the tenor, payment_t = CFADS_t / DSCR (PySAM: SAM's debt = this sum to 7e-9); fees and DSRA
are equity-funded. **The circularity is in gearing** (fees, DSRA and IDC depend on the debt amount when
`gearing_base="total_uses"`) and in the `max_gearing` cap on a sculpted tranche (SAM
`dscr_maximum_debt_fraction`, base = TIC × (1+fee), review #2): fixed point, relative tolerance 1e-6, ≤ 50
iterations, non-convergence → `debt` `not_established` with the residual (spec §6.3). Repayment: `annuity`
(SAM `payment_option=0`), `level` principal (`payment_option=1`), `dscr_target`. Grace years count
**inside** the tenor (SAM `loan_moratorium`, interest-only); **SAM ignores the moratorium under sculpting**
(round 2 N3: `debt_option=1` with `loan_moratorium=1` gives the no-moratorium result) — our sculpted
tranches refuse `grace_years > 0` (`grace_with_sculpting`). DSRA = `dsra_months/12` × next year's debt service, funded at
close, adjusted yearly, released at maturity.

**C9 — Returns.** IRR by `scipy.optimize.brentq` on NPV over a bracket found by scanning `r ∈ (−0.99, 10]`
for a sign change of NPV; the root returned is the one **closest to 0 above −0.99** found by the scan (the
conventional IRR); no sign change → `None` + `irr_not_established:no_sign_change`; more than one sign change
in the cash series → solved, flagged `irr_multiple_sign_changes`. NPV at a stated rate, end-of-year
discounting with `y0` undiscounted (SAM, verified). **Which cash, which rate:** *unlevered project* (capex,
CFADS, unlevered tax, incentives, terminal value) at `wacc_nominal`; *equity* (after debt, after tax) pre and
post tax at `cost_of_equity`; SAM's "project" return is the equity return (review #1 — mapping table
below). Payback: first year the cumulative undiscounted post-tax equity cash ≥ 0 (linear interpolation,
stated). DSCR = CFADS / debt service (senior and total); min, average; LLCR = PV(CFADS over the remaining
loan life, at the debt rate) / debt at COD; PLCR the same over the project life. LCOE = −NPV(annual
costs) / NPV(energy) (SAM `lcoe_nom`, verified) with `lcoe_real` alongside. **Lifecycle-cost NPV** =
NPV of the owner's total cash with the investment (costs and revenues, not incremental) at
`cost_of_equity` — always reported (C13). **Solve-for-PPA:** `brentq` on the price of a contract the owner
**sells**, whose settlement is linear in price and whose `changes_dispatch` is false (a finance-only path
cannot re-dispatch → refused `solve_ppa_needs_redispatch`); target after-tax equity IRR in a target year
(SAM `ppa_soln_mode=0`, `flip_target_percent`, `flip_target_year`, verified to 1.8e-14); bracket 0 … a cap;
no root → `not_established`. The solve never mutates stored inputs.

**C10 — The WACC gate (spec §6.6).** `wacc_vs_discount_rate_consistent` = `|wacc_nominal −
cfg.discount_rate| ≤ 1e-6` **and** no owner asset's own `discount_rate` (the overnight-cost override,
`models/schemas.py:140`) differs from `wacc_nominal`, **and** — only when `auto_discount_periods` is on —
`|inflation − cfg.inflation_rate| ≤ 1e-6` (otherwise the inflation leg is `n/a`: the LP never used it);
`None` when a finance value it needs is `None`. The report states the annuity basis (nominal rate on real
costs) and the PV basis (real, Fisher, only under `auto_discount_periods`) and names each number the sizing
used. The gate never feeds back into the LP (decision 10).

**C11 — Packs are data with sources, versioned by the law's date.** Every rule is a `Rule(value, source)`
citing the statute or official publication (section / table). **`load_pack(as_of=financial_close)`** picks
the law state; rates that change **per tax year** during the case (the KSt path to 2032, the Mindestbesteuerung share)
are **schedules inside that version** (`{"schedule": [[from_year, value], …]}`), read per tax year; values
fixed **once by a case date** are rules tested against the case dates (C2): bonus by acquisition date,
OBBBA's begin-construction / placed-in-service tests, the degressive AfA window, **the §48E storage
applicable percentage (100 / 75 / 50 / 0 % by the year construction begins, credit taken once at placed in
service)** and **the Canadian Clean Technology ITC rate (by the year the property becomes available for
use)** — round 2 N4. Values listed in WP4.3/4.4 are the **expected content**; each lands only with its cited
source, and the implementation reviewer checks each against the source text; a rule that cannot be sourced
is absent (`not_established` at lookup). `pack_hashes.json` pins **per version**.

**C12 — Unknown is `None` + a flag.** A `None` ledger line, overnight cost, escalation class, degradation
entry, pack rule, fee, DSRA months or case date that a computation needs makes the dependent section(s)
`not_established` with the reason; headlines built on them stay `None` (ADR-0001). A required input the
user must state (fees, DSRA months, grace years, `tax_losses`) has no default: `None` until typed, 0 must be
typed.

**C13 — Incremental returns against a counterfactual (review #8; round 2 N1, N2, R1, R8).** An edge
owner's ledger net is mostly the supply cost, so returns on it are meaningless. The adapter builds a
**counterfactual supply cost**: the same site, tariff and connection agreement **without the owner's
investable assets**:
- **The meter:** import = the site-side **electric** loads' demand (`loads_t.p_set`, or the static `p_set`
  without a series; grid-side and non-electric loads excluded) minus the actual dispatch's shed (DSR
  activation and VoLL slack) — i.e. **the served load**, the same shed on both sides because P4 does not
  re-dispatch; export 0. A PoC import chain with efficiency ≠ 1 → `counterfactual_not_established:lossy_poc`
  (round 3 M1). Shed volume is disclosed (`load_shed_excluded:<mwh>`), never valued (**DSR slack, P2 carry-in: an
  opportunity cost, excluded from cash on both sides and disclosed**).
- **The tariff bill:** rated by a new **`billing.rate_meter(n, cfg, import_mw, export_mw, *, meter_history)`**
  factored out of `bill_site` — it calls `tariff_engine.rate(...)` with exactly `bill_site`'s arguments
  (`_represented` → `billing_period` / `represents_hours`, `capacity_kw` = the PoC `p_nom_opt`, `timezone`,
  `power_factor`, `meter_history` defaulting to `cfg.meter_history_peaks_kw` exactly as `bill_site` does);
  `bill_site` then calls it, and a test pins every P2 bill unchanged. With no import tariff the bill term is
  0 on both sides (commodity only), not `None`. A
  counterfactual peak above the PoC capacity is flagged `counterfactual_exceeds_connection` (a BESS that
  shaved the peak makes it real) and rated as metered.
- **The commodity (round 3 M1):** when the site's energy is a grid-side supply generator (the P3 ledger's
  `commodity_from_grid_side_generator` line, `participants.py:559`), **both** commodity terms are computed on
  the meter basis, Σ_t w_obj,t · import_t · mc_t (`generators_t.marginal_cost` where present). The
  actual-side term must equal the ledger's commodity line within a cent — otherwise (grid-side loads or
  costed grid-side assets beside the supply generator, a quadratic cost, several differently priced grid-side
  generators) → `counterfactual_commodity_not_established`, never a number. A counterfactual import above the
  supply generator's `p_nom × p_max_pu` is flagged `counterfactual_exceeds_supply`.
- **The connection fee lines** are identical on both sides (by construction) and cancel.
- **Contracts** that exist only because of the assets (PPAs on them, EaaS / lease on them) are absent.

**Incremental operating cash = (counterfactual bill + commodity + connection) − (actual bill + commodity
+ connection) + asset-linked revenues (export, contracts) − asset-linked costs (fom, vom, fuel).** Returns
(IRR, NPV, payback, solve-for-PPA) are on incremental cash + capex; the lifecycle-cost NPV (C9) on the
total. A site with no load → a zero counterfactual → incremental = the owner's cash. The counterfactual's
hash (tariff, served load, connection, commodity) is in the report's provenance.

**C14 — Contract tenor (review #11).** A contract's lines run from COD for `tenor_years` operating years,
indexed from the contract's `base_year`; after the tenor they **stop** (the physical energy's value stays
in the bill / export lines, which continue). A tenor ending inside the analysis period is disclosed
(`contract_ends:<id>:<year>`); a merchant-tail price is not P4.

---

## Fixtures and oracles (bind the gate)

**The SAM oracle.** PySAM **7.1.1.post1** (`nrel-pysam`, BSD-3-Clause) is not an app dependency: it is
installed only in a developer venv to (re)generate fixtures. `tests/fixtures/investment_case/sam/`:
- `generate_sam_cases.py` — builds each case from `SystemOutput.gen` (a committed profile) and the case's
  overrides on `Singleowner.default("PVWattsSingleOwner")`, sets `en_electricity_rates=1` (required for
  `ppa_soln_mode=0` standalone, review #7), **zeroes every default that P4 does not model** (below), runs,
  and writes `<case>.json` = `{provenance: {pysam_version, ssc_version, generated, script_sha,
  profile_sha, license}, sam_inputs: <the FULL m.export() of inputs>, outputs: {scalars, cf_* arrays},
  deviations: [...]}`. Not run in CI; a test pins `script_sha` / `profile_sha` against the committed files.
- `gen_profile.csv` — one synthetic 8,760-hour profile (clear-sky PV shape × a seeded cloud factor; seed and
  formula in the script).
- `PROVENANCE.md` + `SAM_LICENSE`.
- **`sam_case.py`** — the single reviewed **SAM → `FinanceCase` mapping** (review #27) used by every
  parity test: `base_year = cod_year`, `opex = inflation + escal` (C4), the PPA line = `gen × price ×
  (1+ppa_escalation)^(k−1) × (1−d)^(k−1)`, tax layers state then federal with their own depreciation
  profiles, `tax_losses="offset_other_income"`, `financing_fee_tax="not_deducted"`, counterfactual = none
  (a generator-only case: incremental = total).

**Zeroed defaults** (not modelled in P4; stated deviations in the findings): property tax, insurance,
`months_working_reserve`, `months_receivables_reserve`, `equip1..3_reserve_cost`, `reserves_interest`
(except S2), `construction_financing_cost`, `ptc_fed_amount` (default 0.03!) except where a case sets it,
state ITC, CBI / IBI / PBI, TOD factors (`dispatch_tod_factors` = 1), capacity payments, `salvage_percentage`
except S3, the default depreciation mix (reset to the case's allocation).

**SAM → P4 output mapping (review #1).**

| SAM output | P4 quantity |
|---|---|
| `cf_project_return_aftertax`, `project_return_aftertax_irr`, `_npv` | the **equity** post-tax cash, IRR, NPV (equity rate = SAM `nominal_discount_rate` = (1+real)(1+infl)−1) |
| `cf_project_return_pretax` | equity pre-tax cash; its IRR is computed by the test (`irr(...)`; SAM 7.1.1 has no pre-tax IRR scalar) |
| `cf_cash_for_ds`, `cf_ebitda`, `cf_debt_*`, `cf_pretax_dscr`, `cf_reserve_debtservice`, `cf_feddepr_*`, `cf_stadepr_*`, `cf_fedtax`, `cf_statax`, `cf_ptc_fed`, `itc_total`, `size_of_debt`, `min_dscr`, `ppa_price` (S1b), `lcoe_nom` | the same-named P4 arrays / scalars |
| — | the **unlevered project IRR** has no SAM counterpart except in S1 (all equity: it equals the equity IRR) |

**Units:** SAM's `ppa_price_input`, `ppa_price` and `lcoe_nom` are ¢/kWh and its energy is kWh;
`sam_case.py` converts to currency/MWh (× 10) and MWh, and the tests compare in P4 units (round 2 R2).

| Id | SAM set-up (beyond the zeroed defaults) | What it checks (to T) |
|---|---|---|
| **S1** | all-equity, SL-20 100 % federal and state, federal 21 % + state 7 %, PPA fixed with 1 %/yr escalation, O&M per kW-yr + fixed with inflation 2.5 % **and `om_capacity_escal = om_fixed_escal = 1.0`** (catches C4; `sam_case.py` refuses unequal O&M escalations — one `opex` class, round 2 N5), degradation 0.5 %/yr, 25 years; **S1b** `ppa_soln_mode=0`, target 11 % in year 20 | revenue, O&M, EBITDA, federal / state depreciation (half-year SL: 2.5 %, 5 % × 19, 2.5 % in year 21), state / federal tax, equity (= project) cash; IRR, NPV; S1b the solved price |
| **S2** | DSCR sculpting (1.3, 18 yrs, 7 %, fee 2.75 %, DSRA 6 months, `reserves_interest` 1.75 %), MACRS-5 90 % + SL-20 10 %, federal bonus 100 % on MACRS-5 **and state bonus 0** (per-layer depreciation, review #7) | debt size, service / interest / principal, DSRA balance and movements, reserve interest, CFADS (= EBITDA), DSCR per year and min, depreciation per layer and class, taxes, equity cash pre / post, IRR, NPV, LCOE |
| **S3** | gearing 60 % of TIC, fee 0, DSRA 0, standard amortisation, 15 yrs, 6 %, `loan_moratorium` 1 (a year-1 interest-only grace inside the tenor, then a 14-year annuity — round 2 N3); federal ITC 30 % with the basis-reduction flag federal = 1, state = 0 (stated); MACRS-5 100 %, no bonus; salvage 10 % | debt schedule, ITC in year 1, reduced federal basis, unreduced state basis, salvage in EBITDA and taxed, taxes, equity cash, IRR, NPV |
| **S3f** | S3 with a 2.75 % closing fee | the recorded **gearing-with-fee deviation** (C8): SAM D = 0.6·TIC·(1+0.6·f) vs our `gearing_base="capex"` D = 0.6·TIC — the test asserts the difference is exactly the SAM one-step term |

**Tolerance T:** per-year arrays |ours − SAM| ≤ max(1.0, 1e-6·|SAM|) currency units; IRR ≤ 1e-5 absolute;
NPV, debt size, solved PPA price ≤ 1e-6 relative; DSCR ≤ 1e-6 absolute. A SAM convention not followed is a
**recorded deviation** (findings, the JSON's `deviations`, a test asserting its size) — never a loosened
tolerance.

**Hand oracles** (self-authored, stdlib arithmetic beside the fixture): **F1** IDC (two construction years,
60/40, one loan at 8 %); **F2** DE (KSt + SolZ + GewSt with the §8 Nr. 1 add-back and its own §10a pool,
AfA, Mindestbesteuerung, 6 years incl. two loss years); **F3** US (MACRS-7 with 100 % bonus, §163(j) with the
ATI basis, NOL 80 %, 5 years); **F4** escalation / indexation / tenor (a PPA ending in year 8, a base year two
years before COD); **F5** terminal value and replacement; **F6** the counterfactual on a toy site (a 1 MW
load, 2 MW PV, a TOU tariff with a demand charge, a grid-side supply generator with a marginal cost —
the incremental cash, bill and commodity, by hand).

**The integration fixture (review #9).** A new **`build_edge_hourly_year`** (with `overnight_cost` and a
per-asset `discount_rate` on the owner's assets — PyPSA needs both — set equal to the case's
`wacc_nominal`, so the C10 gate reads consistent; a variant with a different asset rate asserts the
inconsistent state; round 2 R7): the edge site of
`build_edge_15min` (US tariff, PV + BESS, a firm connection fee, an export price, the load) at **hourly
resolution for a full year** (8,760 snapshots, solves in seconds), `single_owner`, with a `FinanceInputs` →
the study end to end through the routes; the template is annual (C3), the owner's incremental cash is
positive in operation and the equity IRR is **finite** (asserted, with its value pinned), year-1
incremental EBITDA = (counterfactual bill + commodity) − (actual bill + commodity) + export − asset opex
to the cent (C13). The 7-day
`build_edge_15min` asserts the `template_not_annual:168` refusal, and with `annualise=True` a number flagged
`template_annualised:52.14` — after checking that its bill is established (monthly and demand items with
`billing_period=None`, not `period_not_billed`; round 2 R6).

---

## Dependency graph

```
WP4.0 foundations: oracle fixtures + mapping, contract amendments, spec errata, tripwire, integration fixture
 ├─ WP4.1 timeline, operating cashflows, tenor, terminal value (+ npv/irr core)   ← S1 revenue/opex/EBITDA, F4, F5
 │   ├─ WP4.3a tax + depreciation engine; us_federal, eu_de packs                 ← S1 tax and returns, F2, F3
 │   │   ├─ WP4.3b eu_nl, ca_federal packs
 │   │   └──────────────┐
 │   └─ WP4.2a debt: amount / gearing (+ fixed point), annuity / level, fees, IDC   ← S3 debt schedule, S3f, F1
 │       └─ WP4.2b DSCR sculpting + max_gearing cap + DSRA + reserve interest      ← S2 debt, DSRA, DSCR
 │           │          │
 │           └─ WP4.4 incentives with dated rules (needs 4.3a + 4.2a)             ← S3 ITC and taxes
 │               └─ WP4.5 metrics, solve-for-PPA, WACC gate, all-case parity       ← S1, S1b, S2, S3, S3f
 │                   └─ WP4.6a adapter: FinanceCase, counterfactual, template checks  ← F6, the integration fixture
 │                       └─ WP4.6b finance inputs storage + route, runner, study routes, persistence
 │                           ├─ WP4.6c chat tools
 │                           ├─ WP4.6d xlsx export
 │                           └─ WP4.7a FE finance inputs form ─ WP4.7b FE run, results, export
```

WP4.3b may land last (MVP-A needs `us_federal` + `eu_de`).

**Per-WP invariants.** The P1–P3 gates stay green (`qa_commercial_lp.py`, `qa_billing_contracts.py`,
`qa_value_flows.py` in each WP's verification). **P4 never changes the LP, a committed commercial hash or the
ledger** (decision 10). C12. Every new persisted field is backward compatible (P3 projects load unchanged),
mirrored in `frontend/src/api/types.ts` and covered by the new `types.ts` parity test (WP4.0). Tripwires
(C1). The SAM parity checks a WP can reach run in its verification.

---

## WP4.0 Foundations

- **SAM oracle fixtures** as specified: generator, profile, S1 / S1b / S2 / S3 / S3f JSON with the full
  input export, provenance, license, the sha pin test, the shape test, and **`sam_case.py`** (the mapping,
  reviewed here once).
- **The integration fixture** `build_edge_hourly_year` (and its solve time pinned under 30 s).
- **Spec errata** (a dated "Errata" block at the end of the spec): §6.3 CFADS is pre-tax per SAM (C8);
  §6.6 "L641–698" → `assumptions.py` block 4b; §4.2 `escalation` gains `export` and `capex`; §6.1 gains
  `analysis_years` and the case dates; §6.6's gate gains the asset-rate and `auto_discount_periods` legs
  (C10); returns are incremental against a counterfactual (C13).
- **Contract amendments** (`models/finance.py`, backward compatible; `types.ts` mirrored):
  - `FinanceInputs`: `analysis_years`, `acquisition_date`, `construction_start`, `annualise: bool = False`,
    `tax_losses`, `financing_fee_tax`, `hebesatz_pct` (DE GewSt), `state_rate` (US state layer; `None` =
    not stated, distinct from a typed 0), `pwa_met: bool | None` (US ITC rate), `small_business_163j:
    bool | None`, `reserves_rate`, `solve_ppa: SolvePpa | None` (`contract_id`, `target_irr`,
    `target_year`); `degradation_by_asset: dict[str, float | list[float]]`; `escalation` documented as the
    six C4 classes.
  - `DebtTranche`: `gearing_base: Literal["capex", "total_uses"]`, `max_gearing`, `rate: float |
    list[float]` (each element ≥ 0); **P0 finding 5:** `upfront_fee`, `commitment_fee`, `dsra_months`,
    `grace_years` default → `None` (typed at run time; the commitment fee is not required when there is no
    construction period); `TaxEquityStructure.itc_recapture_years` → `None` (P7's).
  - `Provenance`: `source_id`, `contract_id`, `period` (P3 pin).
  - A finance-side **`CashflowStream = ValueStreamKind | Literal["corporate_tax", "terminal_value",
    "financing_fee", "reserve", "interest", "principal"]` (incentives use the existing `incentive`
    kind)** for `CashflowLine`
    (round 2 R4): corporate tax never collides with the P3 levy stream `tax`, and the ledger's
    `ValueStreamKind` (the P3 FE unions, the Sankey, committed hashes) is unchanged — a test pins that no
    committed P3 hash moved.
  - `FinanceInputs.participants` is **derived**: the value-flow config's participants win; a stored list
    that differs is refused (`participants_mismatch`).
- **Tripwire and import test** (C1); **a `types.ts` parity test** (new: every `FinanceInputs` /
  `DebtTranche` / report field the FE uses is present in `types.ts` with the same optionality — review #17).
- **Carry-in placements recorded** (see the register) and the P0 finding-5 archetype defaults (`GensetSpec`
  heat rate, `DataCentreLoadSpec` UPS loss, `BessSpec` DoD) **deferred to P5** in the P0 findings note.

## WP4.1 Timeline, operating cashflows, tenor, terminal value

- `timeline.py`: `Timeline` (C2) with the refusals (`cod_mismatch`, `analysis_years_missing`,
  `financial_close_after_cod`, `asset_lifetime_short`, `staged_build_not_supported`).
- `cashflow.py`: the stream-class map (`energy_import`, `network_*`, `demand_charge`, `retail_fixed`,
  `certificates`, levies → `tariff`; `energy_export`, `ancillary` → `export`; contract streams → the
  contract's indexation, else `ppa`; `fom`, `vom`, `other` → `opex`; `fuel` → `fuel`; `capex` → `capex`);
  operating cash per year and line key (escalation C4 × degradation C5 × tenor C14); capex per construction
  year and contingency (C6); replacement; terminal value (`none`, `fixed`, `book_value` = remaining tax basis,
  `multiple_of_ebitda`; SAM salvage = `fixed` at `salvage_percentage × TIC`, **not inflated, inside EBITDA
  and taxed** — verified); EBITDA; `CashflowLine`s with provenance.
- `metrics.py` core: `npv`, `irr` (C9).
- Tests: F4, F5; degradation (constant and list); the class map per kind; capex `None` / contingency `None`
  → `not_established`; tenor stop and disclosure; the C3 multi-period mapping on a two-period toy; a `None`
  template line → `not_established`; **S1 parity: revenue, O&M (additive escalation), EBITDA.**

## WP4.2a Debt: amount / gearing, annuity / level, fees, IDC

- `debt.py`: draws pro rata with capex phasing; IDC capitalised (C6); upfront fee (share of commitment, at
  close; equity-funded under `gearing_base="capex"`, debt-funded through the fixed point under
  `"total_uses"` — round 2 R5); commitment fee on the undrawn balance during construction; repayment from COD with
  grace years inside the tenor; `annuity` / `level`; several tranches (senior first; DSCR on senior and on
  total service); `gearing` with `gearing_base` and **the fixed point** for `total_uses` (C8), non-
  convergence → `not_established` with the residual (a forced pathological case tested here); sources and
  uses at COD, equity = balance (`debt_exceeds_uses` refused).
- Tests: F1; closed-form annuity and level; grace years inside the tenor; two tranches; the fixed point
  converges on a normal case and reports the residual on a forced one; **S3 debt schedule parity**; **S3f
  deviation size**.

## WP4.2b DSCR sculpting, the `max_gearing` cap, DSRA, reserve interest

- Sculpting in closed form on pre-tax CFADS (C8); the `max_gearing` cap (SAM base TIC × (1+fee)) with the
  fixed point; negative-CFADS years (payment 0, flagged); DSRA per C8; reserve interest at `reserves_rate`
  (taxable, below CFADS).
- Tests: **S2 parity** (debt, schedule, DSRA balance and movements, reserve interest, CFADS, DSCR per year
  and min); the cap binding on a variant; a negative-CFADS year.

## WP4.3a Tax and depreciation; `us_federal` and `eu_de` packs

- `packs/base.py`: the `tax_layers` rule schema (C7), schedule values (C11), per-version hash pins.
- `tax.py`: depreciation — straight-line with **SAM's half-year convention** (SL-n spans n+1 years: ½, 1 …
  1, ½ — verified), declining balance with switch to SL, **MACRS** (half-year, Pub. 946 Table A-1; the
  mid-quarter convention is not modelled — a stated deviation, as in SAM), **bonus**, the allocation of the
  basis across classes, **per-layer profiles**; the ITC basis hook (WP4.4); layers with deductibility;
  loss treatment (C7) with per-layer pools; interest limits (carryforward mode only).
- **`us_federal`** (version as of 2026-01-01; each sourced): 21 % (IRC §11(b)); MACRS 5/7/15/20 tables;
  the classification of each asset class (the 5-year class for energy property — **checked against the
  2025 act's change to §168(e)(3)(B)(vi)** before it is assigned; unsourced → absent); bonus 100 % for
  property acquired after 2025-01-19 (the 2025 act), the TCJA phase-down under a binding contract before
  2025-01-20 (by `acquisition_date`); NOL 80 % limit, indefinite (§172); §163(j) 30 % of ATI with the ATI
  basis (EBITDA for tax years after 2024 per the 2025 act) and the §163(j)(3) small-business exemption
  (`small_business_163j`); the state layer slot (rate from `state_rate`, own depreciation profile —
  default decoupled from bonus, stated).
- **`eu_de`** (version as of 2026-01-01; each sourced): KSt 15 % with the enacted path (schedule: −1 pt per
  year 2028–2032 to 10 %, the 2025 Investitionssofortprogramm law); SolZ 5.5 % of KSt; GewSt = 3.5 % ×
  `hebesatz_pct` (§11 GewStG; `None` → `not_established`) on the Gewerbeertrag **with the §8 Nr. 1 add-back**
  (25 % of financing costs incl. rent / lease shares above €200k) and its **own §10a loss pool (€1m + 60 %)**;
  GewSt not deductible (§4 Abs. 5b EStG); KSt loss carryforward (§10d EStG: €1m + 70 % for 2024–2027, 60 %
  after — schedule); AfA useful lives per class from the BMF AfA tables (each cited); the degressive AfA
  window (movables acquired 2025-07-01 … 2027-12-31, up to 3× SL, max 30 %) by `acquisition_date`;
  Zinsschranke (§4h EStG) as a **Freigrenze** (€3m net interest; above it the 30 % cap applies to all net
  interest) — the stand-alone / escape clauses and the EBITDA carryforward are **not modelled**, and the
  case is flagged `zinsschranke_simplified` whenever the Freigrenze is exceeded (never a silent low tax).
- Tests: **S1 full parity** (depreciation per layer, taxes, equity cash, IRR, NPV); F2, F3; MACRS tables sum
  to 100 %; bonus by acquisition date; carryforward limits and schedules; per-layer loss pools; the
  Zinsschranke flag; a missing rule → `not_established` naming it; `pack_hashes.json` regenerated per
  version and the diff reviewed.

## WP4.3b `eu_nl` and `ca_federal` packs

- `eu_nl` (sourced): VPB brackets 19 % / 25.8 % at €200k (art. 22 Wet Vpb 1969, dated); depreciation with the
  20 %/yr cap (art. 3.30a Wet IB 2001) and useful lives; loss carryforward €1m + 50 % (dated); art. 15b
  earnings stripping (24.5 % of fiscal EBITDA, €1m threshold) in carryforward mode; EIA as an incentive slot
  (absent unless sourced).
- `ca_federal` (sourced): 15 % general federal rate (38 % − 10 % abatement − 13 % rate reduction), a
  provincial slot; CCA classes 43.1 (30 %) and 43.2 (50 %, its acquisition window cited) declining balance
  with the half-year rule and the Accelerated Investment Incentive (dates cited); the Clean Technology ITC
  (30 %, 15 % from 2034 — by the available-for-use date, C11) **reducing UCC by 100 %** (s.13(7.1) ITA).
- `test_finance_packs.py`: the registry becomes {eu_de, eu_nl, us_federal, ca_federal}; hashes pinned per
  version.
- Tests: a hand case per pack (tax per layer, depreciation per class for 5 years).

## WP4.4 Incentives with dated rules

- `incentives.py`: **ITC** (share of eligible basis, cap, basis reduction as a **0/1 flag per layer with a
  fixed 50 % reduction** — SAM's `itc_*_deprbas_*`, review #7; credited to after-tax cash in year 1, not
  netted in the tax line — verified); **PTC** (rate × generation × degradation for a term; the escalated rate **rounded each year** — SAM
  rounds to $0.001/kWh, §45(b)(2) rounds and indexes by calendar year; the rounding rule is part of the
  pack rule and the hand test — round 2 R3); **grant** (amount or share at COD; basis reduction per rule); `accelerated_depreciation` (a
  schedule override); `cfd` / `capacity_payment` are contracts (§6.5).
- Eligibility against the case dates (C2); phase-out tables; the FEOC flag (`None` → `not_established` for a
  rule that requires it; `True` → refused; `False` → eligible); the US ITC **rate = base 6 % × 5 when
  `pwa_met`** (IRC §48E(a)(2)/(3); `None` → `not_established`, never an assumed 30 %).
- `us_federal` incentive data (sourced): §48E / §45Y with the 2025 act's wind / solar termination (begin
  construction by 2026-07-04, beginning-of-construction per **Notice 2025-42**, or placed in service by
  2027-12-31), the storage ITC runway and phase-down (by `construction_start`, C11), the FEOC material-assistance rules for
  construction beginning after 2025-12-31. Unsourced values absent.
- Tests: **S3 ITC** (credit, reduced federal basis, unreduced state basis, taxes); PTC by hand; eligibility on
  both sides of each cliff; phase-out; FEOC's three states; the PWA multiplier; a grant reducing basis.

## WP4.5 Metrics, solve-for-PPA, the WACC gate

- `metrics.py` (C9): unlevered project IRR / NPV, equity IRR / NPV pre and post tax, payback, DSCR min / avg
  (senior and total), LLCR, PLCR, LCOE nominal and real, lifecycle-cost NPV, solve-for-PPA (C9).
- The WACC gate (C10) and its report block.
- **All-case SAM parity** (S1, S1b, S2, S3, S3f) in one parametrised test to T, through the output mapping.
- Tests: IRR edge cases (no sign change, multiple sign changes → the pinned root, all negative); payback
  interpolation; LLCR / PLCR closed forms; solve-for-PPA refusals (not owner-sold, nonlinear,
  `changes_dispatch`), no root; the gate's states (consistent; wacc differs; an asset rate differs;
  inflation differs with and without `auto_discount_periods`; `None`).

## WP4.6a Adapter: `FinanceCase`, counterfactual, template checks

- `services/results/finance_case.py::build_finance_case` (C1): the owner's operating-year template per
  period from the ledger (P3 mapping; `annuity` / `model_only` not converted; `None` → not established);
  the annual check (C3); per-asset export parts from `LedgerInputs.export_split` (C5); `billing.rate_meter` factored out of
  `bill_site` (every P2 bill pinned unchanged); the counterfactual supply cost (bill, commodity, connection)
  and the incremental cash (C13); the shed-load exclusion (C13); overnight capex (C6); degradation links;
  contract tenors (C14); the base year; the resolved pack rules and versions; a `FinanceCase` hash.
- Tests: F6; the integration fixture (template = the ledger's owner net to the cent; the year-1 incremental
  EBITDA identity; a finite equity IRR, pinned); `template_not_annual` on the 7-day fixture and the
  `annualise` path; a load-free generator site (incremental = total); the shed-load exclusion disclosed and equal on both
  sides; several grid-side generators → `counterfactual_commodity_not_established`; a counterfactual peak
  above the PoC → `counterfactual_exceeds_connection`; **a grid-side load beside the supply generator →
  `counterfactual_commodity_not_established`** (the ledger cross-check fails; round 3 M1); a lossy PoC chain
  → not established.

## WP4.6b Finance inputs route, runner, study routes, persistence

- **Storage:** `solver_config.finance` (a dict, `FinanceInputs` JSON); **excluded from the solve digest**
  (a finance edit never marks the dispatch stale); `GET/PUT /api/simulation/finance` with If-Match (the
  value-flows pattern); a solver-config PUT keeps the stored `finance` and refuses a change to it through
  that route (the value-flows guard); persisted with the project; bundle round trip.
- **Staleness:** the stored report's `assumptions_hash` = hash(FinanceCase inputs: finance, value flows,
  commercial, dispatch digest, pack versions); a later edit to any of them → `GET /results/investment_case`
  marks the report `stale` (never silently current).
- **P3 hygiene:** the connection capacity fee's shape (`fee_eur_per_mw_year`) checked at the solver-config
  PUT (422 at save, not at the solve); `replacesInline`'s trust in the PUT: the project **load** path re-runs
  the `import_tariff_ref` hash check and flags a mismatch (`import_tariff_ref_conflict_on_load`).
- **The study:** `services/finance/investment_case_runner.py::start_investment_case(body, *, build_case,
  solver_state, state_update, publish_study)` — `build_case` injected by the router (C1); `STUDY_KEYS` /
  `STUDY_LABELS` / `ABORTABLE_STUDIES` gain `investment_case`; `ProjectSolverState.investment_case`
  (classified in `test_project_state.py`); routes `POST/GET /results/investment_case`, `POST
  /results/investment_case/abort`, `GET /results/investment_case/report` (`ic_report_http_payload`); the
  report with `project`, `debt`, `tax`, `participants`, `gates` filled and the others `skipped` —
  `services/finance/report.py::assemble_finance_sections`.
- **Registration checklist (as tests):** route inventory, `ROUTE_SURFACES`, the facade and range tests, the
  swap-guard abort regex (`test_adequacy_study_swap_guard.py`), `RESULT_STATE_KEYS` (holds
  `investment_case_report`), a `test_results_seam.py` case, `_RESULTS_STATE_SCHEMA` unchanged or bumped with a
  migration test.
- Tests: the routes (409 during a solve or another study, 204 before a run, abort mid-run), persistence and
  bundle round trip, staleness, the P3 hygiene items.

## WP4.6c Chat tools

- `run_investment_case` (Safety tier `execution_long_running`, campaign-gated like `run_eh_study`),
  `get_investment_case` (summary under the 4,000-character cap; `detail="cashflows"` paged; ids and flags cut
  as in P3), `solve_ppa_price` (C9 refusals; never mutates stored inputs), `explain_cashflow` (the largest
  contributions to the equity IRR and to the min-DSCR year, by stream and year) — no collision with the
  existing `explain_investment`.
- **The four guard tests:** `test_chat_tools_schema_match.py`, `test_chat_tools_argshape.py`,
  `test_chat_tools_endpoint_map.py`, `test_chat_tools_identity.py`; error kinds in `tool-error-kinds.json`
  (and the FE manifest test).

## WP4.6d xlsx export

- `export_xlsx.py` (openpyxl, the `asset_results` pattern): one sheet per section, `CashflowLines`, and
  `About` (packs and versions and hashes, the assumptions hash, the CFADS definition, the WACC gate, the
  counterfactual's provenance, every flag); `None` written as an explicit `not_established` cell; **formula
  injection guarded** (a user-named asset / party / contract beginning with `= + - @` is written as text);
  `GET /results/investment_case/export.xlsx`.
- Tests: an **Excel round trip** (read back; every number equal; every `None` → `not_established`); an
  injection name stays text.

## WP4.7a Frontend: finance inputs

- In the Investment tab: a **Finance inputs** section (dates, analysis years, annualise, capex phasing and
  contingency, escalation by class, degradation by asset, tranches with sizing / base / shape / fees / DSRA /
  grace, tax pack + Hebesatz / state rate / loss treatment / fee treatment / PWA / small-business, incentives,
  WACC / cost of equity / inflation / reserves rate, solve-for-PPA) saving through `PUT /simulation/finance`;
  the server judges (422 mapped to fields — the WP3.7 pattern); `None` shown as "not stated", never 0.
- Tests: a `FinanceInputs` round-trips unchanged; a 422 lands at its field; `expectAllButtonsNamed`.

## WP4.7b Frontend: run, results, export

- **Run** with progress and abort; **Results**: headline returns (unlevered, equity pre / post, NPV,
  lifecycle-cost NPV, payback, min DSCR, LCOE), the WACC gate chip, the incremental-vs-counterfactual
  statement, the cashflow table by year and stream, the debt schedule with DSCR, the tax table by layer;
  completeness chips; the stale marker; **Export** (xlsx).
- Tests: run / abort with a mocked API; a fixture report including `not_established` and `stale`;
  `expectAllButtonsNamed`.

---

## Carry-in register

| Item | Source | Placed |
|---|---|---|
| numeric defaults (fees, recapture, escalation) | P0 finding 5 | WP4.0 amendments, C4, C12 |
| archetype defaults (genset heat rate, UPS loss, BESS DoD) | P0 finding 5 | **deferred to P5** (recorded in WP4.0) |
| finance inherits the solve stack | P0 finding 7 | C1, WP4.0 tripwire + subprocess test |
| escalate from the modelled year, never both | P2 | C3, C4 |
| settlement covers represented hours | P2 | C3 (annual check / annualise) |
| tenor, escalation, tax | P2 | C4, C7, C14 |
| DSR slack: cash or opportunity cost | P2 findings | **C13: opportunity cost, excluded from cash on both sides (the counterfactual is rated on the served load), disclosed**; tested in WP4.6a |
| `contracts_record` persistence | P2 | **deferred to P7** (the report assembler persists what P7 needs; P4 persists the finance report only) |
| windowed-tier split residue | P2 | **deferred** (a billing matter, not finance; stays in the P2/P3 carried list) |
| ledger → `CashflowLine` mapping | P3 WP3.1 | WP4.6a |
| per-asset export parts | P3 | C5 (read from `LedgerInputs.export_split`) |
| capacity fee's shape at the PUT | P3 hygiene | WP4.6b |
| `replacesInline` load-time re-check | P3 hygiene | WP4.6b |
| annual per-connection tier bands disclosure | P3 | **deferred** (billing; P3 carried list) |

---

## Phase 4 e2e QA gate

- [ ] `backend/tests/qa_investment_case.py` (auto-discovered): S1, S1b, S2, S3, S3f to T through the engine
  (the S3f deviation sized); F1–F6 by hand; the integration fixture through the routes (finance PUT → solve →
  value flows → `POST /results/investment_case` → report → xlsx round trip) with a **finite** equity IRR;
  the 7-day fixture refused `template_not_annual`; a `None` ledger line and a missing escalation class →
  `not_established`; staleness after a finance edit; the P1–P3 drivers still pass.
- [ ] Frontend vitest and `tsc` green.
- [ ] Full backend `not slow`, all QA drivers green; findings note
  `docs/superpowers/findings/<date>-ic-p4-finance.md` (every SAM deviation with its size; the counterfactual
  definition; the pack sources); assessor verdict recorded here.
- [ ] ADR-0002: the live probe for P2–P4's chat changes run and recorded, or stated as owed.

## Scope boundaries (not P4)

- Tax equity (P7); per-asset CODs, staged builds, archetype cash streams (P5); realistic-dispatch revenue
  (P6); scenario matrix, tornado, P50/P90 (P7); the full report assembler and narration (P7).
- SAM features not modelled (stated deviations): property tax, insurance, working-capital / receivables /
  equipment reserves, state ITC, CBI / IBI / PBI, TOD revenue factors, the mid-quarter convention, SAM's
  one-step gearing-with-fee (S3f).
- State / provincial packs beyond slots (spec §14); the Zinsschranke's escape and stand-alone clauses; the
  merchant tail after a contract's tenor.

## Risks

1. **SAM conventions not in its documentation.** The oracle decides; each is pinned by a test once matched;
   one not followed is a sized deviation.
2. **Pack content accuracy.** C11: cited sources, reviewer checks, unsourced = absent, schedules and case
   dates for time-dependence.
3. **The counterfactual (C13)** is a modelling choice (REopt's business-as-usual): the report states it;
   degradation's bill effect is first order (C5).
4. **Multi-period templates (C3)** approximate; staged builds refused.
5. **Long-running study** registration: the WP4.6b checklist is a test list.

---

## Plan review

**Round 1 (v0.1): FAIL — 14 binding findings, 13 recommended; v0.2 addresses all.** The reviewer ran PySAM
7.1.1.post1 on each SAM claim.
- **SAM conventions (binding 1–7):** SAM's "project" return is the levered equity return (→ the output
  mapping table); `debt_percent`'s base is TIC and SAM's fee is a one-step, not a fixed point (→ C8
  `gearing_base`, S3 fee 0 / DSRA 0, S3f sizing the deviation, the tax-basis composition in C6); CFADS
  excludes DSRA movements and reserve interest (→ C8); sculpting is closed form, the fixed point belongs to
  gearing and the cap (→ C8, WP4.2a); SL uses the half-year convention (→ WP4.3a, S1); O&M escalation is
  additive (→ C4, S1 with escal 1 %); state and federal depreciation differ (→ per-layer profiles C7, S2
  state bonus 0, S3 flags stated). Generator traps (→ `en_electricity_rates=1`, the zeroed-defaults list incl.
  `ptc_fed_amount`, the full input export, the version string).
- **Semantics (binding 8–12, 14):** no counterfactual (→ C13, F6, a finite-IRR integration assertion); the
  template is not annual (→ C3, `build_edge_hourly_year`, the 7-day refusal); degradation needs per-asset
  export parts (→ C5 reads `export_split`, the bill effect first order); contract tenor (→ C14, F4); pack
  versions vs per-year rates and case dates (→ C11 schedules, C2 dates, per-version hashes); the runner's
  import (→ C1 injection, the transitive subprocess test, `IC_RESULTS_MODULES`).
- **Carry-ins (binding 13):** → the register (DSR slack decided in C13; the rest placed or deferred).
- **Recommended, taken:** 15 (graph edges), 16 (amendment fields, `ValueStreamKind` additions, layers as a
  pack rule, list bounds, required inputs), 17 (a new `types.ts` parity test), 18 (asset rates, inflation
  only under `auto_discount_periods`), 19 (digest exclusion, staleness, the guard), 20 (WP4.6a–d, WP4.7a–b),
  21 (staged builds refused, short lifetimes), 22 (solve-for-PPA restrictions), 23–25 (pack content:
  GewSt add-back and own pool, Zinsschranke Freigrenze and flag, §163(j) ATI and small-business exemption,
  PWA multiplier, Notice 2025-42, the 5-year class check, the bonus binding-contract date, state `None` vs 0,
  mid-quarter deviation, NL 15b and the 20 % cap, the CA ITC's 100 % UCC reduction), 26 (errata lines), 27
  (guard test names, the IRR root rule, xlsx `not_established` and injection, the mapping module,
  participants derived, commitment fee only with construction).

**Round 2 (v0.2, d547d81): PASS WITH CONDITIONS — five binding (N1–N5), all plan text; v0.3 addresses them
and the recommendations.** The reviewer re-ran PySAM: S3 with fee 0 and DSRA 0 gives D = 0.6·TIC (1e-15);
the basis-reduction flags, S3f's one-step term, S2's state bonus 0 and the C9 nominal rate hold;
`en_electricity_rates=1` is harmless.
- **N1** the counterfactual missed the grid-side commodity → C13 is the full supply cost (bill + commodity +
  connection), the identity restated, F6 with a commodity.
- **N2** `bill_site` cannot take a synthesised meter → `billing.rate_meter` factored out (P2 bills pinned),
  the capacity basis and `counterfactual_exceeds_connection`.
- **N3** SAM ignores the moratorium under sculpting → moved to S3 (gearing), the convention stated,
  `grace_with_sculpting` refused.
- **N4** the storage §48E percentage and the CA Clean Technology ITC are fixed by case dates, not per tax
  year → C11 split.
- **N5** S1 escalates both O&M lines equally; `sam_case.py` refuses unequal ones.
- Recommended, taken: R1 (the counterfactual on the served load — shedding excluded on both sides), R2
  (¢/kWh units), R3 (PTC rounding), R4 (a finance-side `CashflowStream`, the ledger enum unchanged), R5 (fee
  funding wording), R6 (the annualise expectation and the 7-day bill check), R7 (overnight cost and asset
  rate on the fixture), R8 (electric site loads only).

**Round 3 (v0.3, 03fa319): PASS WITH CONDITIONS — one binding (M1, LOW), closed in text (v1.0).** N1–N5
verified closed (PySAM: S3 with the moratorium D = 0.6·TIC, year 1 interest-only then a 14-year annuity; S3f's
difference = 0.36·f·TIC unaffected). The commodity term's sign and weights match the ledger; M1: its basis
matched only in the fixture → both commodity terms on the meter basis with the ledger cross-check, a lossy
PoC chain not established, a WP4.6a test with a grid-side load. `rate_meter` feasible (the `meter_history`
default and the no-tariff case stated). Recommended, taken: the served-load wording, `incentive` reused
(no `incentive_credit`), `counterfactual_exceeds_supply`, the annualise precondition kept.

**Plan status: PASSED (v1.0).** Implementation starts with WP4.0.
