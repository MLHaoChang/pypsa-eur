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

### New findings from the P26 first-time-user review (not fixed; for the orchestrator)

1. **The run card is not plain.** In Guided the most common card, Improve's `run_eh_study`, shows the header "Confirm", the raw tool id, and the JSON arguments with engine stage ids (`apply_pack`, `mc_certify`, `dtc_stress`, …). A plain one-line summary per tool (for example "Run the study again with …") would need a per-tool wording table and a spec decision. The P25 re-gate fixed "Confirm" for the run and delete tiers.
2. **A denied card leaves technical lines** in the transcript. Examples: "✗ run_eh_study — confirmation_denied: deny on confirmation for 'run_eh_study'", "denied: run_eh_study", and the "preparing run_eh_study" / "→ run_eh_study" progress lines. These are shared chat rendering, so the same lines appear in Expert.
3. **The greeting says "Not solved yet."** after the EH study and the sweep have finished. The study solves a copy, and no foreground solve is recorded. This is correct, but it reads as a contradiction next to a finished study. It is shared with Expert (bug-2 family).
4. **The greeting offers "Add an Anthropic API key to talk to me"** whenever no Anthropic key is configured, even while a ready non-Anthropic profile is active. It is shared with Expert.
5. **The FMEA tab has an engine vocabulary** when opened from Guided: the eyebrow "SIMULATION · RESULTS", the title "Optimization results", "FOR", class letters, and `lp_proxy` / `copt` badges. This is an Expert surface that Guided links to (spec §1 obstacle 7). It is worth a Guided header, or a plain column legend.
6. **The request's step count is lost on reload.** A reloaded Improve request shows the plain label but not its "(step i of N)" suffix; the live bubble shows it. This is cosmetic.
7. **The "created from template" toast covers the Send button** for a few seconds after a project is created. This is cosmetic.

## 5. Fixes made in P26 (test-first; details in the plan's P26 section)

- **Guided card wording by tool purpose** (the item carried from the P25 gate).
- **Reloaded Improve label.** A reloaded Improve request now uses the same plain words as the live label.
- **Microgrid Improve wording.** "draws" became "runs", and "confidence interval" became "range of the estimate".
- **"Next" buttons.** Site gets "Next: Goal" and Results gets "Next: Improve".

**The implementer's runs after these fixes** (the full backend suite and the independent gate belong to the orchestrator):
- tsc exit 0.
- vitest 233 files / 2452 passed (P25: 2431; 21 new).
- Backend gate row 2, 14 files: 613 passed. There is no backend change in P26.
- Smokes: P26 PASS (34 screenshots), P25 PASS (12), P24 PASS (21), P23 PASS (13), P24-BE PASS (10), P22.9 PASS (10).

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
