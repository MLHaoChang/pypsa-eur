# Edge Investment Case: Phase 3 end-to-end QA (participants and value flows)

**Plan:** `docs/superpowers/plans/2026-09-29-edge-investment-case-p3.md`. **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md`, §7 and §15.
**Branch:** `claude/energy-tool-features-research-fdixs0`. **Head at the gate:** the commit carrying this note.

## What Phase 3 delivers

| WP | Deliverable | Review rounds → final verdict |
|---|---|---|
| 3.0 | Value-flow config (participants, externals, assignments, allocation key), its route with If-Match, hashing, FE types | conditions → **PASS** |
| 3.1 | The ledger: sources with legs, lines, coverage, the four conservation checks, reconciliation to `cost_breakdown` through the bridge terms | conditions → conditions → **PASS** (round 3) |
| 3.2 | Templates (`single_owner`, `btm_ppa`, `landlord_tenant`, `dso_developer`, `energy_hub`), drafts with open money fields, edited / stale / outdated status | FAIL → **PASS WITH CONDITIONS**, closed (binding #10 fixed and tested; no reviewer round 3) |
| 3.3a | Energy-hub allocation: metered energy per member, peak items, the four keys, floor source month | conditions → FAIL → **PASS** (round 3) |
| 3.3b | Group net-import LP variable (P2 carry-in): a net cost energy item priced on net import | conditions → **PASS** |
| 3.4 | `GET /api/results/value_flows` (the 31st result kind); chat `get_results` detail / paging and `define_participants`; five error kinds | conditions → FAIL → **PASS** (round 3); **ADR-0002 live probe owed** |
| 3.5 | Frontend foundation: API clients, the Investment tab shell, commercial writes | conditions → conditions → **closed** |
| 3.6 | Participants designer, the per-participant table, the Sankey | FAIL → **PASS** |
| 3.7a | Library browser (items, versions, pins, URDB import with refusals, series and meter uploads, attach) | FAIL → FAIL → **PASS** (round 3) |
| 3.7b | Tariff builder and the bill preview route (`POST /api/results/billing/preview`) | conditions → FAIL → **PASS** (round 3) |
| 3.7c | Contracts and connection-agreement editors | conditions → conditions → **PASS** (round 3) |

Each round's findings, and what was done about them, are in the plan under the WP.

## Gate evidence (2026-09-29)

| Check | Result |
|---|---|
| `tests/qa_value_flows.py` | **235/235** (33 s) after the assessor's hardening (222/222 at bfb5bef). See "The driver" below. |
| All QA drivers (`tests/run_qa_drivers.py`) | **25/25 passed** (includes `qa_value_flows`, `qa_billing_contracts`, `qa_commercial_lp`) |
| Full backend suite (`-m "not slow"`, Python 3.12 venv), first run at bfb5bef (edits landed mid-run) | 7,887 passed, 31 skipped, **2 failed**, both triaged: `test_hourly_assumption_audit` (the energy-hub adequacy sites from the master merge e364837, unlisted — gate condition 1, listed with reasons) and `test_openpyxl_parses_uploads_with_defusedxml_in_this_environment` (the local venv lacked the pinned `defusedxml==0.7.1` — an environment gap, installed; no code change). |
| Full backend suite at e27fe5d / 024d21b (after the round-1 fixes), run in 14 chunks (the container restarts and background tasks are time-limited) | 7,891 passed, 31 skipped, 0 failed (11 deselected `slow`) |
| **Full backend suite at e731683 — the final backend code** (after the round 2–5 fixes; later commits are docs only), 14 chunks | **7,904 passed, 31 skipped, 0 failed** (11 deselected `slow`) |
| **All QA drivers at the final code** | **25/25 passed** (`qa_value_flows` 235/235, `qa_billing_contracts` 49/49, `qa_commercial_lp` 30/30) |
| Frontend `vitest run` at 024d21b (no frontend change since) | **248 files, 2,754 tests passed** |
| Frontend `tsc --noEmit` | clean |

### The driver

Every template case runs through the routes: POST template → drafts priced and confirmed through the
solver-config route → PUT value flows (If-Match) → solve → GET `/api/results/value_flows`. Each case
asserts status ok, `conservation_ok` True, all four checks in every period, the route's reconciliation
difference under a cent, and an independent reconciliation the driver rebuilds from
`/results/cost_breakdown`, the LP rows, `/results/billing` and the network's export revenue — equal to both
the ledger side and the route's bridge figure. That bridge is independent of the ledger's own
reconciliation in how each party is classed (participant or external), in the `cost_breakdown` total and
in the export revenue (computed from the network's arrays); it SHARES the bill, the settlement and
`commercial_cost_terms` with the product, so an error there would pass both sides alike (P2's gate owns
those). Every case also asserts the ledger has periods with lines (the conservation check is ok over no
periods; assessor note).

- **A. V1 `single_owner`:** the grid-supply commodity line = Σ w·p·mc (114,451.41); the fee line = the
  `network_capacity` row (30,636.99).
- **B. V2 `btm_ppa`** (an `as_consumed_btm` PPA and an EaaS; a second site PV so the export split varies):
  the developer's export-price share (20,577.16) and feed-in share (6,553.32), the PPA (29,639.85) and the
  EaaS (7,009.49) equal their hand formulas; the developer pays the PV / BESS cost lines, the site pays
  the bill, the PPA and the EaaS. Also the other two `btm_ppa` cases of the templates test table.
- **C. V3 `landlord_tenant`** with and without a saved lease: lease = payment × hours / 8760 to the cent;
  the landlord pays the leased assets, the tenant the bill.
- **D. V4 `dso_developer`:** DSR dispatches 437.5 MWh; DSO → developer availability (38.36) and activation
  (43,750.00) equal the hand formula.
- **E. `energy_hub`** under each of the four keys (switched through the route, no re-solve): the checks
  and the rebuilt reconciliation hold, every shared item is split in full, each line's method is right
  (metered, peak or keyed). Hand shares (amount × key / Σ key, to the cent) are asserted through the
  routes for `contracted_capacity` and `fixed_shares` only; the `energy` and `peak_contribution` hand
  shares are F's, on the V5 fixture's `LedgerInputs` (not through the routes).
- **F. V5 fixture:** the group bill and per-member metered energy match the worked arithmetic; all 21
  hand shares to the cent under each key; the ledger passes every check.
- **G. V6 group net import:** the `energy_net_group` row = the billed `net_energy` (1,240,541.71); the
  objective gap ~2e-10 %; the billing gap has no gate and nothing unattributed.
- **H. Corruptions on the real V1 ledger:** a swapped payer / payee on the import-energy line and a
  dropped BESS cost source each turn coverage, reconciliation and `ok` False.
- **I. Bundle round trip** of the V2 project: after save → load and after bundle export → import,
  `value_flows`, the ledger (18 lines) and the flags are identical.
- **V1b** (every costed asset in the ledger — a costed Line, a Transformer, an island bus) is covered by the
  suite, not the driver: `test_value_flow_reconciliation.py::test_v1b_*`, flat and multi-period. Its "bus
  without a price column" leg does not apply to the ledger, which lists assets from `n.statistics`, not
  `asset_economics`.

## Findings and carried items

- **ADR-0002 live-API probe: NOT RUN (owed, with P2's).** This environment has no provider credentials
  (no Anthropic key, no local Ollama). P3's chat changes (`get_results` with `result_kind="value_flows"`,
  `detail` / `offset` / `limit`; `define_participants`) are exercised in process through `DISPATCHERS` and
  the guard suites, not against a live API. **The chat surface is not done until this is run.** Someone
  with credentials runs, at a head that includes P3, and records (probe name, date, model, the expected
  and actual calls, the outcome and the transcript) here and in the plan:
  1. **The manifest probe** (P2 step 1, findings `2026-09-29-ic-p2-billing-contracts.md`) — it now carries
     `define_participants` and `get_results`' `detail` / `offset` / `limit`; a vendor rejecting that schema
     breaks every `get_results` call. Then P2's steps 2 (Library tools) and 3 (the rename turns).
  2. **Template turn:** on a solved project with a commercial config (`poc_link` set), ask the assistant
     to set up participants from the `single_owner` template and show the value flows. Expected:
     `define_participants(template="single_owner")` → saved; `get_results("value_flows")` → status ok,
     a summary under 4,000 characters.
  3. **Drafts turn:** on a project with no PPA, ask for the `btm_ppa` template. Expected:
     `{saved: false, status: "drafts_need_pricing"}`, nothing stored (the value-flows route unchanged),
     and the assistant asking for the missing prices rather than inventing them.
  4. **Replace-guard turn:** with `single_owner` stored, ask to switch to `landlord_tenant`. Expected:
     `value_flows_would_be_replaced`, the assistant asking the user; on "yes", the call again with
     `replace=true`.
  5. **Lines turn:** ask for the value-flow lines. Expected: `get_results("value_flows", detail="lines")`,
     then a second page with the returned `next_offset`.
- **The export split's generation rule (gate condition 2, fixed over five assessor rounds).** Under
  `export_revenue_to="asset_owner"` the export revenue is split per interval pro rata to the ELECTRIC
  generation behind the meter, one definition with the `as_consumed_btm` PPA's share (P2):
  - **Generators on an electric bus** (`lp_bindings.site_generators`): a carrier in `_ELECTRIC` or the
    PoC site bus's carrier. A Generator on a gas, heat or other bus — a fuel supply (with or without a
    gas load beside it), a solar-thermal collector, a heat dump — is not electric generation.
  - **Converting Links** (`_converting_links` → `site_generating_ports` / `site_link_generation`): a
    site-side Link whose bus0 is not electric counts what it delivers to site-side electric buses from ANY
    port, NET (Σ −p_k over its electric site-side ports — port 0 too when it is the electric side of a
    reversible Link; an auxiliary draw on a port with a negative efficiency is subtracted), keyed by
    the Link — a CHP's power on bus2 counts, its heat on bus1 never. An electric-input Link (a feeder,
    the PoC, a heat pump, a charger, an electrolyser) is not generation.
  - **Where the input's energy comes from decides** (`_converting_links`), followed upstream through
    non-electric Links, never what merely sits on the converter's bus0: a **primary** origin is a
    Generator that can produce (gas → CHP; gas → reformer → H2 → fuel cell; gas boiler → heat Store →
    ORC); a **charged** origin is site electricity entering the non-electric side (a charger, an
    electrolyser — however many hops: charger → Store → BMS → inverter; electrolyser → H2 Store → pipe →
    fuel cell). Stores and StorageUnits only buffer and are never an origin. Primary only → generation;
    charged only, or no origin (a Store emptying its initial energy) → **storage, not generation**: its
    export share stays the site's (an EaaS BESS's export goes to the site; a StorageUnit is never
    generation); both → **mixed**: its output is NaN whenever it delivers, so the split and the PPA share
    say not established — never a guess.
  - **PyPSA Link semantics** (round 5): a Link's inputs are bus0 and every port with a negative
    `efficiency{k}` (at any snapshot); its outputs are the other ports. A Link with a negative `p_min_pu`
    is reversible — every port is both — so a reversible Link touching an electric site bus also
    charges its own non-electric side: a reversible fuel cell (either way round) is never plain
    generation (excluded, or mixed when fuel reaches it too). An engine co-firing electrolytic H2 on an
    input port is mixed. One deliberate exception: an ELECTRIC input port on the converter itself (an
    engine's auxiliary draw on the site bus) is netted from its output (Σ −p_k over its electric
    site-side ports), not an origin — that draw is consumption, not stored electricity coming back.
  - **Unknown generation** (a NaN) in a period makes that period's split not established
    (`export_split_not_established:<source>:<period>`, blocking) and the source's amount None — never a
    silent share for the site. The PPA path already said `generation_not_established`.
  - An empty bus carrier counts as electric (PyPSA's default is "AC"; only a hand-cleared carrier is
    empty).
  - Blocking input flags apply to the whole ledger: a not-established split in one period of a
    multi-period run makes every period's `ok` None (the other periods' numbers stay right). Safe, not
    per-period precise — recorded, not changed.

  Before the fix the assessor's probes gave silent misattributions with `conservation_ok` True (internal
  payees; checks 3–4 cannot see them): a gas supply behind a CHP (21,825.92 vs 26,368.16), a gas supply
  with a boiler beside it (19,161.48 vs 31,877.02), a solar-thermal collector (18,059.84 vs 19,824.27), a
  multi-output CHP's bus2 power ignored (42,825.27 vs 31,877.02), NaN generation given to the site
  (19,184.77 vs 19,824.27), a Store battery's discharger counted as generation (310,452.72 vs 448,000.00),
  then (round 4, a bus0-only rule) a gas-heated heat Store's ORC excluded (449,122.27 vs 434,202.31), a
  reformer-plus-electrolyser H2 loop taken as storage only (454,854.60, should be not established), a
  two-hop H2 loop (416,590.60 vs 448,018.80) and a two-hop battery (310,193.58 vs 448,000.00) counted as
  generation; then (round 5, one-way Links) a reversible SOFC counted in full (367,405.79, should be not
  established), an H2 co-firing engine counted in full (407,389.96, not established), and a reversible
  Link with an electric bus0 dropped (441,340.69, not established). Tests: `test_value_flow_reconciliation.py::test_the_export_split_counts_
  electric_generation_only` (eight topologies: CHP, gas supply with a load, multi-output CHP,
  solar-thermal, Store battery, two-hop battery, two-hop H2, heat Store with ORC — split and PPA to the
  cent), `test_a_converter_fed_by_fuel_and_storage_is_not_established` (bought H2; gas through a
  reformer; a reversible SOFC; an H2 co-firing engine; a reversible Link with an electric bus0) and `test_unknown_generation_makes_
  the_export_split_not_established` (each new case fails on the code before its fix). The same `site_generators` feeds the preflight, the CFE score
  and the dispatch-PPA check: a non-electric Generator is no longer "on-site generation" there either.
- **Accepted residues and deviations (from the WP records):**
  - WP3.1 R4: with a meter bypass every asset carries `meter_bypass` (placement right, preflight warns;
    noise only).
  - WP3.2 INFO: `_contract_parties`, `same_party` and the models are outside the templates' `code_sha`.
  - WP3.2 M5 deviation: `btm_ppa` drafts no EaaS (the developer owns only the PPA's assets).
  - WP3.3a deviations: per-kWh import levies and certificates are metered like energy items; every group
    member must be a hub member when a key is set; a hub member that is `site_party` gets no line to itself.
  - WP3.3b INFO: tiered and capacity net items on a group are outside the LP (`not_in_lp`), never priced
    twice; a net revenue item on a group is refused.
  - WP3.4 R2-3: a CfD's direction is nominal (net is null either way); the sleeving party is not named
    on an unsettled line.
  - Per-asset export parts as line metadata: deferred (WP3.4 → WP3.6 → P4 if the returns need them).
  - WP3.6: the contracted-MW input has no `min` (the server refuses 0).
  - WP3.7b round 3 LOW: tariff rows are keyed by index (a removed period's invalid text can show on the
    next row; Save stays blocked).
  - The template and designer routes check "solve in flight" without the lock (WP3.2, accepted).
  - Chat lines paging: party ids, asset / contract names and flags are cut (80 / 160 characters) so one
    row always fits a page (assessor note, fixed and tested).
- **Recorded follow-ups (not blocking):**
  - `replacesInline` trusts the solver-config PUT's ref check; a hand-edited `solver_config.json` is not
    re-checked on load (WP3.7a round 2 INFO) — P4 hygiene.
  - The capacity fee's shape (unit, a single all-year period) is checked (`fee_eur_per_mw_year`) at the
    solve, not at the PUT (WP3.7c rounds 2–3) — the editor offers only the supported kind and units and
    states the one-period rule; the PUT check is P4 hygiene.
  - The contracts editor's staleness baseline is the raw stored list: a P0-era untagged list normalised by
    the editor's own connection save reads as "changed" once (a reload recovers it); the pre-save read
    and the save are two requests (WP3.7c round 2, accepted).
  - `appendContracts` reads the config twice (WP3.6 #8, LOW).
- **Carried from P2, unchanged:** the DSR slack cost as a cost row (P4 decision); the windowed-tier split
  residue; annual bands on monthly tiers.
- **Scope boundaries** (plan): annual cash flows, multi-party co-optimisation, DR on assets, allocation over
  periods longer than a month, realistic-dispatch ledgers, and net revenue items on the group net meter
  are not P3.

## Gate assessor verdict

Six rounds, each PASS WITH CONDITIONS until the last; the record of each round, the probes and what was
done is in the plan's "Phase 3 e2e QA gate".

1. **Round 1 (bfb5bef):** the hourly audit red (the master-merge adequacy sites, listed with reasons);
   the export split weighted a gas supply behind a CHP by its MW of gas (MEDIUM, silent); the note's
   accuracy (the WP3.2 verdict, scenario E's scope, V1b, the bridge's independence, the residues); the
   ADR-0002 procedure extended. All fixed (e27fe5d).
2. **Rounds 2–5:** the same silent-misattribution class under ever narrower topologies — non-electric
   Generators and a CHP's bus2 power (round 2), a Store battery's discharger (round 3), origins read at
   bus0 only (round 4), one-way Links (round 5). Each fixed with a test that fails on the code before it
   (06374d7, b0cbbfe, cb478a9, e731683); the rule is stated under "The export split's generation rule".
3. **Round 6 (e731683): the generation condition CLOSED** — 21 probes right (the 16 earlier and 5 new);
   the assessor accepted the netting of a converter's own electric input port. Remaining: the full suite
   at the final code — now **7,904 passed, 0 failed**, all 25 QA drivers green.

**Phase 3 is closed for participants and value flows; P4 may start.** The chat surface (P2's Library
tools and #63's rename path, P3's `get_results` value flows and `define_participants`) stays **not done**
until the ADR-0002 probe above is run and recorded.
