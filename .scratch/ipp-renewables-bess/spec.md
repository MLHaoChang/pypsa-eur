# IPP renewables + BESS: an example for a developer's revenue case

Status: ready-for-agent (spec; docs only, no code). Owner answered Q-IPP1–5 and the scope question on 2026-10-20, about 19:42 CEST, all A (§11). UX OK at the spec stage. Gatekeeper: engine read, "go with changes" (2026-10-20, about 19:42 CEST), folded into §3 and §10.
Date: 2026-10-20
Author: ipp-renewables-bess session (commissioned through the coordinator)
Base: master `ceff6b1` (#447 merged). #448 read at `760a48e` (`claude/workspace-integration-sensitivities`).
Builds on:
- [workspace-reconciliation.md](../project-workflow/workspace-reconciliation.md) (#444): ten sections, work groups, Home §3, Guided §4, slices §13;
- [commercial-finance.md](../project-workflow/commercial-finance.md) (#447): section 7's five blocks, `lp_input` / `commercial` split, K slices;
- [integration-sensitivities.md](../project-workflow/integration-sensitivities.md) (#448): price data §1, PPA defaults §2, cases §5, S slices.

## The owner's words

2026-10-20, about 19:30 CEST:

> "I need another example for IPP developer to integrate renewables plant and potential integration of BESS which has primary focus on revenues generation through PPAs, wholesale (can be different sensitivities)."

Owner decisions that apply (relayed by the coordinator):
- Templates are generic instantiations: they pre-fill sections or mark them `not_needed`. They never add or reorder a section.
- Commercial & finance is the fifth group card. Its inputs live in section 7 (#447).
- Market prices come from the user (Q8 = A). The shipped data holds only labelled illustrative shapes, so **every price in this example is "illustrative, assumed"**.
- EU PPA defaults: one illustrative set per contract form, with cited ranges (Q11 = A).
- Sensitivities: a base plus up to 8 cases (Q12 = A).
- Free planning years come with E1. Until then the example uses its native horizon and says so.
- Money: "real prices, held flat to the base year" (a label only).

---

## 0. In one paragraph

`eh_ipp_hybrid` is a **generation-only site**. A wind farm and a PV plant (fixed size, new build) and a battery candidate share one grid connection (the point of interconnection, POI). The connection's export limit is below the plants' combined nameplate. The site has no demand. The one Run sizes the battery and dispatches the plant against an illustrative day-ahead price over a full hourly year. Afterwards it settles the PPA and the merchant sale, and the investment case gives NPV and IRR for the developer. Eight pre-built cases vary the price level and shape, the PPA price, share and form, the battery cost, the connection limit and degradation.

Most of it already exists:
- the export price binding;
- curtailment at negative prices, which the LP does by itself;
- capture price and capture rate;
- PPA and CfD settlement;
- degradation;
- NPV, IRR and DSCR;
- solve-for-PPA-price.

Four pieces don't exist yet:
- a PPA **share** (§10 G1);
- a rule for a PPA **sold by the site** next to merchant export (G2);
- a way for a template to **ship a price series** (G3);
- a **revenue-by-stream** view (G6).

Shaped and swap PPAs wait for #448's K7 contract plan (G4).

---

## 1. What exists (master `ceff6b1`), checked

| Piece | Where | Use here |
|---|---|---|
| Grid meter pattern: a two-way grid slack (`p_min_pu = -1`), `grid_import` and `grid_export` Links | `services/study/packs.py:651-662` | the POI. The export Link's `p_nom` is the export limit |
| Export price in the LP: `−price` on the export Link's marginal cost, so a negative price makes export cost money and the LP curtails | `services/commercial/lp_bindings.py:2144-2148`, `_export_price` :977 | merchant day-ahead; curtailment at negative prices |
| Circulation guard: refuses snapshots where export pays more than import costs | `lp_bindings.circulation_risk_snapshots` :1960 | battery grid charging stays honest |
| Export revenue in the IC: Σ w · p0(export) · `ic_export_price` | `services/results/value_flows.py:174-195` | the merchant stream. It reads 0 if the ref is missing, so the template must set it (G3) |
| PPA settlement: pay-as-produced, baseload (financial, `(price − ref) × MW`), `market_plus_premium`, floor/cap, volume cap, indexation | `services/commercial/contracts.py:154-244` | PPA forms |
| CfD: `(strike − ref) × gen`, interval or monthly capture, `suspend_on_negative_price` | `contracts.py:247-280` | CfD / financial PPA |
| Double-count warning: the site sells a PPA on behind-the-meter output that also earns the export price | `services/commercial/preflight.py:269-283` (`commercial.ppa_export_double_count`) | **the IPP's case exactly** (G2) |
| Only a fixed-price pay-as-produced on-site PPA with the site as **buyer** changes dispatch | `lp_bindings._ppa_dispatch_spec`; #447 §7b | an IPP's PPA settles after the solve |
| IC: standalone when the owner is not the site party; incremental = total − counterfactual when it is | `services/finance/engine.py:168-181`; `services/results/finance_case.py:2262-2271` | §6 |
| A site with no load: `_served_load` gives zeros, no refusal | `finance_case.py:691-731` | generation-only works (to be confirmed by a test, G5) |
| Degradation: `degradation_by_asset`, per asset, from each asset's COD. It scales generator-linked export shares and PPA/CfD lines (`degrades_with`); a battery's fade does not touch revenue | `models/finance.py:280`; `services/finance/cashflow.py:99-214`; `finance_case.py:348-375, 524-570` | the degradation case |
| Battery replacement: `replacement_capex` (year, asset, amount) or `replacement_rule: part_lifetimes` | `models/finance.py:306-311`; `services/finance/replacements.py` | battery augmentation |
| IC outputs: project/equity NPV and IRR, payback, DSCR, LLCR/PLCR, LCOE, storage LCOS, **solve-for-PPA price** at a target equity IRR (`FinanceInputs.solve_ppa`) | `services/finance/engine.py:343-417, 559` | KPIs |
| Capture price and rate per generator and storage | `services/asset_results/compute.py:848-866, 1584, 1710`; `registry.py:187-194` | KPIs |
| Curtailment | `services/results/curtailment.py:29`, `services/compare/curtailment.py:26` | KPI |
| Synthetic hourly CF profiles for any year and location (`source: synthetic`, deterministic) | `services/profile_gen/synthetic.py`, `generate_profile` | wind and PV, consumed, not written (TS-generators owns the writers) |
| Pack technologies `onwind`, `solar-utility`, `battery` (power and energy parts) | `defaults_pack/versions/2026-10-20/values.csv` | candidate costs; no new cost rows |
| Experiments: 1–8 typed cases, but only the `network` domain executes | `services/experiments.py:31, 520` | commercial cases need S5 (§7) |
| Value-flow templates: `btm_ppa` drafts an as-consumed PPA and `dso_developer` refuses without a load, so neither fits an IPP. `custom` does | `services/commercial/value_flow_templates.py:199-352` | §6 |

---

## 2. The example

### 2.1 Assets and network

The sizes below are recommendation A of Q-IPP2 (§11).

| Element | Model | Value (Q-IPP2 A) | Source label |
|---|---|---|---|
| `poi` bus | AC, the site side of the meter (`eh_poc`) | — | — |
| `grid` bus + `grid` Generator | two-way slack, `p_min_pu −1`, cost 0 | — | as `packs.py` |
| `grid_export` Link | `poi → grid`, `p_nom` = **export limit** | 70 MW | assumed (connection offer) |
| `grid_import` Link | `grid → poi`, `p_nom` = import limit (auxiliaries and any battery grid charging). It carries no private price: its cost is the 7a import tariff | 20 MW | assumed |
| `wind_farm` | Generator, fixed new build (G5), synthetic CF at the zone's reference coordinates (assumed) until the user sets the pin; regenerated then (never `measured`) | 60 MW | pack `onwind`; profile `generated` |
| `pv_plant` | Generator, fixed new build (G5), synthetic CF, as the wind | 50 MWp | pack `solar-utility`; profile `generated` |
| `bess` | StorageUnit, **candidate**: `p_nom_extendable`, `p_nom_max` 50 MW, `max_hours` 2, round-trip ≈ 0.88, cyclic SoC; throughput cost on dispatch (illustrative €/MWh, against unrealistic cycling) | 0–50 MW / 2 h | pack `battery` (power + energy parts) |
| no Load | export is a Link to the grid slack, not a Load sink. The section-4 card is `not_needed` ("generation-only: export through the POI in section 3"); auxiliaries are inside the plants' net output | — | stated |

Combined nameplate 110 MW on a 70 MW export limit gives an overbuild of 1.57. That is typical of a hybrid POI, and it leaves curtailment for the battery to recover.

### 2.2 Time base

- **One modelled year, full hourly chronology (8 760 h, 2030)** (Q-IPP4 recommendation A). A representative week can't carry negative-price hours, the seasonal capture-price spread or battery arbitrage across days.
- The LP is small: 3 generators, 1 storage unit, 2 Links. Build time is checked in `_build.py`'s feasibility solve, and the issue records the solve time.
- **Native horizon** until E1: financial close 2028, COD 2030-01-01, 25 analysis years. The Builder says "This example is built for 2030" (#444 §3.3) and never snaps silently.
- Real prices, held flat to the 2030 base year (label only, #447 §7d).

### 2.3 Prices (illustrative, assumed)

The template ships **three illustrative hourly day-ahead shapes** for its zone (Q-IPP1). They are deterministic and generated in template code. Each is labelled `illustrative`, `assumed`, "not market data; bring your own (upload or ENTSO-E with your token, #448 S1)":
- `da_base`: a mean level, a solar-hour dip, winter evening peaks, a negative correlation with the template's own wind, and a stated count of negative hours;
- `da_low`: `da_base` × 0.8 (price level case);
- `da_cannibalised`: a deeper solar dip with more negative hours (shape case).

Its own key numbers (mean, negative hours, solar capture rate) go in the ledger. The `da_base` targets are written as **ranges with a cited public source** in the issue (ticket 03), never as a forecast.

**Tariffs at the POI** (owner, about 19:47 CEST: the IPP case is wired to section 7). They are an ordinary 7a `Tariff`, the import tariff, with no template-private prices:
- an import energy item: a flat illustrative €/MWh that includes network charges and levies. It is above the highest `da_base` price at every snapshot, so `circulation_risk_snapshots == 0`;
- an export (injection) item, `measured_on: export`, direction cost. Its illustrative value is 0 €/MWh ("DE: no generator injection charge, assumed") and it is editable in 7a;
- the connection capacity fee, if the user adds one (`ConnectionAgreement.capacity_fee`).

Battery grid charging is therefore priced at the retail import rate. A wholesale-indexed import price, so the battery charges at DA, needs a series-priced import (an `import_price_ref` beside `export_price_ref`): engine gap **G7**, a later gated slice.

---

## 3. Revenue streams and how each is modelled

| Stream | Model | Settles | Group / class |
|---|---|---|---|
| **Merchant day-ahead** | export price `da_base` on `grid_export` | in the LP (dispatch) and in the IC (`value_flows` export) | Capacity, `lp_input` |
| **Curtailment at negative prices** | emerges: export at a negative price costs money, so the plant curtails. The battery may charge instead | in the LP | — |
| **Curtailment at the export limit** | emerges: `grid_export` at `p_nom`. The battery absorbs what it can | in the LP | — |
| **BESS arbitrage** | the battery charges from the plant or the grid and discharges to export against the same price | in the LP | Capacity |
| **PPA pay-as-produced** (base) | on the plant's output × **share** (G1), at a fixed price, settled after the solve. Semantics in G2 | after the solve | Commercial, `commercial` |
| **PPA baseload** | `PpaContract kind=baseload`: financial `(price − ref) × MW`. Exists today | after the solve | Commercial |
| **CfD / financial PPA** | `CfdContract` on the plant, interval reference, `suspend_on_negative_price` per form default | after the solve | Commercial |
| **Shaped PPA, swap** | notional-volume CfD (#448 §2, K7). **Not available** until K7: shown in 7b as "Not yet available", never faked | — | — |
| **Intraday, ancillary, capacity** | later, with K8's market bundle (one reserve model with DC-06). Out of this example's v1 | — | — |

**Negative prices with a PPA.** The LP sees only the merchant price (the PPA settles after the solve), so it curtails the whole plant at a negative price. That matches a PPA with a negative-price clause (common in EU PPAs). The financial PPA therefore carries the CfD's clause, `suspend_on_negative_price`, which defaults to **True** for this form: no settlement at ref < 0, which is consistent with the LP's curtailment there (gatekeeper). A user who sets it to False gets a warning: "dispatch is not contract-optimal: the LP curtails at negative prices while the contract would pay P − ref". A PPA whose payment changes the seller's dispatch is not a v1 feature.

---

## 4. The decisions the example answers

| Question | How the example answers it |
|---|---|
| **How big should the battery be?** | The LP sizes `bess` (0–50 MW at 2 h) against merchant value and recovered curtailment. Cases vary its capex and the connection limit. The duration is fixed per case (2 h base; 4 h is a ninth case a user can add) |
| **How much under PPA, how much merchant?** | The PPA share is one scalar (G1). The base is 70 %; a case sets 30 % (merchant-heavy). With Q12's limit of 8, a full share sweep is the grid helper's job (S5: share × 4 values) |
| **Which PPA form?** | Base: pay-as-produced. One case: baseload at the same expected volume. Shaped and swap join when K7 lands |
| Break-even PPA price | `FinanceInputs.solve_ppa` at a stated equity IRR target gives the price that meets it (G2 confirms it works on the IPP's contract) |

The battery size is an LP result. The PPA share and form are case comparisons. The two are coupled only through the money: the PPA settles after the solve, so it never changes the battery the LP picks. This is stated on the results page.

---

## 5. KPIs

| KPI | Source | State |
|---|---|---|
| Revenue by stream (merchant, PPA, CfD difference, arbitrage) per year | IC `CashflowLine` grouped by `value_stream` | **G6**: a grouping view in 10 › Commercial |
| Capture price and capture rate per plant; battery's average charge and discharge price | `asset_results` capture metrics | exists; shown in 10 › Commercial |
| Curtailment (MWh, %): **by cause**, export limit vs negative price | `results/curtailment.py` + the export Link's loading and the price sign | total exists; **G6** splits it by cause, in 10 › Capacity › Operation, cross-linked from Commercial |
| Battery size, cycles per year, arbitrage spread | LP result; storage results | exists |
| Project and equity NPV and IRR, DSCR, payback, LCOE | IC | exists (the run kind `investment_case` after W0; until then the post-solve action, #447 §5) |
| Break-even PPA price at the IRR target | `solve_ppa` | exists (G2 checks it) |

Compare (S6) shows these for base + 8 with deltas. Until S6, the existing A/B Compare covers two cases at a time.

---

## 6. Parties and the investment case

- Value flows: `custom`. One participant, **`ipp`**, the site party and owner of every asset. The externals are `offtaker` (PPA buyer, CfD counterparty) and `market` (export, import). `export_revenue_to: site_party`.
- The owner is the site party, so the IC builds a counterfactual: the site without its candidates (G5). The plants are new builds with a forced size (G5), so the counterfactual is empty and "incremental = total", which is the developer's standalone case.
- Finance (`FinanceInputs`, illustrative, `assumed`):
  - EUR, real, base year 2030;
  - financial close 2028, COD 2030 for every asset;
  - 25 analysis years;
  - WACC, debt share and tenor and the tax pack from the 2026-10-20 pack's `finance.yaml` where present, else assumed with the source stated;
  - degradation: PV 0.5 %/yr, wind 0.3 %/yr (assumed, cited range);
  - battery replacement via `part_lifetimes` at the pack's battery lifetime.
- The LP discount rate defaults to the WACC (#447 K0 rule) and is labelled "Discount rate for the optimisation (annuity)".

---

## 7. Pre-built sensitivities: base + 8 cases

Each case is a **declared change** in the S5 / experiment shape (dotted path, typed value, old → new), with at most 2 changes per case.

| # | Case | Changes | Answers |
|---|---|---|---|
| base | pay-as-produced PPA, 70 % share, `da_base`, 70 MW export, pack battery cost | — | — |
| 1 | Low price level | `export_price_ref` and the PPA reference → `da_low` | merchant risk |
| 2 | Cannibalised shape | the same two refs → `da_cannibalised` | shape risk, battery value |
| 3 | PPA price −10 % | the PPA `price` | PPA negotiation |
| 4 | Merchant-heavy | the PPA `share` 0.7 → 0.3 | PPA share vs merchant |
| 5 | Baseload PPA | the PPA form: pay-as-produced → baseload at the same expected MWh | PPA form |
| 6 | Cheap battery | the battery's overnight cost −30 % | battery size |
| 7 | Tight connection | `grid_export.p_nom` 70 → 55 MW | connection limit, battery size |
| 8 | Faster degradation | `degradation_by_asset` PV 0.5 → 1.0 %/yr, wind 0.3 → 0.8 %/yr | lifetime revenue |

Cases 1 and 2 change two paths that must move together, the export price and the PPA reference. Each case's declared changes list both.

**Delivery** (Q-IPP5 = A, reconciled with the both-modes rule): the template ships `cases.json` as data only, validated against the experiment contract's path and value rules.
- **Nothing in the UI shows it as a list until S5.** A read-only list would be a lock that the engine doesn't require (UX reviewer).
- Section 8's rows (S5) then turn it into ordinary, editable cases on "Create the example's cases".
- Before S5, the ledger names the 8 prepared sensitivities in one line, without values. The user can still make any of them by hand as a scenario copy and edit it with the normal editors.
- They are never pre-created as hidden scenario copies.

---

## 8. The example in the workspace

### 8.1 Groups (Builder pre-fill)

| Capacity | Grid | Control | Hand-off | Commercial |
|---|---|---|---|---|
| on | off | off | on | **on** |

The user can switch Grid on later (#448 §4): the POI and export limit already live in section 3.

### 8.2 Section by section (the Home card's pre-fill)

| Section | Pre-fill | State after create |
|---|---|---|
| 1 Project & site | name "Renewables IPP with battery"; **no pin** (templates never invent a location, #444 §8); planning period 2030 (native, §2.2) | `warning` "no site location", as every example |
| 2 Carriers & processes | electricity, pre-filled as the data centre does | complete (a user can add a carrier later) |
| 3 Connection & grid | POI; export limit 70 MW, import limit 20 MW (`assumed`, "from the connection offer"); bidding zone from the pin (7c shows it, #448 §1) | complete, with `assumed` badges |
| 4 Loads & demand | none. Export is the `grid_export` Link to the grid slack Generator, not a Load sink, and auxiliaries are netted in the plants' output | `not_needed` ("generation-only: export through the POI in section 3") |
| 5 Assets & sizing | wind 60 MW and PV 50 MWp (fixed new build); battery candidate 0–50 MW at 2 h; costs and lifetimes from the pack | complete, `assumed` |
| 6 Time series & profiles | wind and PV CF `generated` (synthetic); DA price shapes `illustrative`; import price derived | complete. Recheck flag: "replace the illustrative prices with your own" |
| 7 Commercial & parties | 7a export price = `da_base`, import tariff and injection item (§2.3); 7b PPA pay-as-produced, 70 %, fixed price (illustrative, cited range), 10-yr tenor, negative-price clause; 7c bidding zone **DE-LU, `assumed`** (re-derived when the pin is set) and the price-data panel; 7d finance (§6); 7e `ipp` / `offtaker` / `market` | complete. Key flags on PPA price and share, the price series and WACC |
| 8 Scenarios & optimisation | groups §8.1; no reliability goal; VOLL not applicable (no load); the 8 cases (§7) | complete |
| 9 Readiness → run | — | — |
| 10 Results → reports | the usual tab order (templates never reorder). The project opens Results on **Commercial** as its initial tab: revenue by stream, capture, NPV/IRR, break-even PPA. Curtailment by cause sits in Capacity › Operation, cross-linked from Commercial | — |

### 8.3 Every pre-filled parameter is editable in both modes

Owner rule (about 19:43 CEST): "all examples should enable user to change parameters in both guided and expert modes."
- Each value below is editable in Guided (as a key parameter, or under the section's Advanced) and in Expert (in the same editor component, #444 §4).
- Nothing is locked unless the engine requires it, and then the field says why.
- Editing a value makes it `user` (#447 §3).

| Parameter (pre-fill) | Section / block | Guided | Expert editor |
|---|---|---|---|
| Project name; site pin (**unset**) | 1 | key | section 1 editors (name, map pin) |
| Planning period 2030 (native) | 1 | key; other years show the E1 note (#444 §3.3), never snap | section 1 planning period |
| Carrier: electricity | 2 | key (add a carrier) | Carriers & processes card |
| Export limit 70 MW (`grid_export.p_nom`) | 3 | key | section 3 connection editor; the Link's form in Expert |
| Import limit 20 MW (`grid_import.p_nom`) | 3 | key | the same |
| Bidding zone DE-LU (`assumed`; re-derived from the pin) | 7c (shown in 3) | key | 7c zone editor |
| Wind 60 MW, PV 50 MWp (forced size) | 5 | key (size) | asset form (W5): General → Sizing. "Let the study size it" turns the forced bounds into a range |
| Plant overnight cost, FOM, lifetime (pack) | 5 | Advanced | asset form › Sizing / Commercial; reset to pack |
| Battery candidate 0–50 MW, 2 h, efficiencies | 5 | key (max MW, duration) | asset form › Sizing; efficiency under Advanced |
| Battery cost (power + energy parts, pack), throughput cost per MWh discharged | 5 | Advanced | asset form › Commercial; reset to pack |
| Wind and PV CF (synthetic, `generated`) | 6 | key row (status, regenerate, upload) | Time series row → owner's editor; Profile Studio |
| DA price shapes (illustrative) | 6 / 7c | key row with the recheck flag | 7c price-data panel; Library series picker |
| Import tariff: energy item (€/MWh incl. network charges), injection item on export | 7a | key | 7a tariff editor (every item); Library tariff picker |
| PPA: form, price, share 70 %, tenor 10 y | 7b | key | 7b PPA editor |
| PPA: settlement `financial`, reference = export series, negative-price clause, indexation, base year | 7b | Advanced | 7b PPA editor (every field) |
| WACC, inflation, analysis years 25, debt share | 7d | key | 7d `FinanceInputs` editor |
| Financial close, COD, degradation, battery replacement, tax pack, debt terms, LP discount rate | 7d | Advanced | 7d `FinanceInputs` editor; the LP rate beside WACC |
| Parties `ipp` / `offtaker` / `market`, `export_revenue_to` | 7e | key (who owns, who earns export) | the party board (C5) |
| Groups (Capacity, Commercial, Hand-off on; Grid, Control off) | 8 | key (the scope component) | the same component |
| The 8 cases | 8 | not shown until S5 (§7); then editable rows | S5 rows |

**This template follows the owner's both-modes rule** (UX checklist item 12): every value above is editable in Guided and in Expert, with reset to the template value.

**Locks**, each shown read-only with its reason and listed in ticket 03's PR:
- **Engine-required:** a baseload PPA's share is fixed at 1 ("the volume is the baseload MW"; model validator).
- **Engine-required:** the meter roles. `grid_import` and `grid_export` carry `eh_role` `grid_import` / `grid_export`, and `poi` carries `eh_poc`. The commercial meter, the export price and the settlement bind by them (`commercial.binding`), so the Link form shows the role read-only: "the commercial meter is identified by this role". The Links' sizes stay editable.
- **Engine-required:** the grid slack (`grid`) is beyond the meter. It is not an `ipp` asset and its cost is 0 both ways (G5), so its form shows it read-only: "the market side of the meter".
- **This template's form, not an engine refusal:** the 7b editor offers `settlement: financial` as the IPP PPA's form. Switching to `physical` is allowed; the preflight then warns `commercial.ppa_export_double_count` and names the reason. The engine still accepts a physical PPA, so existing projects stay valid (gatekeeper).

### 8.3a Commercial inputs live in section 7 (owner, about 19:47 CEST)

"Ensure that the IPP case is wired to the tariff and commercial section."
- Every IPP commercial input lives in section 7 and is edited there, through the same editors and Library as any project. None is a template-private copy.
- The `lp_input` ones stay visible with the Commercial group off (#447 §2.0).
- Per-asset values the LP reads (overnight cost, lifetime) keep their home on the asset in 5, by #447's rule. 7d shows them read-only with a link.

| Commercial input | Home / editor | Library or pack source | Class |
|---|---|---|---|
| PPA: form, share, price, tenor, settlement, negative-price clause, indexation | 7b PPA editor | the PPA defaults item for the form (#448 S2, `illustrative`); until S2, the template's values `illustrative`, with a cited range | `commercial` |
| Merchant price series `da_base` (and the case shapes) | 7c price-data panel / 7a export price; Library series picker | Library series minted at create (G3), `illustrative`; user-replaceable (upload, ENTSO-E) | `lp_input` |
| Import tariff: energy and network charge; injection item; capacity fee | 7a tariff editor | a Library tariff item (scope common) shipped with the template, `illustrative` | `lp_input` |
| Financing: WACC, inflation, debt and equity, tax, 25-year horizon, degradation, battery replacement | 7d `FinanceInputs` editor | pack `finance.yaml` (2026-10-20), else `assumed` with source | `commercial` (the LP discount rate is `lp_input`) |
| Battery capex (power + energy) | 5, the asset form (home); shown read-only in 7d's capex summary with a link | pack `battery` | `lp_input` |
| Parties and value flows | 7e party board | — | `commercial` |
| Investment case | 10 › Commercial (run); its inputs are the rows above | — | — |

Ticket 03 tests "no shadow value". It changes each row's value through its section-7 route (or 5 for the battery capex) and asserts that the solve (for `lp_input`) or the IC (for `commercial`) changes accordingly, with no other copy read.

### 8.4 Home card and artwork brief

- **Card**:
  - title "Renewables IPP + battery";
  - one-liner: "A wind and solar plant with an optional battery behind one grid connection. Size the battery and weigh PPA against merchant revenue";
  - chips: "Capacity · Commercial", "1 year, hourly", "8 cases".
  - "In development" until ticket 03 merges, then creatable (#444 §3.1 pattern).
- **Artwork** (#436's style; `create-industry-art.mjs`, a new `ipp.svg` beside `ammonia.svg`). Use the existing isometric primitives:
  - three to four `turbine()` on the left;
  - a `solar()` field of 3 × 10 panels in the centre;
  - a `batteries(…, 4)` row beside a small substation `box()`;
  - an export line in the accent red (`#ff7f72`, as the microgrid's feeder) leaving the plot edge to a pylon;
  - no price chart, no text, no logos;
  - the same sky, aura, grain and base slab.

  Title: "Renewables IPP with battery — original illustrative scene". The W2 session places it. This package supplies the brief and, if W2 asks, the SVG.

---

## 9. Not adopted

- **A plant-sizing decision.** The plants are fixed (the developer has a site and a permit). A user can make them extendable in Expert, and the example says so.
- **A load or an industrial offtaker on site.** A behind-the-meter PPA is the H2 hub's pattern, not this one.
- **Pre-created scenario copies.** They would be eight full bundles with no declared changes, which S5 forbids.
- **Our own price forecast.** The shapes are illustrative (Q8 = A).

---

## 10. Engine and data gaps (gated items)

| # | Gap | Proposal | Gate |
|---|---|---|---|
| **G1** | No PPA share: `PpaContract` and `CfdContract` take a whole asset's output | `share: float = 1.0`, in (0, 1], on both, applied to `gen` before settlement and before the volume cap. A baseload PPA refuses a share other than 1. In `hashing.FIELDS_AFTER_V1` (default 1.0). Test: hash stability on an existing PPA and CfD fixture. The degradation tag is unchanged | gatekeeper: **OK as written** |
| **G2** | A PPA **sold by the site** with merchant export is a double count (`commercial.ppa_export_double_count`) | Gatekeeper, **an explicit field, not an automatic rule** (an automatic rule would silently change the cash of existing contracts):<br>1. `PpaContract.settlement: Literal["physical", "financial"] = "physical"`, in `FIELDS_AFTER_V1`.<br>2. `financial` settles `(price − ref) × share × gen` through **the CfD settlement code** (strike − ref), not a second implementation. Its `reference_price` is the export series.<br>3. The cash identity holds with the battery too: Σ ref · export + Σ (P − ref) · s · gen. The "merchant rest" (export − s · gen) can go negative while the battery charges from the plant. The toy test covers that.<br>4. Negative prices: `suspend_on_negative_price`, default True for this form (§3); False warns.<br>5. Preflight: `ppa_export_double_count` stops **only** for `settlement == "financial"`. An info line is added when the PPA's ref isn't the export series (basis risk, not a double count).<br>6. `solve_ppa`: a financial owner-sold PPA is linear in price with no redispatch, so it counts as the single owner-sold PPA. `solve_ppa_ambiguous_contract` stays when there are two.<br>10 › Commercial re-labels the streams for the user (G6) | gatekeeper: **go with these changes** |
| **G3** | A template can't ship a price series: `export_price_ref` and the contracts' `reference_price` are org Library refs | `project_templates/<id>/price_series/*.csv` + `meta.json`. At create, `from_template` mints each into the org Library with `series_store.put_series` (idempotent on content, as `export_series.put_flat_export_series`), labelled `illustrative`, then writes the refs into the project's `solver_config.commercial`. A refusal leaves no half-made project | gatekeeper: **OK**. The minted meta carries #445/#448's currency, `price_basis`, `currency_year`, `source_time_basis` (UTC) and the `illustrative` label. The hash is content-only, so the same template minted into two orgs hashes the same. The K2/S1 sessions are told |
| **G4** | Shaped PPA, swap | #448 K7's notional-volume CfD. Not in v1: 7b shows them "Not yet available" | K7 (not here) |
| **G5** | The plants must carry capex in the IC and drop out of the counterfactual | The plants are candidates with a forced size: `p_nom_extendable`, `p_nom_min = p_nom_max` = size. The IC builds with an empty counterfactual and incremental = total: the developer's standalone case (gatekeeper: **yes**). Traps to test (gatekeeper):<br>1. The grid-side two-way slack is never an `ipp` asset: no capex, vom or COD. It is excluded the way g2p 06 excludes product slacks (it is beyond the meter, `lp.site_generators`), and its marginal cost is 0 both ways.<br>2. With no import tariff, the import cost (`grid_import`'s Link marginal cost × flow) appears **once** as an energy-cost line. It is never dropped and never booked as asset vom. The IC total reconciles with the LP's revenue − cost per stream.<br>3. `circulation_risk_snapshots = 0`: the network charge is > 0 at every snapshot (asserted in the template test).<br>4. No load: per-load, self-sufficiency and `peak_import` capacity metrics give None or "not applicable", never a division by zero (one test over the IC report).<br>5. The battery throughput cost is set as StorageUnit `marginal_cost`, which prices `p_dispatch` only: "per MWh discharged" (not `marginal_cost_storage`, which is per MWh stored).<br>6. Forced-size candidates, `degradation_by_asset` and `part_lifetimes` are fine | gatekeeper: **yes, with these tests** |
| **G7** | No wholesale-indexed import price: a tariff item has fixed rates, so battery grid charging is priced at the retail import rate | `CommercialConfig.import_price_ref` (a Library series on the import Link, like the export), `lp_input`, edited in 7a. Not in v1 | gatekeeper (a later gated slice) |
| **G6** | No revenue-by-stream view; curtailment isn't split by cause | A results reader over the IC `CashflowLine`s (merchant, PPA, CfD difference, import cost, O&M) and a curtailment split (export at `p_nom` vs price < 0). No engine change. UI in 10 › Commercial | UX; gatekeeper told |

---

## 11. Owner choices

Each is sent to the coordinator as 2–4 options, the recommendation first.

| | Question | Options (recommended first) | Status |
|---|---|---|---|
| Q-IPP1 | Reference bidding zone | **A DE-LU**: the largest EU PPA market, frequent negative prices, full ENTSO-E coverage for a user's own data · B ES: strongest solar cannibalisation · C NL | **A** (about 19:42 CEST) |
| Q-IPP2 | Plant sizes | **A hybrid: wind 60 MW + PV 50 MWp at a 70 MW export limit**: shows overbuild, curtailment and the battery recovering it · B PV-only 100 MWp at 70 MW: simplest, strongest solar-shape story · C wind-only 80 MW at 80 MW | **A** (about 19:42 CEST) |
| Q-IPP3 | Battery: candidate or fixed | **A candidate, power sized by the LP at a fixed 2 h duration (0–50 MW)**: answers "how big" · B fixed 30 MW / 60 MWh: revenues only, no sizing · C power and energy both sized (Store + Links): richer, a bigger model change | **A** (about 19:42 CEST) |
| Q-IPP4 | Single year or staged | **A single year, full hourly 2030, 25-year IC**: what an IPP's COD case needs; prices need the chronology · B staged 2027 / 2030 / 2035 on representative weeks: matches the other examples, loses negative-price hours · C single year on 4 representative weeks: faster, coarser | **A** (about 19:42 CEST) |
| Q-IPP5 | How the 8 cases ship before S5 | **A a `cases.json` sidecar, shown only once S5 creates them** (the "read-only list" was dropped for the both-modes rule): no hidden copies · B pre-create 8 scenario copies at create: usable today, but 9 bundles and no declared changes · C ship only the base; cases come with S5 | **A** (about 19:42 CEST) |

---

## 12. Slices (issues/)

| # | Ticket | Gate | After |
|---|---|---|---|
| 01 | [PPA share and the IPP-sold PPA](issues/01-ppa-share-and-site-sold-ppa.md) (G1, G2) | gatekeeper | first; built in this package (owner scope A) |
| 02 | [Template price series minted at create](issues/02-template-price-series.md) (G3) | gatekeeper told | — |
| 03 | [The template `eh_ipp_hybrid`](issues/03-template-eh-ipp-hybrid.md) | gatekeeper (G5, IC); UX (card line) | 01, 02 |
| 04 | [The example's cases](issues/04-example-cases.md) | gatekeeper told; UX | 03; S5 for creation |
| 05 | [Revenue by stream and curtailment by cause](issues/05-revenue-by-stream.md) (G6) | UX; gatekeeper told | 03; K1 shell for placement |
| 06 | [Home card and artwork](issues/06-home-card-artwork.md) | UX | 03; W2 places it |

Order: 01 ∥ 02 → 03 → (04 ∥ 05 ∥ 06). One PR per ticket, each on its own `claude/*` branch.

Scope (owner, about 19:42 CEST): **A**. This package builds ticket 01 first, gated by the gatekeeper, and then 02 and 03.
