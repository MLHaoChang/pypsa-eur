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
| **Capacity expansion** | 1. Asset sizing (expansion) · 2. Dispatch & economics · 3. Process detail | `optimisation` | 1, 2 yes; 3 not yet available (W0) |
| **AC power flow & grid** | 1. Load flow (AC power flow after the optimisation) · 2. Grid code & electrical design (scorecard, campus PCC compliance, transformer and compensation sizing) · 3. N-1 security (SCLOPF) | `loadflow`; campus study; SCLOPF as an `optimisation` option | 1 bundled (needs dispatch); 2 scorecard advisory, campus study per W0; 3 yes |
| **Control** | 1. Converter control presets · 2. Island & ride-through checks · 3. Frequency & dynamics | `control_checks` | 1 inputs only; 2 per W0; 3 not yet available |
| **Reliability** (owner Q17 = A) | 1. Reliability goal & study (LOLE / ENS) · 2. Stress conditions & FMEA · 3. Outage-data screening (COPT) | `study` | 1 yes; 2 yes (sweep); 3 yes (no solve needed) |
| **Commercial & finance** | 1. Tariffs & bill · 2. Contracts & PPAs · 3. Investment case & parties | the LP's tariff/PPA terms (inside `optimisation`); `investment_case` | 1, 2 yes; 3 per W0 |
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

| Example | Capacity | Grid | Control | Reliability | Commercial | Hand-off |
|---|---|---|---|---|---|---|
| Data centre | 1, 2 | 1, 2 | 1, 2 | 1, 2 | 1, 3 | 1 |
| Island microgrid | 1, 2 | 1 | 1, 2 | 1, 2, 3 | 1, 3 | 1 |
| Hydrogen hub | 1, 2 | — | — | 1 | 1, 2, 3 | 1 |
| Ammonia (after #418) | 1, 2 | — | — | 1 | 1, 3 | 1 |
| Own site (`eh_site`) | 1, 2 | — | — | 1 | 1 | 1 |
| Blank / import | 1, 2 | — | — | — | 1 | 1 |

The own site keeps today's reliability study: B1 requests `study` for every project with an EH template, and the Guided Goal card runs it today. These are proposals for the template authors, not domain-reviewed defaults. A sub-option that is "not yet available" is never pre-selected.

## 4. Capacity can be deselected (owner Q18 = A)

- **Sizing off.** Turning Capacity's **sizing** off runs **dispatch on the fixed assets**: the optimisation with expansion disabled for the run (extendable off, as in #448 §3.1's fixed-size mode). This is supported.
- **Sizing off is visible where the sizes live.** Each extendable asset's Sizing block (section 5) reads "Not sized in this run (Capacity › Asset sizing is off); runs at its current size of X MW". Run's summary says "Dispatch on fixed assets". Results label capacities "fixed, not optimised".
- **The whole card off.** This is allowed only when another source provides the operating points, for example a saved or uploaded dispatch for a load-flow-only study. AC power flow needs a solved dispatch today (`dispatch_status` must be fresh). Until W0 proves the uploaded-dispatch path, "Capacity off" reads "Not yet available: needs a dispatch source".
- **A commercial-only study** (a bill and an investment case on fixed assets) uses Capacity with sizing off, so the bill reads a real dispatch.

## 5. Owner choices

| | Choice | Recommended | Status |
|---|---|---|---|
| Q17 | Reliability: its own card (goal & study, stress & FMEA, outage screening) / an option inside Capacity | its own card | **A** (about 20:11 CEST) |
| Q18 | Capacity deselectable: yes, within what the engine supports (sizing off → dispatch on fixed assets; the whole card off only with a dispatch source, after W0) / always on (Q2) / fully free, on raw setpoints | yes, within support | **A** (about 20:11 CEST) |

## 6. What changes in the slice plan

- **W4a**: six cards, each with up to three sub-options, in `flow_scope.json`. Done when the off-card test passes: a deselected card's items don't count, aren't "to check", and don't block; its kinds leave `run_request`; its values survive an off/on round-trip.
- **W0**: classifies each sub-option (§1's last column). A combination is runnable only when all its members are supported.
- **W2 / templates**: each example's card setup (§3).
- **W10**: results tabs per card; "Outside scope" for deselected cards.
