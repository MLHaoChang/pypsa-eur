# Edge Investment Case — Phase 3: participants and value flows (plan v0.2)

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
| V2 | `btm_ppa`: PV sold as consumed to the site; BESS under an EaaS contract; exported PV attributed to the developer (`export_revenue_to="asset_owner"`) | developer receives PPA + EaaS + its export share, pays PV/BESS costs; site pays bill + PPA + EaaS; hand formula for the export split |
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
4. **Reconciliation to independent truth:** Σ over participants of net outflow to externals =
   `cost_breakdown` total for the period (unweighted)
   − the LP commercial rows (`commercial_cost_terms`) + the billed tariff (`per_item`)
   + the connection fees (the `included_in_total: False` rows too) + contract lines with an external party
   − excluded terms, each named with a flag: the DSR slack cost (P2 gate carry: cash vs opportunity is P4's
   decision) and VoLL shedding. Closes to the cent. This check fails on an omitted source, a double-counted
   one and a swapped direction.

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
    asset_owners: list[AssetOwnership] = [],   # unassigned site-side assets → site_party (note)
    hub_members: list[HubMember] = [],         # energy hub only
    allocation: AllocationKey | None = None,   # energy hub only
    export_revenue_to: Literal["site_party", "asset_owner"] = "site_party",
)
CommercialConfig.value_flows: ValueFlowConfig | None = None
```

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
  commercial config **server-side** under the solver-state lock, validates parties only (no
  `bind_commercial`: no network validation, Library resolution or series writes), takes an `If-Match`
  digest of the current `value_flows` (412 on mismatch), 409 `solver_in_flight` during a solve. The chat
  tool uses the same route.
- **Hashing:** `("CommercialConfig", "value_flows"): None` in `FIELDS_AFTER_V1`. The adequacy
  `_config_hash` (`services/adequacy/report.py:55`, `asdict(cfg)`) excludes `commercial.value_flows`, so a
  participant edit does not change an AdequacyReport's `assumptions_hash`.
- **Single source of participants:** `ValueFlowConfig.participants`. `FinanceInputs.participants`
  (`models/finance.py:176`) is filled from it in P4; a P0 fixture that sets it keeps validating (it is
  documented as derived).
- **types.ts:** `Participant`, `ParticipantRole`, `ValueStreamKind`, `AllocationKey`, `ValueFlowConfig` and
  parts; the typed contract variants (`PpaContract` … `RetailContract`) with `CommercialContract` kept as
  their union; `CommercialConfig.value_flows`.

**Tests:** round trip; every P2 driver config validates unchanged; the route's party cases (typo, case,
disjointness, stale party at ledger time → flag, not an invalid config); `If-Match` 412; the
**hash-invariance test**: flip `value_flows` and assert unchanged demand, tiers, PoC links, agreement,
`ppa_dispatch_hash`, `contracts_record`, no `config_changed_since_solve`, and an unchanged adequacy
`assumptions_hash`; the FIELDS_AFTER_V1 inventory test.

---

## WP3.1 Ledger: sources, lines, coverage, reconciliation

`services/commercial/participants.py` (pure; imports `models` and `services.commercial` only):

```python
ValueFlowLine(period: str, payer: str, payee: str, value_stream: ValueStreamKind, source: str,
              source_id: str, tariff_item: str | None, tariff_item_kind: str | None,
              contract_id: str | None, asset: str | None, basis: Literal["cash","annuity"],
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
| Grid-side generators (`lp_bindings._meter_sides`: not behind the meter), their VOM from `asset_economics` | site_party → `market`, flag `commodity_from_grid_side_generator`; never an owned asset | `energy_import` |
| Settlement lines (P2 `settlement_lines`) | payer → payee; a negative amount reversed | ppa_energy, ppa_sleeving_fee → `ppa_settlement`; cfd_difference → `cfd_settlement`; dr_availability / dr_activation → same; lease_payment → `lease`; eaas_fee → `eaas_fee`; unknown → `other` + `unmapped_stream:<s>`. `ppa_excess_mwh` is a volume with no money: disclosed in `notes`, no line |
| Site-side assets, `asset_economics` per period | owner → `capex_supplier` (annualised capex = per-horizon rate × `p_nom_opt` × active(period), `basis="annuity"`); owner → `om_contractor` (FOM, same basis, `basis="cash"`); owner → `om_contractor` (`vom_cost_eur`: PyPSA has no VOM/fuel split, so one `vom` line; storage on discharge, Links on p0 — the `asset_economics` conventions that reconcile exactly to `cost_breakdown.opex`) | `capex`, `fom`, `vom` |

- **No double counting:** the LP's `energy_import` / `energy_export` / `demand_charge` / tier / capacity
  cost rows are never ledger sources (they are the LP's view; P2's gap compares them). A `changes_dispatch`
  PPA appears once (its settlement line). The tariff's export items and the export price are separate
  sources (the LP's `energy_export` row mixes them).
- **Excluded, named:** the DSR slack cost and VoLL shedding (bridge terms of check 4, flags
  `dsr_slack_not_a_cash_flow`, `voll_not_a_cash_flow`).
- **Per period:** one ledger per investment period; money unweighted.
- **P4 mapping (pinned now):** a `ValueFlowLine` becomes one `CashflowLine` per operating year with
  `participant` = the side whose cash it is (both sides for internal lines), `counterparty` = the other,
  `amount` signed from the participant; `basis="annuity"` lines are **not** converted (P4 uses
  `overnight_cost` + `capex_phasing`); a `None` line makes that participant's returns `not_established`
  in P4 (never 0).

**Tests (TDD, `tests/test_value_flow_ledger.py`):** each source row; sign reversals (negative bill, negative
settlement, negative export price); `None` kept; the stream map; party resolution; the four checks, each
failing on its own corruption (dropped source, duplicated source, swapped direction, amount off by 0.01,
phantom internal party); V1 reconciliation to the cent through the bridge; a tariff export item **and**
`export_price_ref` together (no double count).

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

---

## WP3.3a Energy hub: allocation

`participants.allocate(ledger, hub_inputs, vf)` splits the hub's shared lines into internal lines between
`hub` (= site_party) and each member; `hub` then nets to its external payments.

- **Linear import energy items** (energy items without tiers, measured on import): **metered per member**
  exactly — member import × the item's rate per interval (the engine's `lines`), not a key. A share key
  would misallocate TOU costs.
- **Keyed items** (demand, capacity, tiers, fixed, net items, the connection fee, export revenue):
  - `contracted_capacity` — `HubMember.contracted_mw` (all set, sum > 0, else `allocation_not_established`);
  - `peak_contribution` — each member's import in the group's billed interval per demand window and month,
    on the item's **settlement-interval means** (the engine's `interval_key`), so shares re-add to the
    billed kW; ties → the first maximal interval; when a ratchet floor binds, the contributions of the
    month that set the floor (read from `demand_lines`); a floor from meter history (no modelled month) →
    `allocation_not_established:<item>`; non-demand items under this key fall back to `energy` (disclosed);
  - `energy` — per-period member import energy (Σ w·p0 per member in the period), **not** the
    years-weighted `ic_group.energy_share`;
  - `fixed_shares` — the key's shares.
- **Direction:** a cost share runs member → hub; a revenue share (export, a negative line) hub → member.
- **Remainder:** shares in exact floats; the last member in **sorted-id order** takes `item − Σ others`.

**Tests:** V5 under all four keys with the working in the fixture; remainder exactness; tie rule; the
ratchet-floor month; missing-key refusals; conservation checks 3–4 still close after allocation.

---

## WP3.3b Group net-import LP variable (P2 carry-in)

P1 refuses a `measured_on="net"` energy item on a multi-member group with an export Link
(`lp_bindings.validate_for_network`, "gross member import the group meter nets out"). Lift it for the
**cost** direction:

- Variables `ic_group_net_import[t]`, `ic_group_net_export[t]` ≥ 0 with the **equality**
  `net_import − net_export = Σ_members p_member − p_export`, each bounded by Σ member `p_nom` / the export
  `p_nom` (no unbounded direction).
- A net **cost** item is priced on `net_import` only when every rate is ≥ 0 (else refused, naming the
  item); the item is **removed from the per-member adders and from the export adders**, so it is priced
  once. A net **revenue** item keeps the existing gross-export pricing with the `net_split_by_direction` gap
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

---

## WP3.4 `/results/value_flows`, chat

`services/results/value_flows.py` `compute_value_flows(n, cfg, *, result_df)` builds `LedgerInputs` from
`bill_site` / `settlement_lines` (the `compute_billing` pieces), the connection meta, the export price, the
meter sides and `asset_economics`, then runs `build_ledger`, `allocate`, `check_conservation`. **Cache:**
the ledger is stored beside the billing frames (`billing_frames` key, WP0.5) keyed by the billing
provenance + the `value_flows` digest, so a GET does not re-bill a 15-min year (measured; the cache is
required if a GET exceeds 2 s on the P1 15-min fixture).

Payload:

```
{ status: "ok" | "not_established", reason?, participants, externals, template, template_version,
  periods: { "_"|"<year>": {
      lines: [ValueFlowLine...],
      by_participant: {pid: {paid, received, net, by_stream: {kind: net}}},
      sankey: {nodes: [{id, label, side: "payer"|"payee", internal}],
               links: [{source, target, value, stream}]},
      conservation: {ok, checks: [{name, ok, detail}]} } },
  conservation_ok, flags, notes, provenance: {tariff_payees, billing, template} }
```

- **Sankey is a DAG by construction** (recharts' Sankey recurses without a visited set and crashes on
  cycles): a **bipartite** layout — payer nodes `p:<id>` on the left, payee nodes `r:<id>` on the right —
  with links aggregated by (payer, payee, stream); zero and `None` values dropped (counted in `flags`).
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

**Tests:** seam, facade, argshape, endpoint map (31), chat dispatch, manifest, the cap, the cache hit/miss
and invalidation on a `value_flows` edit.

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
  PUT `{commercial}` with only their sub-tree changed. `SolverSettings` is **unchanged** (it already PUTs a
  diff and has no commercial controls); a test asserts its PUT body omits `commercial` even when the server
  config carries one, including the no-baseline fallback path — if that path sends the full payload, it is
  fixed to strip `commercial`.

**Tests (vitest):** clients (axios mocked like `api/mc.test.ts`), the tab registered and reachable from
`resultsTabRequest`, two editors saving different sub-trees in sequence keep both, the SolverSettings
omission, `tsc` clean.

---

## WP3.6 Participants designer, per-participant table, Sankey

- **Designer:** template picker (drafts shown for confirmation in a `Dialog`), participants table (id,
  name, role), externals, asset owners (asset picker grouped by bus; grid-side generators not assignable),
  tariff payees (per item, the resolved default shown), `export_revenue_to`, hub members + allocation key;
  422 errors mapped per field; 412 → "changed elsewhere, reload".
- **Per-participant table:** paid / received / net per participant and stream, per period (the
  `ResultsFilterProvider` period filter), `basis="annuity"` capex labelled, `None` as "not established" with
  its flag (`UnavailableCell`), CSV export.
- **Sankey:** recharts `Sankey` on the bipartite payload, internal vs external colouring, tooltip with
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
mapped to fields). **Bill preview** `POST /api/results/billing/preview {tariff}`: parse and bind-validate
the draft against the network (export link, timezone, power factor), hold the network lock, 409 during a
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
