# IPP renewables + BESS: an example for a developer's revenue case

Status: needs-triage (spec; docs only, no code). Owner choices pending (§11). Gatekeeper and UX review pending.
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
| `grid_import` Link | `grid → poi`, `p_nom` = import limit (battery charging and auxiliaries) | 20 MW | assumed |
| `wind_farm` | Generator, fixed new build (G5), synthetic CF | 60 MW | pack `onwind`; profile `generated` |
| `pv_plant` | Generator, fixed new build (G5), synthetic CF | 50 MWp | pack `solar-utility`; profile `generated` |
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

Import price: `da_base` plus an illustrative network charge (€/MWh) on `grid_import`, so `circulation_risk_snapshots` stays 0.

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

**Negative prices with a PPA.** The LP sees only the merchant price (the PPA settles after the solve), so it curtails the whole plant at a negative price. That matches a PPA with a negative-price clause (common in EU PPAs). The pay-as-produced form's default says so: "the offtaker accepts curtailment at negative day-ahead prices". A PPA that pays through negative prices would change dispatch on the seller side. That is a gatekeeper question (G2), not a v1 feature.

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

**Delivery** (Q-IPP5): the template ships the case list as a sidecar, `cases.json`, validated against the experiment contract's path and value rules. Section 8's rows (S5) turn it into cases on "Create the example's cases". Until S5:
- the list shows read-only in section 8 and in the ledger;
- the network-domain cases (6, 7) can run through today's sweep or experiments;
- commercial cases wait for S5.

They are never pre-created as hidden scenario copies.

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
| 1 Project & site | name "Renewables IPP with battery"; pin at an illustrative site in the zone (Q-IPP1); planning period 2030 (native, §2.2) | complete; pin `assumed` |
| 2 Carriers & processes | electricity, pre-filled as the data centre does | complete (a user can add a carrier later) |
| 3 Connection & grid | POI; export limit 70 MW, import limit 20 MW (`assumed`, "from the connection offer"); bidding zone from the pin (7c shows it, #448 §1) | complete, with `assumed` badges |
| 4 Loads & demand | none. Export is the `grid_export` Link to the grid slack Generator, not a Load sink, and auxiliaries are netted in the plants' output | `not_needed` ("generation-only: export through the POI in section 3") |
| 5 Assets & sizing | wind 60 MW and PV 50 MWp (fixed new build); battery candidate 0–50 MW at 2 h; costs and lifetimes from the pack | complete, `assumed` |
| 6 Time series & profiles | wind and PV CF `generated` (synthetic); DA price shapes `illustrative`; import price derived | complete. Recheck flag: "replace the illustrative prices with your own" |
| 7 Commercial & parties | 7a export = `da_base`, import charge; 7b PPA pay-as-produced, 70 %, fixed price (illustrative, cited range), 10-yr tenor, negative-price clause; 7c zone and price-data panel; 7d finance (§6); 7e `ipp` / `offtaker` / `market` | complete. Key flags on PPA price and share, the price series and WACC |
| 8 Scenarios & optimisation | groups §8.1; no reliability goal; VOLL not applicable (no load); the 8 cases (§7) | complete |
| 9 Readiness → run | — | — |
| 10 Results → reports | the usual tab order (templates never reorder). The project opens Results on **Commercial** as its initial tab: revenue by stream, capture, NPV/IRR, break-even PPA. Curtailment by cause sits in Capacity › Operation, cross-linked from Commercial | — |

### 8.3 Home card and artwork brief

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
| **G1** | No PPA share: `PpaContract` and `CfdContract` take a whole asset's output | `share: float = 1.0`, in (0, 1], on both, applied to `gen` before settlement and before the volume cap. In `hashing.FIELDS_AFTER_V1` (default 1.0, so existing hashes don't move). The degradation tag is unchanged | **gatekeeper** (contract model) |
| **G2** | A PPA **sold by the site** with merchant export is a double count (`commercial.ppa_export_double_count`) | For a site that exports (`site_party` = seller, an export price set), a pay-as-produced PPA settles as **financial**: `(price − ref) × share × gen`, with `ref` = the export price series. That is cash-identical to "PPA price on the share, merchant on the rest", because the export already earns `ref` on every MWh. The preflight then doesn't warn for that form. 10 › Commercial re-labels it for the user as "PPA revenue = price × share × gen" and "merchant = ref × (1 − share) × gen" (G6). Also: confirm `solve_ppa` finds this contract, and the negative-price rule of §3 | **gatekeeper** (settlement semantics) |
| **G3** | A template can't ship a price series: `export_price_ref` and the contracts' `reference_price` are org Library refs | `project_templates/<id>/price_series/*.csv` + `meta.json`. At create, `from_template` mints each into the org Library with `series_store.put_series` (idempotent on content, as `export_series.put_flat_export_series`), labelled `illustrative`, then writes the refs into the project's `solver_config.commercial`. A refusal leaves no half-made project | gatekeeper told (Library write path); the K2/S1 sessions told |
| **G4** | Shaped PPA, swap | #448 K7's notional-volume CfD. Not in v1: 7b shows them "Not yet available" | K7 (not here) |
| **G5** | The plants must carry capex in the IC and drop out of the counterfactual | The plants are candidates with a forced size: `p_nom_extendable`, `p_nom_min = p_nom_max` = size. Test: on the generation-only network, the IC builds with an empty counterfactual and incremental = total. Also: billing on a site with no load | **gatekeeper** (IC semantics) |
| **G6** | No revenue-by-stream view; curtailment isn't split by cause | A results reader over the IC `CashflowLine`s (merchant, PPA, CfD difference, import cost, O&M) and a curtailment split (export at `p_nom` vs price < 0). No engine change. UI in 10 › Commercial | UX; gatekeeper told |

---

## 11. Owner choices

Each is sent to the coordinator as 2–4 options, the recommendation first.

| | Question | Options (recommended first) | Status |
|---|---|---|---|
| Q-IPP1 | Reference bidding zone | **A DE-LU**: the largest EU PPA market, frequent negative prices, full ENTSO-E coverage for a user's own data · B ES: strongest solar cannibalisation · C NL | open |
| Q-IPP2 | Plant sizes | **A hybrid: wind 60 MW + PV 50 MWp at a 70 MW export limit**: shows overbuild, curtailment and the battery recovering it · B PV-only 100 MWp at 70 MW: simplest, strongest solar-shape story · C wind-only 80 MW at 80 MW | open |
| Q-IPP3 | Battery: candidate or fixed | **A candidate, power sized by the LP at a fixed 2 h duration (0–50 MW)**: answers "how big" · B fixed 30 MW / 60 MWh: revenues only, no sizing · C power and energy both sized (Store + Links): richer, a bigger model change | open |
| Q-IPP4 | Single year or staged | **A single year, full hourly 2030, 25-year IC**: what an IPP's COD case needs; prices need the chronology · B staged 2027 / 2030 / 2035 on representative weeks: matches the other examples, loses negative-price hours · C single year on 4 representative weeks: faster, coarser | open |
| Q-IPP5 | How the 8 cases ship before S5 | **A a `cases.json` sidecar, read-only list until S5 creates them**: no hidden copies · B pre-create 8 scenario copies at create: usable today, but 9 bundles and no declared changes · C ship only the base; cases come with S5 | open |

---

## 12. Slices (issues/)

| # | Ticket | Gate | After |
|---|---|---|---|
| 01 | [PPA share and the IPP-sold PPA](issues/01-ppa-share-and-site-sold-ppa.md) (G1, G2) | gatekeeper | owner Q-IPP1–4 |
| 02 | [Template price series minted at create](issues/02-template-price-series.md) (G3) | gatekeeper told | — |
| 03 | [The template `eh_ipp_hybrid`](issues/03-template-eh-ipp-hybrid.md) | gatekeeper (G5, IC); UX (card line) | 01, 02 |
| 04 | [The example's cases](issues/04-example-cases.md) | gatekeeper told; UX | 03; S5 for creation |
| 05 | [Revenue by stream and curtailment by cause](issues/05-revenue-by-stream.md) (G6) | UX; gatekeeper told | 03; K1 shell for placement |
| 06 | [Home card and artwork](issues/06-home-card-artwork.md) | UX | 03; W2 places it |

Order: 01 ∥ 02 → 03 → (04 ∥ 05 ∥ 06). One PR per ticket, each on its own `claude/*` branch.
