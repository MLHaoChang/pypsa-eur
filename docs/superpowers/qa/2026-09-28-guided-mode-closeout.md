# Guided mode: close-out (P22.9, P23–P26)

**Date:** 2026-09-28. **Branch:** `claude/epic-allen-k2t1c4`, on top of `7bc2233` (P25 GO).
**Spec:** [`docs/superpowers/specs/2026-09-27-guided-mode.md`](../specs/2026-09-27-guided-mode.md).
**Plan:** [`docs/superpowers/plans/2026-09-27-guided-mode.md`](../plans/2026-09-27-guided-mode.md). The P26 section lists the fixes made in this phase.

This note records what was implemented and observed before the independent P26 gate. It is not a gate verdict.

## 1. What Guided covers end to end

These steps were checked by `smoke-guided.mjs --phase P26` on all three Energy Hub templates, each in a fresh first-time browser context:

1. **First run.** A browser with no app keys starts in Guided, and that choice is stored implicitly. Every new project starts in Guided unless the user has chosen a mode explicitly. This covers every wizard tab, `Sidebar.newProjectMut`, `ProjectsHomePage.createBlank`, the "+" tab, and the import paths (spec §3.4 and the §10 P23 addendum).
2. **Chrome.** The sidebar shows the Assistant, Hub design, and the project basics (Save, Recent, Projects home). Results shows two tabs, Adequacy and FMEA. Everything that is hidden stays reachable through the command palette and through the assistant (`ui_open_panel`). An explicit request for a hidden Results tab is honoured as a temporary "Advanced" tab.
3. **Hub design, five steps.**
   - **Start:** the three templates, with the template's provenance.
   - **Site:** four readiness rows, each gap with a "Fix with the assistant" button. The one choice is the site type. A "Next: Goal" button follows.
   - **Goal:** the allowed shortfall, which defaults to the template or pack value. The price of undelivered energy is shown read-only, and the assistant can set it. Then **Run study**.
   - **Results:** a plain-language headline (§5.5), the yearly cost of the design, the biggest risks, what the study could not establish, and "Open full report". A "Next: Improve" button follows.
   - **Improve:** the high and medium findings, each with Why / "Let the assistant do this" / Ask. Also "Check risks (FMEA)" and "Add a stress scenario".
   - The rail moves to Results when a study finishes. The `hub_design` tour walks every step.
4. **The assistant does the steps.** Card buttons send plain requests. The chat request carries `ui_mode` / `guided_step` in Guided only, and the Guided addendum sits in the per-turn user content. `suggest_eh_setup` proposes EH tags and applies nothing.
5. **The user confirms every change.** In Guided, write-tier tools go through the confirmation card, as destructive and execution tools already did.
   - Since P26 the card says what a write is for:
     - "Confirm this change" for edits;
     - "Confirm: export a file" for exports;
     - "Confirm: save a copy" for a project snapshot;
     - "Confirm: open a project" for `load_project` / `activate_project`;
     - "Confirm" for runs and deletions.
   - The three non-edit kinds carry a one-line note, such as "Your network is not changed."
   - Every Guided card leads with one plain sentence ("Run the reliability study for this site (about 30 calculation steps)"), with the raw call under a collapsed Details.
   - A declined card leaves one line: "You declined — nothing was changed."
   - Expert is unchanged: write-tier tools still apply directly there.
6. **Mode switching.** One click in the header or the palette. An explicit choice persists across reloads in both directions.
7. **The live network is left alone.** On every template, the live buses, links and generators are equal before and after the study and the FMEA sweep, except the `*_nom_opt` outputs, which are an accepted deviation (see §4).

## 2. Verdicts observed per template (P26 smoke)

| Template | Expected (P20) | Observed verdict | Headline (§5.5) | Improve findings | FMEA rows |
|---|---|---|---|---|---|
| Data Center Energy Hub | fail | `fail` | "Not certified: about 12 h/yr of shortfall vs a 3 h/yr goal — driven by site_transformer" | 2 (`certification_fail`, `fmea_dominant_mode`) | 8 |
| Industrial Hydrogen Hub | no target | none (no goal is set, so the reliability simulation does not run) | "No reliability goal is set for this site, so the study did not certify it — set an allowed shortfall in step 3 (Goal) to get a verdict." | 1 (`fmea_dominant_mode`) | 4 |
| Island Microgrid | inconclusive | `inconclusive` | "Not decided: the shortfall estimate (3–6 h/yr) straddles the 3 h/yr goal — more simulation runs would settle it." | 2 (`certification_inconclusive`, `fmea_dominant_mode`) | 6 |

All three match the P20 expectations. The `*_nom_opt` changes after the sweep were 8 values on the data center, 7 on the H₂ hub and 8 on the microgrid; there were none after the study. On the data center, "Let the assistant do this" on `certification_fail` produced a `run_eh_study` confirmation card (execution tier, header "Confirm"). The user declined, and nothing changed.

## 3. What Guided still does not cover (spec §1 non-goals)

- **Chat-created projects.** `create_project_from_template` and `save_project_as` run from chat do not switch the mode (spec §10).
- **Other workflows.** Guided covers only the EH hub-design flow. Every other workflow (dispatch, capacity expansion, load flow, prices, economics, emissions, time series, scenarios, GridSpine, and so on) is Expert-only. It is reachable from Guided only through the palette or the assistant.
- **Mode storage.** The mode is stored per browser in `localStorage`, not as a per-user preference on the server.
- **No new engine.** There is no new engine and no change to the semantics of the study, sweep, review or readiness; the fields added are additive only.
- **Scope limits.**
  - The assistant's tool tiers are unchanged. Guided only adds the confirmation card for the write tier.
  - The command palette gains only one entry.
  - There is no mobile layout.

## 4. Deferred and known limitations

### Deferred from P22.9 (still open; observed again in P26 where noted)

| Item | What | P26 |
|---|---|---|
| Bug 5 | FMEA rows with severity 0 (class-A gensets, and the C scenarios) show `€0.0` with no explanation. The rows read as "nothing happened". | Seen again on the data-center FMEA tab: `genset_1..3` show `€0.0`. |
| Bug 6 | A one-off 409 on resume after a backend restart with a stale tab open, raised by the identity guard. The app recovered. | Not reproduced; the smoke never restarts the backend. |
| Obstacle 10 | A tour popover can cover its target or overflow the viewport. | Not re-checked in the P26 path. |
| Obstacle 11 | The data-center template raises a `gen_zero_costs` warning on 2 generators. | Not re-checked. |
| Obstacle 12 | The New-project dialog shows a save path that local mode does not use. | Not re-checked. |

### Known limitations recorded at earlier gates

- **Send gate and bound profiles (P22.9-FE).** Send is gated on the active profile's readiness. A session already bound to another ready profile, with the store's `profileId` reset (for example after a reload), is gated wrongly. Picking the profile in the dropdown lifts the gate. Follow-up: expose the session's bound profile id to the client.
- **Edits during a study are reverted (P22.9-BE).** An edit to a restored topology column (`control`, `sub_network`, `generator`) made while a live-network study runs is reverted when the study ends. The restore writes outside the network lock. This is the same class as `freeze_capacities`' undo, and not a regression.
- **`*_nom_opt` after a sweep (P22.9-BE, accepted).** The sweep's closing base re-solve writes the `*_nom_opt` outputs and leaves `dispatch: fresh` with no foreground condition (bug 2). The greeting explains this state.
- **A blank `carrier` is filled from the bus** by PyPSA's topology pass. It is an optional input and is not restored (P22.9-BE).
- **A foreground solve writes topology columns.** `POST /api/simulation/run` still writes `control` / `sub_network` / `generator`, as it always has (spec §2.1 item 4).
- **Surviving mutation at P25.** The confirmation card's TTL-expiry guard has no test that kills a mutation of it. It is harmless defence and was judged not to need one (P25 gate).

### New findings from the P26 first-time-user review, still open (deferred by the coordinator)

1. **Engine vocabulary on the FMEA tab.** When the tab is opened from Guided it shows the eyebrow "SIMULATION · RESULTS", the title "Optimization results", "FOR", class letters, and `lp_proxy` / `copt` badges. This is an Expert surface that Guided links to (spec §1 obstacle 7). A Guided header, or a plain column legend, would help. Bug 5 is part of the same view.
2. **Tool progress lines** ("… preparing run_eh_study", "→ run_eh_study") are still shown in raw form in Guided, above the card. This is shared chat rendering. (The closing sentence "Understood — run_eh_study was not applied." in the smoke comes from the stub model, not from the app.)
3. **Cosmetic.**
   - A reloaded Improve request loses its "(step i of N)" suffix.
   - The "created from template" toast briefly covers the Send button.

## 5. Fixes made in P26 (test-first; details in the plan's P26 section)

1. **Card wording by tool purpose** (Guided; carried from the P25 gate).
   - Exports read "Confirm: export a file", a snapshot "Confirm: save a copy", and a project load "Confirm: open a project".
   - Each of these carries a one-line note. Edits keep "Confirm this change"; runs and deletions keep "Confirm".
2. **Plain summary on every Guided card** (coordinator item 5).
   - The card leads with one plain sentence, for example "Run the reliability study for this site (about 30 calculation steps)". The count comes from `budget_solves` and is dropped when absent.
   - Other examples: "Change the settings of it_bus", "Set the price of undelivered energy to €5,000 per MWh", and "Export the file risks.csv".
   - Tools without their own wording fall back to "The assistant wants to use <tool name in words>".
   - The tool id and the JSON arguments sit in a collapsed "Details". The summary is the dialog's accessible label.
   - Approve / Deny, typed confirmation, and the card itself are unchanged.
3. **A declined card in Guided** (item 6).
   - The transcript shows one line: "You declined — nothing was changed."
   - The raw "denied: <tool>" line sits under a collapsed Details, and the backend's `confirmation_denied` error line is hidden.
   - This is render-only: the stored transcript is unchanged.
4. **The greeting after a study in Guided** (item 7). The Guided sentence from P22.9 was never reached, for two reasons:
   - (a) The EH study solves a copy, so the live network's `dispatch` stays `none` after it. The greeting fell through to "Not solved yet."
   - (b) A sweep started from the Improve card is polled by no mounted panel. FmeaTab re-reads `/simulation/status` only if it is open when the sweep ends, so the cached "none" stayed.

   Fixes:
   - The Guided greeting reads the hub study record, on the same key and fetcher as the hub panel. A finished study gives "A study has run on this network — its results are in Hub design." A running one gives "The hub study is running — follow it in Hub design."
   - The Improve card follows a sweep it started on FmeaTab's own query, and re-reads the status when the sweep ends.
   - Expert does not read the study record, and its greeting is unchanged.
5. **The API-key offer in the greeting** (item 8, both modes).
   - "Add an Anthropic API key to talk to me" now hides while the session's effective profile is ready. The rule is the Send gate's: `profileId ?? active`, with `chat_ready === true` for the active profile.
   - When the user has picked a different profile, its readiness is not known client-side, and the offer behaves as before.
6. **Reloaded Improve label.** It uses the same plain words as the live label.
7. **Microgrid Improve wording.** "draws" became "runs", and "confidence interval" became "range of the estimate".
8. **"Next" buttons.** Site gets "Next: Goal" and Results gets "Next: Improve".

**The implementer's runs after these fixes** (the full backend suite and the independent gate belong to the orchestrator):
- tsc exit 0.
- vitest 233 files / 2481 passed (P25: 2431; 50 new).
- Backend gate row 2, 14 files: 613 passed. It was run before items 5–8, and there is still no backend change.
- Smokes after items 5–8: P26 PASS (37 screenshots), P25 PASS (12), P22.9 PASS (10). The earlier round was P26 / P25 / P24 / P23 / P24-BE / P22.9, all PASS.

## 6. Gate files

- Baseline: [`2026-09-27-guided-mode-baseline.md`](2026-09-27-guided-mode-baseline.md)
- Spec review: [`2026-09-27-guided-mode-spec-review.md`](2026-09-27-guided-mode-spec-review.md)
- P22.9-BE: [`2026-09-27-guided-mode-gate-P22.9-BE.md`](2026-09-27-guided-mode-gate-P22.9-BE.md)
- P22.9-FE: [`2026-09-27-guided-mode-gate-P22.9-FE.md`](2026-09-27-guided-mode-gate-P22.9-FE.md)
- P23: [`2026-09-27-guided-mode-gate-P23.md`](2026-09-27-guided-mode-gate-P23.md)
- P24-BE: [`2026-09-27-guided-mode-gate-P24-BE.md`](2026-09-27-guided-mode-gate-P24-BE.md)
- P24-FE: [`2026-09-27-guided-mode-gate-P24-FE.md`](2026-09-27-guided-mode-gate-P24-FE.md)
- P25: [`2026-09-27-guided-mode-gate-P25.md`](2026-09-27-guided-mode-gate-P25.md)
- P26: `2026-09-27-guided-mode-gate-P26.md`, to be written by the independent gate reviewer (spec §8.5).
- Original QA input: [`2026-09-27-realapp-clickthrough-expert.md`](2026-09-27-realapp-clickthrough-expert.md)
