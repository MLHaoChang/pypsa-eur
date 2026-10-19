<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Review against the owner's copilot vision

Status: owner decisions recorded (see below). The original proposal is kept as written.
Date: 2026-10-19, Europe/Berlin
Reviewed source: master `d006f4beaa302b97432c65757762ae180222731a` (PR376).
Reviewer: Claude session, commissioned by the coordinator. Documents only; no
application code changed and no tests run.

This file adds to the Codex plan. It does not replace it, and it rewrites none
of the ledger's history. Everything below was checked against code; file:line
references are at the reviewed source.

## Owner decisions

Owner decisions, 2026-10-19 (relayed by the coordinator):
Q1 = keep the Codex order (LC-03 → LC-04 → LC-05); the vertical slices become
the acceptance targets that close those layers, not a reordering.
Q2 = a manual-trigger live-model tier with a hard cost cap, reported per
journey, never a merge gate. Q3 = an old→new card before every chat edit, in
both modes. Q4 = with a model available every sentence goes to the copilot;
the local tour and project picker work only without a key.

So the slices VS-1..VS-4 below are acceptance targets inside the Codex layers:
VS-1 and VS-2 close LC-03, VS-3 closes LC-04, VS-4 closes LC-05. The proposed
reordering in "Proposed order" was declined; it stays here as the record.

## The vision, as the owner put it

A live copilot that can use every tool a human can, through ordinary
conversation:

1. "Show me the results of the latest calculation, the load flow results." It
   opens the Load flow tab, walks through it and explains the key points.
2. "Show me something else." Any other result or page.
3. "Help me update the parameters of this line / this storage / any parameter."
   It does it.
4. "Update these parameters and relaunch the simulation." It chains the edit,
   the run and the result walk-through.

It coordinates and converses. It is not a set of shortcuts.

## Verdict

The Codex plan is strong on safety. Exact targets, approvals, provenance,
no-replay and domain oracles are all well specified. Keep all of that.

It is weak on the four things the owner will actually try first:

- **No owner-journey acceptance suite.** No test, in CI or out, plays one of
  the four examples end to end. The 19 generic cases and 24 domain suites test
  parts. None checks "user says X, copilot does Y, and the screen and answer
  show Z".
- **The order delays the first visible win.** The generic layers (LC-03
  registry, LC-04 edit envelope, LC-05 operation graph) all come before any
  example works end to end. Much of what example 1 needs already exists, so a
  thin vertical slice would show it working within one or two PRs.
- **Long jobs break the chain.** `wait_for_job` waits at most 25 s
  (`pypsa-gui/backend/harness/catalogue.py:1844`). Nothing posts a finished
  job back into the chat; the completion observer only feeds the Activity
  panel (`harness/completion_observer.py:1`, `services/assistant_activity.py:112`).
  So example 4 stops at "the solve is running", and the user must ask again.
  The plan's LC-05 graph covers the dependency rules. It does not say how the
  conversation resumes when the job ends.
- **"This" has no resolution rule.** The app already sends what the user is
  looking at: panel, results tab and source, selected component, snapshot
  (`pypsa-gui/frontend/src/utils/uiContext.ts:40-80`). LC-C2 lists the bindings
  but sets no order between what was said, what is on screen, what was
  discussed last and asking. "The latest calculation" also has no rule.
- **The walk-through does not explain anything yet.** The local tour shows a
  fixed one-line blurb per tab and "Matching result data is displayed"
  (`pypsa-gui/frontend/src/components/AssistantDock.tsx:113-114`). For current
  results it walks only Load flow, and only in Expert
  (`pypsa-gui/frontend/src/components/liveWalk.ts:126-131`).

## What exists today (code, not prose)

| Need | What the code has | Evidence |
| --- | --- | --- |
| Tools | 256 tools; 124 read, 75 write, 37 destructive, 20 execution | `harness/catalogue.py:202`, `:3555`, `:4003-4010` |
| Open any results tab | `ui_open_panel(panel_id='results', results_tab=…)` reaches all 15 tabs | `harness/catalogue.py:89-95`, `:1786` |
| Open other pages | Panels incl. campusElectrical, gridspine, reports, hubDesign, economics | `harness/catalogue.py:57-88`, `:1786-1800` |
| Read load flow | `get_loadflow_summary` with exact source, window and freshness | `harness/catalogue.py:3538` |
| Read other results | `get_results` (31 kinds), `get_adequacy_results`, `get_asset_results`, `explain_investment` | `services/chat_tools.py:573-581`, `harness/catalogue.py:955`, `:2560` |
| Run history | `list_project_runs` newest first; `discuss_project_run` opens a run in Results | `harness/catalogue.py:3529`, `:3532` |
| Know what the user sees | `ui_context`: panel, results tab and source, selected component, snapshot, Guided step | `frontend/src/utils/uiContext.ts:40-80`; `harness/loop.py:1719` |
| Edit | `update_component` and `bulk_update_components` on 10 classes plus GlobalConstraint | `harness/catalogue.py:455`, `:496`; `services/chat_tools.py:72-83` |
| Schema discovery | `get_component_schema` (LC-01A, PR372) | `harness/catalogue.py:204` |
| Old→new preview | Only `preview_project_changes` → `apply_project_changes` | `harness/catalogue.py:3549-3550` |
| Approval card | Expert: destructive and execution only. Guided: every write too | `harness/confirm.py:28-43` |
| Undo | `undo_last` reverts one turn's network edits | `harness/catalogue.py:1754` |
| Run and cancel | `run_simulation`, `run_ac_pf_stage`, `solve_queue_enqueue`, `abort_simulation`, `solve_queue_abort` | `harness/catalogue.py:835-850`, `:1343-1361` |
| Wait for a job | `wait_for_job`, at most 25 s, no polling loop | `harness/catalogue.py:1844-1856` |
| Plans | `start_task` (1–12 ordinary steps, durable), `set_goal` (up to 20 auto rounds) | `harness/catalogue.py:3541`; `harness/goals.py:29-30` |
| Guided flows | 8 workflows, including explain-results and run-study | `harness/workflows/*.md` |
| Stale results | Solve-input fingerprint, but only the load-flow summary checks it; other reads compare component shape | `services/workflow_tools.py:423`; `services/loadflow_summary.py:423-427`; `services/dispatch_status.py:103-131` |
| Turn limit | 25 tool calls per turn | `harness/budget.py:29` |

Small defects found on the way, not in the plan:

- `ui_set_snapshot` opens Results but does not set the snapshot
  (`frontend/src/components/uiNavigate.ts:121-126`), although the tool says it
  drives the picker (`harness/catalogue.py:1825-1826`).
- `ui_select_component` selects but leaves an open slide panel in front, so
  the Properties panel stays hidden (`uiNavigate.ts:92-96`; `App.tsx:752`).
- No chat path opens a component's edit card. Only a canvas double-click and
  the tagging tour call `requestPropertiesEdit` (`store/uiStore.ts:921`).
- Source-bound navigation ACKs exist only for Results → Load flow
  (`harness/navigation.py:99-106`), are issued only in checked/strict turns
  (`harness/assurance.py:894`) and are acknowledged only in strict mode
  (`frontend/src/components/ChatPanel.tsx:2385`). In an ordinary turn the
  copilot cannot tell whether the page it opened shows the data it describes.
- Guided steps and tiles cannot be targeted. `ui_open_panel('hubDesign')`
  opens the hub; there is no step or tile argument.

## Coverage table

"Plan" means the Codex plan names it. "Code" means a chat tool can do it today.

| Surface | Plan | Code | Gap |
| --- | --- | --- | --- |
| Projects: list, open, compare | Yes (LC-01B, D01) | Yes; exact-name resolver is WIP on `claude/copilot-target-resolution` | Named open fixed by LC-01B when it lands |
| "Latest calculation" | Partly (LC-C2, T06) | `list_project_runs` newest first | No rule for "latest"; retained load flow not captured |
| Results tabs (15) | Yes (LC-03 adapters) | All reachable with `ui_open_panel` | Narration per tab; source ACK only for Load flow |
| Load flow | Yes (D07) | `get_loadflow_summary`, current only | Historical run returns unavailable |
| Dispatch, prices, curtailment, lost load, emissions | Yes (D08, D10) | `get_results` kinds | No walk-through narration |
| Capacity expansion, multi-period | Yes (D04, D05) | `get_results`, `get_vintage_results`, `explain_investment` | Narration |
| Adequacy, FMEA, Monte Carlo | Yes (D11–D13) | Read and run tools; Guided reachable | Narration; long runs |
| Campus electrical, island, frequency | Yes (D14–D17) | Run tools; panel only | No sub-section navigation or ACK |
| GridSpine planning | Partly (fidelity table) | Panel, run, `get_study_evidence` | No sub-section navigation |
| Decision study, investment | Yes (D18) | Run, `explain_verdict`, Investment tab | Narration |
| Guided hub steps and tiles | Only "preserve Guided" | Opens the hub only | Cannot go to a step or tile |
| Reports | Yes (D21) | `generate_report`, reports panel | — |
| Edit components (11 classes) | Yes (LC-04, D02) | `update_component`, bulk, schema | No old→new card in Expert; cannot open the edit card |
| Costs and economics | Yes (D09, D10) | `update_component` attrs, `get_asset_costs` | Cost-basis clarification not built |
| Time series, snapshots, horizon | Yes (D03) | `set_snapshots`; `ui_set_snapshot` | Snapshot index not set (bug) |
| Solver settings | Yes (D06) | `update_solver_config` | — |
| Run, queue, cancel | Yes (LC-05, T12) | Run, queue, abort tools | Chat does not resume when a job ends |
| Undo, backups, scenarios | Yes (D22) | `undo_last`, `restore_project_snapshot`, `create_scenario` | Undo not offered after a chat edit |
| Import, export | Yes (D20) | Many tools | — |
| Profile generator (PR371–380) | Yes, when merged (T17) | Not on master | Map after merge |
| Process units (PR338) | Yes, when merged (T17) | Not on master | Map after merge |
| Voice | Yes (LC-07) | Not connected | As planned |
| Owner journeys end to end | **No** | **No** | Add OJ suite (issue 13) |

## Answers to the brief's questions

**Every tool a human uses?** Nearly. The plan inherits the 256-tool catalogue
and the 14-family matrix, which is broad. The gaps are UI reach, not tools:
Guided steps and tiles, campus and GridSpine sub-sections, the edit card, the
snapshot index.

**Target resolution.** The order "resolve the target before any local
shortcut" is right. LC-01B does it by narrowing the shortcuts to exact generic
commands. Two things are missing: the resolution ladder for "this", "the" and
"latest" (issue 10), and a rule that, once a model is available, every
sentence goes to the model and the shortcuts are only the no-key fallback
(owner question Q4).

**Navigation and walk-through.** The copilot can open every results tab
today. It cannot yet confirm, outside strict mode, that the tab shows the data
it is describing. The local tour does not narrate numbers. Issue 11 adds a
narrated walk-through grounded in the same reads the tabs use.

**Edits.** The generic tool covers every component class. In Expert a chat
edit runs at once, with no old→new card; only Guided cards every write. The
typed preview exists but is used only for engineering changes. Undo exists
but no prompt or workflow tells the copilot to offer it. After an edit, the
load-flow read refuses changed solve inputs. The other reads and
`get_simulation_status` only call results stale when the component set changes
shape (`services/dispatch_status.py:103-131`), so a parameter-only edit leaves
old results readable with no stale label. Owner question Q3; issue 12 adds the
stale check.

**Multi-step orchestration.** `start_task` already persists a plan of ordinary
steps. What is missing is resuming the conversation when a long job ends
(issue 12), so edit → run → walk-through finishes without a second prompt.
LC-05 should reuse `start_task` and `wait_for_job` and add only that.

**Conversation quality.** Follow-ups and carry-over use `ui_context`, the
discussed run and durable parent refs. "Show me something else" has no rule:
issue 11 makes it "offer the sections not shown yet, with one line each".
Voice stays in LC-07, which is sound; it must reuse the same journeys.

**Evaluation.** CI runs the 133-case pilot gate, 30 QA drivers and 4 backend
shards, all with a scripted model (`tools/run_pilot_evaluation.py`,
`tests/run_qa_drivers.py`, `.github/workflows/test.yaml:130-215`). No test plays
a multi-turn owner journey. The browser runners are local only. No live-model
eval runs anywhere. Issue 13 adds a CI tier (scripted wire, real backend) and a
small, capped, manually triggered live-model tier (owner question Q2).

**Sequencing.** LC-02A and LC-02B are merged (PR369, PR370), so the "first
task" question is settled. LC-01A is merged (PR372). LC-01B is in flight. The
proposal: after LC-01B, run three thin vertical slices before the generic
LC-03/04/05 layers (owner question Q1).

## Proposed order (declined by the owner, Q1; kept as the record)

1. LC-01B exact project names (in flight; unchanged).
2. **VS-1** "show me the load flow results of the latest calculation": issues
   09, 10 (first rung), 13 (OJ-01).
3. **VS-2** "show me something else" and a narrated walk across current
   results tabs: issue 11, plus the navigation fixes in issue 14.
4. **VS-3** "update this line's parameter": issue 10 (focus rung), LC-04
   storage and line examples, owner decision Q3.
5. **VS-4** "update and relaunch, then walk me through": issue 12, LC-05.
6. Then the generic LC-03/04/05 adapters per family, LC-06, LC-07, LC-08, as
   planned. Each new adapter adds its owner journey to the suite.

## Conflicts with in-flight work

- `claude/copilot-target-resolution` (LC-01B, WIP commit `08d0be74`, no PR)
  edits `liveWalk.ts`, the catalogue and the ledger's tail. This review edits
  none of its files' changed regions. Issue 10 builds on LC-01B; it does not
  take it over.
- The ledger's status line and last checkpoint still say PR372 is a draft. It
  merged as `0b33685c`. LC-01B's branch records that; this review does not
  rewrite it.
- Profile-generator PRs 371–380 add chat tools (PR377). They change the
  catalogue the journeys cover, not this plan.
