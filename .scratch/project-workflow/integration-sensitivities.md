# Integration, historical prices, PPA defaults and sensitivities

Status: draft for UX review. Owner answered Q11 and Q12 on 2026-10-20, about 19:09 CEST (§7).
Date: 2026-10-20
Author: workspace-reconciliation session
Builds on:
- [workspace-reconciliation.md](workspace-reconciliation.md) (#444): groups as a scope, one Run, W/P slices;
- [commercial-finance.md](commercial-finance.md) (#447): section 7, K slices;
- [design.md](design.md): the flow, R1–R4.

## The owner's words

2026-10-20, about 19:05 CEST (speech-to-text, lightly cleaned up by the coordinator):

> "We should have, if possible, check for the prices, if historical prices are available, and also if PPA contracts can have some default parameter values of the PPA contract forms, or, you know, specific region. For example, Europe would be the target region on that. Ideally, Europe at the moment. Or you want something to default parameter, just for push off a model. And then the core idea is that the different parts, the capacity expansion, power flow, load flow, etc., they connect with each other, right? And so there's an integrated integration between the different sections. And you also allow to parameterize individually in expert mode and in guided mode if you have not checked one box, but then you want to check one box later within each group, then you can still add additional parameters. That's also very important. Also, consider how we can lay out ... different sensitivity analysis. We have a base case, then we have compare cases that should enable fast comparison of results between the sensitivities in terms of simulation results. So fast parametrization: we should be able to copy and duplicate existing base case or the sensitivity base. And then the fast parametrization of two parameters. And then you run the simulation, and then you have comparative results. It's very important in terms of the overall result."

## What exists (master after #444; facts checked on the branch)

| Piece | Where | State |
|---|---|---|
| Price series in the library | `services/library/series_store.py` (`SeriesMeta`: source, vintage year, provider, settlement, unit, label; timezone in the payload header) | upload only (CSV/XLSX); **no currency or region on a series**; `PriceSeriesRef` carries no unit, currency or region |
| Historical price fetch | — | **not found** (no ENTSO-E or other feed) |
| Market regions and packs | `MarketRegion` (DE, NL, GB, US_*, CA_ON, AU_NEM), `MarketPack` in `models/commercial.py` | defined, **no consumers** |
| PPA and contract model | `PpaContract` (pay-as-produced, baseload, as-consumed BTM, sleeved), `CfdContract`, DR, lease, EaaS, retail | `price` has no default; **no swap and no shaped-profile contract type**; **no PPA defaults** in any pack |
| Scenarios | `POST /{base}/scenarios` (`routers/projects.py`) | a scenario is a **full copy** of the base bundle with a parent link; switch with `activate` |
| Experiments | `services/experiments.py`, `harness/experiment_*` | 1–8 explicit cases, up to 12 typed changes each (dotted path, typed value); per-case ledger with receipts; **chat-only, no REST route** |
| Sensitivity sweep | `services/workflow_tools.py:run_sensitivity_sweep` | 1–4 cases; validates on isolated copies, creates child scenarios, enqueues each; needs a saved baseline |
| Solve queue | `services/solve_queue.py`, `routers/solve_queue.py` | sequential, per project |
| Compare | `pages/CompareView.tsx`, `services/compare/*`, `routers/compare.py` | **A/B pairwise only**; 10 tabs |
| Copilot LC-17 / LC-18 | `.scratch/live-copilot/issues/17-…`, `18-…` | ready-for-agent, **not started** |

---

## 1. Historical prices: what is available, and checked

The owner chose Q8 = A: users bring their own market data, and nothing is redistributed. Europe comes first.

- **Where.** Section 7c (Markets & value streams) gets a **"Price data available"** panel. The **bidding zone** has its home in 7c: it is derived from the section-1 pin and can be overridden only for multi-zone countries. It reads the site's **bidding zone** and the project's **years**:
  - the zone is derived from the section-1 pin's country; a multi-zone country (NO, SE, DK, IT) asks the user to pick its zone;
  - the years come from the horizon (section 1), or from the weather year where profiles use one.
- **Two sources, both the user's own:**
  1. series the user or org has uploaded to the library;
  2. **ENTSO-E Transparency day-ahead prices**, fetched with **the user's own API token**. The token is stored as a user or org secret, never in a project or bundle. The fetch writes an ordinary library series (`source: entsoe`, provider, vintage year, zone), so it is pinned like any other.
- **The check, per zone and year:**
  - coverage (hours present / expected);
  - gaps;
  - resolution (hourly, 15 min);
  - timezone;
  - unit and currency;
  - negative-price hours.

  The panel says, in words, "DE-LU day-ahead, 2019–2025, 100 % of hours" or "NO2 2021: 312 hours missing". A gap is never filled silently: a series with gaps is refused where a full year is needed (`_export_price` already refuses uncovered snapshots).
- **What it feeds**, as library series picked in the usual editors:
  - the export price (7a);
  - a PPA or CfD reference price (7b);
  - the market bundle (7c);
  - the illustrative price shapes, which stay labelled `illustrative`.

  Each of these is an `lp_input` when the LP reads it (#447 §2.0).
- **Engine rules (gatekeeper, 2026-10-20):**
  - a new external fetch service, with timeouts and typed error kinds. A missing or invalid token gives a typed refusal, and the token never appears in logs, series meta or error text;
  - `SeriesMeta` (extra = forbid) and `PriceSeriesRef` gain **currency, `price_basis`, `currency_year` and region**, with defaults. The `PriceSeriesRef` fields go in `services/commercial/hashing.FIELDS_AFTER_V1` (default None), so existing hashes don't move;
  - **region is the ENTSO-E bidding zone** (DE-LU, NO1…NO5, SE1…SE4, IT-*), not `MarketRegion` (a tariff-market literal that can't name zones);
  - **times**: stored in UTC with `source_time_basis`, as in #445;
  - **resolution**: the native one is recorded (day-ahead is 15 min since the SDAC MTU change in October 2025). It is resampled to the snapshot step by a time-weighted mean, and the panel says so;
  - **money basis**: a multi-year series is `price_basis: nominal` with a `currency_year` per delivery year. It is converted to the study's real basis with the existing money-year escalation, never a second one;
  - **gaps** follow the load-upload rules. **Negative prices are kept**: CfD `suspend_on_negative_price` reads them.

---

## 2. PPA defaults by contract form, Europe first

Defaults exist to "push off a model". They are **never market data**.

- One **library item per contract form**, scope `common`, shipped in a defaults-pack version:
  - pay-as-produced;
  - baseload;
  - as-consumed / shaped;
  - sleeved;
  - CfD;
  - swap.
- Each carries:
  - `region: EU`;
  - `source: illustrative`, `assumed`, with a cited public reference range;
  - `currency: EUR`, `currency_year`, `price_basis`;
  - the **key parameters** Guided shows (#444 §10a, #447 §4).

| Form | Key parameters (Guided) | Expert adds | Model today |
|---|---|---|---|
| Pay-as-produced | price, tenor, asset | indexation, volume cap, floor/cap (market + premium), base year | `PpaContract` `pay_as_produced` |
| Baseload | price, MW, tenor | indexation, volume cap | `PpaContract` `baseload` |
| As-consumed / shaped | price, tenor, load | shape, volume cap | `as_consumed_btm` behind the meter; the **shaped off-site (virtual) PPA** is a financial CfD on a shape (the swap row's notional-volume CfD) |
| Sleeved | price, sleeving fee, tenor | sleeving party, indexation | `PpaContract` `sleeved` |
| CfD (financial / virtual PPA) | strike, reference series, tenor | capture mode, negative-price suspension, indexation | `CfdContract` |
| Swap (fixed for floating) | fixed price, volume, reference series, tenor | settlement | **one type, not a new one** (gatekeeper): `CfdContract` gains a volume basis: either `asset_ids` or a notional volume (baseload MW, or a shape series), exactly one, checked by a validator. A notional-volume CfD settles only and is not an LP term. The new fields go in `FIELDS_AFTER_V1`. It is a gated plan when built (K7) |

**Numbers** (owner Q11 = A): one illustrative EU-wide set authored by us, with each value's public reference range cited, and labelled so it can't be mistaken for data. Zone values (DE, NL, ES, Nordics) come only from an org pack (Q10 = A) or the owner's sourcing. The defaults never set `changes_dispatch`.

---

## 3. Integration: how the groups feed each other

The groups share one model. Every hand-over below already exists in code, and each has its own staleness rule. The design makes them **visible**: each producer → consumer edge shows in the consumer's results as **fresh / stale (why) / not run / outside scope**, and "why" names the upstream change.

| From | Data | To | Stale when (existing mechanism) |
|---|---|---|---|
| Sections 1–8 (inputs) | the network and its settings | Capacity: the optimisation (sizing, dispatch, economics) | the network revision moves (`dirty_state.revision`, study `edited_since`) |
| 7 `lp_input` prices | tariff terms, export price, dispatch-changing PPAs, discount rate, CO₂ path | the optimisation | the same; a library pin moving (`LibraryRefStale`) |
| The optimisation | **sized assets** (`p_nom_opt`, `s_nom_opt`) | Grid: the campus electrical draft (`gridspine/producers/campus.py:_rating` uses the optimum for extendable assets); the SCR gate in `eh_readiness` (prefers `p_nom_opt`) | campus `_stale` (network sha, campus file, library); `_milp_stale` |
| The optimisation | **dispatch** | Grid: AC power flow (copies LP dispatch into `p_set`) | `dispatch_status` ≠ fresh → AC PF refused, load-flow summary `stale` |
| The optimisation | dispatch | Control: the island study's hourly tables | the network revision; note: the island study **re-optimises** sizing (`services/solver/island.py`) instead of reading the optimum, see §3.1 |
| The optimisation | dispatch, sizes | Commercial: bill, settlement, investment case | IC `assumptions_hash` (`investment_case_runner.staleness`, extended per #447 §8) |
| Grid: AC PF, campus study, scorecard | loading, voltage, PCC compliance, verdicts | **back** to sections 3, 5 and 9 as flow items (warnings) | each item carries its source's freshness |
| Control: GFM tuning | recommended droop, H, i_max | **back** to section 5 Controls through the EditCard; the hub then needs a re-solve (`_hub_solved_since`) | the applied record |

**Feedback never edits inputs silently.** A downstream finding (an overloaded transformer, a grid-code fail, a tuning recommendation) comes back as a flow item on its home section. Or it comes as a proposed change on the EditCard (old → new, section named). The user, or the copilot with confirmation, applies it, and the one Run reruns what is stale.

**One Run reruns what is stale.** The flow endpoint's `results.freshness` (B1) grows to one freshness per run kind, `{optimisation, study, loadflow, control_checks, investment_case}`. Run then offers "Run (3 stale)" over the requested kinds whose results are stale. Per-kind freshness **never feeds `run_ready`**: "Run (n stale)" is a convenience, and the gate stays R1. This is a B1 follow-up, and the gatekeeper is told. **Staleness is a hash of the inputs each kind reads, never timestamps** (gatekeeper). The grid-readiness scorecard is not a run kind; the investment case has the wider staleness of #447 §8.

### 3.1 One inconsistency to settle (gatekeeper)

The campus draft and the SCR gate read the **optimised** sizes. The island study **re-optimises** its own. The gatekeeper's answer: offer a **fixed-size island mode**, "use the capacity plan's sizes".
- It copies the plan's `p_nom_opt` → `p_nom` with extendable off, and records the source run and its hash. It goes stale when that plan reruns.
- As a bonus, fixed gensets get real unit commitment (extendable ones are skipped today).
- The default stays the current re-sizing mode, byte-identical.
- The campus draft and the SCR gate say which sizes they used.

---

## 4. Turning a group on later

A group or a sub-option can be switched on at any time, in Guided or Expert, from the Builder card or section 8's scope component (the same component, W4a). Turning it on:
- **reveals** the group's blocks in their sections;
- **fills only empty fields**, from the library default and the template's values, marked `assumed`, with the template's recheck flags. A value already set, including one set while the group was off, is kept;
- moves its items to `in_scope: true`, so section states update live;
- adds its kinds to `run_request`. Its results read **"not run"** until the next Run.

Turning it off hides without deleting: values stay, items become `in_scope: false`, and results read "outside scope" but are kept.

W4a's done-when gains a round-trip test: on → set values → off → on keeps every value and source; template defaults never overwrite a user value. The copilot may propose switching a group on, on the EditCard; it never switches one silently.

---

## 5. Sensitivities: a base case and compare cases

### 5.1 Model
- **The base case** is the project, or a scenario the user marks as base.
- **A compare case** is a scenario child: today's full copy, with the parent link. It also records its **declared changes** in the experiment contract's shape: dotted path, typed value, old → new. "What differs" is therefore explicit, not inferred by diffing networks (LC-18's declared input differences).
- **Limit:** a base plus up to 8 cases (owner Q12 = A; the experiment limit). Each case changes 1–2 parameters by default, and up to 12 in Expert.

### 5.2 Screens

**Section 8 › Scenarios & sensitivities** (W7, the old B5 rows):
- one row per case: name, the changed parameters (old → new), which sections they override (Physical 2–6, Commercial 7, Optimisation 8), and the state (not run / queued / running / fresh / stale / failed);
- **"Duplicate and change"** from the base or any case. It opens a small form:
  - pick one or two parameters, through a search across sections that opens each field's **own editor**;
  - set the new values;
  - name the case (derived from the change, editable).

  It creates the copy and records the change.
- **Grid helper**: two parameters × a few values each, making at most 8 cases (e.g. 2 × 4).
- **"Run cases"** queues one Run per case through the solve queue, using the existing sweep path:
  - an unsaved base asks first, never saving silently (LC-17);
  - a failed case doesn't block the others;
  - one Run per case runs that case's requested kinds.

**10 › Compare** (extends today's A/B Compare to base + N):
- **KPI table**: total cost, capex, opex, NPV/IRR (when Commercial is on), LOLE/ENS, emissions, curtailment, peak import, max loading. Base and each case side by side, with deltas and delta % computed in software.
- **Charts** overlaid per tab (capacity, dispatch, prices, loading, …), reusing `services/compare/*`.
- **Incomparable cases** listed with the reason, e.g. a different horizon, a different currency, or a case not run.
- **Export** through the existing path.

**Copilot.** LC-17 (create and run) and LC-18 (compare) are the chat path over the same objects. Each chat-made change shows on the EditCard (old → new, section named).

### 5.3 Rules

- **Declared changes stay true.** While a case is the active project, every write through the normal write path (asset writes, section editors, table edits, the EditCard, the copilot) appends to the case's declared changes (path, old → new). A path that can't append (for example an import that replaces the network) marks the case "edited outside its declared changes". Compare then shows that case's differences as "declared + other edits (see diff)" and links to a network diff against the base. S5's done-when includes a test: edit a case through a normal editor, and its declared changes include the edit.
- **Never edit the wrong project.** While a case is active, a persistent banner shows in both modes: "Editing case <name> (base: <base>) · Back to base". The Duplicate-and-change form writes only to the new case, and its header names the case. Switching between the base and its cases keeps the section (`activeSection`, #444 §7).
- **Each case stores its base's hash at copy.**
- **Re-applying onto a changed base** refuses, change by change, when the target is gone or the base value under it changed since the copy. It never overwrites silently.
- **Compare refuses or labels a case** whose snapshots, money basis or time basis differ from the base's.
- **A case's results are stale when its own inputs change.** They are **not** stale when the base changes after the copy: a copy is a copy, and the row says "base changed since copy". Re-deriving a case from a changed base is an explicit action ("Re-apply changes to the current base").
- A case may switch groups on or off. Compare then lists the group-specific KPIs as "outside scope" for the cases without it.

### 5.4 Check answers: what the user sees (owner input #6)

> Owner, about 19:12 CEST, relayed by the coordinator: with **Check answers** on, every result the copilot states or charts, in every section and results view (the sensitivity compare included), is verified internally by the harness, with no hallucination.

The verification itself is the "Check answers" package (session_01WZre2YpT3SyvNWHk48KsiN). This section is only the **display contract**. It reuses the words the app already shows (`CompanionOutcome.tsx`, `CheckedEvidenceCard.tsx`, the ChatPanel "Check answers" toggle).

**What carries a check state.** Only **copilot-authored** content does:
- a stated value or sentence;
- a copilot-generated chart;
- a copilot narrative over Compare;
- an "Explain this" note on a results tab.

The app's own result views (tables and charts computed by the software from the engine's outputs) don't get a check badge: they *are* the evidence. They show their freshness instead (§3, S4). The two are never mixed, so a native figure never looks "unchecked".

**States**, the same labels everywhere:

| State | Label | Shown with |
|---|---|---|
| checking | "Checking saved answer" | spinner; no value shown yet |
| all requested results checked | "Requested results checked" | the count of facts checked, evidence links, the export, "This does not certify the engineering model" |
| partly checked | "Checked evidence and limitations" | each result's disposition and reason; the limitations list |
| none checkable | "Requested results unavailable" | the reason; no value stated |
| Check answers off | "Answer not checked" | the plain answer, labelled |
| interrupted | "Response incomplete — not checked" | — |

**Copilot charts.**
- With Check answers on, a copilot chart draws only series whose every point traces to evidence. Series it can't check are left out and listed under the chart ("Not drawn: <series> — <reason>"); they are never drawn unlabelled.
- With Check answers off, the chart header reads "Not checked" and links to the native view of the same data.

**Compare (§5.2).**
- The KPI deltas are computed by the software, not by the copilot, so they need no check badge.
- When the copilot summarises a comparison, each case column carries its check state.
- A case that **can't be checked** (not run, stale, incomparable, evidence missing) gets a greyed column headed "Not checked: <reason>". The copilot states no value or delta for it, and the native table still shows whatever the software computed, with its freshness.

**Results by group (W10).**
- Each group tab shows the freshness of its native results.
- Copilot notes on a tab ("Explain this", a pinned copilot chart) each carry their own check state, using the labels above.
- A tab whose group is out of scope reads "Outside scope". The copilot states no result there.

**Placement.** Check states on transient companion cards follow #444 §9: they dock into the launcher's footprint or collapse, and never cover content.

---

## 6. Slices

These join the W/P/K plan.

| Slice | What | Gate | After |
|---|---|---|---|
| **S1 Historical prices** | §1: bidding zone from the pin; ENTSO-E fetch with the user's token (secret storage); the availability check; `SeriesMeta` / `PriceSeriesRef` currency, basis, year, region | **gatekeeper** (external fetch, series schema); UX | K2 |
| **S2 PPA defaults** | §2: one library item per form (EU, illustrative) in a pack version; swap and shaped-profile gaps as a gated model plan | gatekeeper (contract model); UX | K2, K7, Q11 |
| **S3 Group on/off round-trip** | §4 in W4a's done-when (fill-empty-only; keep on off) | UX | W4a |
| **S4 Freshness per kind** | §3: `results.freshness` per run kind; "Run (n stale)"; edge reasons in results | gatekeeper told (B1 follow-up); UX | W4b |
| **S5 Cases** | §5.1–5.3: case changes recorded, and kept true on every later write (test: an edit through a normal editor appears in the declared changes; non-appending paths mark "edited outside"); the case banner and "Back to base"; section-8 rows (W7); Duplicate and change writing only to the new case; grid helper; Run cases | gatekeeper (run and staleness semantics); UX | W7, W4a |
| **S6 Base + N compare** | §5.2: Compare for base + up to 8, KPI deltas, overlays, incomparable list | UX; gatekeeper on comparability | S5 |
| **S7 Copilot** | LC-17 and LC-18 over S5/S6 | per LC-05 | S5, S6 |
| **S8 Check-answers display** | §5.4's display contract on copilot statements, charts, Compare narratives and results-tab notes; the verification is the Check-answers package's | UX; aligned with session_01WZre2YpT3SyvNWHk48KsiN | that package's contract, S6, W10 |

---

## 7. Owner choices

| | Choice | Recommended | Status |
|---|---|---|---|
| Q11 | PPA default values: our illustrative EU-wide set with cited ranges / owner-sourced zone values as a pack / no defaults | our illustrative set; zone values later from a pack | **A** (about 19:09 CEST) |
| Q12 | Sensitivity size: base + up to 8 cases, 1–2 parameters each, grid helper / pairwise only / unlimited grids | base + 8 | **A** (about 19:09 CEST) |
