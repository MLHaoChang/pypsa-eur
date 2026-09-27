# Edge Investment Case — Implementation Plan, Phase 2 (billing pass & contracts)

> **For agentic workers:** same protocol as the P0–P1 plan (`2026-09-26-edge-investment-case-p0-p1.md`,
> § Phase QA gate + TDD protocol): per work package red → green → verify, an independent implementation
> review until `PASS`, then the phase e2e QA gate with an independent assessor before P3. House rules:
> `.cursor/skills/gui-backend-change/SKILL.md`, `.cursor/rules/pypsa-gui-backend.mdc`, ADR-0001
> (unresolvable → `None` + flag, never 0), thin routers, `services/{commercial,finance,library}` import
> neither routers nor `solver_service`, existing `_HANDLER_PARAMS` entries unchanged (new handlers add
> entries).
>
> **Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` (§4.1 contracts, §5.3, §5.5
> billing pass, §11 Library, §12 API, §13 P2 row, §15 oracles). **P1 findings:**
> `docs/superpowers/findings/2026-09-27-ic-p1-commercial-layer.md`.
>
> **Plan status:** v0.1 draft — plan review loop pending.

**Goal of P2.** Every tariff item a US or EU site is billed is rated **exactly** on the solved dispatch (the
billing pass), including the URDB constructs P1 left out; every contract is settled into dated, attributable
lines; the gap between the LP's dispatch-grade cost and the exact bill is reported **per item kind with a
cause**, and an unexplained gap raises a gate; tariffs, contracts and connection agreements live in the
Library (versioned, org-scoped, importable from URDB JSON) and are pinned by the project; `/results/billing`
and `/results/cfe_score` serve the numbers through thin handlers. No finance arithmetic (P4), no
participants (P3), no Investment tab UI (P3).

**Entry state (from P1).** `tariff_engine.rate` bills energy/TOU, fixed per-month, demand (per window,
settlement-interval means, ratchets on actual prior peaks with meter-history seed, partial months in full)
and convex/non-convex tiers on a single catch-all period. The LP carries energy adders, a connection fee,
demand/ratchet/tier variables and a group cap, with reload-safe rows and drift hashes. Open P1 binding
conditions that land here: **(4)** move `routers/simulation._bind_commercial` and
`routers/library._series_from` logic into services — by WP2.4; **(5)** merge or resolve the FOM branch
(`origin/claude/fix-fom-reconciliation`, 68c6661) — WP2.0, before WP2.3.

**Environment.** As P1: venv `/home/user/.venv-gui312`; backend `python -m pytest -m "not slow"`;
`python tests/run_qa_drivers.py`; frontend `npx vitest run`, `npx tsc --noEmit -p tsconfig.json`.

**Dependency order.**

```
P2 billing pass & contracts
 ├─ WP2.0 FOM reconciliation merged (plain-solve gap 0 with fom_cost > 0)          ── before WP2.3
 ├─ WP2.1a engine: URDB constructs (per-day fixed, demand tiers, tiers inside TOU windows,
 │         designated-month and cyclic-year ratchets, capacity items on contracted capacity)
 ├─ WP2.1b site billing adapter: solved network → per-period dispatch → rate; compact billing frames
 ├─ WP2.1c LP: designated-month / cyclic ratchets; non-convex tiers at the history-predicted tier
 ├─ WP2.2a contracts settlement engine (PPA ×4, CfD, DR, lease, EaaS, retail)
 ├─ WP2.2b contracts on CommercialConfig + preflight double-count checks (P1 WP1.8 deviation)
 ├─ WP2.2c `changes_dispatch` PPA in the LP (+ rows, gap 0)
 ├─ WP2.3 billing_vs_lp_gap per item kind, cause attribution, warn gate; one not-established convention
 ├─ WP2.4a Library items: tariffs / contracts / connection agreements (store, routes, pins, import_tariff_id)
 ├─ WP2.4b import schemas: URDB JSON → Tariff; CSV/xlsx series and meter data; condition 4 refactor
 ├─ WP2.4c Library chat tools
 └─ WP2.5 compute_billing / compute_cfe_score thin results + handlers + seam cases
```

**Per-WP invariants (all of P2).** The P1 reconciliation gate (`test_commercial_objective_reconciliation.py`,
nine cases) stays green after every WP; an LP change (WP2.1c, WP2.2c) adds its own case. The engine never
returns 0 for an item it cannot rate (ADR-0001). Every new persisted record carries a content hash so a
config edit without a re-solve is `config_changed_since_solve` (P1 gate condition 2 pattern).

---

## Oracles (bind the gate)

Committed under `backend/tests/fixtures/investment_case/oracles/` with a `PROVENANCE.md` naming the
source repository, commit and licence.

| Id | Source | What it pins | Expected value source |
|---|---|---|---|
| R1 | REopt.jl `test/scenarios/leap_year.json` (Apache-2.0, commit `6d43289`) — full `urdb_response`: weekday/weekend TOU energy, flat (facility) + TOU demand, fixed `$/day` | TOU energy on the right weekday in 2023 vs leap 2024; facility + TOU demand; Feb-29 handling; per-day fixed | REopt `runtests.jl` "leap year" testset formulas (energy = rate × 10 kWh; demand = (flat + TOU) × 10 kW; the two-peak facility case) |
| R2 | REopt.jl `tiered_tou_demand.json` — demand **tiers** | tiered demand on a flat load | `12 × (tier1_max × r1 + (peak − tier1_max) × r2)` |
| R3 | REopt.jl "Lookback Demand Charges" cases 2 and 3 (custom rates in the test body, inputs copied into the fixture) | designated lookback months `[1,4,12]` at 75 %; `lookback_range = 6` at 75 %; REopt treats the rate year as **cyclic** | the testset's `monthly_peaks` vectors |
| R3′ | REopt.jl lookback case 1 (URDB label `539f6a23…`, not in the repo): a URDB-shaped response reconstructed from the testset comment (35 % lookback, 5 cold months at 10.5 $/kW, 6 warm at 11.5 $/kW) | URDB lookback fields through the importer | `100 × (10.5 + 0.35·10.5·5 + 0.35·11.5·6)`; marked *reconstructed* in PROVENANCE |
| R4 | REopt.jl "Blended tariff" (`no_techs.json`: flat 10,000 kWh/yr, 0.10 $/kWh, 10 $/kW-month) and the 15-min blended-rate case (sub-hourly load, 0.12 $/kWh, 20 $/kW-month) | blended rates, 15-min demand | 1,000.00 and 136.99; monthly energy × rate, monthly 15-min peak × rate |
| H1–H3 | Hand-rated bills (committed as fixtures with the working): DE (Netzentgelt with Leistungspreis/Arbeitspreis, §19 StromNEV atypical-use discount slot as a documented non-support), NL (energy tax bands as convex/non-convex tiers, fixed per day), US C&I (TOU + windowed demand + ratchet + fixed) | every item kind at 15-min | the fixture's hand arithmetic, to the cent |
| C1 | Hand-settled contract fixture: PV + BESS site, PPA pay-as-produced, baseload, as-consumed BTM, sleeved; CfD two-sided; DR availability + activation; lease; EaaS | settlement lines | hand arithmetic, to the cent |

"To the cent" = `abs(actual − expected) < 0.005` in the tariff currency per bill line and in total.
URDB rates are USD; the engine is currency-agnostic (spec decision 13).

---

## WP2.0 FOM reconciliation (P1 binding condition 5)

Files: merge `origin/claude/fix-fom-reconciliation` (68c6661) into this branch; `backend/tests/test_commercial_objective_reconciliation.py`.

- [ ] Red: a tenth reconciliation case `fom`: the edge fixture with `fom_cost > 0` on the BESS and PV and
  no commercial config — `gap_pct` within 1e-6 before and after save → load (fails on the current branch).
- [ ] Green: merge the branch (merge commit; resolve conflicts in `cost_breakdown.py`,
  `objective_decomposition.py`, `asset_economics.py`, `physical_quantities.py` by keeping both behaviours);
  re-run the branch's own tests and the P1 reconciliation gate. If the branch does not reconcile the
  commercial rows' `Commercial` component, fix here.
- Acceptance: ten cases green; P0 WP0.3 seam test's FOM TODO removed (the seam compares `fixed_cost`
  directly); findings note carry item closed.

## WP2.1a Billing engine: the URDB constructs

Files: `backend/models/commercial.py`, `backend/services/commercial/tariff_engine.py`,
`backend/tests/test_tariff_engine_urdb.py`.

Model changes (backward compatible; old JSON validates unchanged):
- `TariffItem.unit` gains `per_day` (fixed charges pro-rated by covered days on the local clock).
- `Tier` on a **demand** item (tiered demand on the billed kW of the (month, window)).
- Tiers **inside TOU windows** (URDB `energyratestructure[period][tier]`): each period may carry its own
  tier list; tier position is the month's **total** energy across periods (URDB semantics); each period's
  energy is split into tiers **in proportion** to the month's total (the standard bill practice).
  Documented deviation from REopt's LP, which allocates tiers to periods optimally.
- `Ratchet` gains `months: list[int] | None` (designated lookback months, URDB `lookbackMonths`) and
  `cyclic_year: bool = False` (REopt's steady-state year: January's lookback reads December of the same
  rate year). `lookback_months` keeps its meaning (URDB `lookbackRange`).
- Capacity items (`per_kw_year`, `per_kva_year`) rate on the **contracted** capacity passed by the caller
  (`capacity_kw`, and `power_factor` for kVA — absent ⇒ `not_established`), pro-rated by covered time.

- [ ] Red (unit, engine only): R1 (both years, both sub-cases), R2, R3 (cases 2 and 3), R3′, R4 energy and
  demand parity to the cent; per-day fixed over a leap February; tiers inside windows split proportionally
  (hand fixture); cyclic ratchet with no meter history is complete (no `ratchet_seed_missing`), non-cyclic
  keeps P1 behaviour; a kVA item without power factor is `not_established`; every P1 engine test unchanged.
- [ ] Green: implementation; `_unsupported_reason` shrinks to what stays unsupported (listed in the module
  docstring with the reason).
- Performance (spec §16 risk 1, measured here): rating a 15-min year (35,040 rows) with 8 items < 2 s on the
  CI runner; recorded in the findings note, asserted with a generous bound (10 s) in a `slow`-marked test.
- Acceptance: all oracles above green in the engine; no P1 test changed.

## WP2.1b Site billing adapter and compact billing frames

Files: `backend/services/commercial/billing.py` (new), `backend/services/finance/report.py`
(`store_billing_frames` shape), `backend/tests/test_site_billing.py`.

`bill_site(n, commercial, *, meter_history=None) -> SiteBill` builds, from the solved network and the
committed records, the dispatch the engine rates: import = Σ `import_links` `p0` (the group meter), export
= export Link `p0`; UTC index when `timezone` is set; `step_hours` from the axis; per investment period (one
`RatingResult` per period, keyed by period); representative weeks through `represents_hours` from the
objective weights and a `billing_period` of the represented calendar year (P1's represented-year rule);
capacity items on the PoC `p_nom_opt` (kW). Returns per-period results, flags, and the provenance hashes
(tariff hash, energy/demand hashes of the solve it rates).

`billing_frames` (P0 carry): persist only `interval, tariff_item, quantity_kwh, amount` as float32, per
period, and the `monthly`/`demand_lines` frames; a size test asserts < 3 MB for a 15-min year with 8 items.

- [ ] Red: the adapter's bill on the P1 QA networks equals `rate()` on the same frames (identity); a group
  site is billed on the summed members; a two-period network yields two results with the period's years in
  provenance; representative weeks bill 12 months with `represents_hours` and flag the unsampled months the
  same way the LP rows do (see WP2.3 convention); an unsolved network → `None` + `not_solved`; frames
  round-trip through `results_state.pkl` under the size bound.
- Acceptance: tests green; `tests/qa_save_load_roundtrip.py` unchanged.

## WP2.1c LP: new ratchet semantics and history-predicted non-convex tiers

Files: `backend/services/commercial/lp_bindings.py`, `backend/tests/test_ratchet.py`, `test_tiers.py`,
reconciliation gate (new cases).

- [ ] Red: designated-month and cyclic ratchets in the LP equal the engine's billed demand per (month,
  window) and gap 0 (R3 cases as LP fixtures on a flat 1-year hourly load with a fixed dispatch: an LP with
  no flexible asset so the dispatch is the load); a non-convex tiered item with
  `CommercialConfig.meter_history_energy_kwh` ({"YYYY-MM": kWh}) is priced at the tier the history
  predicts (spec §5.3), flagged `nonconvex_tier` with the predicted tier, and without history at the first
  tier as in P1; drift hash covers the new fields.
- [ ] Green: ratchet constraints reuse `ic_billed_demand`; cyclic wrap within the rate year of each
  investment period; tier prediction is a pure function shared with preflight.
- Acceptance: two new reconciliation cases (`ratchet_designated`, `ratchet_cyclic`) green before and after
  save → load.

## WP2.2a Contracts settlement engine

Files: `backend/models/commercial.py` (fields below), `backend/services/commercial/contracts.py` (new),
`backend/tests/test_contracts_settlement.py`.

`settle(contract, quantities, prices, *, timezone, years) -> SettlementResult` is pure: `quantities` are
per-asset / per-load interval series (MW) from the physical-quantity seam (`physical_quantities`, P0 WP0.3)
plus the PoC flows; `prices` are resolved Library series (reference prices) aligned with
`lp_bindings.align_to_snapshots`. Output lines: `(period, contract_id, payer, payee, value_stream,
quantity_mwh, amount, flags)` with `value_stream` from spec §4.1 (`ppa_settlement`, `cfd_settlement`,
`dr_availability`, `dr_activation`, `lease`, `eaas_fee`, `energy_import` for retail).

| Contract | Rule (per interval unless stated) |
|---|---|
| PPA `pay_as_produced` | buyer pays seller `price_t × gen_t` (price indexed by `indexation_pct_per_year` from the contract's base year), volume capped by `volume_cap_mwh_per_year` (excess settles at the reference price, flagged); floor/cap clamp a market-indexed price when `reference_price` is set |
| PPA `baseload` | contracted `baseload_mw` (new field) every interval: buyer pays `price × baseload`; the seller's shortfall `max(0, baseload − gen)` is bought at the reference price by the seller, surplus sold at it (financial firming) — reference price required, else `not_established` |
| PPA `as_consumed_btm` | `min(gen_t, site_load_t)` at `price`; surplus not covered (export settles through the tariff) |
| PPA `sleeved` | pay-as-produced plus `sleeving_fee_eur_per_mwh` (new field) paid by the buyer to the retailer (`sleeving_party`, new field) |
| CfD | two-sided: generator receives `(strike − ref_t) × gen_t` (negative when ref > strike); ref required |
| DR | availability `availability_eur_per_mw_year × contracted MW × represented-year fraction`; activation `activation_eur_per_mwh × activated MWh` where activation = the DSR/curtailable dispatch of the named assets/loads; `max_events` / `max_duration_h` checked on the dispatch and violations flagged (not enforced — enforcement is an LP matter for P5/P6) |
| Lease | `annual_payment × represented-year fraction` lessee → lessor |
| EaaS | `fee_eur_per_mwh × delivered MWh` and/or `fee_eur_per_year` customer → provider |
| Retail | the referenced tariff rated by the engine on the PoC flows (the retail bill *is* the tariff bill; it attributes payer/payee) |

Tenor and escalation beyond the modelled year are **finance** (P4); P2 settles the modelled period(s) and
records the contract's base year and indexation for P4.

- [ ] Red: C1 to the cent; missing reference price → `None` + `reference_price_missing`; an asset id that is
  not in the network → refusal; a PPA and a CfD on the same asset settle independently (double count is a
  preflight warning, WP2.2b); multi-period networks settle per period.
- Acceptance: tests green; pure module (no router / `solver_service` imports; tripwire covers it).

## WP2.2b Contracts on the config + double-count preflight

Files: `backend/models/commercial.py` (`CommercialConfig.contracts: list[Contract]`, discriminated by a
`type` field; `frontend/src/api/types.ts` mirror), `backend/services/commercial/preflight.py`,
`backend/tests/test_validation_commercial.py`, `backend/tests/test_contracts_config.py`.

- [ ] Red: contracts round-trip through `PUT /api/simulation/solver_config`, project save → load and bundle
  export → import (reference-price Library pins included); preflight warns
  `commercial.ppa_export_double_count` (PPA without `changes_dispatch` on an asset whose output also earns
  the export price), `commercial.dr_double_count` (DR contract on a load in `dsr_buses` —
  `services/adequacy/archetypes.dsr_double_count_warnings` reused), `commercial.contract_asset_missing`
  (error); drift: a changed contract after the settlement flags `config_changed_since_solve`.
- Acceptance: P1 WP1.8's recorded deviation closed.

## WP2.2c `changes_dispatch` PPA in the LP

Files: `backend/services/commercial/lp_bindings.py`, `cost_rows.py`, reconciliation gate.

A PPA with `changes_dispatch=True` on a generator replaces, for that generator's output, the export price it
would otherwise see: the LP adds `(−price) × p_gen` for the contracted volume as a transient
`marginal_cost` adder on the generator (committed as `generators_t["ic_ppa_price"]` + a meta record with
the contract hash), and `cost_rows` adds the row `ppa_settlement` so the gap stays 0.

- [ ] Red: dispatch changes as expected on a fixture (a PV behind the PoC curtails less under a PPA price
  above the export price); rows reconcile; the settlement engine's pay-as-produced line equals the LP row on
  the same dispatch; undo restores the user's `marginal_cost`; drift on a price change.
- Acceptance: reconciliation case `ppa_changes_dispatch` green before and after save → load.

## WP2.3 Billing vs LP gap per item kind, with causes

Files: `backend/services/commercial/gap.py` (new), `backend/tests/test_billing_gap.py`.

`billing_vs_lp_gap(n, commercial, site_bill) -> {item_kind: {lp, billed, gap_pct, causes: [...],
unattributed_pct}}` for `energy`, `demand`, `tiers`, `capacity`, `fixed`, `contracts`. LP cost per item kind
is recomputed from the committed records and the same dispatch (energy per item from the item's rates — the
LP applied exactly those — not from the summed adder). Causes (spec §5.5 + P1 learnings):
`resolution` (LP step coarser than settlement), `nonconvex_tier`, `fixed` (not in the LP), `ratchet_seed`
(history-seeded lower bound), `not_in_lp` (an item the LP left out, with its reason),
`months_not_established` (see convention), `partial_months`. Each cause carries the amount it explains,
computed (not asserted): e.g. `resolution` = bill on the LP-resolution dispatch minus bill at settlement
resolution. `unattributed_pct` above 5 % (configurable) raises the `billing_gap_unexplained` warn gate.

**One not-established convention (P1 carry).** Both the LP rows and the engine report, for months without
snapshots, the sampled sum **and** `months_not_established`, and neither claims a year total: the engine's
`per_item` stays `None` for such items and exposes `per_item_sampled`; the LP rows keep the sampled sum with
the flag. The gap compares sampled to sampled.

- [ ] Red: on every P1 reconciliation case and every oracle, `unattributed_pct < 1e-6`; a hand-built case
  with an LP resolution of 1 h and 15-min settlement attributes the whole gap to `resolution`; a fixed item
  is attributed `fixed`; a deliberately mismatched rate (monkeypatched adder) produces an unattributed gap
  and the warn gate.
- Acceptance: tests green; requires WP2.0 (a plain-solve gap of 0).

## WP2.4a Library items: tariffs, contracts, connection agreements

Files: `backend/services/library/items.py` (new; JSON payloads on `LibraryItem.kind in {"tariff","contract",
"connection_agreement"}`, versioned and content-hashed like series), `backend/routers/library.py`
(`GET/PUT /api/library/{kind}` and `/{kind}/{name}` — thin), `services/library/bundle_pins.py` (pins for
the new kinds), `backend/services/commercial/lp_bindings.py` (`import_tariff_id` resolution),
`backend/tests/test_library_items.py`. No migration (the `kind` column is a plain string; the unique key
already includes it).

- [ ] Red: CRUD with org ACL identical to series (reuse the WP1.1b rules and tests' shape); a PUT of identical
  content returns the existing version; `import_tariff_id` names a Library tariff (`{"id": name, "version":
  n}`), resolved by the config route into the typed config with its hash pinned in the bundle; a missing or
  stale id is `library_ref_stale` (409) at the route and `binding_invalid` in preflight; a bundle imported
  into another org reports `library_issues` for tariff pins exactly as for series.
- Acceptance: the P1 refusal "import_tariff_id … arrives in P2" is replaced by resolution; tests green.

## WP2.4b Import schemas + P1 binding condition 4

Files: `backend/services/library/urdb.py` (new), `backend/services/library/series_io.py` (new; CSV/xlsx
series and meter data), `backend/services/commercial/binding.py` (new; the logic now in
`routers/simulation._bind_commercial`), `routers/simulation.py`, `routers/library.py` (handlers call the
services), `backend/tests/test_urdb_import.py`, `test_series_io.py`, `test_commercial_binding_service.py`.

- [ ] Red: `urdb_to_tariff(urdb_response)` maps `energyratestructure` / weekday / weekend schedules (12×24),
  `demandratestructure` (+ tiers), `flatdemandstructure` / `flatdemandmonths`, `fixedchargefirstmeter` with
  `fixedchargeunits` (`$/day`, `$/month`), `lookbackpercent` / `lookbackrange` / `lookbackmonths`, `demandunits`
  (kW only; kVA → refusal); unsupported fields (`coincidentrate*`, `demandwindow` ≠ 15/30/60, `energyratestructure`
  units other than kWh, sell rates with `sell` tiers) are listed in a refusal with the field names, never
  dropped silently; R1, R2 and R3′ imported through the importer rate to the oracle values. Series import:
  CSV (`timestamp,value`) and xlsx (first sheet) with the WP1.1b timestamp/zone rules (moved from
  `_series_from` into `series_io`); meter data: 15-min interval kW → monthly peaks `{"YYYY-MM": kW}` and
  monthly energy for `meter_history_*`. Condition 4: `_bind_commercial` moves to
  `services/commercial/binding.py` with an injected org resolver; the router keeps HTTP mapping only;
  every existing test of the route passes unchanged.
- Acceptance: tests green; `routers/simulation.py` and `routers/library.py` shrink (line counts recorded).

## WP2.4c Library chat tools

Files: `backend/services/chat_tools*.py` (per the existing tool registry), guard tests.

- [ ] Red: `list_library_items(kind)`, `get_library_item(kind, name, version?)`, `import_urdb_tariff(json)`
  (confirmation tier: write), `attach_tariff(name, version?)` (sets `import_tariff_id` through the same
  service as the route); the four chat guard tests (schema match, arg shape, endpoint map, identity) include
  them.
- Acceptance: guard tests green; no new route logic in chat (tools call services).

## WP2.5 `compute_billing` / `compute_cfe_score` thin results

Files: `backend/services/results/billing.py`, `backend/services/results/cfe_score.py`, `routers/results.py`
(`get_billing`, `get_cfe_score` — added to `_HANDLER_PARAMS` and `_LIFTED`), `tests/test_results_seam.py`
cases, `chat_tools` `get_results` mapping.

- `compute_billing(n, cfg, *, state)` → `{per_period: {bill lines, monthly, per_item, total | None, flags},
  contracts: settlement lines, gap: WP2.3 payload, provenance}`; persists compact frames (WP2.1b) in
  `billing_frames`; `None` → 204 before a solve.
- `compute_cfe_score(n, cfg)` → hourly 24/7 CFE matching: `Σ_h min(load_h, clean_supply_h) / Σ_h load_h` where
  clean supply = on-site generation of carriers marked clean (`co2_emissions == 0` on the carrier, overridable
  list) + PPA-contracted clean volume + grid import × grid CFE share (a Library series `grid_cfe_share`; absent
  ⇒ grid counted 0 **and** `grid_cfe_share_missing` flag). Hourly aggregation from 15-min by energy.
- [ ] Red: seam cases (module defines the function; handler has only the lookup / gate / 204 mapping);
  facade test updated with the two new names (existing entries unchanged); a hand fixture for CFE (PV + load,
  one day) to the 1e-9; chat `get_results(kind="billing")` returns the payload.
- Acceptance: tests green; results facade and seam tripwires green.

---

## Phase 2 e2e QA gate

- [ ] `backend/tests/qa_billing_contracts.py` (auto-discovered): (A) import R1 through the URDB importer
  into the Library → attach by `import_tariff_id` through the config route → rate a fixed load dispatch →
  REopt parity to the cent for 2023 and 2024; (B) R2, R3, R3′, R4 likewise; (C) the P1 US site with a PPA
  pay-as-produced, a CfD and a DR contract → solve → `/results/billing` → settlement lines to the cent vs the
  hand fixture, gap per item kind with `unattributed_pct` 0; (D) a `changes_dispatch` PPA reconciles (gap 0)
  before and after save → load and bundle export → import with Library pins for tariff and reference price.
- [ ] Full backend `not slow` suite, all QA drivers, frontend vitest + `tsc` green; findings note
  `docs/superpowers/findings/<date>-ic-p2-billing-contracts.md`; assessor verdict recorded here.

---

## Scope boundaries (explicitly not P2)

- Investment tab / Tariff builder / Library UI — P3 (spec §12 UI); P2 ships API + chat only.
- Participants, allocation, conservation — P3 (P2 lines already carry payer/payee for it).
- Tenor, escalation over years, tax — P4.
- Windowed dispatch with demand, tiers or fees (running-peak carry) — P6.
- Group net-import variable for net energy items on multi-member groups — P3 (energy hub template).
- Coincident demand (URDB `coincidentrate*`), kVA demand, state/provincial riders — refused with a reason.
