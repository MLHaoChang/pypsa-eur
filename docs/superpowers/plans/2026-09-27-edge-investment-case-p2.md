# Edge Investment Case — Implementation Plan, Phase 2 (billing pass & contracts)

> **For agentic workers:** same protocol as the P0–P1 plan (`2026-09-26-edge-investment-case-p0-p1.md`,
> § Phase QA gate + TDD protocol): per work package red → green → verify, an independent implementation
> review until `PASS`, then the phase e2e QA gate with an independent assessor before P3. House rules:
> `.cursor/skills/gui-backend-change/SKILL.md`, `.cursor/rules/pypsa-gui-backend.mdc`, ADR-0001
> (unresolvable → `None` + flag, never 0), ADR-0002 (chat tools: live-API probe), thin routers,
> `services/{commercial,finance,library}` import neither routers nor `solver_service` (so they never call
> `physical_quantities`, which imports `solver_service` — P0 gate finding 7), existing `_HANDLER_PARAMS`
> entries unchanged (new handlers add entries).
>
> **Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` (§4.1, §5.3, §5.5, §11, §12,
> §13 P2 row, §15). **P1 findings:** `docs/superpowers/findings/2026-09-27-ic-p1-commercial-layer.md`.
>
> **Plan status:** v0.3 — round 1 `FAIL` (22 findings) → v0.2; round 2 `PASS WITH CONDITIONS` (C1–C7 +
> 9 LOW) → v0.3; round 3 **PASS** (three residues closed in text). Implementation starts at WP2.0.

**Goal of P2.** Every tariff item a US or EU site is billed is rated **exactly** on the solved dispatch (the
billing pass), including the URDB constructs P1 left out; every contract is settled into attributable lines;
the gap between the LP's dispatch-grade cost and the exact bill is reported **per item kind with a cause**,
and an unexplained gap raises a gate; tariffs, contracts and connection agreements live in the Library
(versioned, org-scoped, URDB-importable) and are pinned by the project; `/results/billing` and
`/results/cfe_score` serve the numbers through thin handlers. No finance arithmetic (P4), no participants
(P3), no Investment tab / Tariff builder / Library UI (added to the P3 outline).

**Entry state (from P1).** `tariff_engine.rate` bills energy/TOU, fixed per-month, demand (per window,
settlement-interval means, ratchets on actual prior peaks with a meter-history seed, partial months in full)
and convex / non-convex tiers on a single catch-all period. The LP carries energy adders, a connection fee,
demand/ratchet/tier variables and a group cap, with reload-safe rows and drift hashes. Open P1 binding
conditions: **(4)** move `routers/simulation._bind_commercial` and `routers/library._series_from` logic into
services — WP2.4b-0, first of the Library block; **(5)** FOM — WP2.0.

**Environment.** venv `/home/user/.venv-gui312`; backend `python -m pytest -m "not slow"`;
`python tests/run_qa_drivers.py`; frontend `npx vitest run`, `npx tsc --noEmit -p tsconfig.json`.

**Dependency order.**

```
P2 billing pass & contracts
 ├─ WP2.0  FOM/capex-convention merge + P1 row/drift hygiene                    ── first
 ├─ WP2.1a-0 demand windows keyed by period NAME (engine + LP)                   ── before any URDB work
 ├─ WP2.1a-i   engine: per-day fixed, tariff capacity items, demand tiers
 ├─ WP2.1a-ii  engine: tiers inside TOU windows
 ├─ WP2.1a-iii engine: designated-month and cyclic ratchets
 ├─ WP2.1b site billing adapter + compact billing frames + per_item_sampled
 ├─ WP2.1c LP: convex demand tiers, windowed tiers, new ratchets, predicted non-convex tier
 ├─ WP2.4b-0 condition 4 refactor: services/commercial/binding.py (router keeps a wrapper)
 ├─ WP2.4a Library items (tariff / contract / connection agreement) + import_tariff_ref + pins v2
 ├─ WP2.2-0 settlement inputs: contracts config field, seam interval frames, DSR commit, config-time series
 ├─ WP2.2a contracts: PPA ×4 + CfD
 ├─ WP2.2b contracts: DR, lease, EaaS, retail (retail needs WP2.4a)
 ├─ WP2.2c contracts round trip + double-count preflight
 ├─ WP2.2d `changes_dispatch` PPA in the LP (buyer case)
 ├─ WP2.3 billing_vs_lp_gap per item kind, causes, warn gate
 ├─ WP2.4b-i URDB importer   ├─ WP2.4b-ii series / meter-data import   ├─ WP2.4c chat tools
 └─ WP2.5 compute_billing / compute_cfe_score thin results
```

**Per-WP invariants (all of P2).** The P1 reconciliation gate stays green after every WP; each LP change adds
its own case. The engine never returns 0 for an item it cannot rate (ADR-0001). Every new persisted record
carries a content hash, so an edit without a re-solve is `config_changed_since_solve`. Every model change is
backward compatible (old JSON validates unchanged; a test loads the P1 fixtures) and mirrored in
`frontend/src/api/types.ts`.

---

## Oracles (bind the gate)

Committed under `backend/tests/fixtures/investment_case/oracles/` with `PROVENANCE.md`: source repository,
commit, file and line, licence (REopt.jl, Apache-2.0, commit `6d43289`), REopt's `NOTICE` reproduced
("Copyright 2023 Alliance for Sustainable Energy, LLC"), and each fixture marked **verbatim**, **derived**
(inputs copied from a test body) or **self-authored**. Every URDB source ships with a hand-translated `Tariff`
JSON beside it (so WP2.1a can test the engine before the importer exists; WP2.4b-i asserts the importer
produces exactly these JSONs).

| Id | Source (REopt `test/`) | Axis | What it pins | Expected (formula from the testset) |
|---|---|---|---|---|
| R1 | `scenarios/leap_year.json` (verbatim; schedules are JSON **strings** in the file) + runtests.jl L4143–4210 | 8,760 naive hours from Jan 1, `timezone=None`, no DST; in 2024 the axis ends Dec 30 (REopt truncation, `utils.jl` L569–570), so December is a partial month; December load is 0 | weekday/weekend TOU energy in 2023 vs leap 2024; facility + TOU demand; the Feb-28/29 facility case | energy = rate × 10 kWh, demand = (flat [+ TOU]) × 10 kW, per the testset branches. The per-day fixed charge is **not** pinned by R1 (REopt converts `$/day` × 30.4375; a documented deviation — the engine pro-rates by covered days; per-day is pinned in H3) |
| R2 | `scenarios/tiered_tou_demand.json` (verbatim) + L2025–2037 | 8,760 flat hours at 1e6/8760 kW | tiered demand (0 $/kW to 50 kW, then 12 $/kW), 2 tiers | `12 × (tier1_max × r1 + (peak − tier1_max) × r2)` |
| R3 | "Lookback Demand Charges" cases 2 and 3, runtests.jl L1911–1956 (**derived**) | 8,760 hours, 2022 | designated months `[1,4,12]` at 75 % (year-wide); range 6 at 75 % (cyclic) | the testset's `monthly_peaks` × `monthly_demand_rates` |
| R3′ | case 1 (L1893–1909): URDB label `539f6a23…` is not in the repo; a URDB-shaped response **self-authored** from the testset comment (35 % lookback all months, 5 cold months 10.5 $/kW, 6 warm 11.5 $/kW) | 8,760 hours, 2022 | URDB lookback fields through the importer, authored in **range** mode (`lookbackrange = 11`), imported with `cyclic_year=True` | `100 × (10.5 + 0.35·10.5·5 + 0.35·11.5·6)` |
| R4a | "Blended tariff", `scenarios/no_techs.json` (verbatim) L384–391 | flat 10,000 kWh/yr hourly | blended energy and demand | energy `0.10 × 10000 = 1000.00`; demand `12 × 10 × 10000/8760 = 136.9863…` (the formula, not the rounded 136.99) |
| R4b | "Fifteen minute load" L537–545 (**derived**: REopt asserts only annual kWh; the bill is formula-derived) | 35,040 × 1 kW, 2017, 15-min | 15-min rating | energy `0.10 × 8760 = 876.00`; demand `12 × 10 × 1 = 120.00` |
| H1–H3 | self-authored hand-rated bills with the working in the fixture: DE (Arbeitspreis / Leistungspreis on annual peak, §19 StromNEV as a documented non-support), NL (energy-tax bands as tiers, fixed per day), US C&I (TOU + windowed demand with a **split-peak** period + ratchet + per-day fixed + a 3-tier windowed energy tariff) | 15-min | every item kind | the fixture's arithmetic, to the cent |
| C1 | self-authored contract fixture: PV + BESS behind the PoC with the BESS charging from PV **and** the site exporting in some intervals; each contract type of WP2.2a/b | 15-min, 7 days | settlement lines | the fixture's arithmetic, to the cent |

"To the cent" = `abs(actual − expected) < 0.005` per bill line and in total. Only **2-tier** REopt cases are
pinned (REopt reads URDB tier `max` as a width, `electric_utility_constraints.jl` L278/L385; URDB defines it as
cumulative — the engine follows URDB; for 2 tiers the readings agree). Engine timing on a 15-min year with 8
items is **recorded by the QA driver** (spec §16 risk 1), with a bound of 10 s asserted there.

---

## WP2.0 FOM / capex convention merge + P1 row and drift hygiene (P1 condition 5)

`origin/claude/fix-fom-reconciliation` (68c6661) is 68 files / +5,680 lines: it also carries master #53 (EH
wired stages, `EhReferenceDesignPanel`, `chat_tools_schema.py`, `routers/results.py`) and changes the capital
cost convention ("scale annual capital_cost; charge fixed cost only in active periods"). A dry `git merge-tree`
is textually clean; semantically it breaks `test_physical_quantities_seam.py::test_agrees_with_asset_economics_generators`.

Files: merge commit; `backend/services/results/physical_quantities.py` (`fixed_cost_eur` to the new
convention), `backend/tests/test_physical_quantities_seam.py` (L117 test updated), reconciliation gate,
`services/commercial/connection.py` / `cost_rows.py` (hygiene).

- [ ] Red: reconciliation case `fom` (edge fixture, `fom_cost > 0` on PV and BESS, no commercial config) —
  gap < 1e-6 before and after save → load; the seam test at the new convention; P1 INFO hygiene: every
  committed connection agreement (also fee-less) carries `agreement_hash` (a cap edit on a non-firm agreement
  is drift), a test for the non-firm fixed-fee drift path, `block["flags"]` in a stable documented order.
- [ ] Green: merge (merge commit, no rebase); change `physical_quantities.fixed_cost_eur`; hygiene.
- [ ] Verify (the merge brings unrelated work): full backend `not slow`, all QA drivers (incl.
  `qa_commercial_lp`, `qa_eh_reference_design` if present), vitest, `tsc`.
- Acceptance: ten reconciliation cases green; the findings-note carry "multi-period gap with discounting on
  plain solves" measured on the merged tree and recorded (fixed or still open; WP2.3 does not depend on it —
  its gap compares per-item LP cost with the bill, not the objective).

- **As implemented (c4bacde merge + 0a86b17):**
  - Merged the branch tip ea384c3 (it had moved past 68c6661), with a merge commit and no conflicts.
  - `physical_quantities` now charges fixed cost as (entry `fixed_cost`, else annuitised investment + FOM per horizon) × capacity × active years.
  - The seam tests compare FOM on the same per-horizon basis; the old TODO is gone.
  - Tenth reconciliation case `fom`. It is not red on its own, because gap 0 held under both conventions; the red evidence is the branch's `test_fom_reconciliation.py`, which failed 23 of 26 on the pre-merge code.
  - `n.meta["ic_connection"]` records every committed agreement's hash.
  - Flags are sorted.
  - The hourly-assumption audit allow-lists `solver/periodized_costs.py` (3 unit sites; now 46 sites in 22 files).
- **Verification on the merged tree:**
  - full `not slow` suite: 6,317 passed, 31 skipped, 1 failure (the audit, fixed);
  - all 23 QA drivers (including `qa_eh_reference_design`);
  - vitest 1,956;
  - `tsc` clean.
- **Carried "discounting gap" item, measured:**
  - two periods at a 7 % rate: raw `gap_pct` −43.4 %, fully explained by the merged `period_weighting_adjustment_eur` bridge; `residual_gap_pct` ≈ −1e-9;
  - at a 0 % rate: gap −2.6e-9.
  - Resolved by the merge. **Which figure to read:** `gap_pct` on commercial solves (the bridge does not know the
  commercial LP terms, so `residual_gap_pct` is non-zero there by construction — measured 27.7 % flat with a fee);
  `residual_gap_pct` on plain multi-period solves with discounting.

- **Review round 1 (PASS WITH CONDITIONS) → closed:**
  - **Versioned drift hashes.** `services/commercial/hashing.py` has two recipes:
    - recipe 2 is `exclude_defaults`, so a new optional field never changes a hash;
    - recipe 1 is the full dump; legacy records are compared with it, minus the later fields listed in `FIELDS_AFTER_V1`.
    
    Every record carries `hash_version`, and comparisons use the record's recipe. A P1 hash is pinned in `test_commercial_hash_versions.py`.
  - **Seam degraded paths.**
    - A failed cost resolve nulls both FOM and fixed cost.
    - A class missing from the resolver is charged FOM only, like `_fixed_rates`.
    - `fom_cost_eur_annual` reads the resolver's typed `fom_cost_annual`.
  - **Reconciliation gate.**
    - Two-period cases are solved with `multi_investment_periods=True`.
    - An 11th case, `poc_capex_fee`.
  - **Gap-figure note** amended.

- Round 2 → **PASS**. Hardening R1 closed:
  - `FIELDS_AFTER_V1` is keyed by (model, field) and walked per model.
  - `test_commercial_hash_versions.py` pins the recipe-1 field inventory of every model reachable from `CommercialConfig`, and fails on a field added without its registration and default.
  - The `hashing.py` docstring says that changing an existing default must bump `HASH_VERSION` (R2).
  
  **Every later WP that adds a commercial model field registers it there.**

## WP2.1a-0 Demand windows keyed by period name

A URDB demand period is one `TariffPeriod` per `[start, end)` fragment and per weekday/weekend set; P1 keys
peaks and ratchets by the period's **list position** (`tariff_engine._period_index`, L201–218; LP keys
likewise), so a split-peak period is billed as two peaks.

Files: `tariff_engine.py`, `lp_bindings.py`, `test_tariff_engine_demand.py`, `test_lp_bindings_peak_demand.py`.

- [ ] Red: a split-peak demand period (two fragments, one name) bills ONE peak per month in the engine and the
  LP (gap 0); ratchets read the named window; the demand drift hash covers the name mapping; windows are keyed
  by **(month, name)**, so a summer "peak" and a winter "peak" at different rates (disjoint months) stay valid —
  only fragments of one name that apply in the **same month** with different rates are refused (`TariffItem`
  validator, demand items only); all P1 tests unchanged (unique names = old behaviour). The demand hash recipe
  is **versioned** (`info.hash_version`; P1 records have none and are compared with the P1 recipe), so
  P1-solved projects do not flip to `config_changed_since_solve` on upgrade.
- Acceptance: the committed `ic_demand_peaks` key uses the name; P1 records (position keys) still produce rows
  (read-compat test).

- **As implemented:**
  - `tariff_engine.demand_windows(item, local)` maps each interval to its NAMED window (plus the fragment index). `window_rate` takes a month's window rate from the matched fragments. The engine and `lp_bindings._demand_spec` both use them.
  - LP keys are `item|name|period|month`.
  - A `TariffItem` validator refuses same-name demand fragments with overlapping months and different rates.
  - `ic_demand_info.hash_version = 1` (`DEMAND_HASH_VERSION`).
  - Tests in `test_lp_bindings_peak_demand.py` (WP2.1a-0 section):
    - split peak billed once (engine) and equal in the LP;
    - validator;
    - disjoint-month rates;
    - name in keys;
    - rename is drift;
    - P1 position-keyed records still produce rows.

- **Review round 1 → FAIL, fixed:**
  - A free (rate-0) month of a window sets no ratchet in the engine, as in the LP; the month still counts as modelled.
  - The validator covers every item billed as demand (`peak_import` too). It checks the EFFECTIVE first-match rate per (month, name) on the exact 12 × 7 × 24 grid: a shadowed same-name default (the P1 override pattern) is valid, and weekday/weekend variants at two rates are refused.
  - `window_rate` raises when the matched fragments disagree.
  - `|` is refused in demand item ids and period names, and peak keys are asserted unique.
  - A P1 record without `hash_version` holding two peaks for one (item, window, month) flags `demand_recipe_changed`; unique-name P1 records are unflagged.
  - New live LP = engine tests: ratchet on a split peak; summer/winter windows with and without a ratchet; a free winter with a ratchet; net hourly; two periods. Engine tests are in `test_tariff_engine_demand.py`.
  - **Upgrade note:** two kinds of saved P1 demand item no longer validate — same-name fragments with two
  effective rates in one month, and a demand item id or period name containing `|`. The project still loads and
  its rows are still shown, flagged `commercial_config_invalid`; they also carry `config_changed_since_solve`
  (the stored config no longer parses, so it cannot be matched — the config itself did not change) and, for a
  split window billed twice under P1, `demand_recipe_changed`. A re-solve needs the tariff fixed (rename one
  fragment, or drop the `|`).
- Round 2 → **PASS** (conditions C1, C2 closed: the upgrade note above).

## WP2.1a-i Engine: per-day fixed, tariff capacity items, demand tiers

Model delta (all optional, old JSON unchanged): `TariffItem.unit += "per_day"`; `Tier` allowed on demand
items; `CommercialConfig.power_factor: float | None` (for `per_kva_year`).

- **Per-day fixed**: rate × covered days on the local clock (partial days pro-rated by hours).
- **Capacity items** (`per_kw_year`, `per_kva_year`): rated on the **PoC `p_nom_opt`** (the sized connection —
  the same quantity P1's connection fee charges), pro-rated like the LP's fee: by **represented hours (Σ
  objective weights) / 8760** — the `nyears` of `connection.add_fee_term` — so leap years and representative
  weeks agree with the LP to the cent;
  `per_kva_year` divides by `power_factor`, absent ⇒ `not_established`. A tariff capacity item **and** a
  `ConnectionAgreement.capacity_fee` on the same PoC is a preflight error `commercial.capacity_double_count`.
  The engine takes the capacity as an argument; the adapter (WP2.1b) supplies `p_nom_opt`.
- **Demand tiers**: tier thresholds on the billed kW of the (month, window); cost Σ rate_k × kW in tier k.

- [ ] Red: R2, R4a, R4b to the cent (hand-translated JSON); per-day over a leap February (H3 slice); capacity
  on a two-period network; kVA without power factor `not_established`; P1 engine tests unchanged.

- **As implemented:**
  - `rate(..., capacity_kw=, power_factor=)`. The engine handles capacity items in their own branch, before the demand branch.
  - **Deviation (DE fixture):** a capacity item measured on `peak_import` (the German Leistungspreis) bills the year's **measured** peak (settlement-interval mean) × rate × represented hours / 8760, once per year. Other capacity items bill the contracted `capacity_kw`. In P1 the engine refused every capacity item (`unsupported:capacity_via_connection_agreement`, so the bill's `total` was withheld), and the LP never carried it. The change is from unsupported to billed; `test_a_15min_year_rates_in_under_a_second` pins the amount.
  - Demand tiers price the billed kW through `_tier_cost`. A windowed demand tier is `unsupported:demand_tiers_with_windows` until WP2.1a-ii.
  - `capacity_double_count` is refused in `validate_for_network`.
  - `power_factor` is registered in `FIELDS_AFTER_V1`.
  - `types.ts` is mirrored.
  - Oracles are under `tests/fixtures/investment_case/oracles/`, with PROVENANCE and REopt's NOTICE. R2, R4a and R4b pass to the cent (`test_tariff_engine_urdb.py`).
  - The hourly audit allow-lists the capacity unit (48 sites in 23 files).
  - LP: demand tiers, capacity and per-day items stay `not_in_lp` until WP2.1c.

- **Review round 1 → FAIL, fixed:**
  - A NaN interval makes the measured peak not established (`nan_quantity:N`), never 0.
  - A tiered demand window is free only if every tier rate is 0, so a ratchet applies at R2's period-rate-0 convention.
  - At the engine boundary:
    - a non-finite `capacity_kw` is not established;
    - a negative one is refused;
    - a `power_factor` outside (0, 1] is refused.
  - Tiers on a capacity item are `unsupported:tiers_on_capacity`.
  - Capacity over gappy data without `represents_hours` gets `capacity_on_partial_coverage`, and `total` is withheld (C4).
  - Per-day charges count real local calendar days (23/24/25 h).
  - The pro-rating conventions are disclosed: `capacity_prorated_by_represented_hours` and `peak_from_partial_year`.
  - The LP's reason for capacity items is `capacity_not_in_lp_until_WP2.1c`, checked before demand.
  - Preflight emits `commercial.capacity_double_count`.
  - Oracle hygiene: verbatim REopt scenarios committed, and the R2/R4a tests read their inputs from them. `REOPT_LICENSE` is shipped, and PROVENANCE marks the derived files.
  - Tiered demand lines show `rate` NaN.
  - Carried to WP2.1b (finding 12): capacity on a two-period network. The engine applies the scalar `capacity_kw` to every year, while the LP fee counts only the periods in which the PoC is active. The adapter passes a per-period capacity, and a test pins it.

- **Round 2 → PASS WITH CONDITIONS, closed:**
  - `capacity_prorated_by_represented_hours` fires whenever a year's represented hours differ from the 8760 divisor, including a full leap year (8784/8760).
  - A per-day charge over a `billing_period` uses each local day's exact overlap with `[lo, hi)`: no hourly walk, so sub-hour bounds and 30-min DST shifts are exact.
  - The stale "hours / 24" line is removed.
  - **Recorded limitation:** a dispatch row is attributed to the local day (and month) it starts in, the same rule the per-month charge uses. Rows longer than an hour that cross local midnight shift a fraction of a day between days (5-h rows over 2024 in Amsterdam: 366.0036 days). Sub-daily axes at ≤ 1 h are exact.
  - **Recorded behaviour change:** `_lp_reason` checks capacity before demand. A P1 item of kind `capacity`, unit `per_kw_month`, `measured_on="peak_import"` was carried by the P1 LP as a monthly demand peak, while the engine refused it. Now neither bills it: the LP reports it `not_in_lp` and the engine `unsupported:unit_per_kw_month_for_capacity`. No committed fixture uses the combination. A re-solved P1 project with such an item drops that LP term; the correct modelling is kind `demand`.

- Round 3 → **PASS** (no residue).

## WP2.1a-ii Engine: tiers inside TOU windows

Model delta: `TariffPeriod.tier_rates: list[float] | None` aligned with the **item's** `tiers` thresholds (one
threshold list per item; each period carries its own rates). **Single-period tiered items keep P1 semantics**
(rates on `Tier.rate`, `tier_rates` absent). A tiered item with **more than one period** must carry
`tier_rates` on every period, and then every `Tier.rate` must be 0 (refused otherwise — one source of rates). URDB semantics: the tier position is the
month's **total** energy across periods (URDB `max` is cumulative); each period's energy is split into tiers in
proportion to the month's total (standard bill practice; REopt allocates optimally — a documented deviation,
gap cause `tier_allocation` in WP2.3).

- [ ] Red: H3's 3-tier windowed fixture to the cent; proportional split verified by hand; a multi-period tiered
  item with a period lacking `tier_rates`, or with a non-zero `Tier.rate`, is refused; every P1 tier test
  unchanged.

- **As implemented:**
  - `TariffPeriod.tier_rates`, registered in `FIELDS_AFTER_V1`. `is_windowed_tiered(item)` is true when the periods carry `tier_rates`.
  - Energy: per month, the total energy positions the tiers, and each fragment's intervals pay the blended rate Σ_k tier_rates[p][k] · Q_k / E. The interval lines add up to the bill; the hand bill is 872.00 to the cent.
  - Demand: each window's billed kW is priced through the thresholds with that window's `tier_rates`. A window is charged if any of its tier rates is non-zero, which gates ratchets.
  - The validator:
    - requires `tier_rates` on every period of a windowed tiered item, with every `Tier.rate` at 0;
    - refuses `tier_rates` without the item's `tiers`;
    - requires same-name demand fragments to agree on `tier_rates`.
  - The LP reports any item with `tier_rates` `not_in_lp` (`tiers_with_windows`) until WP2.1c, so its zero `Tier.rate` is never priced as 0.
  - `types.ts` mirrored.
  - (Superseded by review round 1 below: the P1 windowed shape now migrates rather than being refused.)

- **Review round 1 → FAIL, fixed:**
  - **Lossless migration.** A before-validator on `TariffItem` migrates the P1 shape (tiers with rates on `Tier.rate` and periods that are not one catch-all) to per-period `tier_rates` holding the same rates in every window, with `Tier.rate` set to 0. Persisted P1 configs keep validating. A single windowed period (P1: `unsupported:tiers_with_windows`) bills only inside its window; intervals outside are unrated (`unrated_intervals:N`), like any windowed item without a catch-all, and are never billed.
  - The validator treats a single non-catch-all tiered period as windowed.
  - Windowed-tier flags count only uncovered and NaN intervals, and name `tier_month_not_established:<YYYY-MM>`.
  - `tiers_on_represented_volume` is noted when `represents_hours` scales the volume that monthly thresholds see; carried to WP2.1b's representative-week work.
  - **Recorded URDB mapping limit (for WP2.4b-i):** the model holds ONE cumulative threshold list per item and the same number of rates on every period. REopt takes each month's tier limits from the weekday hour-0 period, reads `max` as a width, and tolerates periods with different tier counts. The importer refuses URDB tariffs whose periods differ in tier `max` or tier count, naming the field; it never picks one silently.
  - **Recorded deferral:** the spec's "H3 3-tier windowed fixture" is an in-test hand bill here (872.00, `test_windowed_energy_tiers_split_the_month_in_proportion_to_the_total`). The H3 fixture file (the full US C&I hand bill) lands with the P2 QA gate driver.

- Round 2 → **PASS WITH CONDITIONS**, closed: the migration validator returns malformed tier input (a missing, null or boolean rate; non-list `tiers`) unchanged, so field validation reports a `ValidationError`, never a raw `TypeError` (a 500 on the config route). The reviewer confirmed no false drift for P1-solved projects: recipe-1 hashes are identical between the P1-tip and the current code, and the migrated items were never in a hashed set.

## WP2.1a-iii Engine: designated-month and cyclic ratchets

Model delta: `Ratchet.lookback_months: int | None` (was required `ge=1`); new `Ratchet.months: list[int] |
None` (designated months, URDB `lookbackMonths`) and `Ratchet.cyclic_year: bool = False`. Exactly one of
`lookback_months` / `months` is set (old JSON always has `lookback_months`).

- **Range mode** (`lookback_months = N`): as P1 (the N months before, on actual peaks, meter history for months
  before the horizon); with `cyclic_year=True` the lookback wraps within the rate year of each investment
  period (January reads December of the same year — REopt's steady-state year, `mod(mth−lm−1,12)+1`).
- **Months mode** (`months = [..]`): **year-wide**: every month of the rate year is billed at least `share ×`
  the maximum actual peak over the designated months of that rate year (REopt
  `electric_utility_constraints.jl` L424–436: January is billed from April).
- `cyclic_year` applies to range mode only (a validator refuses it with `months`; months mode is year-wide by
  definition).
- **Unmodelled lookback months.** Range mode, non-cyclic: a month before the horizon reads meter history under
  its own key (P1). Cyclic range mode and months mode: a lookback month of the rate year Y that is not modelled
  (e.g. representative weeks) reads meter history under its **same-rate-year key** `f"{Y}-{mm}"`; if absent it
  is unknown ⇒ `ratchet_seed_missing` (the bill a lower bound, `total` withheld); never inferred from other
  months. Consequence (stated in the engine docstring and the Library meter-data help): the same-rate-year key
  resolves only when the modelled year is a metered year; for a future year use range mode with meter history
  (or `cyclic_year=True`, which reads the modelled months themselves).
- Ratchets apply to the demand item they sit on; the URDB importer (WP2.4b-i) attaches the URDB lookback to
  the **facility** (flat) demand item only (REopt behaviour).

- [ ] Red: R3 cases 2 and 3; R3′ with `cyclic_year=True` (hand-translated) — complete, no seed flag; the same
  with `cyclic_year=False` and no history ⇒ `ratchet_seed_missing`; months mode on representative weeks
  missing a designated month ⇒ `ratchet_seed_missing`.

- **As implemented:**
  - `Ratchet.months` and `Ratchet.cyclic_year`, and `lookback_months` is now optional. The validator enforces exactly one mode, allows `cyclic_year` in range mode only with `lookback_months ≤ 11`, and requires months to be unique values in 1..12. Both fields are registered in `FIELDS_AFTER_V1`.
  - `tariff_engine._ratchet_floor_prior` dispatches the three modes. Cyclic and months modes read an unmodelled month's history under its same-rate-year key; a month that is still unknown sets `ratchet_seed_missing`.
  - The LP reports the new modes `not_in_lp` (`ratchet_mode_not_in_lp_until_WP2.1c`), so it never reads a missing `lookback_months`.
  - Oracle fixtures, with PROVENANCE:
    - `r3_case2`, `r3_case3` (derived);
    - `r3prime.urdb.json` (self-authored);
    - `r3prime.tariff.json`.
  - R3 cases 2 and 3 match REopt's `monthly_peaks` per month to the cent. R3′ matches in cyclic range mode.
  - `types.ts` mirrored.

- **Review round 1 → PASS WITH CONDITIONS, closed:**
  - Both ratchet-prior functions are NaN-aware. A NaN lookback peak makes the dependent (month, window) billed demand NaN, flagged `ratchet_prior_unknown:<month>`, so `monthly`/`annual` never carry a floor that appears or vanishes with the NaN's position.
  - The engine docstring states the same-rate-year history consequence: it resolves only for a metered year; for a future year use non-cyclic range with history, or `cyclic_year`. **Carried to WP2.4b-ii:** the same sentence goes in the Library meter-data help.
  - A representative-weeks months-mode test is pinned.
  - **Recorded:** a new-mode ratchet takes the WHOLE demand item out of the LP (`not_in_lp`) until WP2.1c, not only its ratchet. The LP does no peak shaving for that item until then; the engine bills it exactly.

- Round 2 → **PASS** (no residue; 40 random P1 range-mode cases bill identically to the pre-WP engine).

## WP2.1b Site billing adapter, compact billing frames, `per_item_sampled`

Files: `backend/services/commercial/billing.py` (new, pure), `services/finance/report.py`
(`store_billing_frames` / `load_billing_frames`), `backend/tests/test_site_billing.py`.

`bill_site(n, commercial, *, meter_history=None) -> SiteBill`: import = Σ `import_links` `p0` (group meter),
export = export Link `p0`, UTC index when `timezone` is set, `step_hours` from the axis, one `RatingResult` per
investment period (keyed by period, the period's years in provenance), representative weeks via
`represents_hours` from the objective weights and a `billing_period` of the represented calendar year (P1
rule), capacity items on the PoC `p_nom_opt`. Provenance: tariff hash, and the energy / demand hashes of the
solve it rates.

**Engine convention (P1 carry, moved here from WP2.3):** for items with months not established the engine's
`per_item` stays `None` and a new `per_item_sampled` carries the sampled sum; LP rows keep the sampled sum
with `demand_months_not_established`; both compare sampled to sampled.

**Billing frames** (P0 carry): flat store keys `"{period}:{frame}"` with frames `lines` (wide: index = interval,
naive UTC; one float32 column per item, amount), `quantities` (wide float32 kWh), `monthly`, `demand_lines`
(no Categorical columns, no tz-aware columns — the restricted unpickler refuses them, `routers/projects.py`
L312–337, and one refused value drops every side result). Size bound: < 3 MB for a 15-min year with 8 items
(2.52 MB measured for the wide float32 shape). `_RESULTS_STATE_SCHEMA` is not bumped (still DataFrames); a
round-trip test through `_safe_unpickle_results` proves it.

- [ ] Red: capacity per investment period follows the PoC's activity in that period (WP2.1a-i review #12);
  adapter bill == `rate()` on the same frames (identity) on the P1 QA networks; group site billed on
  summed members; two periods ⇒ two results; representative weeks ⇒ 12 months with `per_item_sampled` and the
  unsampled months flagged; unsolved ⇒ `None` + `not_solved`; frames round-trip through
  `_safe_unpickle_results` under the bound.

- **As implemented:**
  - `services/commercial/billing.py` (pure; the tripwires confirm it imports no routers and no `solver_service`) provides `SiteBill(per_period, flags, provenance)`, `bill_site` and `compact_frames`.
  - An unsolved network returns `per_period={}` with `not_solved`; a config without an import tariff returns `no_import_tariff`. There is no `None` bill, so callers test the flag.
  - The step is the median in-period step; gaps longer than 24 h between sampled stretches are excluded, so representative weeks keep a 0.25 h step.
  - Representative weeks: `represents_hours` is the objective weights, passed only when they differ from the step. The `billing_period` is the calendar year holding most of the weight, when the period's Σ weights is 8760 h ± 1 % (the `lp_bindings` rule). `commercial/billing.py: 1` is added to the hourly audit allow-list, now 51 sites in 24 files.
  - Capacity: `capacity_kw` is the PoC `p_nom_opt` (falling back to `p_nom`) × 1000 in periods where `get_active_assets` has the PoC active, and 0 otherwise. Meter history seeds the first period only.
  - Provenance: `tariff_hash` (current recipe), plus the solve's `energy_hash` (`ic_poc_links`) and `demand_hash` (`ic_demand_info.items_hash`), `period_years` from `investment_period_weightings.years`, and `timezone`.
  - `RatingResult.per_item_sampled` is the finite sum of the item's monthly parts (None when there are none). `per_item` still stays None when months are not established.
  - `compact_frames` keys are `"{period|_}:lines" / ":quantities"` (wide float32 from a `pivot_table`), plus `":monthly"` and `":demand_lines"` (month, item and period columns stored as object strings). The existing `store_billing_frames` / `load_billing_frames` pair keeps the zone.
    - A 15-min Europe/Berlin year with 8 items round-trips through `_safe_unpickle_results` under 3 MB.
  - **Carried item `tiers_on_represented_volume` resolved:** monthly tier thresholds see the month's real volume when that month's rows represent its calendar hours (days × 24, ±1 %), as with one week per month weighted to its month. Only months that stand for more or less are noted, as `tiers_on_represented_volume:<month>`. There is no bare note any more.
  - Tests: `tests/test_site_billing.py` has 8 tests. Four live solves cover the identity on the 15-min edge, the group meter, two periods with the PoC retired before 2040 and an off-grid backup supplying that period, and representative weeks with `per_item_sampled` and not-established flags. The other four cover the unsolved case, the meter-history spy, `per_item_sampled` on a missing month and the unpickler round-trip.
    - `test_tariff_engine_urdb.py` gains the self-represented-month case.

## WP2.1c LP: convex demand tiers, windowed tiers, new ratchets, predicted non-convex tier

Files: `lp_bindings.py`, `cost_rows.py`, `test_ratchet.py`, `test_tiers.py`, reconciliation gate.

- **Convex demand tiers** (rising rates): stacked variables on `ic_billed_demand` per key (`ic_demand_tier_q`),
  cost Σ w_obj · rate_k · q_k; falling ⇒ `not_in_lp` with reason (billed exactly, gap cause).
- **Convex windowed energy tiers**: variables `ic_tier_q[item, month, period name, k] ≥ 0` with, per (item,
  month): Σ_k q[p,k] = the period's volume (Σ w·p over the period's snapshots) for every period p, and Σ_p q[p,k]
  ≤ width_k for every tier k; cost Σ w_obj · tier_rates[p][k] · q[p,k]. Convex iff the rates rise in k for
  **every** period; any other shape ⇒ each period priced at its first tier, flagged `nonconvex_tier`. The
  committed `ic_tier_volumes` carries the per-period allocation (with the items hash) so rows reconcile after a
  reload. The engine's proportional bill is ≥ the LP optimum on the same dispatch, so WP2.3's `tier_allocation`
  (engine − LP) is ≥ 0.
- **Designated-month and cyclic ratchets**: linear on `ic_billed_demand` (months mode: billed[m] ≥ ρ·peak[k]
  for every designated k of the rate year).
- **Predicted non-convex tier** (spec §5.3): with `CommercialConfig.meter_history_energy_kwh` ({"YYYY-MM":
  kWh}), month m is priced at the **marginal rate** of the tier its **same month one year earlier** (m−12)
  lands in; absent ⇒ first tier (P1). Flag `nonconvex_tier` carries the predicted tier.
- **Tariff capacity items** (`per_kw_year`, no connection fee on the PoC — the combination is a preflight
  error): an explicit `p_nom` term reusing `connection.add_fee_term` (extendable PoC); fixed `p_nom` ⇒ reported
  as `network_capacity_fixed`, outside the LP, as P1 does for a fixed connection fee. Reconciliation case
  `tariff_capacity`.
- **Falling demand tiers** are priced at the first tier (as P1 does for falling energy tiers), flagged
  `nonconvex_tier`.
- LP coverage after P2 (stated in the module docstring): everything the engine bills is in the LP except fixed
  and per-day items, `per_kva_year` items, and contract settlements other than WP2.2d (`not_in_lp` /
  `settlement_only` with reasons).

- [ ] Red: each construct = engine on the same dispatch, gap 0; reconciliation cases `demand_tiers`,
  `windowed_tiers`, `ratchet_designated`, `ratchet_cyclic` before and after save → load; drift hashes cover the
  new fields.

## WP2.4b-0 Condition 4 refactor (lands before the Library work)

Files: `backend/services/commercial/binding.py` (new: org resolution by an injected resolver, alignment,
FCA planning, writes), `backend/services/library/series_io.py` (new: the timestamp / zone rules of
`routers/library._series_from`), `routers/simulation.py`, `routers/library.py`.

- [ ] Red→Green: all existing route tests pass unchanged; `routers.simulation._bind_commercial` stays as a thin
  wrapper calling the service (`tests/test_connection_envelope.py:299` monkeypatches it); line counts of both
  routers recorded.

## WP2.4a Library items: tariffs, contracts, connection agreements

Files: `backend/services/library/items.py` (new; JSON payload written as a file under the org library dir —
`LibraryItem.path` is NOT NULL — versioned, content-hashed), `routers/library.py` (`GET/PUT
/api/library/items/{kind}` and `/api/library/items/{kind}/{name}`, `kind ∈ {"tariff","contract",
"connection_agreement"}` enum-validated; the `/series` routes unchanged), `services/library/bundle_pins.py`,
`models/commercial.py`, `services/commercial/binding.py`, `backend/tests/test_library_items.py`. No migration
(`kind` is `String(32)`, the unique key includes it).

- **References.** New `LibraryItemRef(kind, id, version, hash)`; `CommercialConfig.import_tariff_ref:
  LibraryItemRef | None`. The binding service resolves it at `PUT /solver_config` into the inline
  `import_tariff` (the typed config keeps both; the inline copy is what the solve uses; the ref is provenance
  and drift). `import_tariff_id` (P1 string) stays for backward compatibility with its P1 meaning.
  **Contracts and connection agreements from the Library are templates copied inline** (the inline object
  carries `library_ref`); nothing resolves them at solve time.
- **Pins v2**: the sidecar gains `kind` per pin (schema 2), reads schema 1 (series only) unchanged.

- [ ] Red: CRUD + org ACL like series; identical PUT returns the existing version; `import_tariff_ref`
  resolution through the route; stale/missing ref = `library_ref_stale` (409) at the route and
  `binding_invalid` in preflight; bundle into another org reports `library_issues` for tariff pins; schema-1
  sidecars still read.

## WP2.2-0 Settlement inputs

Files: `services/results/physical_quantities.py` (extension), `services/solver/assumptions.py` (DSR capture
hand-off, no refactor), `services/solver_service.py` (the DSR commit site, no drive-by refactor),
`services/commercial/binding.py`, `models/commercial.py` (`CommercialConfig.contracts: list[Contract]` lands
**here**, discriminated by `type` — see WP2.2a on the discriminator), `routers/network_time_axis.py` (guards), the bus create / rename code paths (`routers/network*.py` and their
services) and the netCDF upload path (refuse bus names starting `ic:`), tests.

- **Interval quantities.** `physical_quantities` additionally returns interval frames per asset (Generator
  `p`, StorageUnit `p` split into charge/discharge, Store, Link `p0`/`p1`) and per **load**, plus the PoC
  flows **on the commercial meter** (`import_links` sum, export Link) — the WP0.3 agreement test stays green.
  The caller of settlement is `services/results/billing.py` (it may import the seam); `settle` stays pure.
- **DSR dispatch commit.** The per-bus DSR slack's dispatch (`dsr_t`, captured in the undo of
  `_apply_modelling_assumptions`, which also runs on failed and sweep solves) is handed to `run_simulation`,
  which commits it **only after a successful, non-operational solve** (the P1 `_ic_operational` rule) as
  `buses_t["ic_dsr_p"]` and clears it after a successful solve without DSR; its axis hash is part of the
  settlement record's drift check. DR activation reads it; absent ⇒ `None` + `dr_activation_not_established`.
- **Reference series at config time.** Contract reference prices and `grid_cfe_share` are Library series
  resolved by `binding.py` at `PUT /solver_config` (same path as P1's export price) and written as
  `buses_t["ic_ref_price"]` (columns **`ic:contract:<id>`**) and `buses_t["ic_grid_cfe_share"]` (column
  **`ic:cfe:grid`**) with an axis hash; only fully covered series are written; a missing or stale frame ⇒
  `reference_price_missing` / `grid_cfe_share_missing`. New config field `CommercialConfig.grid_cfe_share_ref:
  TimeSeriesRef | None`. **Namespacing and lifecycle:** the `ic:` prefix cannot collide with a bus name (the
  network routes refuse bus names starting `ic:`); PyPSA's load warning "Components … of Bus are not in main
  components dataframe" for these frames is filtered in the project load path (documented); tests: netCDF round
  trip and `copy()` (probe: they survive), bus remove and bus rename leave the frames intact, a snapshot-axis
  change leaves NaN rows ⇒ `*_not_established`, never a refusal to solve; the `ic_*` time-series guards of
  `routers/network_time_axis.py` (L1025–1187) are extended to `buses_t`.

- [ ] Red: interval frames sum to the seam's period totals; DSR commit survives save → load; a reference series
  survives reload and is re-hashed; a changed ref version is drift.

## WP2.2a Contracts: PPA (four kinds) and CfD

Files: `models/commercial.py`, `backend/services/commercial/contracts.py` (new, pure), `test_contracts_ppa_cfd.py`.

**Model delta** (defaults keep old JSON valid):

| Model | New fields |
|---|---|
| all contracts | `type: Literal[...]` discriminator **with a per-class default** (so `PpaContract(...)` without `type` still constructs), `base_year: int \| None`, `library_ref: LibraryItemRef \| None` |
| `PpaContract` | `pricing: Literal["fixed","market_plus_premium"] = "fixed"`, `premium_eur_per_mwh: float \| None` (any sign; `price` stays `ge=0` for fixed pricing), `baseload_mw: float \| None`, `sleeving_fee_eur_per_mwh: float \| None`, `sleeving_party: str \| None` |
| `CfdContract` | `generator_owner: str \| None`, `counterparty: str \| None`, `indexation_pct_per_year: float = 0.0`, `reference: Literal["interval","monthly_capture"] = "interval"`, `suspend_on_negative_price: bool = False` |

Every new field is Optional or defaulted, so the P0 fixtures and `tests/test_investment_case_contracts.py`
(L310–312, L388) validate unchanged; an absent party ⇒ the line's payer/payee `None` + `party_not_established`.
**Discriminator:** a pydantic discriminated union refuses a dict without the tag (`union_tag_not_found`) even
when the field has a default, so `CommercialConfig.contracts` uses a `model_validator(mode="before")` that
fills `type` from the dict's shape for untagged P0-era payloads (`strike` ⇒ `cfd`, `availability_eur_per_mw_year`
⇒ `dr`, `annual_payment` ⇒ `lease`, `fee_eur_per_*` ⇒ `eaas`, `tariff_id` ⇒ `retail`, else `ppa`) before the
union validates; a test feeds untagged P0 JSON. **Allowed combinations:** `market_plus_premium` with
`pay_as_produced`, `as_consumed_btm`, `sleeved` (requires `premium_eur_per_mwh` and a reference price);
`baseload` uses `price` against the reference (financial) and refuses `market_plus_premium`. **Assets:**
contracted `asset_ids` must be Generators (other component classes refused in P2).

**Indexation:** P2 indexes a price to the **modelled** year y (investment period, else the snapshots' majority
year): `price_y = price × (1 + indexation_pct_per_year/100)^(y − base_year)`; `base_year` absent ⇒ y. P4
escalates from the modelled year onward — never both.

**Output line**: `(period, contract_id, payer, payee, value_stream, quantity_mwh, amount, flags)`, amounts for
the **represented hours (Σ objective weights) of one period-year** (unweighted by period years); P4 applies
period years.

| Kind | Formula (interval t, gen = the contracted assets' output) |
|---|---|
| PPA `pay_as_produced`, fixed | buyer → seller `price_y × gen_t`; volume cap: chronological within the settlement year, cap pro-rated by represented hours / hours in the year; volume above the cap is not settled under the PPA (line `ppa_excess_mwh`, flag) |
| PPA `market_plus_premium` | effective price `clamp(ref_t + premium_y, floor, cap)` × gen_t (`premium_eur_per_mwh` indexed like `price`); ref required, else `None` + `reference_price_missing` |
| PPA `baseload` | **financial** (virtual) shape: buyer → seller `(price_y − ref_t) × baseload_mw × Δt` (negative ⇒ seller pays); no physical volumes, so no overlap with PoC export revenue; ref required |
| PPA `as_consumed_btm` | consumed = `gen_t − export_attr_t`, where PoC export is attributed to the contracted generators pro rata to their share of total on-site generation in t (capped at gen_t); BESS charging from PV counts as consumed; buyer → seller `price_y × consumed`; the export revenue of the attributed share stays with the PoC owner (the tariff/export price, P3 attributes it) |
| PPA `sleeved` | buyer → seller `price_y × gen_t`; buyer → `sleeving_party` `sleeving_fee × gen_t`; the import tariff still rates PoC import (network, levies); if the import tariff has any energy item, preflight warns `commercial.sleeved_commodity_double_count` (the user removes the commodity item) |
| CfD | generator_owner ← counterparty `(strike_y − ref) × gen_t` (`strike_y` indexed by the CfD's `indexation_pct_per_year` from `base_year`), negative when ref > strike; `reference="interval"`: ref_t; `"monthly_capture"`: the month's generation-weighted ref; `suspend_on_negative_price`: intervals with ref_t < 0 settle 0 (German §51 EEG style) |

- [ ] Red: C1 PPA/CfD lines to the cent (C1 has BESS charging from PV and exporting intervals); missing ref ⇒
  `None` + flag; asset id not in the network ⇒ refusal; multi-period settles per period.

## WP2.2b Contracts: DR, lease, EaaS, retail

**Model delta:** `DrContract.counterparty: str | None`, `DrContract.contracted_mw: float | None` (absent ⇒
availability `None` + `contracted_mw_not_established`); `asset_ids` on DR refused in P2 (activation of a BESS
or generator is a P5 archetype matter) — DR targets `load_ids`.

| Kind | Formula |
|---|---|
| DR availability | counterparty → site `availability_eur_per_mw_year × contracted_mw × represented_hours / hours_in_calendar_year` (per period; leap years use 8,784) |
| DR activation | activated MWh = `ic_dsr_p` on the named loads' buses, attributed pro rata to the named loads' share of the bus load; counterparty → site `activation_eur_per_mwh × MWh`; an **event** = a maximal run of consecutive intervals with activation > 0; `max_events` / `max_duration_h` checked and violations flagged (enforcement is P5/P6) |
| Lease | lessee → lessor `annual_payment_y × represented_hours / hours_in_calendar_year` |
| EaaS | customer → provider `fee_eur_per_mwh × delivered + fee_eur_per_year × fraction`; delivered = Generator `p>0`, StorageUnit discharge, Link `p1` delivered at bus1 of the named assets |
| Retail | the retail bill **is** the import tariff's bill (WP2.1b): the contract only names payer (customer) and payee (retailer) of those lines — no extra lines; `tariff_id` is compared with the payload's **`Tariff.id`** (of the inline or ref-resolved import tariff), else refused |

- [ ] Red: C1 DR/lease/EaaS/retail to the cent; DR on representative weeks and a leap year; event counting on a
  hand series; `asset_ids` on DR refused.

## WP2.2c Contracts on the config + double-count preflight

Files: `models/commercial.py` (`CommercialConfig.contracts: list[Contract]` discriminated by `type`),
`types.ts`, `services/commercial/preflight.py`, tests.

- [ ] Red (the config field itself lands in WP2.2-0): contracts round-trip through `PUT /solver_config`, save → load, bundle export → import (reference
  pins); preflight: `commercial.ppa_export_double_count` (only when the site is the **seller**
  — `seller == site_party` — of a PPA without `changes_dispatch` on an asset whose output also earns the export
  price; in the buyer case exporting surplus is correct — warning), **`commercial.dr_double_count`** (new check: a DR contract
  whose `load_ids` sit on a bus in the DSR configuration — the P1 `dsr_double_count_warnings` checks storage on
  DSR buses, a different thing), `commercial.contract_asset_missing` (error),
  `commercial.sleeved_commodity_double_count`, `commercial.capacity_double_count`; a changed contract after the
  settlement ⇒ `config_changed_since_solve` (contract hash in the settlement record).
- Acceptance: P1 WP1.8's recorded deviation closed.

## WP2.2d `changes_dispatch` PPA in the LP (buyer case only)

v1 supports `changes_dispatch=True` only for **pay-as-produced PPAs on on-site generators where the PoC owner
is the buyer** (`buyer == CommercialConfig.site_party`, new field, default `"site"`): the site pays `price_y`
for every MWh the asset produces, so the LP adds `+price_y × p_gen` as a transient `marginal_cost` adder on the
generator (committed `generators_t["ic_ppa_price"]` + meta with the contract hash); exported surplus still earns
the export price through the export Link (correct: the buyer owns the output). Refused (binding error with a
reason): seller case, other PPA kinds, `volume_cap_mwh_per_year` set (an annual cap is not a marginal cost),
an asset not a Generator.

- [ ] Red: the PV curtails when `price_y` exceeds the value of its output; rows add `ppa_settlement` and the gap
  stays 0; the settlement line equals the LP row on the same dispatch; undo restores `marginal_cost`; drift on a
  price change; reconciliation case `ppa_changes_dispatch` before and after save → load.

## WP2.3 Billing vs LP gap per item kind, with causes

Files: `backend/services/commercial/gap.py` (new, pure), `test_billing_gap.py`.

`billing_vs_lp_gap(n, commercial, site_bill)` → per item kind (`energy`, `demand`, `tiers`, `capacity`,
`fixed`, `contracts`): `lp`, `billed`, `gap_pct`, `causes: [{cause, amount}]`, `unattributed_pct`. LP cost per
item is recomputed from the committed records on the same dispatch (energy per item from the item's rates).

**Basis:** per investment period, on the **unweighted** period-year amounts (the `cost_rows` items per
period, before `years` weighting; bill and settlement lines as produced) — never the years-weighted block.

Causes, each with a **computed** amount: `settlement_only` (a contract with no LP term: its settled amount);
`fixed` (not in the LP: its billed amount); `not_in_lp` (billed amount
of each left-out item, with its reason); `nonconvex_tier` (billed − LP for the item); `tier_allocation`
(windowed tiers: engine proportional − LP optimal); `ratchet_seed` (the history-seeded lower bound's unknown
share, reported as `None` amount + flag); `months_not_established` and `partial_months` (convention of
WP2.1b); `config_changed_since_solve` (rating an edited tariff on an old dispatch: the whole difference).
**`resolution` is a disclosed risk, not a computed cause:** the LP and the bill read the same dispatch at the
same resolution (P1 measures demand on settlement-interval means in both), so a finer real load cannot show as
a gap; when the LP step is coarser than an item's settlement the payload carries `resolution_risk` with the
preflight's warning. Spec §15's "billed ≥ LP − tol on convex tariffs" is replaced by this model: WP2.3 edits
the spec text of §15 (and §5.5's cause list) in the same commit.

`unattributed_pct > 5 %` (configurable) raises the `billing_gap_unexplained` warn gate.

- [ ] Red: on every **LP** fixture (the reconciliation cases) `unattributed_pct < 1e-6`; a monkeypatched adder
  mismatch ⇒ unattributed gap + warn gate; an edited tariff ⇒ cause `config_changed_since_solve`, no warn;
  windowed tiers ⇒ `tier_allocation` equals engine − LP.

## WP2.4b-i URDB importer

Files: `backend/services/library/urdb.py` (new), `test_urdb_import.py`.

`urdb_to_tariff(urdb_response, *, name, cyclic_year: bool = False) -> (Tariff, refusals)`; `cyclic_year`
(URDB has no such field) is set on the imported ratchet in range mode and disclosed in the Library item's meta.
Route: `POST /api/library/items/tariff/import_urdb` (body: an upload id, `name`, `cyclic_year`,
`accept_partial`), thin, calling the service. **Non-empty refusals ⇒ 422** listing them, unless
`accept_partial=true`; a partial import stores the refused field names in `Tariff.unsupported_fields` (new
field, default `[]`) and the engine then flags `tariff_incomplete` with `total = None` (ADR-0001). Mapped: `energyratestructure` +
`energyweekdayschedule` / `energyweekendschedule` (12×24, JSON arrays **or** JSON strings), tiers with `max`
(cumulative), `rate + adj` (REopt adds `adj`, `urdb.jl` L305/L413/L443); `demandratestructure` + demand
schedules (TOU demand, tiers); `flatdemandstructure` + `flatdemandmonths` (facility demand); fixed charges:
`fixedmonthlycharge` first, else `fixedchargefirstmeter` with `fixedchargeunits` `$/month`, `$/day` (per-day
item) or `$/year` (a monthly fixed item of 1/12, as REopt); `lookbackpercent` with `lookbackrange` (range mode)
or `lookbackmonths` (months mode), attached to the facility item; `lookbackpercent == 0` ⇒ no ratchet; "both
set" = `lookbackrange ≠ 0` **and** any `lookbackmonths` entry true (12 zeros are common and mean months mode is
off) ⇒ refused; `demandwindow` 15/30/60 ⇒ settlement, absent ⇒
`"15min"` with note `demandwindow_absent_assumed_15min` (REopt ignores the field); periods of one URDB period
become fragments with one **name** (WP2.1a-0); energy tiers whose `max` differ across periods ⇒ refused.
Refused with the field name (never dropped): `mincharge` / `minchargeunits` / `annualmincharge`,
`coincidentrate*`, `demandunits` other than kW, energy units other than kWh, `sell` tiers, `demandwindow`
values other than 15/30/60.

- [ ] Red: importer output equals the hand-translated JSON of R1, R2, R3′ (with `cyclic_year=True`) exactly;
  each refused field listed; 422 without `accept_partial`; a partial tariff bills with `tariff_incomplete` and
  `total = None`.

## WP2.4b-ii Series and meter-data import

Files: `services/library/series_io.py` (from WP2.4b-0), `routers/library.py` (upload endpoints, thin),
`test_series_io.py`.

- CSV (`timestamp,value`) and xlsx (first sheet) with the WP1.1b timestamp/zone rules.
- Meter data (15-min kW): monthly peaks `{"YYYY-MM": kW}` measured on a stated `settlement` (default
  `"15min"`, the finest URDB demand window; recorded in the item's meta) → `meter_history_peaks_kw`, and monthly
  energy → `meter_history_energy_kwh`.

- [ ] Red: round-trips; DST-day rows; a meter file at 5-min averaged to the stated settlement.

## WP2.4c Library chat tools

Files: `services/chat_tools.py`, `chat_tools_schema.py`, `route_inventory_phase0.txt` (regenerated), guard tests.

Chat tools call **router handlers** (the existing pattern, `chat_tools.py` L540/L1323), with the acting user
from the context var (L1799) for the Library ACL. Tools: `list_library_items(kind)`, `get_library_item(kind,
name, version?)`, `import_urdb_tariff(upload_id)` (an uploaded file id, not an LLM-emitted blob; tier: write),
`attach_tariff(name, version?)` (sets `import_tariff_ref` through the route). The endpoint-map test gets real
routes; ADR-0002 live-API probe recorded.

- [ ] Red: the four guard tests (schema match, arg shape, endpoint map, identity) include the tools.

## WP2.5 `compute_billing` / `compute_cfe_score` thin results

Files: `services/results/billing.py`, `services/results/cfe_score.py`, `routers/results.py` (`get_billing`,
`get_cfe_score` — added to `_HANDLER_PARAMS` and `_LIFTED`), `tests/test_results_seam.py`, `chat_tools.py`
(`_RESULTS_ENUM`, `_RESULTS_HANDLER_NAMES`: `billing`, `cfe_score`), `chat_tools_schema.py` (`RESULTS_ENUM`),
`route_inventory_phase0.txt`.

- `compute_billing(n, cfg, *, state)` → `{per_period: {lines, monthly, per_item, per_item_sampled, total |
  None, flags}, contracts: settlement lines, gap: WP2.3 payload, provenance}`; stores compact frames (WP2.1b);
  `None` → 204 before a solve. It is the caller of `physical_quantities` and `settle`.
- `compute_cfe_score(n, cfg)`: hourly 24/7 matching `Σ_h min(load_h, clean_h) / Σ_h load_h`; `load_h` = site
  loads (storage charging excluded); `clean_h` = on-site generation of clean carriers (`co2_emissions == 0`,
  overridable) **consumed on site** (minus the PoC export attributed to it, WP2.2a rule) + **off-site** PPA
  volume of clean assets (on-site PPA assets are already in on-site generation) + grid import ×
  `ic_grid_cfe_share` (absent ⇒ grid counted 0 **and** `grid_cfe_share_missing`); 15-min aggregated to hours by
  energy. "On-site" = behind the PoC (buses downstream of the import Links). Disclosed limitation:
  storage-shifted clean energy is not credited (charging counts as consumption of what charged it; discharge is
  not clean supply).

- [ ] Red: seam cases; facade test with the two new names (existing entries unchanged); CFE hand fixture (PV +
  load + export, one day) to 1e-9; `get_results(result_kind="billing")` returns the payload.

---

## Phase 2 e2e QA gate

- [ ] `backend/tests/qa_billing_contracts.py` (auto-discovered): (A) R1 imported through the URDB importer into
  the Library → attached by `import_tariff_ref` through the config route → a fixed-load dispatch → REopt
  parity to the cent for 2023 and 2024; (B) R2, R3, R3′, R4a, R4b likewise (records engine timing on a 15-min
  year, bound 10 s); (C) the P1 US site with a PPA pay-as-produced, a CfD and a DR contract → solve →
  `/results/billing` → settlement to the cent vs C1-style hand values, gap per item kind with
  `unattributed_pct` 0; (D) a `changes_dispatch` PPA reconciles before and after save → load and bundle
  export → import with Library pins for tariff and reference price.
- [ ] Full backend `not slow`, all QA drivers, vitest + `tsc` green; findings note
  `docs/superpowers/findings/<date>-ic-p2-billing-contracts.md`; assessor verdict recorded here.

---

## Scope boundaries (not P2)

- Investment tab, Tariff builder and Library UI — added to the **P3** outline in the P0–P1 plan (spec §12).
- Participants, allocation, conservation — P3 (P2 lines carry payer/payee).
- Tenor, escalation beyond the modelled year, tax — P4.
- Windowed dispatch with demand, tiers or fees — P6. DR on BESS / generators — P5.
- Group net-import variable for net energy items on multi-member groups — P3 (energy-hub template).
- `CommercialConfig.demand_items` stays refused (P1): no use case needs a selection; revisit only with one.
- Coincident demand, kVA demand, minimum charges, state/provincial riders — refused by the importer with the
  field name.

---

## Plan review

**Round 1 (FAIL, 22 findings) → v0.2.** R4b replaced by REopt's flat 15-min case (formula-derived) and R4a
pinned by formula (#1); R1 no longer pins per-day fixed, axis stated, string schedules (#2); ratchet modes
defined (range / cyclic / year-wide months), facility-only attachment, unknown months (#3); demand windows keyed
by name, new WP2.1a-0 (#4); wide float32 frames, flat keys, unpickler round trip (#5); reference series and
`grid_cfe_share` resolved at config time into `ic_*` frames (#6); seam interval frames, DSR commit, caller in
`services/results` (#7); contract model delta + one formula per row, BTM allocation, financial baseload,
indexation boundary with P4 (#8); `changes_dispatch` restricted to the buyer case with refusals (#9);
`resolution` a disclosed risk, `config_changed_since_solve` a cause, LP fixtures only (#10); WP2.0 rewritten
as merge + seam convention change + full verification (#11); `import_tariff_ref` + pins v2 + templates inline +
`/items/{kind}` routes (#12); LP coverage stated, convex demand/windowed tiers in WP2.1c, capacity precedence
(#13); item-level thresholds, 2-tier oracles only (#14); importer field list with `adj`, fixed precedence,
min charges, lookback conflict, `demandwindow` default (#15); WPs split and reordered, hand-translated JSON in
WP2.1a, `per_item_sampled` in WP2.1b, WP2.4a before retail, refactor first with wrapper, INFO items in WP2.0
(#16); chat tools via handlers, enums, inventory, ADR-0002, upload id (#17); `demand_items`, new DR check,
m−12, meter-data settlement (#18); CFE definitions (#19); §15 amendment in WP2.3 (#20); UI to P3 outline (#21);
timing in the QA driver, NOTICE and derived marks, `types.ts` (#22).

**Round 2 (PASS WITH CONDITIONS) → v0.3, all closed in text.**
- C1: R3′ authored in range mode; `urdb_to_tariff(cyclic_year=)` is disclosed in meta; `cyclic_year` refused in months mode; unmodelled lookback months read the same-rate-year history key.
- C2: `ic:`-prefixed columns, bus names starting `ic:` refused, load warning filtered, tests for bus remove/rename and axis change, guards extended to `buses_t`.
- C3: DSR commit only after a successful non-operational solve in `run_simulation`; cleared without DSR; hashed.
- C4: new contract fields Optional; untagged P0 payloads get their tag from a before-validator; same-name windows keyed by (month, name); P1 single-period tiers unchanged; versioned demand hash.
- C5: tariff capacity items in the LP via `add_fee_term`, reconciliation case `tariff_capacity`; falling demand tiers at the first tier.
- C6: 422 unless `accept_partial`; `Tariff.unsupported_fields` ⇒ `tariff_incomplete`, `total = None`; `$/year` fixed charge; the "both lookbacks set" rule; `lookbackpercent == 0`.
- C7: `settlement_only` cause; unweighted per-period basis.
- LOW:
  - the contracts config field moved to WP2.2-0;
  - a URDB import route;
  - the windowed-tier LP formulation spelled out;
  - `premium_eur_per_mwh` added, CfD indexation, allowed pricing combinations, Generators only;
  - the PPA/export double-count check restricted to the seller case;
  - the represented-hours wording;
  - m−12 uses the marginal rate;
  - CFE: "on-site" defined, storage limitation disclosed;
  - the §15 amendment goes into the spec text;
  - `RetailContract.tariff_id` compares against `Tariff.id`.

**Round 3 (PASS).** Residues closed in text: capacity items pro-rated by represented hours / 8760 like the LP
fee (no leap-year or representative-week gap); bus create/rename and netCDF upload paths in WP2.2-0's files;
per-class default for the contract `type`; the same-rate-year history consequence stated.
