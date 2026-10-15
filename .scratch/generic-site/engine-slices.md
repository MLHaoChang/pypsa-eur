# Engine slices H2e and M2e: investment-case counterfactuals for the H2 hub and the island

Status: needs-triage (design for the gatekeeper)

## Where the counterfactual stands today (master 3a8fb179)

- `finance_case._counterfactual` is analytical. It rates the served load on the tariff and
  prices the commodity from the grid-side supply's marginal cost, with the shed at VoLL
  (#294). Nothing re-solves.
- The engine takes `inc_net = net − cf_net`. **Counterfactual savings are inside CFADS, debt
  sizing, EBITDA and the LCOE value** (`engine.py:168-316`, `debt.py:229`).
- `owner_conversion_load` puts a None line in every period, so the case runs but every metric
  is None.
- `CommercialConfig.poc_link` is required (`min_length=1`). The site/grid split
  (`classify_buses`) and the value-flow templates seed from it.
- The facade fields are frozen exactly (`test_engine_facade_frozen.py:270-288`); the §6 note
  goes with any change. A defaulted new field is kept out of `_canon` while unset, so
  existing hashes stay unchanged (the precedent is `stage_capex_lead_years`).
- The solver's `co2_price` touches only fossil Generators' marginal cost, and is reverted
  after the solve. Nothing in finance reads CO2.

## H2e: grey H2 and a CO2 price by period (owner Q3)

**Inputs** (all assumed or synthetic, ledgered by the template, none constant in the engine).
Two new defaulted fields on `CommercialConfig`:

- `reference_purchases: list[ReferencePurchase] = []`, where `ReferencePurchase` is
  `{load, price_eur_per_kg, lhv_kwh_per_kg=33.33, co2_t_per_kg, source}`. `load` names a
  non-electric site Load: what the site would buy without the investment.
- `co2_price: Co2Price | None = None`, where `Co2Price` is `{eur_per_t_by_year: {year: price},
  money_year, source}`. Each case year takes the price of the last listed year at or before
  it, and the case output shows which listed year each case year falls in.

**Counterfactual**, for each period k:

- `owner_conversion_load` is not a blocker when every non-electric site load beside an
  owner conversion Link has a reference purchase. Without one, it stays a blocker, as today.
- `counterfactual:purchase:<load>`: −Σ_t w_t·L_t / lhv × price. Stream "fuel", esc_class
  "fuel", money year = the base year.
- `counterfactual:co2:<load>`: −kg × co2_t_per_kg × price(year of k). Stream "other". The
  price is already per year, so it is not escalated again.
- The served electric load is unchanged: the owner's electrolyser draw is not added, so the
  grid-only site buys power for its process load only.
- The hash adds `{purchases, co2}` only when they are set.

**Test (the gatekeeper's):** a two-period case with CO2 prices 0 and 100. The counterfactual
cost rises by exactly kg × intensity × 100 in period 2. There are also by-hand tests for the
purchase line and the LHV conversion, and a test for each new flag.

## M2e: the island's diesel-only baseline (owner Q4) and the off-grid rule (UX H4)

**Inputs:**

- `CommercialConfig.site_mode: "grid" | "off_grid" = "grid"`.
- With `off_grid`:
  - `poc_link` may be None, and when None every bus is a site bus;
  - `import_tariff` must be None;
  - the Investment detail hides the PoC, export and tariff fields (H4, frontend).
- `FinanceInputs.counterfactual_basis: "grid" | "baseline_resolve" = "grid"`.
  `baseline_resolve` is required with `off_grid`.

**Baseline:**

- A copy of the solved network is re-solved through `run_simulation` with every owner
  candidate built after the first period fixed at 0. The existing fleet and the same
  config, energization and island rows are kept.
- The live network is never touched, which matches the adequacy re-solve pattern
  (`dtc.py`, `levers.py`).
- The result is cached on (network revision, the cfg digest, the candidate set), so the IC
  view does not solve twice.

**Counterfactual lines:**

- The baseline's owner operating lines: fuel and vom per existing asset, keyed like the
  actual ledger's.
- Plus its shed at the case's VoLL (`counterfactual:shed_at_voll`, as #294).
- If the baseline is infeasible, the lines are None and the case is flagged
  `counterfactual_baseline_infeasible`.

**Disclosure:** next to the NPV, through `baselineCaveats` (frontend `financeModel.ts`) and
the xlsx `BASELINE_CAVEATS`.

**Test:** a small island whose diesel-only cost can be checked by hand: one diesel, a flat
load, and a PV candidate that the actual plan builds.

## PR order

1. H2e (engine) plus the staged H2 hub shipping its inputs.
2. M2e-a (engine): site_mode, the baseline re-solve and the cache.
3. M2e-b (frontend): the off-grid Investment detail (H4).
