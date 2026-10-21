# Modular work cards: at most three sub-options each

Status: draft for UX review. Owner answered Q17 and Q18 on 2026-10-20 (§5).
Date: 2026-10-20
Author: workspace-reconciliation session
Amends: [workspace-reconciliation.md](workspace-reconciliation.md) §5 (groups as the scope; Q2); [commercial-finance.md](commercial-finance.md) §2.0 (the `lp_input` / `commercial` split); [integration-sensitivities.md](integration-sensitivities.md) §4 (turning a group on later).

## The owner's words

Owner input #10, 2026-10-20, about 20:10 CEST:

> "Make sure that the different cards like capacity expansion, AC power flow part, the commercial part, the control part, and reliability part, make sure that all the different sections that we select in each of them and it shouldn't be too many. I think we limit to 3, which is good. Should be modular pieces that you can really select and deselect, right? So if you don't want to run something, then it will not be parameterized and will not run. That is also important. So these are modular pieces that you add combined with each other, and the examples, the exemplary templates that are there, give you examples how these cards can be set up. Right, but then you ultimately still can modularize individual piece of work. Consider that in your design spec."

## 1. The cards

"Group" in the earlier documents is a **card** here: the same scope, stored in `flow_scope.json`, with its home in section 8 (W4a). There are six cards, each with **at most three sub-options**.

| Card | Sub-options (≤ 3) | Run kind | Runnable today |
|---|---|---|---|
| **Capacity expansion** | 1. Asset sizing (expansion) · 2. Dispatch & economics · 3. Process detail | `optimisation` | 1 + 2 together yes; 2 alone (sizing off) not yet available (needs the run option in §4); 3 not yet available (W0) |
| **AC power flow & grid** | 1. Load flow (AC power flow after the optimisation) · 2. Grid code & electrical design (scorecard, campus PCC compliance, transformer and compensation sizing) · 3. N-1 security (SCLOPF) | `loadflow`; campus study; SCLOPF as an `optimisation` option | 1 bundled (needs dispatch); 2 scorecard advisory, campus study per W0; 3 yes, in LOPF mode only (not with the rolling strategy; lines and transformers; linear, no AC) |
| **Control** | 1. Converter control presets · 2. Island & ride-through checks · 3. Frequency & dynamics | `control_checks` | 1 inputs only; 2 per W0; 3 not yet available |
| **Reliability** (owner Q17 = A) | 1. Reliability goal & study (LOLE / ENS) · 2. Stress conditions & FMEA · 3. Outage-data screening (COPT) | `study` | 1 yes; 2 yes (sweep); 3 yes: no solve, but it needs unit outage rates and availability profiles |
| **Commercial & finance** | 1. Tariffs & bill · 2. Contracts & PPAs · 3. Investment case & parties | the LP's tariff/PPA terms (inside `optimisation`); `investment_case` | 1 yes, after the capacity/dispatch run (the bill reads the solved dispatch); 2 yes; 3 per W0 |
| **Hand-off** | 1. Reports · 2. Data exports · 3. Specialist packet (Planning → dynamics, PowerFactory read-back) | — | yes |

Earlier items that move into these sub-options:
- Detailed process modelling → Capacity 3.
- Electrical infrastructure sizing and the grid-code scorecard → Grid 2.
- Contingencies → Grid 3, as far as SCLOPF reaches. Wider contingencies stay "not yet available" inside Grid 3.
- The reliability study → its own card.

A sub-option that W0 hasn't classified as supported shows **"Not yet available"**. It can't be selected.

## 2. Truly modular: deselected means not parameterised and not run

Deselecting a card or a sub-option means three things, enforced by the server (B1's `/flow` and `run_request`):
- **Not parameterised.** Its input blocks are hidden in Guided and Expert. Its items are `in_scope: false`: they don't count toward a section's state, are never "to check", and never block (#444 §5.2).
- **Not run.** Its kinds leave `run_request`. Its results read "Outside scope", and earlier results are kept, not deleted.
- **Not lost.** Values stay. Selecting it again reveals them, and fills only empty fields from the library or the template, marked `assumed` (#448 §4).

**One exception keeps the model honest** (#447 §2.0). A value the optimisation reads is part of what runs, so it stays shown while the optimisation runs, in its own home section, whichever card it belongs to. A card is a scope, not a place. Two examples:
- With Commercial off, the import energy price still shows in **7a under "Prices the optimisation uses"**, tagged with the Capacity card.
- With Reliability off, **VOLL** still shows in section 8, because the LP prices lost load with it. The reliability goal and the stress conditions do hide.

**Cards combine freely.** One Run computes the selected combination: `run_ready` is the AND over the requested kinds (R1). Which combinations are runnable is decided by the W0 matrix, never by the card layout. A combination with an unknown or unsupported member can't be run, and the card says which member and why.

## 3. Templates are example card setups

An example pre-selects cards and sub-options and pre-fills them. The user then adds or removes any card or sub-option, in Guided (the Builder and section 8's scope component) or in Expert (the same component). Every pre-filled value stays editable in both modes (#449, the owner rule of about 19:43 CEST).

Encoded as each template's default scope in `pypsa-gui/backend/services/flow_scope.py` (`TEMPLATE_PRESETS`, `SITE_TYPE_PRESETS`, W4a / GS-6, 2026-10-21). Sub-options shown in *italics* are intended but **not yet available** (W0), so they are not pre-selected yet. Each one lights up in the preset once W0 marks it supported.

| Example (template id) | Capacity | Grid | Control | Reliability | Commercial | Hand-off |
|---|---|---|---|---|---|---|
| Data centre (`eh_datacenter`, `eh_datacenter_staged`) | 1, 2 | 2 | 1, *2* | 1, 2 | 1, *3* | 1 |
| Island microgrid (`eh_microgrid`, `eh_microgrid_staged`) | 1, 2 | — | 1, *2* | 1, 2, 3 | 1, *3* | 1 |
| Hydrogen hub (`eh_h2_hub`, `eh_h2_hub_staged`) | 1, 2, *3* | — | — | 1 | 1, 2, *3* | 1 |
| Ammonia (`eh_ammonia_staged`) | 1, 2, *3* | — | — | 1 | 1, *3* | 1 |
| IPP hybrid (`eh_ipp_hybrid`, #485) | 1, 2 | — | — | — | 1, 2, *3* | 1 |
| Own site: industrial / commercial / campus / other (`eh_site`) | 1, 2 | — | — | 1 | 1, *3* | 1 |
| Own site: hospital (`eh_site`) | 1, 2 | — | — | 1, 2 | 1, *3* | 1 |
| Blank / import / legacy networks | 1, 2 | — | — | — | 1 | 1 |

Grid 1 (load flow) and Grid 3 (N-1 security) are on exactly when their solver settings are. Until W4b makes them run kinds, Solver settings is their one editor, so no preset names them. This follows the UX gate on W4a: a checked box never means "not computed".

Alignment with the research mapping (`FIVE-CARD-MODULARITY.md` §3):
- The H₂ hub and ammonia examples intend Process detail (research: "S capacity/process").
- The IPP hybrid leaves Grid, Reliability and Control off (research: "off by default"). Its study is therefore not requested.
- The C&I own site intends the investment case (research: "S tariff, finance").

The own site keeps today's reliability study: B1 requests `study` for every project with an EH template, and the Guided Goal card runs it today. These are proposals for the template authors, not domain-reviewed defaults. A sub-option that is "not yet available" is never pre-selected.

## 4. Capacity can be deselected (owner Q18 = A)

- **Sizing off = dispatch on the sizes of a chosen source** (gatekeeper, 2026-10-20). Simply flipping extendable off is not supported: preflight rejects every never-built candidate at zero capacity (`generator_p_nom_invalid`, `link_p_nom_invalid`).
  - The mode reuses the existing `adequacy.sweep.freeze_capacities`: extendable stays on, and each size is clamped to its source value.
  - The source is either **a capacity run** (its `*_nom_opt`) or **the configured nameplate**. The run records which one, and is refused when that source has no sizes. Before any solve, `p_nom_opt` is 0.
  - It is a new solver-config run option, a gated slice built with #448 §3.1's fixed-size island mode. **It is not runnable today**: the sub-option reads "Not yet available" until that slice lands.
  - It is incompatible with the myopic and rolling strategies, which freeze capacity themselves; the card says so.
- **Sizing off is visible where the sizes live.** Each extendable asset's Sizing block (section 5) reads "Not sized in this run (Capacity › Asset sizing is off); runs at X MW from <the capacity run of <date> | the nameplate>". Run's summary says "Dispatch on fixed sizes from <source>". Results label capacities "fixed, not optimised".
- **The whole card off.** This is allowed only when another source provides the operating points, for example a saved or uploaded dispatch for a load-flow-only study. AC power flow needs a solved dispatch today (`dispatch_status` must be fresh). Until W0 proves the uploaded-dispatch path, "Capacity off" reads "Not yet available: needs a dispatch source".
- **A commercial-only study** (a bill and an investment case on fixed assets) uses Capacity with sizing off, once that option lands, so the bill reads a real dispatch. Until then it runs with sizing on.

## 5. Owner choices

| | Choice | Recommended | Status |
|---|---|---|---|
| Q17 | Reliability: its own card (goal & study, stress & FMEA, outage screening) / an option inside Capacity | its own card | **A** (about 20:11 CEST) |
| Q18 | Capacity deselectable: yes, within what the engine supports (sizing off → dispatch on fixed assets; the whole card off only with a dispatch source, after W0) / always on (Q2) / fully free, on raw setpoints | yes, within support | **A** (about 20:11 CEST) |

## 6. What changes in the slice plan

- **F1 Fixed-size run option** (new, gated, with #448 §3.1's island mode; workspace-reconciliation §13): `freeze_capacities` driven by a chosen size source; refused without sizes; incompatible with myopic and rolling.
- **W4a**: six cards, each with up to three sub-options, in `flow_scope.json`. Done when the off-card test passes: a deselected card's items don't count, aren't "to check", and don't block; its kinds leave `run_request`; its values survive an off/on round-trip.
- **W0**: classifies each sub-option (§1's last column). A combination is runnable only when all its members are supported.
- **W2 / templates**: each example's card setup (§3).
- **W10**: results tabs per card; "Outside scope" for deselected cards.

## 7. The GridSpine research on the six cards (GS-0)

Owner decision, 2026-10-21: **keep six cards and map the research.** The research in draft PR #491 (branch `claude/gridspine-research-publication`, `.scratch/gridspine-open-source-research/`) proposes five cards with up to five features each, and puts Hand-off outside the cards. This document's model stands: six cards, Hand-off is one of them, and each card has **at most three sub-options**. Every research feature is mapped onto an existing sub-option below. Where a feature does not fit one, it becomes something smaller than a sub-option (an input provider, a metric, or a post-run evaluation inside a sub-option), never a fourth sub-option.

Research ids carry the prefix **`GS-`** here (GS-B1 is the research's B1, BLAST-Lite ageing). The research reuses B1, B2, C1, A1, A2, R1, P1, W1, Q1 and P0–P3, which all mean something else in these documents. Its priority labels P0–P3 are written **prio 0–3**.

### 7.1 Features → sub-options

| Research feature | Card › sub-option | How it fits |
|---|---|---|
| `energy.capacity` | Capacity › 1 Asset sizing + 2 Dispatch & economics | The research keeps sizing and dispatch together; here they split, and "sizing off" is F1 (§4, workspace-reconciliation §13) |
| `energy.process_detail` | Capacity › 3 Process detail | The same sub-option |
| `energy.thermal` | Capacity › 3 Process detail | A thermal performance map is a process provider (GS-T1, GS-DC1) |
| `energy.demand_baseline` | **input provider** for section 4 (Loads & demand), not a sub-option | It produces a load profile, which is an input. It never runs as a study, and it shows as a source on the load (TSG-01's provenance rule) |
| `energy.lifecycle` | **post-run evaluation** inside Commercial › 3 Investment case; its usable-energy result feeds Reliability › 1 | Battery ageing on the frozen dispatch (GS-B1) changes replacement timing in the case; the feedback (GS-B2) is a reviewed proposal, never a silent edit (integration-sensitivities §3) |
| `grid.load_flow` | Grid › 1 Load flow | The same sub-option |
| `grid.sizing`, `grid.compliance` | Grid › 2 Grid code & electrical design | The research splits what Grid 2 already bundles |
| `grid.contingency` | Grid › 3 N-1 security | SCLOPF reach only; wider contingencies stay "Not yet available" (W0) |
| `reliability.adequacy` | Reliability › 1 Reliability goal & study | The same |
| `reliability.critical_service` | Reliability › 1 (the goal) and › 2 (the stress study) | |
| `reliability.stress` | Reliability › 2 Stress conditions & FMEA | The same |
| `reliability.production` | **a metric** inside Reliability › 1 | Lost product next to LOLE/ENS (GS-R1) |
| — | Reliability › 3 Outage-data screening (COPT) | No research counterpart; GS-V1 (PRAS) is its benchmark |
| `commercial.tariff` | Commercial › 1 Tariffs & bill | |
| `commercial.contracts` | Commercial › 2 Contracts & PPAs | |
| `commercial.merchant_risk` | Commercial › 2 Contracts & PPAs | Market exposure of the IPP's unsold volume, beside its PPA (GS-I1) |
| `commercial.finance` | Commercial › 3 Investment case & parties | |
| `control.policy_replay` | Control › 1 Converter control presets, read as **control strategy** | A strategy is chosen in 1; replaying it is the check run by 2 |
| `control.grid_forming` | Control › 1 (presets) and › 2 (island & ride-through screen) | |
| `control.process_restart` | Control › 2 Island & ride-through checks | Restart and pick-up after an island event (GS-M2) |
| `control.dynamic_validation` | Control › 3 Frequency & dynamics | |
| Hand-off (outside the research's cards) | **Hand-off** card, 1 Reports · 2 Data exports · 3 Specialist packet | Kept as a card (owner Q1 = A). It is never labelled Reliability (the research's GS-F9 rule holds) |
| Shared context (project, assets, planning) | sections 1–6 | Not a card in either model |

The research's "2^5 = 32 card selections, each a plan or a precise refusal" becomes **every combination of six cards and their sub-options**, decided by the W0 matrix (§2: a combination runs only when every member is supported).

### 7.2 The 38 slices → cards and §13

| GS id (prio) | What | Card › sub-option | Here it is |
|---|---|---|---|
| GS-F0 (0) | Current-source map and overlap reconciliation | — | **this section** |
| GS-F1 (0) | Server-authoritative card/feature registry | — | **W4a** (`flow_scope.json`, six cards) |
| GS-F2 (0) | Optional worker admission on native jobs | — | **GS-X0**, the isolated optional-worker path (§7.3) |
| GS-F3 (0) | Assurance dispositions for adapter outputs | — | checked-results S1; a precondition of GS-X0 |
| GS-F4 (0) | Chronological staged process support | Capacity › 3 | grid-to-process 09; W0's "detailed process" row |
| GS-F5 (0) | All-port product results and counterfactual bases | Capacity › 3 | grid-to-process 04/06 |
| GS-F6 (0) | Dependency resolver, standalone input providers | — | **B1** `run_request` + **W0** verdicts; "Capacity off needs a dispatch source" (§4) |
| GS-F7 (0) | Preview/apply selection lifecycle, invalidation | — | **W4a** (§2's not parameterised / not run / not lost) |
| GS-F8 (0) | Exact-adapter availability register | — | **W0** matrix (native kinds); GS-X0 for adapters |
| GS-F9 (0) | Legacy grouping and registry-version migration | Hand-off | **W4a** (`flow_scope.json` `version`) |
| GS-C1 (1) | Native C&I meter and bill reference | Commercial › 1 | commercial-finance tariff/bill work |
| GS-D1 (1) | oemof-demand synthetic profile | section 4 input provider | after GS-X0 |
| GS-D2 (1) | OpenDSM measured baseline | section 4 input provider | after GS-X0 |
| GS-B1 (1) | BLAST-Lite frozen-dispatch ageing | Commercial › 3 (lifecycle evaluation) | **first** adapter after GS-X0 |
| GS-B2 (1) | Lifecycle feedback into finance/reliability | Commercial › 3 → Reliability › 1 | after GS-B1 |
| GS-H1 (1) | H2Integrate feasibility, one H2 evaluator | Capacity › 3 | after GS-X0 |
| GS-H2 (1) | H2 planning envelope and replay | Capacity › 3 | after GS-H1 |
| GS-A1 (1) | Extend the merged ammonia boundary | Capacity › 3 | after GS-H1, #418 |
| GS-R1 (1) | Production-aware reliability | Reliability › 1 (metric) | after GS-F4/F5 |
| GS-M1 (1) | Native concentrator example | template | grid-to-process 10; a §3 example row when it lands |
| GS-U1 (1) | Cards, one example per family, Guided/Expert parity | all | **W2, W3a, W3c** on six cards |
| GS-T1 (2) | TESPy thermal map | Capacity › 3 | after GS-X0 |
| GS-DC1 (2) | Calibrated cooling evaluator (HPE subset) | Capacity › 3 | after GS-X0 |
| GS-DC2 (2) | Data-centre integrated example | template | after GS-DC1, GS-B2 |
| GS-I1 (2) | Reconcile #453, extend IPP scenarios | Commercial › 2 | commercial-finance |
| GS-I2 (2) | Optional market-data acquisition | Commercial › 1/2 inputs | library-and-live-lookup; integration-sensitivities §1 |
| GS-Q1 (2) | Extend tariff oracle coverage (PySAM) | Commercial › 1 | test oracle, no card change |
| GS-O1 (2) | Microgrid rule-based/feedback policy comparison | Control › 1 → 2 | after W0's `control_checks` row |
| GS-O2 (2) | python-control adapter, one plant | Control › 3 | after GS-X0, GS-O1 |
| GS-M2 (2) | Mining restart/pick-up coupling | Control › 2 | grid-to-process 11–13 |
| GS-A2 (2) | Ammonia operational/reliability refinement | Capacity › 3, Reliability › 1 | after GS-A1, GS-R1 |
| GS-P1 (3) | One more H2-to-product recipe | Capacity › 3 | deferred |
| GS-W1 (3) | WaterTAP treatment envelope | Capacity › 3 | deferred |
| GS-O3 (3) | do-mpc strategy (LGPL-3) | Control › 1 → 2 | deferred |
| GS-G1 (3) | One dynamics adapter (ANDES / OpenIPSL) | Control › 3; Hand-off › 3 | deferred; GPL-3 for ANDES |
| GS-G2 (3) | Detailed distribution adapter (OpenDSS) | Grid › 1 | deferred |
| GS-V1 (3) | Bulk-adequacy benchmark (PRAS) | Reliability › 3 | deferred |
| GS-M3 (3) | Haulage-to-energy demand adapter | section 4 input provider | deferred |

No research slice needs a fourth sub-option on any card. Eight are already covered by a W-slice (GS-F0, F1, F6, F7, F8, F9, U1 and, through checked-results, F3); they are not built twice.

### 7.3 External engines: an ordered follow-up (owner, 2026-10-21)

External Python engines come in through **one isolated optional-worker path**, never into the app's own environment, and only after **W0, W4a and W3a** have landed. It is recorded here as an order, not built.

1. **GS-X0 Optional-worker path** (GS-F2 with GS-F3's dispositions): a pinned, separately installed environment per engine, run as a native job with bounded time and memory, inputs and outputs through declared schemas, and a licence inventory. Separate environments are required, not a convenience: BLAST-Lite needs NumPy < 2 and OpenDSM needs NumPy ≥ 2. Gate: gatekeeper; a security review of the worker boundary.
2. **GS-B1 BLAST-Lite** first: one chemistry on a declared temperature/SOC/duty range, evaluated on a frozen dispatch (it reads F1's or the capacity run's dispatch; optimisation weights are never used as ageing time). Then GS-B2's reviewed feedback.
3. Then, each its own gated slice with the four gates of the research's ADAPTER-READINESS (plan, build, availability, domain): **H2Integrate** (GS-H1; its PEM water-unit and clipping defects fixed or refused), **OpenDSM** (GS-D2), **TESPy** (GS-T1), **python-control** (GS-O2; a study only, never a grid-forming certifier).

An engine without an admitted adapter reads "Not yet available" in its sub-option, the same rule as an unknown W0 row.
