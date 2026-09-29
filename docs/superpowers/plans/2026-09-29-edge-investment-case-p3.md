# Edge Investment Case — Phase 3: participants and value flows (plan v0.1)

**Spec:** `docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` §7 (participants), §12 (UI), §13
(P3 row), §15 (conservation doctrine), decision 4 (participants first-class), decision 9 (splits never enter
the objective). **Outline:** P0–P1 plan, "P3 Participants & value flows". **Carried in from P2:**
the UI for the Tariff builder, Library browser and Contracts editor (P2 scope boundaries); the group
net-import variable for net energy items on multi-member groups (P2 scope boundaries, "energy-hub
template"). **Branch:** `claude/energy-tool-features-research-fdixs0`.

**Process (owner's instruction, spec §13):** this plan → review loop until PASS → per WP: TDD, then an
implementation-review loop until PASS → Phase 3 e2e QA gate (a `qa_value_flows.py` driver discovered by
`run_qa_drivers.py`, frontend vitest + `tsc`, full backend suite, findings note, independent assessor)
before P4.

---

## What P3 delivers

A **value-flow ledger**: every money flow the tool can already compute — the exact tariff bill per item
(P2 billing pass), every contract settlement line (P2), and every asset's annualised capex, FOM, VOM and
fuel (P0 physical-quantity seam) — as a double-entry line **payer → payee** with a `ValueStreamKind`, per
modelled period, **unweighted** (one period-year; P4 applies years, escalation and discounting). Parties are
**participants** (internal) or **external counterparties** (grid, market, tax authority, suppliers). A
**template** fills the participants and the assignments for the five spec §7 structures. The ledger feeds a
per-participant table and a Sankey in a new Results tab `investment`, which also hosts the editors whose APIs
landed in P2 (Library browser, Tariff builder, Contracts and connection-agreement editors).

**Not P3:** money over time (years, escalation, discounting, debt, tax) — P4 turns ledger lines into annual
`CashflowLine`s; multi-party co-optimisation (decision 9: the ledger never enters the LP objective).

## Dependency graph

```
WP3.0 value-flow config + hashing registration + types.ts
 ├─ WP3.1 ledger: sources → lines (tariff, contracts, assets, export) + conservation + reconciliation
 │   ├─ WP3.2 templates (single_owner, btm_ppa, landlord_tenant, dso_developer, energy_hub)
 │   │   └─ WP3.3 energy hub: allocation keys + group net-import LP variable (P2 carry-in)
 │   └─ WP3.4 /results/value_flows + chat (define_participants, get_results enum, tab navigation)
 └─ WP3.5 FE foundation: api/library.ts, results clients, typed contracts, `investment` tab shell,
     commercial-config single writer
      ├─ WP3.6 Participants designer + per-participant table + Sankey
      ├─ WP3.7a Library browser (items, series, URDB and meter-data import)
      ├─ WP3.7b Tariff builder + bill preview (POST /results/billing/preview)
      └─ WP3.7c Contracts and connection-agreement editors
```

**Per-WP invariants (all of P3).** The P1 reconciliation gate and the P2 billing gate stay green after every
WP (the driver `qa_billing_contracts.py` runs in each WP's verification). No ledger input changes the LP or
any committed commercial hash (decision 9): a test per WP flips the value-flow config and asserts every
recipe hash and `objective_decomposition` are unchanged. Unknown is `None` + a flag, never 0 (ADR-0001).
Every new persisted field is backward compatible (P2 configs validate unchanged) and mirrored in
`frontend/src/api/types.ts`. Services never import routers or `solver_service` (tripwires).

---

## Fixtures and oracles (bind the gate)

| Id | What | Expected |
|---|---|---|
| V1 | `single_owner` on the P1 edge fixture (`build_edge_15min`, US tariff, PV + BESS) | every line's payer is the owner or an external party; Σ owner outflows to externals = billed total + Σ asset costs + contract lines with externals, to the cent |
| V2 | `btm_ppa`: developer owns PV + BESS; site buys PV as consumed (`as_consumed_btm`) | developer receives the PPA line and pays the PV/BESS capex/opex; the site pays the bill and the PPA; internal lines (site ↔ developer) net to 0 across the two |
| V3 | `landlord_tenant` with a `LeaseContract` | landlord pays capex, receives the lease; tenant pays lease + bill |
| V4 | `dso_developer` with a `DrContract` (counterparty `dso`) on an active DSR bus | DSO → developer availability + activation lines, hand formula (P2 C-style) |
| V5 | `energy_hub`: 3 members on a group connection, **hand-computed** splits under each allocation key (contracted capacity, peak contribution, energy, fixed shares), 15-min, 7 days | each member's share of every shared item to the cent (self-authored fixture with the working, like H1–H3) |
| V6 | group net-import: a net energy item on a 3-member group with an export Link | LP cost row = billed item to the cent; objective gap 0 (was refused in P1/P2) |

"Conservation" (spec §7, §15) is checked three ways, all asserted per period:
1. **double entry** — every line has exactly one payer and one payee, `payer != payee`, a finite amount or
   `None` + flag; Σ over all parties of net position = 0 (a bookkeeping identity; guards the code, not the
   economics);
2. **internal streams net to zero** — Σ over internal participants of the net of lines whose payer AND
   payee are internal = 0 (spec §7 literally);
3. **reconciliation to the sources** — the ledger re-adds to its inputs: per tariff item, the lines sum to
   the billed item (`bill_site`); per contract, to the settlement line; per asset, to the seam's
   annualised capex + FOM + VOM/fuel; per shared item under allocation, the member shares sum to the group
   item. Tolerance: to the cent (`abs < 0.005`), on money that is itself finite.

`conservation_ok` (the existing `GatesBlock` field, P0) is `True` only when all three hold on every period;
any `None` line makes it `None` + `ledger_incomplete:<n>` (never `True` over unknown money).

---

## WP3.0 Value-flow config, hashing registration, types

**Model** (`models/commercial.py`):

```python
TariffPayeeRule(kind: TariffItemKind | None = None, item_id: str | None = None, payee: str)  # item_id wins
AssetOwnership(asset_id: str, component: Literal["Generator","StorageUnit","Store","Link"], owner: str)
HubMember(link: str, participant: str, contracted_mw: float | None = None)
ValueFlowConfig(
    template: Literal["single_owner","btm_ppa","landlord_tenant","dso_developer","energy_hub","custom"]
              = "custom",
    template_hash: str | None = None,          # set by the template builder; drift if edited after
    participants: list[Participant] = [],      # internal parties; ids unique; site_party must be one
    externals: list[str] = ["grid", "market", "tax_authority", "capex_supplier",
                            "om_contractor", "fuel_supplier"],
    tariff_payees: list[TariffPayeeRule] = [],  # default rule below
    asset_owners: list[AssetOwnership] = [],   # unassigned assets → site_party (disclosed note)
    hub_members: list[HubMember] = [],         # energy hub only
    allocation: AllocationKey | None = None,   # energy hub only
)
CommercialConfig.value_flows: ValueFlowConfig | None = None
```

- **Default tariff payees** (no rule matches): `energy`, `fixed`, `certificate` → `Tariff.dso_or_retailer`
  or `"retailer"`; `demand`, `capacity` → `"dso"`; `tax_levy` → `"tax_authority"`. The resolved payee of
  every item is echoed in the ledger's `provenance.tariff_payees` (never silent).
- **Validation:** participant ids unique and disjoint from `externals`; every party named by a contract
  (`seller`, `buyer`, `lessor`, …), an ownership or a hub member is a participant or an external — else the
  config route answers 422 naming the party (a typo would otherwise create a phantom party that conserves
  trivially); `site_party` ∈ participants when `value_flows` is set; `hub_members` and `allocation` only with
  `template ∈ {energy_hub, custom}` and a group contract; `AllocationKey.fixed_shares` keys = hub
  participants.
- **Hashing:** `("CommercialConfig", "value_flows"): None` in `FIELDS_AFTER_V1`. No LP record hashes
  `value_flows`; `tests/test_value_flow_config.py` flips it and asserts every committed commercial hash
  (demand, tiers, PoC links, agreement, PPA dispatch, settlement) is unchanged, and `cost_rows` raises no
  `config_changed_since_solve`.
- **types.ts:** `Participant`, `ParticipantRole`, `ValueStreamKind`, `AllocationKey`, `ValueFlowConfig` and
  its parts; the typed contract variants (`PpaContract`, `CfdContract`, `DrContract`, `LeaseContract`,
  `EaasContract`, `RetailContract`) replacing the loose `CommercialContract` (kept as the union type);
  `CommercialConfig.value_flows`.

**Tests:** model round trip; P2 configs validate unchanged (fixture: every P2 driver config); the party
validation cases; the hash-invariance test; the FIELDS_AFTER_V1 inventory test passes.

---

## WP3.1 Ledger: sources → lines, conservation, reconciliation

`services/commercial/participants.py` (pure; imports `models` and `services.commercial` only):

```python
ValueFlowLine(period: str, payer: str, payee: str, value_stream: ValueStreamKind, source: str,
              source_id: str, tariff_item: str | None, contract_id: str | None, asset: str | None,
              amount: float | None, flags: list[str])
build_ledger(inputs: LedgerInputs, vf: ValueFlowConfig) -> Ledger
conservation(ledger, inputs) -> ConservationResult(ok: bool | None, checks: list[Check], flags)
```

`LedgerInputs` is built by the caller in `services/results/value_flows.py` (WP3.4) from:

| Source | Lines | Stream mapping |
|---|---|---|
| `bill_site` per period, `per_item` (exact) | one line per tariff item: a `cost` item site_party → payee; a `revenue` item payee → site_party; amount = \|billed\| | kind → stream: energy (import) → `energy_import`, energy (export / revenue) → `energy_export`, demand → `demand_charge`, capacity → `network_capacity`, fixed → `retail_fixed`, certificate → `certificates`, tax_levy → `tax`; a DSO-paid `energy` item → `network_energy` |
| P2 settlement lines (`settlement_lines`) | as is (payer, payee, amount sign rule of `contracts.py`: a negative amount flips payer and payee so every ledger amount is ≥ 0) | ppa_energy / ppa_excess_mwh → `ppa_settlement`, ppa_sleeving_fee → `ppa_settlement`, cfd_difference → `cfd_settlement`, dr_availability / dr_activation → same, lease_payment → `lease`, eaas_fee → `eaas_fee`; an unknown string → `other` + flag `unmapped_stream:<s>` |
| Export price (`export_price_ref`, cost row `energy_export`) | `market` → site_party | `energy_export` |
| Physical seam per asset (`physical_quantities`) | owner → `capex_supplier` (annualised capex), owner → `om_contractor` (FOM), owner → `fuel_supplier` (VOM + fuel from marginal cost × energy) | `capex`, `fom`, `vom` / `fuel` |

Rules:
- A tariff item billed `None` (unrated, not established) → a line with `amount=None` and the engine's flag;
  it is never dropped.
- A party that is neither a participant nor an external (a contract edited after validation, a stale
  ownership) → `party_not_established:<name>`, amount kept, the line excluded from conservation check 2 and
  counted in `ledger_incomplete`.
- **No double counting:** the PoC import energy is billed once (the tariff); the LP's `energy_import` cost
  row is NOT a ledger source (it is the LP's view, compared in the P2 gap). A PPA with `changes_dispatch`
  appears once (its settlement line). Asset marginal costs of the grid-supply generator (the PoC price
  proxy in P1 fixtures) are excluded by `POC_ROLES` (the seam already tags them).
- Money is per period, unweighted (like `cost_rows`); multi-period networks get one ledger per investment
  period.
- `physical_quantities` imports `solver_service` (periodized capital costs), so `services/commercial` never
  calls it: the results caller passes plain per-asset frames in `LedgerInputs` (tripwire stays green).

**Tests (TDD, `tests/test_value_flow_ledger.py`):** each source maps as tabled; revenue items flip; a
negative settlement flips; `None` bills stay `None`; unknown party flagged; the three conservation checks
fail on a corrupted ledger (drop a line, flip a sign, change an amount by 0.01, add a phantom internal
party); reconciliation to the cent on V1.

---

## WP3.2 Templates

`services/commercial/value_flow_templates.py`: `TEMPLATES: dict[str, TemplateBuilder]`, each a pure
function `(network_summary, commercial) -> ValueFlowConfig` plus `template_hash` (sha256 of the builder's
canonical payload, the pack recipe of `services/finance/packs/base.py`). Builders never invent contracts:
a template that needs one reports `template_needs_contract:<type>` and leaves the config `not_established`.

| Template | Participants | Ownership / flows |
|---|---|---|
| `single_owner` | `site` (site_owner) | every asset owned by `site` |
| `btm_ppa` | `site` (offtaker), `developer` | on-site generators and storage owned by `developer`; needs an `as_consumed_btm` or `pay_as_produced` PPA seller=developer, buyer=site |
| `landlord_tenant` | `landlord`, `tenant` (= site_party) | assets owned by `landlord`; needs a `LeaseContract` lessor=landlord, lessee=tenant |
| `dso_developer` | `developer` (= site_party), `dso` as a **participant** (it pays) | assets owned by `developer`; needs a `DrContract` counterparty=dso |
| `energy_hub` | one `hub_member` per group member Link + `hub` (the group contract holder, = site_party) | members own the assets on their bus; shared items allocated by `allocation` (WP3.3) |

`POST /api/simulation/value_flows/template` — `{template}` → the built `ValueFlowConfig` (not saved; the
client shows it, then saves through the solver-config route). **Tests:** each template on its fixture;
the missing-contract refusal; the hash is stable and changes when a builder changes (pinned in
`tests/fixtures/investment_case/value_flow_template_hashes.json`); editing a built config makes
`template_edited` a disclosure (template hash kept, config differs).

---

## WP3.3 Energy hub: allocation keys and the group net-import variable

**Allocation** (`participants.allocate`): the group's shared lines (every tariff item billed on the group
meter, the group capacity fee, contracts whose party is `hub`) are split into per-member internal lines
`member → hub` by the key, so `hub` nets to its external payments and each member carries its share:

- `contracted_capacity` — `HubMember.contracted_mw` (all set, sum > 0, else `allocation_not_established`);
- `peak_contribution` — each member's import at the **group's** billed peak interval per demand window
  (coincident), for demand and capacity items; energy items fall back to `energy` (disclosed);
- `energy` — years-weighted import energy per member (`n.meta["ic_group"]["energy_share"]`, committed in P1);
- `fixed_shares` — the key's shares.

Remainders: shares are computed in exact floats and the last member takes `item − Σ others`, so the split
re-adds to the item exactly (conservation check 3).

**Group net-import variable (P2 carry-in).** P1 refuses a `measured_on="net"` energy item on a
multi-member group with an export Link (`lp_bindings.validate_for_network`, the "gross member import the
group meter nets out" refusal). Implement it: a non-negative `ic_group_net_import[t] ≥ Σ_members p_member[t]
− p_export[t]` (and `ic_group_net_export[t] ≥ p_export − Σ p_member`), the net item priced on the variable
(cost direction) or on the export side (revenue direction), cost row `energy_net_group` for every term,
objective decomposition gap 0. The billing adapter meters the group net the same way (`bill_site` with the
group meter = Σ member import − export per interval, floored in the billing direction, as the engine's
`net` rule). The refusal is removed only for this case; the other group checks stay.

**Tests:** V5 (hand splits under all four keys, to the cent, with the working in the fixture), remainder
exactness, missing-key refusals, V6 (LP row = billed to the cent, gap 0, a reconciliation case in
`test_commercial_objective_reconciliation.py`), the refusal still raised for the unchanged cases.

---

## WP3.4 `/results/value_flows`, chat

`services/results/value_flows.py` `compute_value_flows(n, cfg, *, result_df)` — builds `LedgerInputs` from
`compute_billing`'s own pieces (`bill_site`, `settlement_lines`) and the physical seam, runs
`build_ledger` + `conservation`. Payload:

```
{ participants: [...], externals: [...], template, template_hash,
  periods: { "_"|"<year>": {
      lines: [ValueFlowLine...],
      by_participant: {pid: {paid, received, net, by_stream: {kind: net}}},
      sankey: {nodes: [{id, label, internal}], links: [{source, target, value, stream}]},
      conservation: {ok, checks: [{name, ok, detail}]} } },
  conservation_ok, flags, provenance: {tariff_payees, billing: <compute_billing provenance>} }
```

Sankey links aggregate lines by (payer, payee, stream); `None` lines are excluded from the Sankey and
counted in `flags`. The route `GET /api/results/value_flows` follows `get_billing` (409
`solver_in_flight`, 204 when not dispatch-ready or no `value_flows` config). Registries: `_HANDLER_PARAMS`,
`_LIFTED`, `IC_RESULTS_MODULES` (tripwire), `RESULTS_ENUM` (31), `_RESULTS_HANDLER_NAMES`, the route
inventory, `ROUTE_SURFACES`, the no-snapshot-series exemption, a seam case (golden network → 204).

**Chat:** `define_participants(template | config)` — builds from a template or validates a custom config
and saves it through the solver-config route (tier `write`, same guard tests as `attach_tariff`);
`get_results("value_flows")` with a result cap (lines summarised per participant and stream when over
4,000 characters, the full list behind `detail="lines"`); `investment` added to `RESULTS_TAB_ENUM` so chat
can open the tab. New `error_kind`s in `tool-error-kinds.json` and the FE manifest. **ADR-0002:** a live
probe is owed (with P2's), recorded before the chat surface is called done.

**Tests:** results seam, facade, argshape, endpoint map (31), chat dispatch, manifest, the cap.

---

## WP3.5 Frontend foundation

- `src/api/library.ts`: typed clients for every `/api/library/*` route (items list/get/put, series
  list/get/post, `series/upload`, `meter_data`, `items/tariff/import_urdb`), with the DTOs (`ItemOut`,
  `SeriesOut`, `UrdbImportOut`, `MeterDataOut`) in `types.ts`.
- `resultsApi.getBilling`, `getCfeScore`, `getValueFlows`, `previewBilling` (204 → null; 409 → a typed
  `SolverInFlight` error).
- The `investment` tab: `ResultsTab` union, `VALID_TABS`, `TABS` (lucide icon, tip), 
  `RESULTS_TO_COMPARE_TAB` (`'overview'`), the render switch; `InvestmentTab.tsx` with sections
  (Participants, Bill, Library, Tariff, Contracts) and completeness chips — extract the EH chips into a
  shared `CompletenessChips` component (EH panel reuses it; its tests stay green).
- **Commercial config single writer:** `useCommercialConfig()` — reads `solverConfig`, and every save
  refetches, merges the edited sub-tree into the **latest** `commercial` and PUTs only `{commercial}`;
  `SolverSettings` never sends `commercial` (it has no commercial controls; a test asserts its PUT body
  omits the key even when the server config carries one) and refreshes its baseline on query invalidation.
  Test: two editors saving different sub-trees in sequence keep both.

**Tests (vitest):** clients (axios mocked like `api/mc.test.ts`), the tab registered and reachable from
`resultsTabRequest`, the single-writer merge, `tsc` clean.

---

## WP3.6 Participants designer, per-participant table, Sankey

- **Designer:** template picker (`POST …/value_flows/template`), participants table (id, name, role),
  externals, asset owners (asset picker from the network, grouped by bus), tariff payees (per item, default
  shown), hub members + allocation key (energy hub); validation errors from the 422 shown per field; save
  through `useCommercialConfig`.
- **Per-participant table:** paid / received / net per participant and stream, per period (period filter
  from `ResultsFilterProvider`), CSV export (`downloadCSV`), `None` shown as "not established" with the flag
  (`UnavailableCell`).
- **Sankey:** recharts `Sankey` (no new dependency), nodes coloured internal vs external, link tooltip with
  stream and amount, SVG export (`downloadSVG`); an empty or incomplete ledger shows `UnavailableBlock` with
  the flags.
- **Conservation chip:** ok / failed (with the failing check) / not established.

**Tests:** designer round trip with mocked API, 422 mapping, table sums, Sankey renders nodes/links from a
fixture payload, conservation states, a11y (`expectAllButtonsNamed`, `Dialog` for confirmations, no
`PageKit.Toggle` — it is not keyboard accessible; use a native checkbox).

---

## WP3.7a Library browser

List items per kind (tariffs, contracts, connection agreements) and series, with version history and
pins; view an item (JSON + summary); **URDB import** (file → `import_urdb`, `accept_partial` and
`cyclic_year` toggles, refusals shown by field; `urdb_multiple_rates` → pick `item_index`); series upload
(CSV/xlsx, unit, label) and meter data (required unit; `meter_meta_conflict` 409 shown); "attach as import
tariff" (the P2 attach semantics: inline tariff never replaced silently — a confirm `Dialog`).
**Tests:** each flow with mocked API; the refusal and conflict states.

## WP3.7b Tariff builder + bill preview

Items / periods (months, weekdays, hours) / tiers (thresholds, per-period `tier_rates` for windowed) /
ratchets (range, cyclic, months) / settlement / measured_on / direction, validated client-side with the
backend's rules mirrored minimally and **server-side as the source of truth** (422 mapped to fields).
**Bill preview:** `POST /api/results/billing/preview {tariff}` — a thin handler rating the current
dispatch with the draft tariff through `bill_site` (no state change, no drift), payload =
`compute_billing`'s `summary` + `per_period` for that tariff; 204 when not dispatch-ready. Save to Library
(`PUT items/tariff/{name}`) or inline. **Tests:** builder produces the H3 tariff exactly (fixture
equality); preview shows the H3-like totals on a mocked payload; backend preview handler seam + no-drift
test.

## WP3.7c Contracts and connection-agreement editors

Typed forms per contract type (P2 models; allowed pricing combinations; party pickers from the value-flow
participants + externals), and the connection agreement (kind, caps, envelope ref, capacity fee item via
the Tariff builder's item editor, `available_from`, group); Library pin shown when set. **Tests:** each
type round-trips to the P2 model; invalid combinations surface the 422.

---

## Phase 3 e2e QA gate

- [ ] `backend/tests/qa_value_flows.py` (auto-discovered): V1–V4 through the routes (template → config
  route → solve → `/results/value_flows`) — conservation ok on every template, reconciliation to the cent;
  V5 hand splits to the cent under all four keys; V6 group net-import gap 0 and LP row = billed; the P2
  driver still 49/49; a bundle round trip keeps `value_flows` and the ledger identical.
- [ ] Full backend `not slow`, all QA drivers, vitest + `tsc` green; findings note
  `docs/superpowers/findings/<date>-ic-p3-participants.md`; assessor verdict recorded here.

## Scope boundaries (not P3)

- Annual cashflows, escalation, degradation, debt, tax, incentives, returns per participant — P4 (the
  ledger lines become `CashflowLine`s with years).
- Multi-party co-optimisation — later, behind a flag (decision 9).
- Coincident-peak allocation over billing periods longer than a month; allocation of ratchet floors
  (members share the billed kW pro rata to their coincident contribution in the month the floor binds) —
  documented, not a key of its own.
- Annual per-connection tier bands (P2 gate finding): stays a documented limitation; an importer/preflight
  disclosure is P4.
- Realistic-dispatch ledgers — P6 (the ledger is built on the current dispatch, labelled `mode="pf"`).

---

## Plan review

(pending)
