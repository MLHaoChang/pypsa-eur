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
applicable percentage (100 / 75 / 50 / 0 % by the year construction begins — 2033 / 2034 / 2035 / 2036+,
the applicable year being 2032, §45Y(d)(3) as amended; verified in WP4.4 — credit taken once at placed in
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
  generators) → `counterfactual_commodity_not_established`, never a number. An uncosted export sink (a
  generator with `p_max_pu = 0`, `p_min_pu < 0`, zero marginal cost — it never supplies) is not a
  "differently priced supply generator" and does not trip the check (WP4.0 review B1). A counterfactual import above the
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

**Units:** SAM's `ppa_price_input` is $/kWh (× 1000); `ppa_price`, `lcoe_nom` and `cf_ppa_price` are ¢/kWh
(× 10); its energy is kWh (every `cf_energy_*` except `cf_energy_value`, which is money — WP4.0 review
B3, R5); `sam_case.py` converts to currency/MWh and MWh, and the tests compare in P4 units (round 2 R2).

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

**WP4.0 implementation.**
- **SAM oracle** (`tests/fixtures/investment_case/sam/`): `generate_sam_cases.py` (PySAM 7.1.1.post1, SSC 306;
  the zeroed defaults and the five cases as tabled; PPA 10 ¢/kWh so the cases are economically sensible —
  equity IRRs S1 6.81 %, S1b 11.71 % at the solved 14.4656 ¢/kWh, S2 14.52 % with a 86.34 M$ sculpted
  debt at min DSCR 1.3, S3 36.09 %, S3f 34.31 % with D − 0.6·TIC = 0.36·f·TIC), `gen_profile.csv`,
  `s1/s1b/s2/s3/s3f.json` (full input export with the 8,760-long arrays as sha256; every `cf_*` array),
  `PROVENANCE.md`, `SAM_LICENSE` (PySAM's BSD-3 text); **`sam_case.py`** — `sam_params` (P4 units; refuses
  unequal O&M escalations and any unmodelled SAM input) and `sam_expected` (the output mapping, ¢/kWh →
  $/MWh, kWh → MWh, % → fractions, the no-debt DSCR sentinel → None). `test_sam_fixtures.py` (18): sha
  pins, array lengths, the profile, and the mapping cross-checked on SAM's own arrays (year-1 revenue =
  energy × price; year-1 O&M; the additive year-2 escalation).
- **Contracts** (`models/finance.py`): `DebtTranche` gains `gearing_base`, `max_gearing`, a per-year `rate`
  list (each ≥ 0, not empty); `upfront_fee` / `commitment_fee` / `dsra_months` / `grace_years` → `None`;
  `TaxEquityStructure.itc_recapture_years` → `None`; `SolvePpa`; `FinanceInputs` gains `analysis_years`,
  `acquisition_date`, `construction_start`, `annualise`, `tax_losses`, `financing_fee_tax`, `hebesatz_pct`,
  `state_rate`, `pwa_met`, `small_business_163j`, `reserves_rate`, `solve_ppa`, list-valued degradation
  (rates in [0, 1)), and `ESCALATION_CLASSES` validation; `Provenance` gains `source_id` / `contract_id` /
  `period`; `CashflowStream` = the ledger's `ValueStreamKind` + the finance-only kinds (the ledger enum
  unchanged). `types.ts` mirrors all of them; **`test_finance_types_ts_parity.py`** (new, 8) checks names,
  optionality and nullability; `test_finance_contracts_p4.py` (8).
- **Tripwire:** `services.results` forbidden in `services/finance/**`; a subprocess test imports every
  finance module and asserts `services.solver_service` / `routers` are not loaded (both checks fail on a
  probe module importing `physical_quantities`, then removed).
- **Integration fixture** `edge_hourly_year.py` (8,760 h, 2030; PV 40 MW and BESS 10 MW / 4 h with
  `overnight_cost` + `discount_rate` 0.07 + lifetime; an export Link) — solves in about 3 s;
  `test_edge_hourly_year_fixture.py` (2).
- **Errata** block appended to the spec (CFADS, the §6.6 line reference, the gate's legs, escalation classes,
  the axis and dates, incremental returns). P0 findings 5 and 7 annotated (placed / deferred to P5 /
  closed).

**WP4.0 review round 1: PASS WITH CONDITIONS — 4 binding; fixed.**
1. B1 — the fixture's `grid_supply` absorbed export at 60/MWh (the ledger commodity nets export; the C13
   meter basis does not) and the export Link was costed → `grid_supply` `p_min_pu` 0, an uncosted
   `grid_sink` (`p_max_pu 0, p_min_pu −1`), export Link marginal cost 0; the test pins supply opex = 60 ×
   import and the PV capex annuity (R6); C13 states the sink is not a priced supply generator.
2. B2 — the ITC classes come from SAM's `depr_itc_fed_<class>` for both the ITC base and each layer's
   reduction (the reviewer probed all 8 flag combinations) → `itc_qualifying_classes` / `itc_base_share`;
   tested on a 70/30 split.
3. B3 — `cf_energy_value` is money, not kWh → never converted; every other `cf_energy_*` → MWh; tested.
4. B4 — `sam_params` did not refuse every unmodelled input → refuses schedule lists, per-MWh O&M, TOD
   factors, CBI/IBI/PBI, fuel, land lease, capacity payments, curtailment price, recapitalisation, lifetime
   output, the `dscr_limit_debt_fraction` cap, custom depreciation, a finite ITC cap, a battery;
   `debt.percent` / `debt.dscr` only for their option; 11 monkeypatched refusal tests.
Recommendations taken: R1 (`types.ts` parity: reverse nullability; the `ValueStreamKind`, `CashflowStream`,
`EscalationClass` literal sets), R2 (the subprocess tripwire also forbids `services.solver` and
`services.results`), R3 (S1b's year-20 IRR and target year in `sam_expected`), R4 (the generator zeroes
CBI/IBI/PBI, fuel, curtailment, recapitalisation explicitly; regenerated — every input and output identical,
only `script_sha` moved), R5 (units above), R6 (WP4.6a), R7 (the committed profile = the generator's, and
every case's `gen` sha = the profile's), R9 (`cf_pretax_dscr` → `None` in years without debt service).
R8 (noted): the relative-import detector test writes a probe into `services/finance` while the subprocess
test imports every finance module — a race only under `pytest-xdist`, which the suite does not use; a
parallel run must put the two in one xdist group.

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

**WP4.1 implementation.**
- `services/finance/case.py`: the engine's input contract — `FinanceCase` (inputs, owner, base year, COD,
  templates, assets, flags), `Template` (first operating year, lines, generation per asset),
  `TemplateLine` (base-year money signed from the owner; `amount` None = not established; escalation class or
  `contract` with its own indexation; tenor; the degrading asset; ledger drill-down fields), `AssetFinance`,
  `FinanceRefused(code)`.
- `timeline.py`: `Timeline` (y0, COD year, base year, analysis years; construction / operating years, index,
  operating k) and `build_timeline` with the C2 refusals (`analysis_years_missing`,
  `financial_close_after_cod`, `cod_mismatch`, `capex_phasing_mismatch` — one phasing entry per construction
  year, `[1.0]` without one — and `asset_lifetime_short` unless a replacement names the asset). Staged builds
  are the adapter's to refuse (WP4.6a: it reads the network).
- `cashflow.py`: `STREAM_CLASS` / `esc_class_for`; `build_operating` — per line and year: the template in
  force (C3), `(1+r)^(y − base_year)` (class rate, or the contract's own indexation, else `ppa`), the
  degradation factor (C5: `(1−d)^(k−1)`, or the product of a per-year list with the last entry repeating),
  the tenor stop and `contract_ends:<id>:<year>` (C14); a `None` amount, a missing class rate or a missing
  degradation entry → `operating` not established with the reason; capex = Σ overnight × (1 + contingency) ×
  phasing over the construction years (or y0), a `None` overnight cost or contingency → `capex` not
  established; replacement capex (calendar year, escalated by `capex`, inside the operating axis); terminal
  value `none` / `fixed` (not escalated) / `multiple_of_ebitda`; `book_value` → not established until the tax
  basis (WP4.3a); EBITDA = revenue − costs + terminal (SAM's salvage is inside EBITDA); generation per asset
  degraded.
- `metrics.py` core: `npv` (index 0 undiscounted) and `irr` (C9: scan + `brentq`, the root closest to 0, the
  two flags).
- Tests `test_finance_cashflow.py` (12): **SAM parity on energy, revenue (+ salvage = SAM's total revenue),
  O&M and EBITDA for S1, S1b, S2, S3, S3f**; F4 (base year two years before COD, a contract's own indexation
  and tenor, class rates, degradation); F5 (fixed, multiple-of-EBITDA and book-value terminal values;
  replacement escalated); the not-established paths; the axis refusals; a two-template axis; list
  degradation; the IRR / NPV core (a two-root series picks 10 %). `sam_case.to_finance_case` builds the SAM
  cases (S1b at SAM's solved price).

**WP4.1 review round 1 (f851907): PASS WITH CONDITIONS — 6 binding; fixed.** The reviewer re-ran PySAM
with two variants (other escalations, degradation, 30 years; salvage at 15 %) and our engine matched every
year — the parity is not an artefact of the committed cases.
1. `irr` returned 0.0 on all-zero cash → no sign change → `None` + `irr_not_established:no_sign_change`
   before the scan.
2. energy of an asset with no degradation entry was NaN with status ok → `None` + `degradation_missing:<a>`;
   an asset absent from a later template does not generate there (documented).
3. `sam_params` silently dropped per-MWh O&M and schedule (list) inputs → refused (`SamMappingError`).
4. a multi-period template in its own period's money was escalated twice → `Template.money_year` (default the
   base year); escalation and contract indexation run from it; tested with non-zero rates. **The adapter
   (WP4.6a) sets each period template's `money_year` to the period year.**
5. a replacement for an unknown asset was accepted → `replacement_unknown_asset`.
6. the class map per kind → a test over every `ValueStreamKind` (`incentive` / `debt_service` have none).
Non-binding, taken: 7 (`no_root_in_scan` when the cash changes sign but no root lies in the scan; the scan
vectorised to 10; `npv` None at a rate ≤ −100 %); 8 (list degradation = annual steps, not SAM's cumulative
schedule — documented in `FinanceInputs`); 9 (`cod_by_asset` must equal the case COD; negative phasing
refused); 10 (the missing-rate reason names `ppa`; a `None` amount after the tenor is never read;
`contract_ends` per contract, once; capex reasons de-duplicated; the unused `index_base_year` dropped); 11
(degradation per asset and the template per year computed once); 13 (docstrings, the unused import).
**Conventions recorded for later WPs (review #12):** COD in the financial-close year puts capex and operating
year 1 both at index 0 (undiscounted — a year earlier than SAM's layout; the report states it); initial
capex is not escalated by `capex` (overnight money of the construction years); EBITDA includes the terminal
value, so WP4.5's unlevered cash must not add it again and WP4.2b sculpting must not size debt on it (a
tenor reaching the last year excludes the terminal from CFADS); the adapter converts P2's
`indexation_pct_per_year` (a percent, default 0.0) to a fraction and passes 0 as 0 — the `ppa` fallback
applies only to contract lines without an indexation field (DR availability / activation).

**WP4.1 review round 2 (6a07029): PASS.** Every binding fix confirmed; the PySAM variants re-run on the new
code match every year (largest EBITDA difference 5.8e-8); v3 (per-MWh O&M) and v4 (a degradation schedule)
are refused. Low findings, taken: `template_missing` and `contract_tenor_below_one` refused in
`build_timeline`; one contract's lines of different tenors end with the longest; `asset_lifetime_unknown:<a>`
flagged (the docstring claimed it); `Template.money_year` keyword-only. For WP4.6a: every asset in
`energy_mwh` needs a degradation entry — list generators only (storage absent, or a typed 0).

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

**WP4.2a/b implementation (one module, `services/finance/debt.py`; reviewed together).**
- **Amendments:**
  - **Sizing is to the debt AT COD** (IDC inside) for every tranche. `gearing` × capex, `gearing` × total
    uses, `amount`, and the sculpted PV all size that one quantity.
  - **IDC timing** replaces the "mid-year drawdown" above. Construction year j's capex is paid, and its debt
    drawn, at the axis point j, the same end-of-year points the returns discount. Interest compounds to the
    COD point, the last construction point. SAM's one construction year therefore gets 0 IDC, as SAM has
    none. F1 = two points, 60/40: the year-0 draw carries one year of interest.
  - **A sculpted tranche is sized by its DSCR.** The model refuses `amount` / `gearing` on it and refuses
    `max_gearing` on the others. The P0 contract test is updated.
  - **CFADS** = EBITDA − the terminal value − replacement capex. Replacement is the major-equipment spend,
    taken as it occurs; P4 has no equipment reserve.
  - **The DSRA release at maturity** sits in `dsra_funding` (−). SAM books it as `cf_disbursement_debtservice`.
  - **A new oracle case, S2c:** S2 with SAM's maximum-debt-fraction cap at 60 %. SAM caps the debt at
    0.6·TIC·(1+fee) and scales the sculpted service pro rata (flat DSCR 1.6245), which is what we do.
    Generated with the others: S1–S3f are unchanged.
- **Behaviour:**
  - Draws follow capex phasing, or the timing of every cash use under `total_uses`.
  - The commitment fee accrues on the undrawn commitment between construction points.
  - The upfront fee is paid at close on the debt.
  - Repayment: grace years inside the tenor; the annuity is re-amortised each year at that year's rate; or
    level principal.
  - Sculpting runs senior first, on the CFADS left over. Negative-CFADS years pay 0 (flagged), and the last
    year closes the loan.
  - DSCR is computed on the total and on the senior tranche.
  - Sources and uses at COD; `debt_exceeds_uses` is refused.
  - The fixed point runs over total uses (tolerance 1e-6, ≤ 50 iterations). Non-convergence →
    `debt_fixed_point_not_converged:residual=…`.
  - Every missing input is a named reason (C12): `upfront_fee`, `dsra_months`, `grace_years`,
    `commitment_fee` (only with an undrawn period) and `reserves_rate` (only with a DSRA). So are
    `grace_with_sculpting`, `grace_not_inside_tenor`, `rate_list_length` and
    `debt_tenor_beyond_analysis`.
- **Tests (`test_finance_debt.py`, 23):**
  - **SAM parity on S2, S2c and S3:** debt size; interest; principal; balance; DSRA balance and movements;
    reserve interest; CFADS over the tenor; DSCR per year and min; total uses = SAM's year-0 investing
    activities. All within 1e-6 relative.
  - **The S3f deviation = 0.36·f·TIC exactly.**
  - F1 by hand (IDC, commitment and upfront fees).
  - Level with grace; an annuity with a per-year rate list.
  - Two tranches (junior sculpted on the rest; the senior DSCR).
  - The fixed point converging, and the forced non-convergence.
  - DSRA and reserve interest; a negative-CFADS year; COD in the close year; every not-established path.

**WP4.2 review round 1 (79db4c9): PASS WITH CONDITIONS; fixed.**

The reviewer compared build_debt with 13 PySAM variants in which SAM follows its own conventions. All
matched to ≤ 1.7e-14:
- level principal and annuity with moratoria;
- DSCR 1.20 / 1.45 at other tenors and rates;
- DSRA 3 and 12 months with reserve interest;
- the cap not binding and binding;
- a tenor of 1;
- grace = tenor − 1.

**Binding findings:**
1. **B1:** CFADS depended on the terminal value, so a book-value terminal blocked sculpting. Fixed: CFADS
   = revenue − costs − replacement. With CFADS unknown, an amount/gearing schedule stays established and
   `dscr_not_established:…` is flagged.
2. **B2:** three SAM deviations are now recorded:
   - **(a) salvage in CFADS.** SAM sculpts on the salvage when the tenor reaches the last year. New oracle
     **S2t**: the test asserts SAM D − ours = salvage / 1.3 / 1.07^25 exactly.
   - **(b) the DSRA in SAM's gearing base**, D = g·(TIC + DSRA(D))·(1 + g·f). New oracle **S3d**:
     `gearing_base="capex"` gives g·TIC; `"total_uses"` reproduces SAM's debt and schedule to 1e-7. C8's
     one-step fee term (S3f) is the other half.
   - **(c) negative sculpting basis.** SAM books a negative service (with all-negative CFADS, a negative
     debt); ours pays 0 and flags `sculpt_basis_negative_no_service`. No oracle case pins it: the mapping
     cannot express a one-year CFADS drop. It is tested by hand.
3. **B3:** IDC used the first tenor rate silently. Fixed: it is stated, and flagged `idc_at_first_rate`
   when the rate is a list.
4. **B4:** the capitalised-interest flag used an absolute threshold. Fixed: the threshold is relative and
   the closing year is skipped.
5. **B5:** plan text, now amended:
   - the DSRA is funded at the COD point (not "at close");
   - the upfront fee is a share of the debt at COD (not "of commitment").
6. **B6:** the "forced non-convergence" case had a fixed point. Fixed:
   - the iteration is accelerated by Aitken (≤ 8 steps);
   - a contraction ratio ≥ 1 before the first jump → `debt_fixed_point_diverges`;
   - fees are capped at 1 in the model;
   - tested with fee 0.9 (converges) and 1.0 (diverges).

**Recommendations taken:**
- `idc_axis_point_draws` flags that axis-point draws accrue less IDC than the mid-year approximation (F1
  27.5 k vs 49.1 k);
- `first_service_at_draw_point` is flagged for COD in the close year;
- the spec §4.2 note on sculpted sizing;
- a junior `max_gearing` caps against TIC × (1 + its own fee), as documented.

**Not taken:** pinning the 13 variants as committed oracles (S2t and S3d cover the deviations).

**WP4.2 review round 2 (8717ac0): two findings; fixed.** Every round-1 fix was confirmed: S3d
`total_uses` now matches SAM to 1e-14. A 2,400-case random sweep against plain iteration converged on
every case in ≤ 19 steps.
1. **r2-1:** Aitken can land about 1e-6 above the fixed point, and the `debt_exceeds_uses` check allowed
   only 1e-9, so 100 % gearing was falsely refused (11 of 36 probes). Fixed: the check uses the fixed
   point's tolerance.
2. **r2-2:** the exit ignored the draw weights (a 3.4e-4 error at a 56 % fee). Fixed: stop only when the
   weights have settled too.

Aitken now starts after two plain steps, so the first estimate no longer carries the initial weights.
Tested: 100 % in one tranche, 60 % + 40 %, and with fees and a DSRA.

**WP4.2 review round 3 (1a883f7): PASS.**
- A g = 1 grid of 144 cases: none refused, ≤ 1.09e-6 from a tight reference.
- A 4,000-case random sweep against plain iteration: 0 false divergences, 0 non-convergences, 0
  established/refused disagreements, ≤ 17 iterations.

**Noted:**
- a slow contraction leaves the debt up to 1/(1 − q) × TOL off (worst 6e-5);
- equity may be −1e-6 × uses at 100 % gearing.

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

**WP4.3a implementation.**
- `tax.py`: schedules — `sl_half_year` (SAM's SL-n, n + 1 years), `sl_pro_rata` (§7 Abs. 1 EStG by month),
  `declining_balance` (switch to straight-line), `normalised`; `DepreciationClass` (share, schedule, bonus,
  `itc_reduces`), `LossRule` (allowance + limit share, a per-year schedule allowed, years), `InterestCap`
  (share of tax EBITDA, an optional Freigrenze that caps ALL interest once exceeded, carryforward of the
  disallowed part), `TaxLayer` (rate or `{from_year: rate}`, own depreciation, deductibility in later layers,
  own loss pool, the interest add-back above an allowance, a surcharge, the ITC basis-reduction flag, the
  interest cap); `depreciation(...)` (bonus in year 1, the rest on the schedule, the ½-ITC reduction pro rata
  to the reducible classes); `compute_tax(...)` (per layer: EBITDA − depreciation − (capped) interest +
  other income − other deductions − earlier deductible liabilities; `offset_other_income` or FIFO
  `carryforward`; flags `interest_capped:<layer>`).
- `tax_layers.py`: `resolve_tax_layers(pack, fin, assets, cod)` → layers, flags, **missing** (never assumed):
  US — federal 21 % with the NOL rule, MACRS classes from `depreciation_class_by_asset` (the pack assigns
  none: the 2025 act changed energy classes), bonus by `acquisition_date` (100 % after 2025-01-19, else the
  TCJA phase-down by placed-in-service year), §163(j) unless `small_business_163j`, a state layer from
  `state_rate` (`None` = missing, 0 = no layer; bonus decoupled and the loss rule stated); DE — GewSt (Messzahl
  × Hebesatz, the §8 Nr. 1 add-back, own €1m + 60 % pool, not deductible) and KSt (the rate path, SolZ, €1m
  + 70 %/60 % schedule), the Zinsschranke as a Freigrenze (carryforward mode), AfA by carrier (PV 20 years,
  pro rata by COD month) or the degressive window by acquisition date (`db_min(3/n, 30 %)_n`).
- Packs: `us_federal` (21 %, MACRS Table A-1, bonus, NOL, §163(j)) and `eu_de` (KSt path, SolZ, GewSt
  Messzahl / add-back / loss, KSt loss, Zinsschranke, degressive AfA, the AfA life for PV, the pro-rata
  first year) with a cited source per rule; unsourced items absent (US energy MACRS classes, the DE battery life;
  DE onshore wind 16 years added in review round 1 — AfA-Tabelle AV Fundstelle 3.1.5). `FinanceInputs.depreciation_class_by_asset` (+ `types.ts`). `pack_hashes.json` pinned **per
  version** and every registered version must be pinned.
- Tests: `test_finance_tax.py` (12) — **SAM parity on state / federal depreciation, taxable income and tax
  per layer for all five cases** (S2/S3 fed SAM's interest and reserve interest until WP4.2), **S1 / S1b
  after-tax equity cash, IRR (≤ 1e-5) and NPV (1e-6)**, the pack's MACRS tables (Table A-1 sums, n + 1
  years), schedules, carryforward with an allowance and a limit, a non-deductible layer with an add-back
  and a surcharge, a rate schedule and an ITC basis reduction; `test_finance_tax_packs.py` (8) — **F2**
  (Germany) and **F3** (US) by hand, bonus by acquisition date, the degressive window and the state
  layer, the missing inputs named, the Zinsschranke Freigrenze, every rule cited. Book-value terminal
  value reads the remaining basis in WP4.5's assembly.

**WP4.3a review round 1: PASS WITH CONDITIONS, 8 binding findings, all fixed.** The reviewer ran 10 new
PySAM variants: state bonus, MACRS-15 + SL-39 over 30 years, state rate 0, 25 loss years under offset, the
ITC reducing both layers, and all classes qualifying. Every one matched per layer, year by year, to ≤ 1.5e-8,
except SL-15 (B6). The reviewer also web-checked the statutes: the KSt path, the degressive window, the OBBBA
bonus rule, §172 NOL 80 %, §163(j) on an EBITDA basis, MACRS Table A-1 and the AfA-Tabelle.
1. **B1:** degressive AfA ignored the month of COD. Fixed: `declining_balance(rate, n, months_first_year)`
   gives year 1 pro rata by month (§7 Abs. 2 → Abs. 1 Satz 4 EStG), then declining balance or
   straight-line on the remaining months. Tested: a December COD takes 12.5 k€ of 1 M€ at 15 %; a January
   COD takes 150 k€.
2. **B2:** a missing `acquisition_date` in Germany became linear AfA silently. Fixed: it is now `missing`.
3. **B3:** the resolver returned usable-looking partial layers while `missing` was non-empty. Fixed: no
   layers whenever anything is missing. Tested for each item, in both packs.
4. **B4:** the Zinsschranke had three faults, all fixed and tested:
   - the Freigrenze boundary was wrong; it is now `<` ("weniger als drei Millionen Euro");
   - there was no Zinsvortrag; it is now in the pack rule (`carryforward`, §4h Abs. 1 Satz 5);
   - `zinsschranke_simplified` was never raised; it is now raised whenever the Freigrenze is reached.
5. **B5:** the GewSt add-back included interest the Zinsschranke disallowed. Fixed: it now adds back only
   deducted interest (§8 GewStG chapeau). Tested: 7.70 m, not 7.825 m.
6. **B6:** SAM's SL-15 is the rounded IRS Table A-8, not 1/15 with the half-year convention. Fixed:
   `sam_case` refuses it.
7. **B7:** class strings were not validated. Fixed: schedules are checked (years ≥ 1, 0 < rate ≤ 1,
   entries ≥ 0, summing to 1, or 1e-4 for the rounded MACRS table) and `ZeroDivisionError` is caught. A
   bad class is now `missing`, never a crash or negative depreciation. An ITC reduction above a class's
   basis, or shares summing above 1, raise.
8. **B8:** recorded deviation from C7. The layer STRUCTURE (which layers, their order and wiring, the US
   state slot) is code per jurisdiction in `resolve_tax_layers`. The pack carries, and its hash covers,
   the rates, schedules and rules it cites. WP4.3b adds a branch per pack.

Recommendations taken:
- `TaxResult.remaining_basis` per layer, so the book-value terminal can read it;
- the state flags are `state_bonus_decoupled` (only with a federal bonus) and
  `state_interest_limit_follows_federal`;
- `sources` now also name the bonus, MACRS, degressive and AfA-life rules;
- `degressive_afa_elected` is flagged;
- a Hebesatz below 200 % is refused (§16 Abs. 4 GewStG);
- DE onshore wind 16 years, cited;
- sharper citations: §4h Abs. 1 Satz 2 and Satz 5, and Pub. L. 119-21 §70301.

Both pack versions re-pinned (these packs have not been released).

Noted, not taken:
- the Wachstumschancengesetz 2024 degressive window (2× SL, 20 %). Property acquired 2024-04-01 …
  2024-12-31 takes linear AfA; this is a WP4.3b pack addition;
- the "longer production period" phase-down and Notice 2026-11 for self-constructed property, which go
  into the `acquisition_date` help (WP4.7a);
- loss vintages expire only in income years; the current packs are indefinite;
- §163(j) interest still carried forward at the axis end is not reported; this is for WP4.5's report.

**WP4.3a review round 2 (eaf3951): PASS.**
- **SAM variants:** the eight non-SL-15 variants still match to ≤ 1.5e-8. The SL-15 variant is refused.
- **B1:** checked against an independent exact-fraction hand calculation for 7 rate/life pairs × 12 COD
  months (≤ 1e-12).
- **B2–B8:** each re-probed.

Nit taken: the degressive rate keeps full precision (`repr`, not `:g`).

## WP4.3b `eu_nl` and `ca_federal` packs

- `eu_nl` (sourced): VPB brackets 19 % / 25.8 % at €200k (art. 22 Wet Vpb 1969, dated); depreciation with the
  20 %/yr cap (art. 3.30 lid 2 Wet IB 2001) and useful lives; loss carryforward €1m + 50 % (dated); art. 15b
  earnings stripping (24.5 % of fiscal EBITDA, €1m threshold) in carryforward mode; EIA as an incentive slot
  (absent unless sourced).
- `ca_federal` (sourced): 15 % general federal rate (38 % − 10 % abatement − 13 % rate reduction), a
  provincial slot; CCA classes 43.1 (30 %) and 43.2 (50 %, its acquisition window cited) declining balance
  with the half-year rule and the Accelerated Investment Incentive (dates cited); the Clean Technology ITC
  (30 %, 15 % from 2034 — by the available-for-use date, C11) **reducing UCC by 100 %** (s.13(7.1) ITA).
- `test_finance_packs.py`: the registry becomes {eu_de, eu_nl, us_federal, ca_federal}; hashes pinned per
  version.
- Tests: a hand case per pack (tax per layer, depreciation per class for 5 years).

**WP4.3b implementation.**

**`eu_nl`** (Wet Vpb 1969 / Wet IB 2001, and the Belastingdienst 2026 pages, verified at the source):
- the VPB brackets 19 % to €200k, 25.8 % above (art. 22);
- losses €1m + 50 %, with no time limit (art. 20 lid 2);
- earnings stripping: the cap is the higher of 24.5 % of fiscal EBITDA and €1m, with carryforward
  (art. 15b). This is a new `InterestCap.allowance`, not a Freigrenze;
- depreciation at most 20 % a year (art. 3.30 lid 2), enforced on every class;
- the class `slm_<n>` (straight line, pro rata by month);
- residual value and carryback flagged as not modelled;
- useful lives and the EIA absent.

**`ca_federal`** (ITA, the Regulations, NRCan and CRA; enacted law):
- the federal rate is 15 % (38 − 10 − 13);
- a provincial slot (`state_rate`), not deductible from each other;
- CCA classes 43.1 (30 %) and 43.2 (50 %, acquired 2005-02-23 … 2024-12-31) on declining balance
  (`cca_declining`):
  - the half-year rule, or the enhanced first year for property acquired after 2018-11-20: 100 % before
    2024, 75 % in 2024–25, 55 % in 2026–27, by available-for-use (COD) year;
- non-capital losses carry 20 years;
- the Clean Technology ITC: 30 %, 15 % in 2034, 0 % after; −10 points when the labour requirements
  (`pwa_met`) are not met; only on class 43.1 / 43.2 assets; it reduces the capital cost by **100 %** (new
  `TaxLayer.itc_basis_reduction_share`);
- EIFEL absent and flagged;
- ~~the 2025 budget proposals are not included (not enacted)~~ — wrong: enacted 2026-03-26 (S.C. 2026,
  c. 3); a second pack version carries them (WP4.3b review round 1, B1).

**Tax engine additions:** progressive `brackets` (a negative base is valued at the top bracket, flagged)
and the `cca_declining` schedule.

**Registry:** {ca_federal, eu_de, eu_nl, us_federal}, pinned per version.

**Tests (`test_finance_tax_packs_nl_ca.py`, 13):**
- NL brackets, losses and straight line by hand;
- earnings stripping with the threshold and the carryforward;
- the 20 % cap;
- CA CCA 43.1, four first-year cases (100 / 75 / 55 % and half-year), then declining balance;
- CA layers; the 43.2 window; the EIFEL flag;
- the Clean Tech ITC at 30 / 20 / 15 / 0 % and a non-43 class.

**WP4.3b review round 1 (0742677): FAIL for `ca_federal`; `eu_nl` passes with one condition; fixed.**
The reviewer verified against the statutes: the NL rates, losses, art. 15b (a threshold, with
carryforward) and pro rata; the CA 15 % rate, the Class 43.2 window, the Reg. 1100(2) factor mechanics
(`cca_declining` exact), the 20-year losses, the ITC rates, refundability and labour; the layers. Every
arithmetic probe was exact.
1. **B1 (high).** The Budget 2025 Implementation Act, No. 1 (S.C. 2026, c. 3, assented 2026-03-26, deemed
   in force from 2025-01-01) closes the old incentive to property acquired before 2025 (Reg. 1104(4)). It
   gives Class 43.1 property acquired after 2024 100 % before 2030, 75 % in 2030–31 and 55 % in 2032–33
   (Reg. 1100(2) A.1(b), 1104(4.01)). The pack applied 55 % / the half-year rule instead. Fixed:
   - **a second pack version valid from 2026-03-26** (C11: the law state at close), with
     `first_year_programs` (aiip bounded to acquisitions before 2025; raiip for 43.1 after 2024);
   - the 2026-01-01 version keeps the law as then enacted;
   - both versions pinned.
2. **B2.** The ITC capital-cost reduction applies from the FOLLOWING year (s. 13(7.1)(e), s. 127.45(6)),
   so the first-year CCA is on the unreduced cost and a negative UCC is recaptured (s. 13(1)). Fixed with
   `TaxLayer.itc_basis_reduction_lag` = 1 for CA. Tested: 100 % expensing gives [1.0, −0.3 recapture, 0];
   the half-year rule gives [0.15, 0.55·0.3, …]. Citation corrected.
3. **B3.** The ITC was gated on COD, not the acquisition date. Fixed: acquired before 2023-03-28 →
   ineligible; `acquisition_date` None → missing.
4. **B4.** ITC eligibility was assumed from the CCA class. Fixed with a cited `clean_technology_property`
   rule (s. 127.45(1)) that fails closed: cogeneration / fossil not eligible, unclassified not
   established.
5. **B5.** The NL citations: art. 3.30 **lid 2** Wet IB 2001; art. 22 Wet Vpb has no lid. Re-pinned.

**Taken:**
- `nl_brackets_standalone_in_offset_mode`, `ca_loss_carryback_not_modelled`,
  `provincial_layer_follows_federal_cca_and_losses` and `ca_itc_rate_stated_statutory_checks_skipped`;
- citations for Reg. 1104(4) and s. 123.4(1);
- tests for 43.2 inside its window, post-2024 acquisitions, the recapture and the NL flag.

**WP4.3b review round 2 (44b8324): PASS.**
- **B1–B5:** verified against the statute text.
- **Version selection by financial close:** checked across the boundary.
- **First year:** 11 acquisition / available-for-use combinations match the law.
- **The `run_case` lagged reduction:** CCA [1.0 M, −300 k, 0]; the half-year and 55 % cases match by hand.

**Two citation fixes (no re-review):**
- the `clean_technology_property` rule cites s. 127.45(1) para. (d)'s sub-items;
- the plan text says art. 3.30 lid 2.

**Taken:**
- a reduction still pending at the axis end is recaptured in the last year
  (`negative_basis_recaptured_at_end`);
- large `hydro` is left unclassified (only small hydro qualifies);
- `ca_first_year_superseded_retroactively` flags the 2026-01-01 version when the acquisition is on or
  after 2025.

**Noted, not taken:**
- art. 15b / §4h / §163(j) cap the debt interest, not the net interest balance with fees (an EBITDA proxy
  — stated);
- the credit is paid after year-end;
- the rolling-start rule s. 13(27)(b);
- Canadian construction interest (s. 20(1)(c) vs s. 21) against the C6 IDC-in-basis convention.

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
  construction by 2026-07-04, beginning of construction per the notices in force — Notice 2025-42 was vacated
  on 2026-06-06 — or placed in service by
  2027-12-31), the storage ITC runway and phase-down (by `construction_start`, C11), the FEOC material-assistance rules for
  construction beginning after 2025-12-31. Unsourced values absent.
- Tests: **S3 ITC** (credit, reduced federal basis, unreduced state basis, taxes); PTC by hand; eligibility on
  both sides of each cliff; phase-out; FEOC's three states; the PWA multiplier; a grant reducing basis.

**WP4.4 implementation.**

`services/finance/incentives.py::build_incentives(case, tl, op, pack)` returns per-year cash, the ITC total
and its assets (for the basis reduction), the grant basis reduction, one line per incentive, and the
reasons, flags and sources. Each incentive kind:
- **ITC:** rate × eligible basis (capex incl. contingency), capped by `amount` and scaled by the phase-out
  share. It is credited to after-tax cash in the first operating year.
- **PTC:** only with a pack rule. The US §45Y amount is 0.3 ¢ / 1.5 ¢ × the calendar year's published
  inflation-adjustment factor, rounded each year to 0.05 ¢ / 0.1 ¢ (halves up). A later year projects the
  factor at `inflation` and is flagged `ptc_factor_projected`. The credit runs for 10 operating years
  from COD.
- **Grant:** cash at the COD point; it reduces the basis (flagged: pro rata over the classes).
- **Refused here:** `accelerated_depreciation`, `cfd` and `capacity_payment`, each with the place it
  belongs. ITC and PTC on the same asset are refused.

Eligibility:
- **The incentive's own rules:** `begin_construction_by` (needs `construction_start`),
  `placed_in_service_by`, `asset_classes` (needs a carrier on `AssetFinance`, new), and the `phase_out`
  dates.
- **US statute, applied to ITC and PTC under the `us_federal` pack:**
  - the PWA multiplier (`pwa_met` None → not established);
  - the phase-out after 2032;
  - the wind/solar termination: placed in service after 2027-12-31 with construction begun after
    2026-07-04 → ineligible; storage is exempt;
  - FEOC (`feoc_flag` None → not established for construction after 2025-12-31; True → ineligible).

The `us_federal` pack gains six cited rules: `clean_electricity_itc` (§48E(a)(2)), `clean_electricity_ptc`
(§45Y(a)(2), (b)(1)(B), (c) plus the 2026 factor 2.0570 — 91 FR 56942, 0.6 / 3.1 ¢),
`clean_electricity_phase_out`, `wind_solar_termination` (beginning-of-construction notices; 2025-42 vacated), `feoc_material_assistance` and
`itc_basis_reduction` (§50(c)). The federal layer takes the reduction; the state slot does not. Re-pinned.

**Verified at the source before encoding:** Cornell LII §48E / §45Y text, the Federal Register notice, and
law-firm summaries of Pub. L. 119-21.

**Tests: `test_finance_incentives.py` (26):**
- **the S3 ITC = SAM's `itc_total` in year 1, and the federal and state taxes with it match SAM;**
- the PTC by hand (the published 0.6 / 3.1 ¢ amounts, the rounding, the projection, the 10-year term);
- both sides of the wind/solar cliff, and storage exempt;
- the phase-out 2032 … 2036;
- FEOC's three states;
- the PWA multiplier;
- a stated rate with a cap, grants, and user dates / phase-out / classes;
- the refusals;
- the sources.

The `sam_case` mapping gives S3/S3f their ITC as an `Incentive`.

**For WP4.5:**
- flag `state_itc_basis_unreduced` when an ITC is claimed with a state layer;
- set each class's `itc_reduces` from `itc_assets`;
- reduce the basis by the grant.

**WP4.4 review round 1 (ea3ea21): FAIL, fixable in one round; fixed.** The arithmetic was verified:
- ITC and ITC cap = SAM exactly;
- grant cash = SAM;
- PTC at the alternative amount = SAM exactly when SAM is fed the unrounded factor.

The reviewer also verified every statutory fact against Cornell LII and the Federal Register.

**Binding findings:**
1. **B1:** technology eligibility failed open (a diesel or gas asset got the 30 % ITC; a battery got the
   PTC). Fixed with a cited `clean_electricity_technology` rule: wind/solar, other zero-emission and
   storage (ITC only) qualify; a named list does not qualify; any other carrier is **not established**,
   never eligible.
2. **B2:** the termination's carrier list failed open for `wind`, `pv`, `offwind`, `solar rooftop` and
   `solar-hsat`. Fixed: it reads the classification's wind/solar list.
3. **B3:** grants. Fixed:
   - `Incentive.grant_tax_treatment` (`reduces_basis` | `taxable`, no default) is new, in the model and in
     `types.ts`;
   - a basis-reducing grant also reduces the ITC base of its assets (SAM `deprbas` = 1: 32.12 M, not
     33.62 M);
   - a taxable grant is income in the year received (the engine adds it to every tax run).
4. **B4:** citations:
   - §48E(a)(2)(B), (c)(3) and (e)(4)(C) added;
   - Pub. L. 119-21 §§70512(l), 70513(g) cited;
   - **Notice 2025-42 was vacated on 2026-06-06** (D.D.C.), so the earlier beginning-of-construction
     notices apply, subject to appeal.
   Re-pinned.
5. **B5:** a second ITC on the same asset is refused.

**Non-binding, taken:**
- FEOC reaches only construction after its date (`feoc_flag=True` earlier is no longer ineligible);
- the flags `itc_base_excludes_idc`, `ptc_term_in_operating_years` and `ptc_on_all_generation` (the
  §45Y sale requirement at the edge);
- `Incentive.rate` units and `pwa_met`'s exceptions documented;
- tests pin the 0.05 ¢ base step (2027: 0.6294 → 0.65 ¢) and the PTC phase-out.

**Recorded deviation:** SAM rounds the PTC to $0.001/kWh; the statute rounds the base amount to 0.05 ¢
(±$65k a year, +0.036 % over the term on the probe).

**Not taken:**
- a generic stated production incentive (SAM PTC/PBI with its own term and rounding) — a P5 item;
- the 2025 factor (unreachable with a 2026+ close).

**WP4.4 review round 2 (0cdc690): two minor findings; fixed.** B1–B5 were confirmed fixed. The engine run
with a grant matches SAM's IBI: `reduces_basis` year by year, and `taxable` with the tax amounts equal to
1e-9.
1. **r2-1:** a basis grant above its assets' cost gave a negative ITC. Fixed: `grant_exceeds_cost` is
   refused, and a base below 0 gives `grant_exceeds_itc_base`.
2. **r2-2:** a second PTC on one asset was paid twice. Fixed: `ptc_twice_same_asset`.

**Taken:**
- carriers match case-insensitively;
- a battery's charger and discharger links count as storage;
- `itc_base_excludes_idc` is flagged only with IDC;
- the stale Notice 2025-42 text is corrected.

**Recorded deviation:** a taxable grant is taxed in the year received (index 0); SAM taxes it in year 1
(−0.94 bp of IRR on S1 + $5M).

**WP4.4 review round 3: two findings; fixed.**
1. **r3-1:** the storage-list change was not re-pinned, so the pack-hash test failed on three commits. That
   was a process slip: a commit went in without the full P4 run. Re-pinned; the full set now runs before
   every commit.
2. **r3-2:** grants were checked one at a time, so two could together exceed an asset's cost and the engine
   raised a `ValueError`. Fixed:
   - the running total per asset is checked;
   - the engine turns any impossible basis into `tax_basis_invalid:…`, never an exception.

**Taken:** `asset_classes` match case-insensitively; a missing overnight cost is named as such.

**WP4.4 review round 4 (0742677): PASS.**
- **Grants:** checked per asset (the table of cases in the review).
- **Impossible bases:** reported as reasons.
- **Earlier results:** the round-2 SAM results still hold.

**Taken:** a refused grant no longer adds to the totals.

**Deferred to the P4 gate:** a `≤ 1` validator on `Incentive.rate` for ITC and grants (for now it is caught
downstream as `tax_basis_invalid`).

## WP4.5 Metrics, solve-for-PPA, the WACC gate

- `metrics.py` (C9): unlevered project IRR / NPV, equity IRR / NPV pre and post tax, payback, DSCR min / avg
  (senior and total), LLCR, PLCR, LCOE nominal and real, lifecycle-cost NPV, solve-for-PPA (C9).
- The WACC gate (C10) and its report block.
- **All-case SAM parity** (S1, S1b, S2, S3, S3f) in one parametrised test to T, through the output mapping.
- Tests: IRR edge cases (no sign change, multiple sign changes → the pinned root, all negative); payback
  interpolation; LLCR / PLCR closed forms; solve-for-PPA refusals (not owner-sold, nonlinear,
  `changes_dispatch`), no root; the gate's states (consistent; wacc differs; an asset rate differs;
  inflation differs with and without `auto_discount_periods`; `None`).

**WP4.5 implementation.** `services/finance/engine.py::run_case(case, pack, *, layers)` assembles the WPs
on a plain `FinanceCase`.

**Pipeline:**
1. The timeline, then the total operating cash and the **counterfactual's** (new
   `FinanceCase.counterfactual` templates), giving the incremental cash.
2. Debt on the incremental CFADS.
3. Incentives.
4. Tax, run three times: levered; unlevered (no interest, no IDC); and on the total.
   - The basis is capex + IDC − grants.
   - The ITC reduces only its own assets' `asset:class` classes.
   - Replacement capex depreciates as vintages (new `compute_tax(vintages=…)`).
   - The remaining basis is written off in the last year (new `write_off_remaining`). **Recorded
     deviation:** SAM drops the remaining basis. It is 0 on every oracle case.
   - A `book_value` terminal sells at the last layer's remaining basis, so there is no gain.
   - Financing fees are `amortised` over the first tranche's tenor, or `not_deducted`; with fees and
     neither stated → not established.
5. The cash series:
   - equity pre-tax = EBITDA − capex − replacement − fees − service + draws + reserve interest − DSRA
     funding + grants (SAM `cf_project_return_pretax`, verified);
   - post-tax = pre-tax − tax + ITC + PTC;
   - the project (unlevered) series;
   - the lifecycle series on the total.
6. **Metrics:**
   - equity and project IRR / NPV, pre and post tax;
   - payback (linear interpolation, years from index 0);
   - DSCR min / avg, total and senior;
   - LLCR / PLCR at the tranche rates (debt-weighted with several tranches, flagged);
   - LCOE nominal / real = (NPV revenue − NPV post-tax equity) / NPV energy. This is **SAM's definition,
     found and verified on all 8 oracle cases to 1e-12**; the salvage is outside revenue;
   - the lifecycle-cost NPV.
7. The WACC gate (C10), with each leg's state and the LP's bases. `FinanceCase.lp_basis` is new.
8. Solve-for-PPA: brentq on the price scale of an owner-sold `ppa_settlement` contract. `TemplateLine`
   gains `price` and `changes_dispatch`. The target is the post-tax equity IRR over operating years 1 …
   target. The refusals are: not found / ambiguous / not linear / not owner-sold / needs redispatch /
   price unknown / no root. The stored inputs are never mutated.

**Tests: `test_finance_engine.py` (22):**
- **all-case SAM parity:**
  - covers S1, S1b, S2, S2c, S3, S3d and S3f; S3d/S3f run at SAM's debt, since their sizing deviations are
    sized in the debt tests;
  - checks equity pre / post cash (1e-12), federal / state tax, IRR (1e-6 absolute — SAM's solver),
    NPV (1e-9), LCOE nominal and real (1e-10), min DSCR and debt size;
- **S1b solve-for-PPA = SAM's 144.6559168 $/MWh to 1e-10;**
- S1 project = equity (all equity); S2's LLCR = its DSCR 1.3;
- payback; IRR edges;
- the counterfactual (incremental returns, lifecycle on the total);
- the book-value terminal and a replacement vintage;
- the solve refusals;
- the gate's 8 states;
- the not-established paths.

**WP4.5 review round 1 (059eb18): FAIL; fixed.**

The reviewer compared the full engine with 11 new PySAM variants: sculpting with a DSRA at other rates;
bonus + ITC + debt; 30 years; salvage with gearing or sculpting; negative income; level principal with a
moratorium. The results:
- equity cash to 2.3e-7 dollars;
- tax exact;
- IRR within SAM's solver tolerance (≤ 1.04e-6);
- NPV ≤ 2e-13; LCOE ≤ 1.1e-15;
- solve-for-PPA equal to SAM's price to 9e-16 on six targets.

**Binding findings:**
1. **B1:** the `multiple_of_ebitda` terminal used the owner's total EBITDA (with the supply bill). Fixed:
   with a counterfactual it uses the incremental EBITDA.
2. **B2:** LCOE mixed total revenue with incremental equity cash (a behind-the-meter PV gave −8.30 $/MWh).
   Fixed: LCOE = (PV of the investment's value gross of its own fom/vom/fuel − PV of the post-tax equity
   cash) / PV of its generation. Without a counterfactual this is exactly SAM's. Tested: the same
   incremental cash via a PPA or via bill savings gives the same LCOE.
3. **B3:** the solve bracket failed under sculpting, because at 2× the price the debt exceeds the uses.
   Fixed:
   - it grows by 1.5×;
   - it treats a not-established point as an upper limit and bisects back to an established one;
   - it refuses only when no established point reaches the target.
   Tested on S2 at 12 % in year 15.
4. **B4:** the pre-tax headlines were numbers with an unresolved grant. Fixed: the cash series need the
   incentives established.
5. **B5:** replacement vintages lost their asset. Fixed: a vintage (index, amount, asset) depreciates on
   the asset's `asset:class` classes, renormalised, falling back to every class.
6. **B6:** payback returned 0 on a zero start. Fixed: it counts only a crossing from negative;
   `payback_not_sustained` is flagged.
7. **B7:** the write-off deviation had no oracle. Fixed with a new oracle **S1l** (S1 on SL-39). The test
   asserts the last-year post-tax difference equals the state + federal shield on (1 − 24.5/39) × TIC
   exactly; every other year matches to 1e-12. Size on S1l: IRR +0.23 pp vs SAM.

**Taken:**
- the solved price is the earliest template's, with its money year;
- zero-amount lines are skipped;
- `solve_ppa_target_year_clamped`;
- `lcoe_real_not_established:inflation_missing`;
- the gate reports the asset rates;
- tests for the pack path, PLCR / LLCR closed forms and payback.

**Noted, not taken:**
- the unlevered book-value terminal carries the levered basis (IDC residual);
- the book value follows the last layer (with 100 % federal bonus the state layer books a loss);
- the lifecycle tax in carryforward mode never offsets the supply-cost losses.

**Stated:**
- commitment fees are amortised with the upfront fee;
- payback is counted from financial close;
- WP4.6a passes generation only in `energy_mwh`.

**WP4.5 review round 2: one finding; fixed.**

B1 and B3–B7 were confirmed fixed:
- the sculpted solve equals SAM's 101.78863114 $/MWh from any start (0.01×–50×);
- every earlier variant still matches.

B2 still had a gap without a counterfactual: LCOE left out cost lines other than fom, vom and fuel (a
connection fee lowered it). Fixed: the value adds back the cost part of every actual line with no
counterfactual counterpart. Without a counterfactual this is exactly SAM's revenue. Tested across fom,
`network_capacity`, `other` and `lease`: the LCOE equals SAM's formula, and a cost never lowers it.

**WP4.5 review round 3 (52e38af): PASS.**
- **Hand table:** matches SAM's formula for every cost stream.
- **Counterfactual cases:**
  - the behind-the-meter probe gives 51.6961, equal to the PPA route;
  - a shared connection key stays netted;
  - a lease counts as a cost.
- **SAM parity:** LCOE and the sculpted solve unchanged, ≤ 8.9e-16.

**WP4.6d implementation (93f840e).** `services/finance/export_xlsx.py::build_workbook(report)`:
- sheets: About (case, assumptions hash, packs, CFADS definition, WACC gate, counterfactual, completeness,
  every flag and reason), Summary, one sheet per report section (scalars as rows, record lists as tables)
  and CashflowLines;
- `None` becomes an explicit `not_established` cell;
- a string starting with = + - @, a tab or a CR is stored as text (the injection guard);
- tests: an Excel round trip (numbers equal, None explicit) and injection names staying text.

The `GET …/export.xlsx` route lands with WP4.6b.

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
  → not established; **the fixture's BESS cycles and it exports under the demand-charge commercial solve**
  (discharge > 0 and export > 0 — the incremental-EBITDA identity has an export term; in the plain solve
  export is valued at 0, so its amount is arbitrary; WP4.0 review R6 and round 2), and its uncosted
  `grid_sink` passes the commodity check.

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

**WP4.6b review round 1 (1d6ebfe): six findings; fixed.** Concurrency, the mesh, persistence, the
headline mappings, the export's filename and injection guard and the C1 import boundary were confirmed.
- **B1 (High) — the staleness key missed build-time solver config.** The adapter reads
  `discount_rate`, `inflation_rate`, `auto_discount_periods` (for `LpBasis`), `voll` and the `dsr_*`
  settings from the solver config at IC time, not only at the solve. `assumptions_digest` gains a
  `solver_config` part: the whole solver config minus `finance` / `commercial` (their own parts). A
  field that only matters to a solve also marks the report stale — that errs safe. Tested per field and
  through the route (`changed == ["solver_config"]`).
- **B2 (Medium) — the cashflow lines did not reconcile with the cash.** The lines emitted the unresolved
  terminal and left the counterfactual out. `FinanceResult` now carries the resolved `terminal` and the
  counterfactual's `Operating`; the lines emit that terminal and the counterfactual's lines NEGATED
  (source `counterfactual:*`). Tested: each year's Σ lines = `cash["equity_post_tax"]` with a
  counterfactual, a tranche and each terminal method (none, book value, EBITDA multiple). The WP4.6c
  `explain_cashflow` reads the avoided supply cost from those lines (it added `counterfactual_net` itself
  before, which would now double-count) and discounts at the payload's `cost_of_equity`.
- **B3 — unknown finance keys were dropped.** `extra="forbid"` on `FinanceInputs`, `DebtTranche`,
  `Incentive`, `TaxEquityStructure`, `SolvePpa`, `TerminalValueRule` (the TS types carry no extra key).
- **B4 — control characters 500'd the export.** `case_id` pattern `^[\w .-]+$`; `_put` strips
  openpyxl's `ILLEGAL_CHARACTERS_RE`.
- **B5 — an abort during a refusing build stored the refusal.** Both refused branches check `aborted()`
  before storing; the prior report is kept (tested for the adapter and the engine refusal).
- **B6 — the About sheet's disclosures.** The project payload carries a `counterfactual` provenance block
  (`present`, `basis`, `first_years`, `n_lines`, `sources`, `lines_not_established`, `reasons`, `flags`)
  and `cost_of_equity` / `wacc_nominal`; one `CFADS_DEFINITION` (in `debt.py`, what `build_debt`
  computes: incremental revenue − incremental costs − replacement capex) used by the report and the xlsx.

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
