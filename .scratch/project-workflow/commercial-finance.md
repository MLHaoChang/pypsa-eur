# Commercial & finance: the fifth work group

Status: draft for UX review. Owner answered Q7–Q10 on 2026-10-20 (§9).
Date: 2026-10-20
Author: workspace-reconciliation session
Base: master `494a548` (#444 merged). Extends [workspace-reconciliation.md](workspace-reconciliation.md) and [design.md](design.md); folds in package C of [site-commercial-ux/spec.md](../site-commercial-ux/spec.md).

## The owner's words

2026-10-20, about 18:50 CEST:

> "regarding the UX design and the ones that we are implementing for the group parts around capacity expansion and also the expert mode and the guided mode, add a section that specifically also targets the different tariffs and the financing structures and the PPAs, etc., that we have established in the previous runs of conversation. So if you have any questions there, just ask me. But I think we need a part on that to parameterize that based on, ideally based on libraries and files that we can upload, but also default parameters that we can upload."

About 18:56 CEST:

> "So a lot around a commercial section should be there, around PPAs, contracts, tariffs."

Owner decision Q7 = A (about 18:57 CEST): **Commercial & finance is a fifth group card**, on by default, with its inputs in section 7 and its own results tab.

## 0. In one paragraph

Commercial gets the same treatment as the other groups (workspace-reconciliation §5):
- **A group card in the Builder**, on by default for every example.
- **One input home**: flow section 7, Commercial & parties, split into five blocks: Prices & tariffs, Contracts & PPAs, Markets & value streams, Finance, Parties.
- **A results tab**: 10 › Commercial (bill, investment case, value flows, per-party view).
- **Run**: the one Run computes its money through the existing tariff and PPA LP bindings plus the post-solve bill. The investment case becomes a run kind once the separability gate classifies it.
- **Parameter sources**: every value comes from a library item, an uploaded file or a defaults pack, and carries its provenance.
- **Guided** shows the key parameters the template flags for recheck. **Expert** shows everything, with reset. This is the §10a mode rule.

Turning the group off leaves only what the optimisation reads (§2.0). That suits a pure engineering study.

---

## 1. What exists (master `494a548`)

| Piece | Where | State |
|---|---|---|
| Tariff model: items energy / demand / capacity / fixed / certificate / tax-levy; TOU periods; tiers; ratchets; settlement; measured-on; direction | `models/commercial.py` (`Tariff`, `TariffItem`, `TariffPeriod`) | complete; **no currency field on `Tariff`** |
| Rating engine (bill oracle) | `services/commercial/tariff_engine.py`, `billing.py` | complete; unrated intervals flag, never 0 |
| LP bindings: energy as PoC marginal cost, demand charges, tiers, capacity per kW-year, group caps, connection fee, export price, dispatch-changing PPAs | `services/commercial/lp_bindings.py` | complete; fixed per-month/day and per-kVA-year are refused in the LP by name |
| Connection agreement (firm, non-firm static/dynamic, FCA; caps, envelope, curtailment, capacity fee) | `ConnectionAgreement` | complete |
| Contracts: PPA (pay-as-produced, baseload, as-consumed BTM, sleeved; fixed or market + premium; floor/cap; volume cap; indexation; sleeving fee), CfD (strike, interval or monthly capture, negative-price suspension), DR, lease, EaaS, retail | `models/commercial.py`, `services/commercial/contracts.py` | complete; only fixed-price pay-as-produced on-site PPAs change dispatch (v1) |
| Finance inputs: currency, currency year, price basis, financial close, COD per asset, capex phasing, contingency, escalation per class, degradation, analysis years, tax pack and settings, debt tranches, incentives, tax equity, terminal value, WACC, cost of equity, inflation | `models/finance.py` (`FinanceInputs`), `FinanceInputsEditor.tsx` | complete; `stage_capex_lead_years`, `construction_months_by_asset`, `island_outage` have no editor |
| Investment case | `POST /api/results/investment_case`, `services/finance/investment_case_runner.py` | complete; a separate action today |
| Parties and value flows: roles, value streams, allocation keys, asset owners, export revenue, connection-fee payee; five templates | `ValueFlowConfig`, `services/commercial/participants.py`, `value_flow_templates.py`; `ParticipantsDesigner.tsx`, `ValueFlowsView.tsx` | complete; the board (C5) is not built |
| Library: tariff, contract, connection agreement; versioned, content-hashed; series store; project pins | `services/library/` (`items.py`, `series_store.py`, `bundle_pins.py`) | complete; **no `scope`** (org-keyed only) |
| Defaults packs | `services/library/defaults_pack/versions/2026-10-{05,07,13}` (`manifest.json`, `values.csv`, `finance.yaml`, `tariffs.json`, `load_profiles/`) | vendored; 2026-10-20 is on #418 only; **no upload** |
| Uploads | `POST /api/library/series/upload` (CSV/XLSX price series), `POST /api/library/meter_data`, URDB JSON import, project uploads (PDF, XLSX, …) | **no tariff-sheet or term-sheet upload** |
| Market data | `MarketPack`, `AncillaryProduct` in `models/commercial.py` | **defined, unused**; no NO1–NO5, capacity or balancing market |

Today's editors are spread over Economics › Basis / Tariffs & contracts / Library, Results › Investment case (participants, tariff, contracts, finance), `SiteConnectionSection` (timezone, commercial PoC) and Solver Settings (discount rate). #444 §6.2 already moves them all into section 7. This document gives section 7 its internal shape.

---

## 2. Section 7, block by block

### 2.0 The split rule: what follows the group (UX reviewer)

The optimisation already reads money values whose home is section 7. Switching the Commercial group off must never hide them (design §3.2: never hide a blocker; unknown is never complete). So every section-7 field belongs to one of two classes, and **each new money field is classified when it is added**:

- **(a) `lp_input`, read by the optimisation: always shown while Capacity is on**, under the heading "Prices the optimisation uses", whatever the Commercial group's state:
  - every tariff item `lp_bindings` binds: energy and TOU periods, demand, capacity, tiers, group caps;
  - the connection capacity fee (`connection.add_fee_term`);
  - the export price series;
  - PPAs with `changes_dispatch`;
  - the LP discount rate;
  - any market series that feeds one of these.
- **(b) `commercial`, commercial and finance proper: follows the group**:
  - bill-only tariff items (fixed per month or day, per kVA-year), which the LP refuses by name;
  - contracts that settle after the solve;
  - parties and value flows;
  - `FinanceInputs` except the LP discount rate;
  - reference purchases;
  - the market bundle's products not read by the LP;
  - report variants.

Section 7 is therefore **never `not_needed` while Capacity is on**. With the group off, only (b) collapses: its items are `in_scope: false` (#444 §5.2), and 10 › Commercial reads "Outside scope". A block below holds fields of both classes. Its (a) fields stay visible when (b) collapses.

Rules that make it hold:
- **The tag lives with the field definition**, in one place (the model or its field registry), not in the UI.
- **A test asserts that every input `lp_bindings` reads is tagged `lp_input`**, so a new LP-bound field can't slip into the collapsible part.
- **A dispatch-changing contract** (a PPA with `changes_dispatch`) stays listed with the group off, marked "changes dispatch", and **stays in effect**. Whether switching the group off should offer to exclude it from the LP is an engine question for K0 (§8). Until K0 answers, the default is: listed and in effect.


Each value has one home. A value used elsewhere is shown read-only with a link (design §2.2). **Rule for per-asset values:** values the LP reads (overnight cost, lifetime) live on the asset in section 5 (#444 B1). Finance-only per-asset values (COD, degradation, depreciation class) live in 7d's per-asset table and show read-only in the asset form's Commercial block.

### 7a Prices & tariffs
- **Import tariff**: the `Tariff` items with their TOU periods, tiers and ratchets; settlement and measured-on.
- **Export price**: `export_price_ref`, a library price series.
- **Network charges**: the connection agreement's capacity fee, plus the tariff's demand, capacity and tax-levy items.
- **Certificates and levies.**
- Not here: the connection agreement's kind, caps, envelope and curtailment are fence values with their home in **3**, shown here read-only. The power factor that per-kVA-year items need is the connection power factor in **3**. The timezone is derived from the site pin in **1**.

### 7b Contracts & PPAs
- One contract list, one editor per type: PPA, CfD, DR, lease, EaaS, retail (C3).
- Each contract names its assets (from 5), loads (from 4) and parties (from 7e) through pickers, never typed names.
- **Offsite PPA cards** (C9):
  - a financial or virtual PPA is a CfD against a reference price, with no physical flow;
  - a sleeved PPA is a PPA of kind `sleeved` with its sleeving fee and party.

  Both show on the party board. A financial PPA never appears in the energy Sankey.
- PPAs that change dispatch are marked so: today only a fixed-price, pay-as-produced, on-site PPA with the site as buyer. Any other PPA settles after the solve, and the form says so.

### 7c Markets & value streams
- The **market bundle** per country (C2): wholesale, ancillary, capacity, balancing. Its price series show in section 6's status list.
- **Reference purchases** (grey H₂, grey NH₃; GTP-06).
- **DR availability and activation** live in 7b's DR contract. Ancillary products come with C2.
- Capacity and balancing markets don't exist in the engine yet (§8).

### 7d Finance
- Currency, currency year, price basis. The owner's convention is shown as a **label only**: "real prices, held flat to the base year, then escalated".
- Financial close and analysis years: the analysis horizon follows the project's `end_year` (E1).
- **WACC**, cost of equity, inflation.
- Debt tranches, tax pack and settings, incentives, tax equity.
- Terminal value, escalation per class, capex phasing, contingency, replacements.
- Per-asset COD, degradation and depreciation class.
- The project-wide default lifetime.
- The editor-less fields (`stage_capex_lead_years`, `construction_months_by_asset`) gain a field here.
- **The LP's discount rate** (`solver_config.discount_rate`, labelled **"Optimisation annuity rate"**: it annuitises capital in the optimisation; `lp_input`) also lives here, beside WACC (labelled **"Investment-case discount rate (WACC)"**: it discounts the investment case's cashflows; `commercial`). They are two values with two meanings, and the existing finance gate checks they are consistent. Solver Settings shows the LP rate read-only (#444 §6.2, J6). Whether the LP rate should be derived from WACC is a semantics question for the gatekeeper (§8).

### 7e Parties
- The **party board** (C5) is the only editor of `ValueFlowConfig`: one column per party, income and expense cards, a % split per card, and an "unassigned" tray backed by the ledger's conservation check.
- **Party tags** on assets (set in 5) and on contracts (set in 7b) are the same `asset_owners` value, shown on the board (C6).
- **Sector party templates** (C8), next to today's five.
- `export_revenue_to`, `connection_fee_payee`, allocation keys.

---

## 3. Where parameters come from

Every commercial value has a **source**. Fields carry the same provenance labels as #444 §10a.4: `library` (with id and version), `upload` (with file), `pack` (with pack version), `template`, `user`, plus `assumed` or `illustrative` where the source says so. Editing a value afterwards makes it `user`.

| Source | What it gives | How |
|---|---|---|
| **Library** | tariffs, contracts, connection agreements, price series | picked inline from the 7a/7b editors in a drawer (C1). Items gain `scope`: common (shipped) or workspace (the org's). The project pins the version (`bundle_pins`), so a library edit never changes a saved project silently; a newer version shows as "update available" |
| **Uploaded files** | tariff sheets, PPA and contract term sheets, price series | price series: the existing CSV/XLSX upload. Tariffs and contracts (owner Q9 = A): our CSV/XLSX templates parse into library items with validation, and URDB JSON stays. A PDF sheet attaches as the source document; the copilot may propose values from it on LC-04's EditCard, never applied silently |
| **Defaults packs** | technology costs, finance defaults, illustrative tariffs, load shapes | (owner Q10 = A) an org can upload a pack in the vendored format (`manifest.json`, `values.csv`, `finance.yaml`, `tariffs.json`); it is validated, versioned and org-scoped. The project picks its pack at creation (Builder › Advanced) and pins it |
| **Market data** | wholesale, ancillary, capacity, balancing series | (owner Q8 = A) the user brings their own: an uploaded series, or ENTSO-E Transparency with the user's own token. The vendored pack ships only labelled illustrative shapes |
| **Templates** | a starting tariff, finance block and party set per example | as today (`DC_TARIFF`, `DC_FINANCE`, `_staged_commercial_and_finance`, …), marked `assumed` where the template says so |

---

## 4. Guided and Expert

Same rule as #444 §10a.3: **depth, not reach.**
- Guided starts from a library or template item and shows its **key parameters**. The template flags the ones to recheck. The copilot walks through them and proposes each change on the EditCard.
- Expert shows every field, with reset to the library, pack or template value.
- Key flags are deterministic: they come from the item and the template, not from the copilot.

| Block | Guided: decisions (≤3) | Guided: key parameters | Expert adds |
|---|---|---|---|
| 7a Prices & tariffs | pick the import tariff (library, upload, template) | energy rate per TOU band, demand charge, capacity charge; export price series | every item, period, tier, ratchet, settlement, measured-on, certificates and levies; building a tariff from scratch; "Preview the bill" |
| 7b Contracts & PPAs | add a contract: PPA / CfD / DR / other | PPA: price or premium, tenor, volume cap, asset; CfD: strike, reference, tenor; DR: availability and activation price, contracted MW | floor, cap, indexation, pricing mode, sleeving, monthly capture, negative-price rule, notice, events, base year |
| 7c Markets | pick the market bundle or upload a series | the export or wholesale series and its year | ancillary products, reference purchases |
| 7d Finance | pick the finance preset (pack or template) | WACC, inflation, analysis years, debt share | everything in `FinanceInputs` |
| 7e Parties | pick a party template (sector) | who owns each asset; who earns export revenue | the board: splits, allocation keys, payees, custom parties |

---

## 5. Run and results

- **Inside the optimisation** (no new kind): the tariff and PPA LP bindings already price import, export, demand, capacity and dispatch-changing PPAs (`lp_bindings._wrap_with_commercial_bindings`).
- **After the solve**: the bill (`billing.bill_site`) and contract settlement (`contracts.settle`) read the solved dispatch.
- **The investment case** (`POST /api/results/investment_case`) becomes the run kind `investment_case`. It is bundled: it needs a solved dispatch, and it goes stale when the optimisation or any 7d input changes (`investment_case_runner.staleness`). Like every kind, it is requested only once W0 gives it a supported row with a lock-free verdict (#444 §11.1). Until then it stays the existing post-solve action in 10 › Commercial.
- **10 › Commercial**:
  - the bill;
  - the investment case (NPV, IRR, cashflows, debt);
  - value flows (the money Sankey, kept separate from the energy Sankey);
  - the per-party "my view" against the BAU baseline (C7).

  It holds no inputs: each figure links to its 7 block. Report variants by rule (C4) are in 10 › Hand-off.
- **The plain microgrid gets its own investment case** (owner decision): its template carries its own finance block. This is today's `MG_FINANCE`, defined twice; see §8.

---

## 6. Current locations and their homes

All moves are in #444 §6.2 / W3b; this table gives each its block.

| Current | Home |
|---|---|
| Economics › Basis › Rates (discount rate, inflation) | 7d (LP discount rate beside WACC) |
| Economics › Basis › Finance inputs; Results › Investment case › Finance inputs | 7d |
| Economics › Basis › Site connection (commercial PoC, timezone) | PoC → 3; timezone → 1 |
| Economics › Tariffs & contracts › Tariff; Results › Investment case › Tariff | 7a |
| Economics › Tariffs & contracts › Contracts; Results › Investment case › Contracts | 7b |
| Economics › Tariffs & contracts › Participants; Results › Investment case › Participants | 7e (the board, C5) |
| Economics › Library; Results › Investment case › Library | Library page (workspace) + inline pickers in 7a/7b (C1) |
| Economics › Asset costs | 5 (#444 B1); 7d shows the summary |
| Solver Settings › Dispatch › Economic assumptions | discount rate → 7d (read-only in 8); lifetime → 5 |
| Results › Investment case view, Bill, value flows | 10 › Commercial |
| Decision study › Prices and tariffs, Finance, Assumptions | unchanged: its own project kind (desktop-only) |

---

## 7. Package C folded into the slice plan

Package C's order (owner D5: C5 + C6, then C7, C8, C9) is kept. The **K-slices** sit beside #444's W/P plan.

| Slice | What | Package C | Gate | After |
|---|---|---|---|---|
| **K0 Finance semantics** | a gatekeeper plan: LP discount rate vs WACC; one CO₂ price home (§8); currency on `Tariff`; `MG_FINANCE` / `MG_EXISTING` de-duplication | — | gatekeeper | — |
| **K1 Section 7 shell** | the five blocks (§2) on W3a's shell, with the W3b moves; Guided cards with key parameters (§4); the group card and the `commercial` scope flag (W4a) | — | UX | W3a, W3b, W4a |
| **K2 Library scope and pickers** | `scope` common/workspace, the library page, inline pickers in 7a/7b, "update available" on pinned items | C1 | UX; gatekeeper (library schema) | K1 |
| **K3 Uploads** | the tariff/contract CSV/XLSX templates and parsers; PDF source attachments; copilot proposals on the EditCard; the defaults-pack upload, validation and per-project pin (Q9, Q10 = A) | — | gatekeeper (parsers, pack validation); UX | K2 |
| **K4 Party board and groups** | the board as the only `ValueFlowConfig` editor; party tags on contracts and per-stream splits | C5 + C6 | gatekeeper (finance); UX | K1 |
| **K5 Per-party view** | 10 › Commercial "my view" vs BAU; cross-scenario columns | C7 | UX | K4 |
| **K6 Sector party templates** | data centre, e-mobility, C&I, IPP | C8 | UX | K4 |
| **K7 Contracts and offsite PPAs** | C3's contract set in 7b; the offsite PPA cards on the board | C3, C9 | gatekeeper; UX | K4 |
| **K8 Market bundles** | `MarketPack` wired; NO1–NO5 first; ancillary products; capacity and balancing after an engine plan (Q8 = A: user-supplied data and ENTSO-E with the user's own token) | C2 | gatekeeper (engine); UX | K2 |
| **K9 Report variants** | top N by NPV, greatest IRR, per participant | C4 | UX | K5 |
| **W4b: `investment_case`** | the run kind (§5) | — | gatekeeper; UX | W0 row, W4a |

Order: K0 ∥ K1 → K2 → (K3 ∥ K4) → K5 ∥ K6 ∥ K7 → K9; K8 after K2. Package C was queued after DC readiness 03/06 and the gatekeeper's queue; that condition stands for K4–K8.

---

## 8. For the gatekeeper (finance semantics, K0)

- **LP discount rate vs WACC.** Two values, two meanings, reconciled by a gate today (`services/finance/engine.py:504`). Should the LP rate default from WACC (real, pre-tax) and stay separately editable? Either way both live in 7d and are labelled.
- **CO₂ price, three places**: `solver_config` (section 8 in design §2.1), `CommercialConfig.co2_price`, and the staged hubs' `CO2_PRICE`. Each needs exactly one home and one meaning (the LP's price vs a valuation price).
- **Currency on `Tariff`.** It is missing today. Library tariffs from other jurisdictions need it before K2/K3 can import them.
- **`MG_FINANCE` / `MG_EXISTING`** are defined in both `eh_templates.py` and `eh_staged_hubs.py` (#400).
- **Price paths and the analysis horizon** follow E1's rules (step-back labelled "held from <year>"; horizon = `end_year`).
- **Capacity and balancing markets** are engine work before K8 can offer them.

---

## 9. Owner choices

| | Choice | Recommended | Status |
|---|---|---|---|
| Q7 | Where commercial sits | a fifth group card | **A** (about 18:57 CEST) |
| Q8 | Market price data: user-supplied (upload, own ENTSO-E token) / vendored ENTSO-E data / a licensed feed | user-supplied | **A** (about 19:01 CEST) |
| Q9 | Tariff and term-sheet upload: structured templates + PDF as source + copilot proposals / copilot PDF extraction first / JSON only | structured templates | **A** (about 19:01 CEST) |
| Q10 | Defaults packs: org upload, versioned and pinned / vendored only / per-value overrides only | org upload | **A** (about 19:01 CEST) |
