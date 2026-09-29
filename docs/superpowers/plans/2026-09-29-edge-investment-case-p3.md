# Edge Investment Case — Phase 3: participants and value flows (plan v0.4)

**Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` §7 (participants), §12 (UI), §13
(P3 row), §15 (conservation doctrine), decision 4 (participants first-class), decision 9 (splits never enter
the objective). **Outline:** P0–P1 plan, "P3 Participants & value flows". **Carried in from P2:** the UI for
the Tariff builder, Library browser and Contracts editor; the group net-import variable for net energy items
on multi-member groups; the attribution of exported PV under a BTM PPA (P2 plan, WP2.2b: "P3 attributes
it"). **Branch:** `claude/energy-tool-features-research-fdixs0`.

**Process (owner's instruction, spec §13):** this plan → review loop until PASS → per WP: TDD, then an
implementation-review loop until PASS → Phase 3 e2e QA gate (a `qa_value_flows.py` driver discovered by
`run_qa_drivers.py`, frontend vitest + `tsc`, full backend suite, findings note, independent assessor)
before P4.

---

## What P3 delivers

A **value-flow ledger**: every money flow of the modelled site as a double-entry line **payer → payee**
with a `ValueStreamKind`, per modelled period, **unweighted** (one period-year, the `cost_rows` basis; P4
applies years). The lines come from the exact tariff bill per item (P2), the connection-agreement fees, the
export price, the grid-side commodity, every contract settlement line (P2) and every asset's cost (the
Economics seam). Parties are **participants** (internal) or **externals** (retailer, DSO, market, tax
authority, suppliers). The ledger is **reconciled to `cost_breakdown`** through named bridge terms, so an
omitted or double-counted flow fails the gate. A **template** fills participants and assignments for the
five spec §7 structures. A new Results tab `investment` shows a per-participant table and a Sankey, and
hosts the editors whose APIs landed in P2.

**Not P3:** money over time (years, escalation, discounting, debt, tax) — P4 converts ledger lines to
`CashflowLine`s (mapping pinned in WP3.1); multi-party co-optimisation (decision 9).

## Dependency graph

```
WP3.0 value-flow config, route, hashing, types.ts
 ├─ WP3.1 ledger: sources → lines, coverage, reconciliation to cost_breakdown
 │   ├─ WP3.2 templates
 │   ├─ WP3.3a energy hub: allocation keys
 │   └─ WP3.4 /results/value_flows (+ cache), chat
 ├─ WP3.3b group net-import LP variable (P2 carry-in; independent of the ledger)
 └─ WP3.5 FE foundation: clients, typed contracts, `investment` tab shell, commercial writes
      ├─ WP3.6 Participants designer + per-participant table + Sankey
      ├─ WP3.7a Library browser
      ├─ WP3.7b Tariff builder + bill preview
      └─ WP3.7c Contracts and connection-agreement editors
```

**Per-WP invariants (all of P3).** The P1 reconciliation gate and the P2 billing gate stay green after every
WP (`qa_commercial_lp.py` and `qa_billing_contracts.py` run in each WP's verification). **The ledger never
changes the LP or any committed commercial hash** (decision 9): WP3.3b is the only LP change in P3 and is
kept out of the ledger WPs. Unknown is `None` + a flag, never 0 (ADR-0001). Every new persisted field is
backward compatible (P2 configs validate unchanged) and mirrored in `frontend/src/api/types.ts`. Services
never import routers or `solver_service` (tripwires). Parties are compared with `lp_bindings.same_party`
(trimmed, case-insensitive) everywhere.

---

## Fixtures and oracles (bind the gate)

| Id | What | Expected |
|---|---|---|
| V1 | `single_owner` on the P1 edge fixture (`build_edge_15min`, US tariff, PV + BESS, a grid-side supply generator, an export price, a connection-agreement capacity fee) | coverage complete; reconciliation to `cost_breakdown` closes to the cent through the bridge terms; the commodity line and the fee line present |
| V1b | V1 plus a costed Line, a Transformer and an asset on a bus without a price column | every costed asset in the ledger (no silent drop); reconciliation still closes |
| V2 | `btm_ppa`: PV sold as consumed to the site; BESS under an EaaS contract; exported PV attributed to the developer (`export_revenue_to="asset_owner"`); at least one interval with both a tariff export item and an export price | developer receives PPA + EaaS + its export share of both export sources, pays PV/BESS costs; site pays bill + PPA + EaaS; hand formula per interval for the export split |
| V3 | `landlord_tenant` with a `LeaseContract` | landlord pays asset costs, receives the lease; tenant (= `site_party`) pays lease + bill |
| V4 | `dso_developer` with a `DrContract` (counterparty `dso`, a participant) on an active DSR bus (loads only until P5) | DSO → developer availability + activation to the cent (P2 C-style formula) |
| V5 | `energy_hub`: 3 members on a group connection, 15-min, 7 days, self-authored with the working (like H1–H3) | each member's share of every shared item under each key, to the cent; linear import energy metered per member |
| V6 | group net-import (WP3.3b): a net cost energy item on a 3-member group with an export Link | LP row = billed item to the cent; objective gap 0; the P2 gate numbers unchanged |

### Conservation and reconciliation (spec §7, §15)

Four checks per period; `conservation_ok` (`GatesBlock`, P0) is `True` only when all four hold, `None` +
`ledger_incomplete:<n>` when a line is `None` (never `True` over unknown money), `False` otherwise.

1. **Double entry** (bookkeeping): one payer, one payee, `payer != payee`, amount ≥ 0 or `None`.
2. **Internal streams net to zero** (spec §7 literally; also bookkeeping).
3. **Coverage:** every source record maps to exactly one ledger line or one allocated group — every
   `per_item` key of the bill, every settlement line, every connection fee record, the export revenue, every
   grid-side generator, every cost-bearing asset of `asset_economics`. The comparison is on **signed
   site-view amounts** (`gap._site_view` convention), so a swapped payer/payee fails.
4. **Reconciliation to independent truth.** Per period, with every amount on the **unweighted period-year
   basis** (`cost_breakdown.by_period[p]` ÷ years(p) on multi-period networks — `by_period` is years-weighted,
   `cost_breakdown.py:324`; the top-level totals on a flat network; the same for any per-period input):

   Σ over participants of net outflow to externals (participant → external positive, external →
   participant negative)
   = `cost_breakdown` total
   − **every** `commercial_cost_terms` item (import, export, demand, tiers, tariff capacity, connection fee,
     dispatch PPA, and any later term such as WP3.3b's `energy_net_group`)
   + the billed tariff items **whose resolved payee is external**, signed from the participants' side
   + the connection fees **whose payee is external** (the `included_in_total: False` fixed fee too)
   + the contract lines with **one external party**, signed from the participants' side
   − the export-price revenue (Σ w·p0(export_link)·`ic_export_price`, received by participants).

   Closes to the cent. Lines between participants never enter it (they net out); a line between two
   externals (a contract naming two externals) is excluded from both sides and listed in `notes`. A line
   whose party is not established cannot be classed internal or external: it makes check 4 and
   `conservation_ok` `None` (`ledger_incomplete`), like a `None` amount. **Not in either side:** the DSR slack
   cost and VoLL shedding — transient LP terms absent from `cost_breakdown` (they are the objective
   decomposition's residual, `objective_decomposition.py:94-100`); the ledger discloses their amounts
   (from `buses_t["ic_dsr_p"]` and the lost-load capture) as `dsr_slack_not_a_cash_flow` /
   `voll_not_a_cash_flow`, with no line. This check fails on an omitted source, a double-counted one, a
   swapped direction and a mis-resolved payee.

---

## WP3.0 Value-flow config, route, hashing, types

**Model** (`models/commercial.py`):

```python
TariffPayeeRule(kind: TariffItemKind | None = None, item_id: str | None = None, payee: str)  # item_id wins
AssetOwnership(asset_id: str, component: Literal["Generator","StorageUnit","Store","Link"], owner: str)
HubMember(link: str, participant: str, contracted_mw: float | None = None)
ValueFlowConfig(
    template: Literal["single_owner","btm_ppa","landlord_tenant","dso_developer","energy_hub","custom"]
              = "custom",
    template_version: str | None = None,       # builder id + version, pinned
    built_digest: str | None = None,           # digest of the config the builder produced
    participants: list[Participant] = [],      # internal parties; must include `site_party`
    externals: list[str] = ["retailer", "dso", "tso", "market", "tax_authority",
                            "capex_supplier", "om_contractor"],
    tariff_payees: list[TariffPayeeRule] = [],
    asset_owners: list[AssetOwnership] = [],   # owners are PARTICIPANTS; unassigned → site_party (note)
    hub_members: list[HubMember] = [],         # energy hub only
    allocation: AllocationKey | None = None,   # energy hub only
    export_revenue_to: Literal["site_party", "asset_owner"] = "site_party",
)
CommercialConfig.value_flows: dict[str, Any] | None = None   # raw JSON; see below
```

- **`value_flows` is stored raw** on `CommercialConfig` and validated into `ValueFlowConfig` **lazily** by
  `participants.py` (a failure is a `value_flows_invalid` ledger payload). `apply_commercial_for_solve` and
  `_lp._parse` parse the whole `CommercialConfig` (`solver_service.py:751`, `cost_rows`, `billing`): a stored
  `value_flows` that later fails a structural rule must never fail a solve or invalidate the commercial
  rows. Test: a corrupted `value_flows` still solves, bills and reconciles.
- **Asset owners are participants** (validated at the route): an asset owned by an external would create
  external → external cost lines while `cost_breakdown` still counts the asset. A third-party asset owner
  (a PPA seller operating on site) is modelled as a participant.

- **Tariff payee precedence:** an explicit rule (item id, then kind) → the `RetailContract`'s retailer for
  the attached tariff (`contracts.retail_parties`) → the default: `energy`, `fixed`, `certificate` →
  `retailer`; `demand`, `capacity` → `dso`; `tax_levy` → `tax_authority`. `Tariff.dso_or_retailer` is a
  **label** on the `retailer` node, never a party id. The resolved payee of each item is echoed in
  `provenance.tariff_payees`.
- **`site_party` is never changed by P3.** Templates create the participant whose id equals the existing
  `site_party` and give it the template's role (`contracts_record` and `ppa_dispatch_hash` hash
  `site_party`, `settlement_inputs.py:203`, `lp_bindings.py:1651`: changing it would flip settlement drift and
  the dispatch-PPA hash).
- **Validation lives at the route, not in a model validator** (`_lp._parse` runs in `cost_rows`, `billing`,
  `gap`: a stale party must never make the whole commercial layer invalid or fail a project load). The
  route checks: participant ids unique and disjoint from `externals` (by `same_party`); `site_party` is a
  participant; every party named by a contract, an ownership or a hub member is a participant or an
  external; `hub_members` / `allocation` only with a group contract; `fixed_shares` keys = hub
  participants. The model keeps structural checks only. At ledger time a party that no longer resolves (a
  contract edited later) is a line flagged `party_not_established:<name>`. A contract party that is `None`
  in P2 (CfD `generator_owner`, DR `counterparty`) stays `party_not_established` (the P2 rule), never
  defaulted.
- **Route:** `PUT /api/simulation/commercial/value_flows` — merges `value_flows` into the current
  commercial config **server-side** under the solver-state lock, validates it fully (`ValueFlowConfig` +
  parties; no `bind_commercial`: no network validation, Library resolution or series writes), takes an
  `If-Match` digest of the current `value_flows` (412 on mismatch), 409 `solver_in_flight` during a solve,
  409 `no_commercial_config` when there is no commercial config (the `attach_tariff` rule). The chat tool
  uses the same route. Nothing at solve time reads `value_flows`.
- **`PUT /solver_config` keeps the stored `value_flows`** when the submitted `commercial` omits the key
  (it replaces the commercial block today, `routers/simulation.py:401-417`, and `bind_commercial` dumps the
  whole model back). Mechanism: the route reads `"value_flows" in commercial.model_fields_set` **before**
  `bind_commercial` (after it, `resolve_tariff_ref` re-validates from a dump and every field reads as set,
  `binding.py:62-64`) and re-inserts the stored value when the key was omitted. An **explicit**
  `value_flows` in this body (including chat's `update_solver_config`) is refused with a 422 naming the
  value-flows route (it would bypass the full validation and `If-Match`). Tests: omitted, `null`, explicit,
  and a Library-tariff-ref config; a tariff save after a value-flows edit keeps the edit.
- **Hashing:** `("CommercialConfig", "value_flows"): None` in `FIELDS_AFTER_V1`. The adequacy
  `_config_hash` (`services/adequacy/report.py:55`, `asdict(cfg)`) **pops `commercial.value_flows`
  whatever its value** (`bind_commercial` will dump `value_flows: null` into every commercial dict), so
  neither a participant edit nor the new key changes an AdequacyReport's `assumptions_hash`.
- **Single source of participants:** `ValueFlowConfig.participants`. `FinanceInputs.participants`
  (`models/finance.py:176`) is filled from it in P4; a P0 fixture that sets it keeps validating (it is
  documented as derived).
- **types.ts:** `Participant`, `ParticipantRole`, `ValueStreamKind`, `AllocationKey`, `ValueFlowConfig` and
  parts; the typed contract variants (`PpaContract` … `RetailContract`) with `CommercialContract` kept as
  their union; `CommercialConfig.value_flows: ValueFlowConfig | null` (commented: stored raw, validated by
  the server on write; a stored bad value makes the ledger answer `value_flows_invalid`).

**Tests:** round trip; every P2 driver config validates unchanged; the route's party cases (typo, case,
disjointness, stale party at ledger time → flag, not an invalid config); `If-Match` 412; the
**hash-invariance test**: flip `value_flows` and assert unchanged demand, tiers, PoC links, agreement,
`ppa_dispatch_hash`, `contracts_record`, no `config_changed_since_solve`, and an unchanged adequacy
`assumptions_hash`; the FIELDS_AFTER_V1 inventory test.


**WP3.0 implementation (fb35865; master-merge regressions fixed in the next commit).**
- **Review round 1 → PASS WITH CONDITIONS, fixed:**
  - #1 a misshaped body (the config unwrapped, or `{}`) cleared the stored value; the body field is now required and extra keys are forbidden (422);
  - #2 the fixture test was tautological; it is now a recipe-1/2 invisibility check plus route round trips of three P1/P2 shapes;
  - #3 `value_flows: Any`, so a string or list in a hand-edited file cannot fail `_lp._parse`; a live solve with three corrupted values solves, bills and reconciles;
  - #4 a stored commercial config that no longer validates answers 409 `commercial_config_invalid`, not 500;
  - #5 more party checks: blank ids, blank or duplicate externals, a payee rule naming an item the tariff lacks, duplicate hub links or participants, `contracted_capacity` needing every `contracted_mw`;
  - #6 `If-Match` accepts quoted and weak tags;
  - #7 `attach_tariff` strips `value_flows` before its PUT;
  - #8 a live solve, then a value-flows edit, then billing and `cost_breakdown`, shows no `config_changed_since_solve`.
- **Review round 2 → PASS.** Notes applied: `If-Match: *` matches any current value; `types.ts` says a reader trusts the shape only when `status === 'ok'`; the parse error names `value_flows` for a non-object root. **For WP3.3a:** the route now refuses `contracted_capacity` without every `contracted_mw` and a participant on two hub links; the ledger-time `allocation_not_established` stays for configs that became stale after saving.
- The WP3.5 editor and the WP3.4 chat tool must always send `If-Match`. Chat errors from the new 422s arrive as the generic `tool_error` kind, like the existing binding refusals (INFO).
---

## WP3.1 Ledger: sources, lines, coverage, reconciliation

`services/commercial/participants.py` (pure; imports `models` and `services.commercial` only):

```python
ValueFlowLine(period: str, payer: str, payee: str, value_stream: ValueStreamKind, source: str,
              source_id: str, tariff_item: str | None, tariff_item_kind: str | None,
              contract_id: str | None, asset: str | None,
              basis: Literal["cash","annuity","model_only"],
              amount: float | None, flags: list[str])
build_ledger(inputs: LedgerInputs, vf: ValueFlowConfig, *, site_party: str) -> Ledger
check_conservation(ledger, inputs) -> ConservationResult(ok: bool | None, checks, flags)
```

`LedgerInputs` is plain data built by the results caller (`services/results/value_flows.py`, WP3.4) — the
commercial package never calls `physical_quantities` / `asset_economics` (they import `solver_service`).

| Source | Line(s) | Stream |
|---|---|---|
| Bill: `bill_site` `per_item` per period | one line per item, **signed site view**: a positive amount site_party → payee, a negative one reversed (a cost item with negative rates, a revenue item) | by item kind and `measured_on`: energy on import → `energy_import`, on export / revenue → `energy_export`, a DSO-paid energy item → `network_energy`; demand → `demand_charge`; capacity → `network_capacity`; fixed → `retail_fixed`; certificate → `certificates`; tax_levy → `tax` (with `tariff_item_kind="tax_levy"`: P4 treats levies as deductible opex, never as corporate tax) |
| Connection agreement: `n.meta["ic_connection_fee"]` (fee × `p_nom_opt`) and `ic_connection_fixed_fee` | site_party → `dso` (or the agreement's payee rule) | `network_capacity` |
| Curtailment compensation (`curtailment_compensation_eur_per_mwh` set) | `None` + `curtailment_compensation_not_computed` (nothing computes it yet) | `network_capacity` |
| Export price: Σ w·p0(export_link)·`links_t["ic_export_price"]` per period (signed: a negative price means the site pays) | `market` → site_party (or the asset owners, `export_revenue_to`) | `energy_export` |
| Grid-side assets (the meter-side classifier below), any class | VOM: site_party → `market`, flag `commodity_from_grid_side_generator` (`energy_import`); fixed cost: site_party → `market`, flag `grid_side_asset_cost` (`other`), `basis="model_only"` (a modelling proxy, not a cash flow: excluded from the P4 conversion, labelled in the table, kept in check 4); never an owned asset | `energy_import`, `other` |
| Settlement lines (P2 `settlement_lines`) | payer → payee; a negative amount reversed | ppa_energy, ppa_sleeving_fee → `ppa_settlement`; cfd_difference → `cfd_settlement`; dr_availability / dr_activation → same; lease_payment → `lease`; eaas_fee → `eaas_fee`; unknown → `other` + `unmapped_stream:<s>`. `ppa_excess_mwh` is a volume with no money: disclosed in `notes`, no line |
| Site-side assets and network assets (Lines, Transformers), enumerated from the **`physical_quantities` superset** plus Lines / Transformers — **not** `asset_economics` (it excludes Lines and Transformers and skips assets on price-less buses or without a dispatch column, `asset_economics.py:79-85, 340-349, 465-472`) | owner → `capex_supplier`: annualised capex = per-horizon rate × `p_nom_opt` × active(p), `basis="annuity"`; owner → `om_contractor`: FOM, same arithmetic, `basis="cash"`; owner → `om_contractor`: per-asset operational expenditure from **`n.statistics()` "Operational Expenditure"** — the source `cost_breakdown.opex` reads (it covers storage and quadratic cost terms); PyPSA has no VOM/fuel split, so one `vom` line. Network assets default to site_party (assignable). An asset `_meter_sides` cannot classify → its lines carry `asset_side_unclassified` (never dropped) | `capex`, `fom`, `vom` |

- **No double counting:** the LP's `energy_import` / `energy_export` / `demand_charge` / tier / capacity
  cost rows are never ledger sources (they are the LP's view; P2's gap compares them). A `changes_dispatch`
  PPA appears once (its settlement line). The tariff's export items and the export price are separate
  sources (the LP's `energy_export` row mixes them).
- **Meter-side classifier** (`participants.classify_buses`, tested on its own): `lp_bindings._meter_sides`
  returns only `(seen, bypass)` — the site-side buses and the grid-side buses reachable from the site
  without crossing the meter (`lp_bindings.py:483-522`) — so it cannot place the V1 `grid_supply` generator
  on bus `grid`. The classifier: **site-side** = `seen`; **grid-side** = the meter Links' grid buses plus
  everything reachable from them without crossing the meter; `bypass` buses are grid-side and flagged
  `meter_bypass`; **unclassified** = reachable from neither (flag `asset_side_unclassified`, never dropped).
  **Meter Links** (import, export, group members: the PoC capital cost or connection extension) are
  site-side assets owned by site_party; the connection fee stays its own line.
- **Not money, disclosed:** the DSR slack cost and VoLL shedding (not in `cost_breakdown` either; amounts
  in `notes` with `dsr_slack_not_a_cash_flow` / `voll_not_a_cash_flow`).
- **Export attribution** (`export_revenue_to="asset_owner"`): covers the export-price revenue **and** the
  tariff's export revenue items, split **per interval** pro rata to each owner's on-site generation in that
  interval (the `as_consumed_btm` rule, P2); the results caller supplies the interval frames.
- **Per period:** one ledger per investment period; money unweighted.
- **P4 mapping (pinned now):** a `ValueFlowLine` becomes one `CashflowLine` per operating year with
  `participant` = the side whose cash it is (both sides for internal lines), `counterparty` = the other,
  `amount` signed from the participant, `provenance.source` carrying `source` / `source_id` /
  `contract_id` (drill-down survives); `basis="annuity"` and `basis="model_only"` lines are **not** converted — P4 assigns the
  overnight capex (`overnight_cost` + `capex_phasing`) to the owner from `asset_owners`; a `None` line makes
  that participant's returns `not_established` in P4 (never 0).

**Tests (TDD, `tests/test_value_flow_ledger.py`):** each source row; sign reversals (negative bill, negative
settlement, negative export price); `None` kept; the stream map; party resolution; the four checks, each
failing on its own corruption (dropped source, duplicated source, swapped direction, amount off by 0.01,
phantom internal party, a mis-resolved payee); V1 and V1b reconciliation to the cent through the bridge,
flat and multi-period; a tariff export item **and** `export_price_ref` together (no double count); an
internal DSO (V4) closes; the DSR / VoLL disclosures when they dispatch.


**WP3.1 implementation (f66173b).**
- **Review round 1 → PASS WITH CONDITIONS, fixed.** The reviewer's probes closed check 4 on:
  - tariff capacity items (extendable and fixed PoC);
  - the firm and fixed connection fees;
  - DSR and VoLL;
  - activity masking;
  - the export split across DST;
  - a 35,040-snapshot year (ledger in 2.9 s, no cache needed).

  Fixes:
  - **H1:** an unsettled contract (or a retail contract on another tariff) is a None line plus a blocking flag, never a vanished contract.
  - **H2:** the bill's, the cost terms' and the settlement's flags reach `LedgerInputs.input_flags`. Drift, a partial import, an unsettled contract, a term not established and an unbilled period make every period None through a fifth check, `inputs_established`. Per-period bill flags are disclosed.
  - **#3:** a single `_sources` definition gives each source's expected legs (debtor, creditor, signed value) and stream. The builder emits them; coverage compares payer AND payee (`same_party`), signed sums and the stream, so a moved payer, a moved capex payer, a relabelled stream and a line contradicting its source all fail. What check 4 can and cannot see is documented; check 2 is documented as a bookkeeping identity.
  - **#4:** `ValueFlowConfig.connection_fee_payee` replaces the unsaveable `connection_fee` payee rule.
  - **#5:** `classify_buses` runs two searches (site, grid). A bus both reach is behind a meter bypass: flagged, on the nearer side, a tie on the site side. A site-side Generator on a bus of another carrier is a `fuel_supply_generator` (opex → market, stream `fuel`). Eight classifier unit tests.
  - **#6:** `same_party` everywhere.
  - **#7:** a capacity item on `peak_import` is `network_capacity`.
  - **#8:** None disclosures are flagged; sub-cent disclosures are LP noise; the curtailment penalty is disclosed; NaN→0 in `_per_asset` is documented; the per-period total is guarded.
  - **#9:** skipped export-split sources are flagged; bill items split at any sign; `asset_under_external_ppa` notes.
  - **#10:** imports moved to the top, and the `build_ledger` docstring written. The plan's `site_party` argument is `inputs.site_party`.
  - **#11:** V1b is multi-period too; live DSR/VoLL (VoLL equals the lost-load capture's cost); live unsettled and dead-retail contracts, drift, partial import; the export split by hand with two site generators whose shares vary; the phantom-party cases split (a stale party in a source → None, a line contradicting its source → coverage fails).
- **Review round 2 → PASS WITH CONDITIONS, fixed:**
  - **R1 (regression):** split legs resolving to the same (payer, payee) collided in coverage (two generators of one owner; a site generator beside the no-generation share). Legs are now merged by (debtor, creditor), compared with `same_party`, before emitting. Unit and live same-owner tests.
  - **R2:** the blocking list is explicit (exact names plus prefixes). `demand_months_not_established`, `group_energy_share_not_established` and `*_recipe_changed` are disclosures. A representative-week run keeps its ledger (live test).
  - **R3:** `is_fuel_supply` requires a non-electric bus carrier (AC, DC, low voltage, … never qualify), no Load, and a bus that feeds the site only as Link input. Tests: PV on LV and DC buses, gas behind a CHP, a heat bus fed by a Link.
  - **R4 (LOW, accepted):** with a bypass, both searches reach the whole connected network, so every asset carries `meter_bypass`. Placement is right and preflight warns; noise only.
  - **R5:** check 2 stays a documented identity.
- **Review round 3 → PASS.** R1–R3 closed, no regressions in probes 1–9. **Note for WP3.4/WP3.6:** merged export-split legs give one line per (owner, source); if the Sankey or drill-down needs per-asset shares, carry the unmerged parts as line metadata.
---

## WP3.2 Templates

`services/commercial/value_flow_templates.py`: builders `(network_summary, commercial) -> TemplateResult
(config, draft_contracts, notes)`, pure, registered with a pinned `template_version`; `built_digest` =
digest of the produced config. `template_edited` = current digest ≠ `built_digest` (disclosure);
`template_stale` = the network's assets differ from those the builder saw (disclosure). Builders never
save contracts: a missing one is returned as an **unsaved draft** for the user to confirm.

| Template | Participants (`site_party` keeps its id) | Ownership / flows / needed contracts |
|---|---|---|
| `single_owner` | site_party (site_owner) | every site-side asset owned by site_party |
| `btm_ppa` | site_party (offtaker), `developer` | the developer owns **only the PPA's `asset_ids`** (PPA seller=developer, buyer=site_party, `as_consumed_btm` or `pay_as_produced`); other developer assets need an EaaS or lease draft; `export_revenue_to="asset_owner"` pro rata by the `as_consumed_btm` rule |
| `landlord_tenant` | `landlord`, site_party (tenant) | assets owned by `landlord`; a `LeaseContract` draft lessor=landlord, lessee=site_party |
| `dso_developer` | site_party (developer), `dso` moved from externals to participants | assets owned by site_party; a `DrContract` draft counterparty=dso (on loads until P5) |
| `energy_hub` | site_party (hub, the group-contract holder), one `hub_member` per member Link | members own the assets on their bus; `allocation` required (WP3.3a) |

`POST /api/simulation/value_flows/template {template}` → `TemplateResult` (nothing saved). **Tests:** each
template on its fixture; drafts for missing contracts; `template_version` pinned in
`tests/fixtures/investment_case/value_flow_templates.json`; `template_edited` / `template_stale`.


**WP3.2 implementation.**
- `services/commercial/value_flow_templates.py` (five builders, `build`, `template_status`, `config_digest`, `assets_digest`, `source_sha`) and `POST /api/simulation/value_flows/template` (409 `no_commercial_config` / `commercial_config_invalid` / a template refusal; 422 `template_unknown`).
- `template_version` = `<name>@<version>`; the fixture pins version and builder source hash.
- `ValueFlowConfig.built_assets_digest` added (for `template_stale`).
- Meter Links and network branches stay with `site_party` in every template (the connection is the site's).
- `is_fuel_supply` moved beside the classifier (`participants`), and `branch_edges` extracted.

**WP3.2 review round 1 (c41ca58): FAIL** — binding #1–#3; #4–#9 fixed with them; #10–#11 notes.
Probed end to end through the routes (POST template → PUT solver_config with the drafts → PUT
value_flows with If-Match → solve on the session → `value_flow_ledger`).
- #1 HIGH: contract parties were never listed as externals — single_owner / btm_ppa on V1 (PPA + lease)
  got 422 from the value-flows route. → `_with_parties` in `build`: every party a saved contract or a
  draft names that is not a participant becomes an external; no participant stays an external.
- #2 HIGH: the dso_developer DR draft had no `contracted_mw` (ledger `ok=None`) and DSR state was
  ignored. → the draft carries `contracted_mw: null`; `dr_needs_contracted_mw:<id>` for an adopted DR
  without it; `dsr_not_enabled:<bus>` when the load's bus is not among the buses the solve enables
  (the route passes `dsr_buses` only when DSR price and share are > 0).
- #3 MEDIUM (binding): zero-price drafts solved silently. → every draft money field is `null` (the
  contract model refuses to save it until priced) with a `draft_needs:<id>:<fields>` note.
- #4: btm_ppa owns `PPA.asset_ids ∩` site-side generators, notes `ppa_assets_not_on_site`,
  `several_btm_ppas_first_used`, and refuses `template_conflicting_ppa` rather than drafting a second
  PPA over output the site does not buy.
- #5: landlord_tenant gives the landlord `lease.asset_ids ∩` site assets only.
- #6: dso_developer adopts only a DSO-like counterparty (`dso`, or not a default external); others are
  noted (`dr_counterparty_not_a_dso`, `dr_without_counterparty`), never drafted over.
- #7: the pin hashes the whole module plus the classifier (`code_sha()`); the fixture is a history of
  `{code_sha, versions}` whose entries must each bump a version; `template_outdated` when a stored
  `template_version` is older than the registry's.
- #8: `config_digest` leaves defaults out (nested, and a top-level value equal to its default in any
  order) and sorts lists canonically — a reorder or a new defaulted field is not an edit.
- #9: `built_assets_digest` → `built_inputs_digest` (site assets + contracts' type/id/parties/assets/
  loads + PoC + group members; money fields excluded). A build counts its own drafts as saved, so the
  normal flow reads `[]`; an unsaved draft reads `template_stale`. A PoC that leaves nothing site-side
  is noted `no_site_side_assets`.
- #10: the route takes no lock and no solver-in-flight check — it only reads the network and the
  solver config (the solve adds no component the builders read; its slacks are transient) and writes
  nothing; stated in its docstring. The WP3.4 chat and WP3.6 designer surface `notes`.
- #11: landlord_tenant notes `connection_costs_on_tenant`; energy_hub member ids never collide with
  `site_party` and the member region reads bus0–bus4. **M5 deviation (recorded):** btm_ppa drafts no
  EaaS contract; storage stays with the site and the note is
  `site_keeps_assets_developer_needs_eaas_or_lease:<ids>`.
- Tests: `test_value_flow_templates.py` (46 incl. 7 live e2e on V1 through the routes: all five
  templates, with and without drafts; dso with DSR enabled; energy_hub on a two-member hub).

**WP3.2 review round 2 (1e952e7): PASS WITH CONDITIONS** — #1–#9 and #11 closed; binding #10: the
route's "a solve adds no components" was false (the VoLL/DSR slacks sit on the live network for the
whole optimisation; a build mid-solve owned `__voll_*` / `__dsr_*` and drafted a PPA on them). → the
route answers 409 `solver_in_flight` like the value-flows PUT (tested), docstring corrected. INFO taken:
btm_ppa notes `developer_is_default_external:<party>` (btm_ppa → version 3, pin history appended).
INFO left: `_contract_parties` / `same_party` / the models are outside `code_sha`; hub member ids
`member_import_2` vs `member_import2` read alike (the name field disambiguates). **WP3.2 closed.**
---

## WP3.3a Energy hub: allocation

`participants.allocate(ledger, hub_inputs, vf)` splits the hub's shared lines into internal lines between
`hub` (= site_party) and each member; `hub` then nets to its external payments.

- **Linear import energy items** (energy items without tiers, measured on import): **metered per member**
  — each member rated through `tariff_engine.rate` with a one-item tariff and the group bill's own
  `billing_period` / `represents_hours` / `per_item` vs `per_item_sampled` choice; the last member (sorted
  id) takes the remainder; the residual before it is asserted ≤ 1e-9. A share key would misallocate TOU
  costs.
- **Keyed items** (demand, capacity, tiers, fixed, net items, the connection fee, export revenue):
  - `contracted_capacity` — `HubMember.contracted_mw` (all set, sum > 0, else `allocation_not_established`);
  - `peak_contribution` — each member's import in the group's billed interval per demand window and month,
    on the item's **settlement-interval means** (the engine's `interval_key`), so shares re-add to the
    billed kW; ties → the first maximal interval; when a ratchet floor binds, the contributions in the
    month that set the floor. `demand_lines` records neither the peak interval nor the floor's source month
    (`tariff_engine.py:898`), and the engine is P2-gated, so `allocate` **recomputes both** from the engine's
    `interval_key` and the candidate months of `_ratchet_floor_prior` (it returns only the value and a
    missing flag, `tariff_engine.py:287-326`): the floor's source month is the **argmax over the same
    candidate months** for all three modes (range, cyclic, designated months), earliest on ties; a floor
    from meter history (no modelled month) → `allocation_not_established:<item>`; non-demand items under this key fall back to `energy`
    (disclosed);
  - `energy` — per-period member import energy (Σ w·p0 per member in the period), **not** the
    years-weighted `ic_group.energy_share`;
  - `fixed_shares` — the key's shares.
- **Direction:** a cost share runs member → hub; a revenue share (export, a negative line) hub → member.
- **Remainder:** shares in exact floats; the last member in **sorted-id order** takes `item − Σ others`.

**Tests:** V5 under all four keys with the working in the fixture; remainder exactness; tie rule; the
ratchet-floor month; missing-key refusals; conservation checks 3–4 still close after allocation.

**WP3.3a implementation.**
- `services/commercial/hub_allocation.py` (pure): `period_hub(...)` → `participants.HubPeriod`
  (`energy_mwh`, `metered`, `peak`, `flags`); `is_metered`, `is_peak_item`, `floor_source_month`.
  `services/results/value_flows._hub_inputs` feeds it each group member's `p0` on the rating arguments
  `bill_site` uses (step, represented hours, billing period, site clock) and the group bill's own
  `RatingResult` per period (`LedgerInputs.hub`).
- The allocation is a ledger SOURCE (`source="allocation"`, `source_id="<source>:<id>"`, the shared
  source's stream), so the coverage check covers it: `participants._allocation_sources` splits the
  hub's own legs of every bill item, connection fee and export-price revenue (an `asset_owner` export
  split already paid to owners is not re-split); contract and asset lines are never allocated;
  curtailment compensation (always None) is not split.
- Peak contribution: the group's billed interval per (month, window) is re-derived from the engine's
  `interval_key` means and checked against the bill's `peak_kw`; a binding floor splits on its source
  month (argmax over the same candidate months, earliest on ties), checked against the engine's floor
  — a floor from meter history → `allocation_not_established:<item>:ratchet_floor_from_meter_history`.
- A residual above 1e-9 relative, an unknown member rating or key, or an unknown shared amount gives
  None lines flagged `allocation_not_established:<id>:<reason>` (ADR-0001), never a guess.
- **Deviations (recorded):** per-kWh import levies and certificates (no tiers) are metered like energy
  items (a key would misallocate them as it would a TOU rate); `value_flows_problems` requires every
  group member to be a hub member when an allocation key is set (a member outside the hub would leave
  its share with nobody); a hub member that IS `site_party` gets no line to itself.
- Tests: `test_value_flow_allocation.py` (27): V5 regenerates from `fixtures/investment_case/hub/
  v5_arithmetic.py` (stdlib only, no half-cent ties) and every share matches to the cent under all four
  keys with conservation ok; remainder exact; revenue direction; energy fallback disclosed; tie rule;
  ratchet floor month (and parity with `_ratchet_floor_prior` in the three modes); meter-history floor;
  unknown key / rating; config refusals; live: a solved two-member V1 hub closes under every key (flat
  and multi-period).

**WP3.3a review round 1 (271d9d5): PASS WITH CONDITIONS** — binding #1–#3; #4–#5 taken; #6–#8 noted.
The reviewer confirmed (independent recomputation to the cent) TOU windows with 30-min / hourly
settlement across DST, all ratchet modes with windowed and convex tiers, representative weeks,
multi-period, net / peak_import demand, an asset-owner export split with a hub, and that coverage
fails on every tampering of an allocation line.
- #1 HIGH: a hub made stale by a later `group_members` change (the solver-config route does not
  re-validate `value_flows`) was split silently over the remaining members. → `HubInputs.group_links`;
  the ledger refuses every allocation line (`allocation_not_established:<id>:hub_members_stale`) when
  the hub members' Links are not exactly the group's, or fixed shares key other participants.
- #2 MEDIUM: the specific reason (e.g. `ratchet_floor_from_meter_history`) never reached the ledger.
  → `HubPeriod.peak_reason` / `reason`; the line's flag carries it; peaks are computed only under the
  `peak_contribution` key (no spurious reasons under other keys).
- #3 MEDIUM: an export-measured demand item was split by the members' IMPORT in the export peak
  interval. → not a peak item: it falls back to energy, disclosed `allocation_fallback_energy`
  (recorded choice).
- #4 (taken): a linear import item is metered or not split (`member_rating_unknown` /
  `period_not_rated`), never keyed — `HubInputs.metered_items` / `peak_items` are known whether or not
  a period rated.
- #5 (taken): each member is rated once with all its linear items (was once per item).
- #6: members are sorted case-insensitively (`strip().casefold()`, the party-matching rule) — the
  remainder member does not depend on capitalisation. #7: V5 stays one naive month; windows, DST,
  ratchets and ties are pinned by the engine-parity tests (and the reviewer's recomputation). #8:
  noted.
- Tests: +7 (34 in `test_value_flow_allocation.py`).

**WP3.3a review round 2 (83d361f): FAIL, one binding item** — #1–#6 verified closed on the probes
(35k rows × 6 members 4.05 s → 1.47 s; the ratchet split still matches the independent
recomputation to the cent). R2-1: clearing the group contract after the hub was saved made
`_hub_inputs` return None, so the allocation was silently not applied. → it returns a stale
`HubInputs` (no group links), so every allocation line is None flagged `hub_members_stale` (tested).
Not binding, taken in WP3.4: the payload's `flags` name the lines' `allocation_not_established:*`.

**WP3.3a review round 3 (fa2aabc): PASS.** R2-1 verified (the cleared group gives None lines flagged
`hub_members_stale`, conservation None); no regressions (probes re-run, ratchet split to the cent,
35k rows × 6 members 1.58 s). **WP3.3a closed.**

---

## WP3.3b Group net-import LP variable (P2 carry-in)

P1 refuses a `measured_on="net"` energy item on a multi-member group with an export Link
(`lp_bindings.validate_for_network`, "gross member import the group meter nets out"). Lift it for the
**cost** direction:

- Variables `ic_group_net_import[t]`, `ic_group_net_export[t]` ≥ 0 with the **equality**
  `net_import − net_export = Σ_members p_member − p_export`. No capacity bounds (members or the PoC may be
  extendable, so `p_nom` is not the solved capacity); with cost-only pricing at rates ≥ 0 the split is
  bounded. Where a rate is 0 the split is degenerate, so the committed record and the cost row use
  **max(0, Σ p_member − p_export) recomputed from dispatch**, never the raw variable values.
- A net **cost** item is priced on `net_import` only when every rate is ≥ 0 (else refused, naming the
  item); the item is **removed from the per-member adders and from the export adders** — its
  `net_split_by_direction` `_side` handling (today priced on both sides) is replaced for this item — so it
  is priced once.
- Solve strategies: the same coverage as the existing group terms (`add_group_terms`); any strategy that
  refuses group terms refuses this too (stated and tested for rolling and myopic). A net **revenue** item keeps the existing gross-export pricing with the `net_split_by_direction` gap
  cause (P2), or is refused — never priced as a concave max.
- Commit record (`n.meta["ic_group_net"]`) with a drift hash (recipe 2, FIELDS_AFTER_V1 where a model field
  is added), a `cost_rows` label `energy_net_group`, the `cost_breakdown` "Commercial" row, `gap.py`'s LP-side
  mapping of the item, reload tests.
- **No billing change:** `bill_site` already meters Σ members − export (`billing.py:5-8`), so the P2 gate
  numbers are untouched (asserted).
- The other group refusals stay (regression tests).

**Tests:** V6 (LP row = billed to the cent, objective gap 0, a case in
`test_commercial_objective_reconciliation.py`); negative-rate refusal; revenue-direction behaviour; the
removal from adders (no double pricing); reload; P1 and P2 drivers green.

**WP3.3b implementation.**
- `lp_bindings`: `group_net_items(cfg)` (net cost, non-demand, untiered energy items on a ≥2-member
  group with an export Link), `group_net_hash`, `_group_net_spec` (Σ of their rates, €/MWh per
  snapshot), `add_group_net_terms` (the two variables, the balance equality, objective += Σ w·price·
  net_import weighted as a marginal cost over the snapshots the LP holds), `group_net_amounts` (the
  record's amount from the dispatch, max(0, Σ members − export)). `_adders` skips the items (never on
  members or export); `validate_for_network` replaces the P1 refusal with the negative-rate refusal
  (naming the item); a net REVENUE item keeps gross-export pricing and its `net_split_by_direction`
  cause. `LP_RECIPE` 6 (`GROUP_NET_RECIPE`). Commit: `n.meta["ic_group_net"]` (items, members,
  export, link, items_hash, hash_version, lp_recipe) and `links_t["ic_group_net_price"]` (column = the
  PoC); cleared by a solve without it. Scenario networks refuse the term (as the dispatch PPA).
- `cost_rows`: label `energy_net_group` (→ the `cost_breakdown` "Commercial" rows and horizon total),
  drift on `items_hash`, `energy_net_group_not_established` (blocking in the ledger). `gap`: the item's
  LP side is the record's net-import amount, joined to the energy kind's LP total; no
  `net_split_by_direction` cause for it. The energy record's hash still lists the item (drift is
  flagged by both records). `bill_site` unchanged (asserted: the bill = the engine on Σ members −
  export).
- Strategies: no strategy refuses group terms, so none refuses this one; rolling (per window) and
  myopic (per period) are tested to bill = row. The P1 refusal test now asserts the net pricing and the
  negative-rate refusal.
- Tests: `test_group_net_import.py` (9; V6 = three members, the third a must-run PV exporting through
  the group's export Link, so the group nets and net-exports), the V6 case in
  `test_commercial_objective_reconciliation.py` (save → load) and so in `test_billing_gap.py`.

**WP3.3b review round 1 (0c052c9): PASS WITH CONDITIONS** — binding #1–#2; #3–#4 taken; #5–#7 INFO.
The reviewer held the row = bill to the cent under zero-rate periods (degenerate split), a timezone,
years/objective weighting, representative weeks, extendable Links, rolling with overlap, myopic, a
net demand item beside it, the V6 value-flow ledger (all five checks), an infeasible re-solve (nothing
committed) and a re-solve without the item (record cleared, drift flagged); QA drivers green.
- #1 MEDIUM: a net REVENUE item on the group was let through on gross export beside the now
  circulation-neutral net cost item — the LP imported through a member and exported at once (12.6 of
  13.4 GWh). → stays refused (as in P1), naming the item; the plan's "keeps the existing gross-export
  pricing … or is refused" resolves to refused (tested alone and beside the cost item).
- #2 LOW–MEDIUM: `_adders` skips group net items before its rate check, so the config route and
  preflight passed an unrated item the solve refuses. → `validate_for_network` dry-runs
  `_group_net_spec` (tested).
- #3 (taken): `simultaneous_import_export` is not raised when every net item is priced on the group's
  net import (members importing while another exports is metering there, asserted on V6).
- #4 (taken): the gap joins the committed net amount on the RECORD, not the current config.
- #5 INFO: a scenario network is refused earlier for every commercial term (pre-existing). #6 INFO:
  rolling / myopic objective gaps are pre-existing (last window / period). #7 INFO: confirmed points.

---

## WP3.4 `/results/value_flows`, chat

`services/results/value_flows.py` `compute_value_flows(n, cfg, *, result_df)` builds `LedgerInputs` from
`bill_site` / `settlement_lines` (the `compute_billing` pieces), the connection meta, the export price, the
meter sides and the asset enumeration of WP3.1, then runs `build_ledger`, `allocate`,
`check_conservation`. **No cache** unless the GET on the P1 15-min fixture measures over 2 s; if one is
added it is in-memory only (not in `RESULT_STATE_KEYS`) and keyed on the solve id, the full commercial
canonical digest (contracts, `site_party`, connection included — settlement re-settles freely on
contract edits, `settlement_inputs.py:195`) and a digest of the enumerated assets' cost columns (a
`capital_cost` edit changes the ledger without a re-solve).

Payload:

```
{ status: "ok" | "not_established", reason?, participants, externals, template, template_version,
  periods: { "_"|"<year>": {
      lines: [ValueFlowLine...],
      by_participant: {pid: {paid, received, net, by_stream: {kind: net}}},
      sankey: {nodes: [{id, label, side: "payer"|"payee", internal}],
               links: [{source, target, value, stream}]},   # ids; the FE maps them to indices
      conservation: {ok, checks: [{name, ok, detail}]} } },
  conservation_ok, flags, notes, provenance: {tariff_payees, billing, template} }
```

- **Sankey is a DAG by construction** (recharts' Sankey recurses without a visited set and crashes on
  cycles): a **bipartite** layout — payer nodes `p:<id>` on the left, payee nodes `r:<id>` on the right —
  with links aggregated by (payer, payee, stream); zero and `None` values dropped (counted in `flags`). A
  payload test asserts no node is both a source and a target.
- Route `GET /api/results/value_flows` like `get_billing` (409 `solver_in_flight`, 204 when not
  dispatch-ready); **no `value_flows` config → 200 `{status: "not_established", reason}`** (a 204 would read
  as "not solved" in chat).
- Registries: `_HANDLER_PARAMS`, `_LIFTED`, `IC_RESULTS_MODULES` (tripwire), `RESULTS_ENUM` in **both**
  `chat_tools.py` and `chat_tools_schema.py` (31), `_RESULTS_HANDLER_NAMES`, the route inventory and
  `ROUTE_SURFACES` for every new route (`GET /results/value_flows`, `POST
  /simulation/value_flows/template`, `PUT /simulation/commercial/value_flows`, and WP3.7b's `POST
  /results/billing/preview`), the no-snapshot-series exemption, a seam case.
- **Chat:** `define_participants(template? | config?)` → the template route (drafts returned, never saved
  silently) then the value-flows route (tier `write`, the `attach_tariff` guard pattern); `get_results`
  gains a `detail` argument (schema + argshape updated for every kind; ignored where not meaningful); the
  value-flows result is summarised per participant and stream under the 4,000-character cap, full lines
  with `detail="lines"` (paginated); `investment` added to `RESULTS_TAB_ENUM`. New `error_kind`s in
  `tool-error-kinds.json` and the FE manifest. **ADR-0002:** a live probe is owed (with P2's) — a gate
  checkbox.

**Tests:** seam, facade, argshape, endpoint map (31), chat dispatch, manifest, the cap, the Sankey DAG
assertion, the `not_established` and `value_flows_invalid` payloads.

**WP3.4 implementation, part 1 (route and payload).**
- `services/results/value_flows.compute_value_flows(n, cfg, *, result_df, lost_load=None)`: None
  (the route's 204) without a commercial config or a solve; `{status: "not_established", reason:
  "no_value_flows_config"}`; `{status: "value_flows_invalid", reason}`; else the payload above plus
  per period `disclosures` (DSR slack, VoLL — never lines) and `provenance.basis =
  "unweighted_per_period"`, `provenance.template.status` (`template_edited` / `_stale` /
  `_outdated`, also in `flags`). The allocation (WP3.3a) runs inside `build_ledger`, so the plan's
  separate `allocate` step is not a call here.
- `by_participant` totals are `number | null` (a party on a line of unknown amount has null
  `paid` / `received` / `net` and that stream; WP3.1 review #11). The Sankey canonicalises parties
  with `same_party` (one node per party) and counts dropped lines in `sankey_dropped_{unknown,zero}:
  <period>:<n>`.
- **No cache:** the GET on the P1 15-min fixture (V1) is under 2 s (asserted in the test).
- Route `GET /api/results/value_flows` (409 `solver_in_flight`, 204); registered in `_RESULTS_ENUM`,
  `_RESULTS_HANDLER_NAMES`, `RESULTS_ENUM` (31) and the `get_results` description, `_HANDLER_PARAMS`,
  `_LIFTED`, `ROUTE_SURFACES`, the route inventory, the no-snapshot-series exemption and a seam case.
  FE type: nullable totals and `disclosures`.
- Per-asset export parts as line metadata: deferred to WP3.6 (the drill-down is its consumer).
- Tests: `test_results_value_flows.py` (7: V1 flat and multi-period with the DAG assertion and the
  2 s bound; not_established; value_flows_invalid; an unlisted party → None; 204 / 409; a money
  cycle stays bipartite), a ledger test for the null totals.

---

## WP3.5 Frontend foundation

- `src/api/library.ts` (items, series, uploads, meter data, URDB import) with the DTOs in `types.ts`;
  `resultsApi.getBilling`, `getCfeScore`, `getValueFlows`, `previewBilling`; `simulationApi.buildTemplate`,
  `putValueFlows` (with `If-Match`); 204 → null, 409 → a typed `SolverInFlight` error, 412 → a typed
  `StaleEdit` error.
- The `investment` tab: `ResultsTab`, `VALID_TABS`, `TABS`, `RESULTS_TO_COMPARE_TAB` (`'overview'`), the
  render switch; `InvestmentTab.tsx` with sections (Participants, Bill, Library, Tariff, Contracts); the EH
  completeness chips extracted to a shared `CompletenessChips` (EH tests stay green).
- **Commercial writes:** `value_flows` edits go through `PUT …/commercial/value_flows` (server-side
  merge). Editors of other commercial sub-trees (tariff, contracts, agreement) refetch the latest config and
  PUT `{commercial}` with only their sub-tree changed **and `value_flows` stripped** (the server keeps it,
  WP3.0). `SolverSettings` is **unchanged** (it already PUTs a
  diff and has no commercial controls); a test asserts its PUT body omits `commercial` even when the server
  config carries one, including the no-baseline fallback path — if that path sends the full payload, it is
  fixed to strip `commercial`.

**Tests (vitest):** clients (axios mocked like `api/mc.test.ts`), the tab registered and reachable from
`resultsTabRequest`, two editors saving different sub-trees in sequence keep both, the SolverSettings
omission, `tsc` clean.


**WP3.5 implementation.**
- **Review round 1 → PASS WITH CONDITIONS, fixed:**
  - #1 the result clients map a solve-in-flight 409 to `SolverInFlightError`;
  - #2 result requests are quiet (`skipErrorToast`) and a 404 (a route not yet deployed) reads as no result;
  - #3 tests for two editors saving in sequence (both kept, `value_flows` kept) and for the Investment tab reached from a results-tab request (Expert, and Guided's advanced chip);
  - #4 the "never zero" test renders null cells;
  - #5 the bill chip is `ok` only when every period has a total (the note names the missing ones);
  - #6 distinct texts for no result, a failed load and invalid participants (`failed`);
  - #7 no currency symbol (the payload has no currency);
  - #8 `aria-controls` only on the selected tab, Home/End, and a focusable panel;
  - #9 `NoCommercialConfigError`, also for the value-flows route's 409s.
- **Carried forward:**
  - #10 `saveCommercial` is GET-then-PUT, and any save re-binds the commercial config: WP3.6/3.7 handle `library_ref_stale` / `import_tariff_ref_conflict`.
  - #11 WP3.4: `by_participant` sums skip None lines, so its totals should be `number | null` (None when a party has a None line).
  - #12 WP3.6/3.7 editors invalidate the results queries after a save.
- **Review round 2 → PASS WITH CONDITIONS, closed (fixed and covered by tests; no round 3):**
  - A: quiet result requests hid billing failures. The Bill section and chip now show `failed` with "could not be loaded" or "a solve is running". Every caller of a quiet result request must render its own error state: nothing is toasted or logged.
  - B: the 404→no-result mapping is limited to the two routes not yet deployed (`/results/value_flows`, `/results/billing/preview`).
  - C: WP3.7b maps the preview's 422 to fields (already planned).
---

## WP3.6 Participants designer, per-participant table, Sankey

- **Designer:** template picker (drafts shown for confirmation in a `Dialog`), participants table (id,
  name, role), externals, asset owners (asset picker grouped by bus; grid-side generators not assignable),
  tariff payees (per item, the resolved default shown), `export_revenue_to`, hub members + allocation key;
  422 errors mapped per field; 412 → "changed elsewhere, reload".
- **Per-participant table:** paid / received / net per participant and stream, per period (the
  `ResultsFilterProvider` period filter), `basis="annuity"` capex labelled, `None` as "not established" with
  its flag (`UnavailableCell`), CSV export.
- **Sankey:** recharts `Sankey` on the bipartite payload (ids mapped to the indices recharts needs,
  tested), internal vs external colouring, tooltip with
  stream and amount, SVG export, a fixed width in tests (ResponsiveContainer renders 0 in jsdom); an
  incomplete ledger shows `UnavailableBlock` with the flags.
- **Conservation chip:** ok / failed (naming the check) / not established.

**Tests:** designer round trip, 422/412 mapping, table sums, the Sankey from a fixture payload **including
a two-way pair** (dso ↔ developer), conservation states, a11y (`expectAllButtonsNamed`, native checkboxes —
not `PageKit.Toggle`).

---

## WP3.7a Library browser

Items per kind (tariffs, contracts, connection agreements) and series, versions and pins; item view (JSON +
summary); **URDB import** (file → `import_urdb`, `accept_partial`, `cyclic_year`; refusals by field;
`urdb_multiple_rates` → pick `item_index`); series upload (CSV/xlsx, unit, label) and meter data (required
unit; `meter_meta_conflict` 409 shown); "attach as import tariff" with the P2 semantics (an inline tariff
is never replaced silently: a confirm `Dialog`). **Tests:** each flow with mocked API; refusal and conflict
states; a11y.

## WP3.7b Tariff builder + bill preview

Items / periods / tiers (per-period `tier_rates` for windowed items) / ratchets (range, cyclic, months) /
settlement / measured_on / direction; minimal client checks, **the server is the source of truth** (422
mapped to fields). **Bill preview** `POST /api/results/billing/preview {tariff}`: parse the draft and check it with
`lp_bindings.validate_for_network` (never `bind_commercial`, which writes series and the FCA registry),
hold the network lock, 409 during a
solve, 204 when not dispatch-ready; call `bill_site` with `state=None`; **replace the drift flags** with
`preview_dispatch_not_optimised_for_draft` (the dispatch was optimised for the attached tariff, not the
draft); no gap computed (stated); no state written. Save to Library or inline. **Tests:** the builder
reproduces `h3_us_ci.json`'s tariff exactly; the preview handler: no state change, the preview flag, no
drift flags, 409; FE preview rendering.

## WP3.7c Contracts and connection-agreement editors

Typed forms per contract type (P2 models, allowed pricing combinations; party pickers from participants +
externals), the connection agreement (kind, caps, envelope ref, capacity-fee item through the Tariff
builder's item editor, `available_from`, group); Library pins shown. **Tests:** each type round-trips to the
P2 model; invalid combinations surface the 422; a11y.

---

## Phase 3 e2e QA gate

- [ ] `backend/tests/qa_value_flows.py` (auto-discovered): V1–V4 through the routes (template → drafts
  confirmed → value-flows route → solve → `/results/value_flows`): all four checks on every template,
  reconciliation to `cost_breakdown` to the cent; V5 hand splits to the cent under all four keys; V6
  group net-import gap 0 and LP row = billed; a swapped-direction and a dropped-source corruption detected;
  a bundle round trip keeps `value_flows` and the ledger identical; the P1 and P2 drivers still pass.
- [ ] Frontend: vitest (including the Sankey two-way case and the a11y checks) and `tsc` green.
- [ ] Full backend `not slow`, all QA drivers green; findings note
  `docs/superpowers/findings/<date>-ic-p3-participants.md`; assessor verdict recorded here.
- [ ] ADR-0002: the live probe for P2's and P3's chat changes run and recorded, or stated as owed in the
  verdict (the chat surface is then not done).
- [ ] `notYetDeployed` (frontend `api/commercial.ts`) removed once `/results/value_flows` and
  `/results/billing/preview` ship (WP3.5 review round 2 B).

## Scope boundaries (not P3)

- Annual cashflows, escalation, degradation, debt, tax, incentives, returns — P4 (mapping pinned in WP3.1).
- Multi-party co-optimisation — later, behind a flag (decision 9).
- DR on BESS / generators — P5 (templates use loads).
- Allocation over billing periods longer than a month; a ratchet floor seeded only from meter history
  (`allocation_not_established`).
- Annual per-connection tier bands (P2 gate): a documented limitation; importer/preflight disclosure in P4.
- Realistic-dispatch ledgers — P6 (the ledger is on the current dispatch, `mode="pf"`).
- Net **revenue** items on the group net meter — kept on gross export with a disclosed gap cause.

---

## Plan review

**Round 1 (FAIL; 8 HIGH, 9 MEDIUM, 5 LOW) → v0.2.**
- **HIGH:**
  - H1 export revenue from `ic_export_price`, never a cost row;
  - H2 connection fees and curtailment compensation as sources;
  - H3 grid-side generators as the commodity (`market`), POC_ROLES claim removed;
  - H4 templates never change `site_party`; the hash test covers `contracts_record` / `ppa_dispatch_hash`; the adequacy hash excludes `value_flows`;
  - H5 payee precedence, `retailer` / `dso` externals, `same_party`;
  - H6 coverage and reconciliation to `cost_breakdown` added (checks 3–4);
  - H7 equality split, bounds, cost-only, removal from adders, no billing change;
  - H8 bipartite Sankey DAG.
- **MEDIUM:**
  - M1 `asset_economics` VOM, per-period fixed basis, no fuel split;
  - M2 `basis="annuity"`, excluded from P4 conversion;
  - M3 signed site view;
  - M4 per-member metering for linear energy, per-period energy key, settlement-interval peaks, tie, ratchet month, direction, sorted remainder, export allocation;
  - M5 BTM ownership limited to PPA assets, EaaS draft, `export_revenue_to`;
  - M6 route-level validation, `None` party rule;
  - M7 dedicated value-flows route with server merge and `If-Match`, SolverSettings unchanged;
  - M8 preview semantics;
  - M9 single participants source, P4 mapping.
- **LOW:**
  - L1 WP3.3b split out;
  - L2 `template_version` / `built_digest` / `template_stale`, drafts;
  - L3 all new routes in the registries, both enums, `detail` argument, `not_established` payload;
  - L4 gate checkboxes, cache;
  - L5 levy semantics, `ppa_excess_mwh` disclosed.

**Round 2 (PASS WITH CONDITIONS; C1–C3 binding) → v0.3.**
- **C1** check 4 rewritten:
  - the export-price revenue is its own term;
  - DSR and VoLL are disclosures, not bridge terms;
  - only lines with an external party enter;
  - the basis is stated (`by_period` ÷ years; flat top-level).
- **C2** assets are enumerated from the `physical_quantities` superset plus Lines and Transformers (not `asset_economics`):
  - per-period fixed cost and VOM use `cost_breakdown`'s arithmetic;
  - unclassified assets are flagged, never dropped;
  - grid-side assets of any class are included;
  - V1b added.
- **C3** asset owners must be participants.
- **C4** no capacity bounds; the record is recomputed from dispatch; the `_side` replacement and solve-strategy coverage are stated.
- **C5** `allocate` recomputes the peak interval and the floor month (no engine change).
- **C6** per-member rating through the engine with the group's settings; residual ≤ 1e-9.
- **C7** `PUT /solver_config` keeps a stored `value_flows` the body omits; editors strip it.
- **C8** `value_flows` is stored raw and validated lazily; a corrupted value still solves; 409 `no_commercial_config`.
- **C9** no cache unless measured; if one is added, the full key is specified.
- **C10** preview uses `validate_for_network`.
- **C11** export attribution covers both sources, per interval; V2 extended.
- **C12** provenance carries the source ids; P4 reads `asset_owners` for overnight capex.
- **C13** id→index mapping tested; the DAG payload assertion.
- The adequacy hash pops `value_flows` whatever its value.

**Round 3 (PASS WITH CONDITIONS; none blocking WP3.0) → v0.4, all closed in text.** The reviewer walked check 4 on V1 and V4 term by term and both close; C1–C13 verified. Closed here:
- **D1** a meter-side classifier (site, grid, bypass, unclassified; meter Links site-side) replaces the bare `_meter_sides`, which cannot place the V1 grid generator;
- **D2** the `PUT /solver_config` mechanism (`model_fields_set` before bind) and an explicit `value_flows` there refused;
- **D3** grid-side fixed cost is `basis="model_only"`;
- **D4** VOM from `n.statistics()` operational expenditure;
- **D5** external↔external lines excluded and noted; an unresolved party makes check 4 `None`;
- **D6** the bridge subtracts every `commercial_cost_terms` item;
- **D7** the floor's source month is the argmax over `_ratchet_floor_prior`'s candidate months, earliest on ties.

**Plan status: PASS** (round 3 conditions closed in text; no open plan-level items). Implementation starts at WP3.0.
