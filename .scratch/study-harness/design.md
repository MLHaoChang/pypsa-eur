<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Study harness: one agentic workflow for every template

Status: needs-triage (design). Plan verdicts, 28 Oct: GK B GO with B1–B4 (settled in §5, §6.6, §7, §8), GK A GO with conditions (§5, §6.0, §6.2, §9.3), UX GO on placement with conditions (§10, issue 08)
Type: design
Baseline: master `38e5b7395`
Owner decisions: SH-1, SH-ENVELOPE-1, SH-RESEARCH-1, SH-PILOT-1, SH-FOLD-1 (PR #829), AI-558, AI-567, AI-567b, COP-1, TOOLSEL-1, SAGC-1, D3-KEEP, DC13-SCOPE, UX-flow
Issues: [`issues/`](issues/) (01–09 the core on the eh_site pilot, 10–16 one profile per template, 17 the workflow files)

## 1. What the owner asked (SH-1)

Every template runs the same end-to-end workflow, built as a backend harness
rather than as chat prompts:

- the stages are fixed, and code decides when a stage is entered and finished;
- the agent steps in at named points: drafting the brief, filling input gaps,
  research, fixing a failed gate, writing the report;
- loops repeat a stage when its gate fails, within a budget;
- a run goes on with the panel closed, survives a restart, and tells the user
  when it needs them or has finished;
- a template is a **profile** on the generic graph, so a new site type adds a
  profile, not a new workflow.

### Words

`pypsa-gui/CONTEXT.md` reserves **decision study** (`services/study/`), and
unqualified "study" already names two other things there. This effort uses:

- **harness run**: one durable end-to-end run of the stage graph for one project.
  It is a `start_task` task (file version 3). It is not a decision study, but it
  may drive one in its explore stage (§6.7).
- **harness profile**: the per-template data (§9). It is not a Library item
  (AI-567b) and not a grid-code profile.
- **envelope**: the approve-once grant for one harness run (SH-ENVELOPE-1, §7).
- **gate**: the machine-checked exit criteria of a stage (§5).
- **change ledger** and **source ledger**: the run's record of what the agent
  changed and where researched values came from (§6, §8).

## 2. What we build on (no second system, AI-567/567b)

| Piece | Where | What the harness takes from it |
|---|---|---|
| Durable task plans | `services/assistant_tasks.py` (`start_task` :281, `admit` :495, `begin_step` :725, `await_job` :545, `_reconcile_lost` :632) | The one store, step order, idempotent `request_id`, job waiting, stale-fingerprint refusal, native read-back. The harness run is a task, file version 3. |
| LC-23 study brief | `.scratch/live-copilot/issues/23-study-copilot.md`; slices 1–2 merged (#573, #582) | The brief (objective, cards, cases, phases), plan-review card, plan card, checkpoints. The brief becomes the run's intake output (stage 0). |
| Unattended rounds | `harness/goals.py` (5 rounds by default, ≤20) | Its stop conditions (declined or expired card, budget, error, generation change). Goals stay a chat feature. A harness run does not use them, because they live in memory on one SSE stream. |
| Job follow-up | `harness/continuation.py` (`follow_job` :50, `settle_from_record` :84, queue follow ≤6 h) | Settling a waiting step from a job record, including after a restart. |
| Background solves | `services/solve_queue.py` (`SolveQueue.enqueue` :273, `restore` :800) | Solves run without a chat, and queue rows survive a restart. |
| Explicit-context runner | `services/study/runner.py` (decision study option runner) | Runs on the project's own context (`hydrate_or_adopt`, `mutation_lock` then `solver_state_lock`, contextvars-bound request context), never on the active one. The harness runner copies this pattern. |
| Approvals | `harness/confirm.py` (`confirmation_policy` :49, `policy` path :210), `harness/approvals.py` | The `policy` disposition already needs a durable approval record (`approvals.decided(by=...)`). The envelope plugs in there. |
| Structured readiness | `services/workflow_tools.py:203` `project_readiness` | The one structured gate today (`ready`, `blockers`, `next_tools`). |
| Pre-solve checks | `validate_network`, `diagnose_network`, `review_eh_study`, `suggest_eh_setup`, `get_eh_readiness` | Input-gate facts. |
| Per-template scope | `services/flow_scope.py:203` `TEMPLATE_PRESETS`, `SITE_TYPE_PRESETS` :212, `preset_for` :228 | Which cards and sub-options a template intends. The profile reads its card scope from here and does not copy it. |
| Template metadata | `project_templates/eh_templates.py:1307` `TEMPLATE_META` (archetype, stages, `dtc_attribution`, `study_notes`), `get_eh_template` | Profile facts such as the archetype and the period years. |
| Assumptions ledger | `services/study/ledger.py` (`apply_user_row`, provenance) | Researched values that land in a decision study go through `apply_user_row` with their source, so there is one assumptions ledger. |
| Notifications | `harness/attention.py` (durable decision observations), `services/email_service.py` (`send_email`, unused by the assistant today) | In-app notice and the optional email. |
| Workflows (prompt-only) | `harness/workflows/*.md` (`hub-design`, `run-study`, `investment-decision`, …), state in memory on `ChatSession.workflow` | They stay as chat guidance. The three named ones are generated from profiles later (issue 17). |

What is missing today (the coordinator's G1–G9, checked against the code):

1. There is no typed stage graph. A workflow `done_when` is free text that nothing evaluates, and `advance_workflow` jumps to any step.
2. Nothing advances a plan without an open panel. Continuation runs only when the panel posts `/stream`, and a restart leaves a waiting step "uncertain".
3. COP-1 needs a card for every write or run, so nothing can run unattended.
4. There is no result gate: LC-20's rules are not built.
5. There is no bounded fix-and-rerun loop and no ledger of changes. Retries are carded and limited to 2 (`TASK_STEP_RETRIES_MAX`), and `_correction` proposes triage reads only.
6. There is no research tool: no web or literature lookup.
7. Several template studies have no chat tool (§9.3).
8. There is no run monitor, and no completion notice.
9. Workflow and goal state live in memory only.

## 3. The stage graph (the same for every template)

```
 0 intake ─► 1 setup ─► 2 inputs ─► 3 input gate ─► 4 solve ─► 5 result gate ─► 6 explore ─► 7 report ─► 8 hand-off
                            ▲            │ fail (loop L1)          ▲     │ fail (loop L2)     │
                            └────────────┘                         └─────┘                    └─ each case: 4 → 5
```

Who acts:

- **H**: the deterministic harness (the runner's own code, no model);
- **A**: the agent (a bounded headless turn, §6.3);
- **U**: the user (a card or a message).

| # | Stage | Acts | What happens | Exit gate (machine rules, §5) |
|---|---|---|---|---|
| 0 | intake | A drafts, U approves | The prompt becomes a brief (objective, template, site, cases, budget) plus an envelope proposal. The agent asks for anything missing (SC-01). The user approves on the plan-review card, or switches to step-by-step mode. | `brief.valid`, `envelope.state == granted` (or `mode == stepwise`), `profile.resolved` |
| 1 | setup | H | `create_project_from_template` with the profile's params (or binds the open project), save, activate on the run's own context. | `project.exists`, `project.template == profile.template`, `params.accepted` |
| 2 | inputs | H, then A | H runs the profile's fetch actions (weather choice, tariff, site context, library pack). A fills the gaps that `project_readiness` names, using Library or template defaults first and research only when the profile allows it (§8). | every `inputs.required[*]` fact present |
| 3 | input gate | H; loop L1 is A | Evaluates `validate_network`, `project_readiness` and the profile's input rules. On a fail, loop L1 runs (§6.1). | all `severity: block` input rules pass |
| 4 | solve | H | Enqueues on the solve queue (`await_job`), or a profile solve action (an EH study or an investment case). | `solve.status == completed`, results fresh against the solve-input fingerprint |
| 5 | result gate | H; loop L2 is A or a native loop | Solver status, the LC-20 plausibility rules, the profile's KPI rules. On a fail, loop L2 runs (§6.2). | all `block` result rules pass, or the loop budget is spent (→ `needs_user`) |
| 6 | explore | A proposes, H runs | ≤4 cases, ≤2 changes each (AI-558), through `run_sensitivity_sweep`, experiments or a decision study. Each case goes 4 → 5 itself. | every admitted case is settled (passed, failed with a reason, or not runnable) |
| 7 | report | H assembles, A narrates | LC-21 chapters, the profile's chapters, and a harness chapter (gates, change ledger, source ledger, gaps). The narrative uses admitted facts only (≤12 per explanation, AI-558). | `report.status == ready`; every chapter evidenced or listed as a gap (SC-15) |
| 8 | hand-off | H | Native read-back of the headline KPIs, a resume point, and a notification. | `handoff.notified`, `run.status == completed` |

Rules for every stage:

- **Order**:
  - a stage starts only when the previous stage's gate passed;
  - the user can waive a `warn` rule but never a `block` rule;
  - a later stage never runs before an earlier one (the order is `admit`'s, as today).
- **Freshness**: an input changed outside the run (`brief_stale`, SC-11) sends the run back to stage 3 and needs re-review before the next effect.
- **Not active**: a stage the profile leaves out is `skipped` with a reason (grid-only templates skip explore by default).
- **Steps per stage**:
  - steps are created when the stage is entered, from the profile's stage template, with ids `<stage>.<n>`;
  - the LC-23 limit (24 steps, 32 000 chars) applies per stage, so the flat cap stays and a run can be longer than 24 steps;
  - loop turns are not plan steps: their writes are ledger rows (§6.4).
- **COP-1 card boundaries**:
  - in step-by-step mode they stay exactly as they are;
  - in envelope mode they become stage boundaries that the runner crosses without a click, because the grant covered them (SH-ENVELOPE-1).

## 4. State and persistence

One store: the task file (`<project>/assistant_tasks/<task_id>.json`, atomic
`_save`). Version 3 adds a `run` block. Version 1 and 2 files stay readable and
have no `run` block.

```
run = {
  "profile":   "eh_site@1",                     # id@version, frozen at intake
  "mode":      "envelope" | "stepwise",
  "status":    "drafting" | "running" | "waiting_job" | "needs_user" | "paused"
               | "completed" | "failed" | "cancelled",
  "stage":     "inputs",                        # current stage id
  "stages":    {"<id>": {"status": "pending|active|passed|failed|skipped|waived",
                         "entered_at", "exited_at", "attempts": int,
                         "gate": {"rules": [GateResult], "evaluated_at", "fingerprint"}}},
  "loops":     [{"loop": "L1|L2|explore", "stage", "case", "round", "trigger": [rule_id],
                 "turn_id", "changes": [change_id], "outcome": "fixed|no_progress|budget|declined"}],
  "changes":   [ChangeRow],                     # §6.4
  "sources":   [SourceRow],                     # §8
  "cases":     [{"name", "changes": [change_id] ≤ 2, "experiment_id"?, "gate", "status"}] ≤ 4,
  "budget":    {"limit": Budget, "used": Budget},     # §6.5
  "envelope":  "<envelope_id>" | null,          # §7
  "waiting":   {"kind": "card|job|question|research_confirm", "ref", "since"} | null,
  "runner":    {"lease_owner", "lease_until", "heartbeat_at", "last_tick", "restarts": int},
  "notices":   [{"kind", "at", "channel", "delivered": bool}]   # §6.6
}
```

- The `run` block is written only by harness code (the runner, the gate
  evaluator, the envelope admission). No tool argument writes it, and the model
  only reads it through `get_task`. That keeps SC-12: a caller-supplied result
  never decides an outcome.
- `start_task` gains one optional argument, `run={"profile", "mode"}`, next to
  `brief` (AI-567b: extend start_task). This adds no new catalogue tool.
  `get_task` returns the stage view; `resume_task` and `cancel_task` keep their
  meaning; `cancel_task` also revokes the envelope.
- The envelope is stored beside the task as `<task_id>.envelope.json`, with its
  own audit rows (§7). It is a separate file so that revoking it never rewrites
  the plan.
- The run's conversation is a runner-owned session, `run:<task_id>`. It is
  rebuilt from the event log (contract 9: model-visible means logged). Every
  agent turn and every policy approval is in that log, so the companion can
  replay what the agent did while the user was away.

## 5. Gates: typed rules over native facts

A gate is a list of rules. A rule is data:

```
Rule = {"id": "in.validate.errors", "fact": "validate.errors", "op": "eq", "value": 0,
        "severity": "block" | "warn", "basis": "one sentence: why this threshold",
        "explain": "template with {fact} slots", "tunable": ["component.attr", …] | []}
GateResult = {"rule": id, "verdict": "pass" | "fail" | "not_evaluated",
              "facts": [{"fact", "value", "unit", "source": "<native reader>"}], "reason"?}
```

- **Facts** come from a closed, versioned fact registry in
  `services/run_gates.py`, a new services module beside `assistant_tasks.py`
  (GK B, B4). Each fact name maps to one native reader, and every reader is
  existing code. Each rule names the engine row id or function it reads, so a
  renamed row breaks a test, not a gate silently (GK A). Profile validation
  refuses an unknown fact when the profile loads, and a test pins this. The
  readers are:
  - `validate.*` reads `validate_network`;
  - `readiness.*` reads `project_readiness`;
  - `diagnose.*` reads `diagnose_network`;
  - `eh.readiness.*` reads `get_eh_readiness`;
  - `eh.review.*` reads `review_eh_study`;
  - `solve.*` reads the solve record or `solver_status`;
  - `plaus.<rule_id>` reads the LC-20 rule set;
  - `kpi.<key>` reads the result adapters (LC-03);
  - `dc.readiness.<row>` reads `grid_readiness_for`;
  - `ic.*` reads the investment case;
  - `adequacy.*` reads the EH study.
- **Operators**: `eq ne lt le gt ge in not_in empty nonempty within[lo,hi]`.
  Units are checked, never converted (as in `ledger.apply_user_row`).
- **Fail closed**: a `block` rule whose fact is unknown, cannot be read, or
  reads as `None` or NaN is `not_evaluated`, which counts as a fail with the
  reason shown. A `warn` rule that cannot be read is listed as not evaluated.
- **Scorecard rows gate by verdict** (GK A):
  - only `pass` satisfies a rule; `unknown`, `warn`, `fail` and a missing row
    never do;
  - each row's `assumed` flag is carried into the gate record. A pass that
    rests on assumed inputs is a **screening pass**, reported as such, and
    never "verified" at hand-off;
  - screening rows (SCR/ESCR, TDD, unbalance, rapid voltage change, flicker,
    harmonic scan, motor start) may block or flag but never certify. TDD is
    always assumed: I_L is not IEEE 519's maximum demand, and S_sc is the
    entered fault level. The wording is "within the screening limit", never
    "meets IEEE 519". Resonance inside the clearance is `warn`.
- **Row families** (GK A): several scorecard row ids are families:
  `energization_peak:<period>`, `dynamic:<id>`, `ride_through:<scenario>`,
  `motor_start:<unit>`. A rule on a family evaluates every row with the
  prefix: every row `pass` → pass; any other verdict → fail; zero rows →
  `not_evaluated`. A reader never picks one row of a family. Issues 05 and 10
  pin this with a test.
- **No wildcards**: a rule names one row id or one family. A `dc.readiness.*`
  wildcard is refused at profile load.
- **No model verdicts**: the model never supplies a fact or a verdict. It reads
  the gate result and narrates it (LC-20 "the model narrates; it adds no
  verdicts").
- **Core rules**, shared by every profile:
  - input:
    - `in.validate.errors == 0`;
    - `in.readiness.ready == true`;
    - `in.diagnose.unserved_islands == 0`;
  - result:
    - `res.solve.status == optimal`;
    - `res.fresh == true`;
    - every LC-20 rule as `plaus.*`, with LC-20's own severities.
  - Profiles add their own rules (§9) and may tighten a core threshold but never
    loosen one.
- **LC-20 home** (SH-FOLD-1): LC-20's rule set is built as the fact source
  `plaus.*` that the result gate reads. LC-20 asked for one read tool;
  TOOLSEL-1 prefers extending an existing results read with a `checks`
  argument over a new name. GK B decides which in issue 05.

## 6. Loops, the change ledger, budgets, runner and notification

### 6.0 What the agent never changes

The agent changes design levers, never the question (GK A). The profile
validation refuses a `tunable` entry or an envelope edit that names one of
these, and a test pins the list:

- grid-code profile values and clauses: every limit, the TDD standard, the
  frequency;
- fault levels S_k''/S_k''min, `eh_poc`, `eh_ibr_mva`, the IEC 60909 c factors;
- contingency sets and stress scenarios;
- the reliability goal: `target_lole_h`, draws, seed. `run_margin_loop`'s start
  is a measured probe by design;
- demand: IT load, process demand, offtake;
- demo or ledgered engine constants (start multiples, starting pf, eta·pf);
- solver tolerances and options;
- any `assumed` flag or verdict;
- gate thresholds, KPI targets, profile rules and the envelope itself (GK B, B2).

A per-unit fact such as `start_method` is tunable only when the profile lists
it as a design choice.

### 6.1 L1, the input loop (stage 3)

- **Trigger**: any `block` input rule fails.
- **Bound**: ≤3 rounds, which a profile may lower. A round that leaves the same
  failing rule set and the same network fingerprint as the round before ends
  the loop early as `no_progress`.
- **What the agent gets**:
  - the failed rules with their facts;
  - the `next_tools` from `project_readiness`;
  - the stage's tool subset (§6.3).
- **What it may change**:
  - attributes named in the failed rules' `tunable` lists;
  - data gaps (attach a time series, a Library default, a tariff), within the
    envelope's edit list (§7).
- **What it may not change**:
  - it may not delete a component or change the objective or the horizon;
  - it may not change a gate rule or a grid-code limit (D3-KEEP: limits are the
    user's per-project entries);
  - it may not change solver settings outside the profile's list.
- **Exhaustion**: the stage becomes `failed`, the run `needs_user`, and the
  user is notified with the failing rules and the change ledger.

### 6.2 L2, the result loop (stage 5)

- **Trigger**: a `block` result rule fails, or the solve status is not optimal.
- **Bound**: ≤2 rounds per case, ≤2 changes per round (AI-558). Each round costs
  one solve from the budget.
- **First move, always**: native triage reads: `get_simulation_log_history`,
  `diagnose_network`, `validate_network`. These are the existing `_correction`
  reads. An infeasible solve is never re-solved unchanged (SC-14 holds: no
  auto-retry without a change).
- **Engine loops replace the agent where the result gate is reliability**
  (GK A):
  - `run_coupling_loop` (ENS cap, energy lever; `adequacy/coupling.py:214`) and
    `run_margin_loop` (reserve margin, firm-capacity lever;
    `adequacy/margin_lever.py`) are LOLE loops for any project with a
    reliability goal (W3b-R). They are not the ammonia coupling report
    (g2p 13);
  - the engine changes the levers. Its own converged / not-converged result
    and its `goal_sources` are the gate fact;
  - one engine-loop call is one round. Its `max_solves` is bounded by the
    profile, and its solves count against the solve budget;
  - **LOLE basis**: `adequacy.lole_h_per_year` is per year (`eh_study.py:519`
    divides by the horizon), while the engine loops take `target_lole_h` on a
    **horizon** basis (`coupling_loop_runner.py:101`). Every LOLE rule names
    its basis. Where an engine loop drives the rule, the profile converts the
    goal once (h/yr × horizon years), and a test pins the conversion on a
    multi-year horizon;
  - an engine loop and the agent loop are never nested, and never run on the
    same rule.
- **Exhaustion**: the case is `failed` with its rules and ledger. In the base
  case the run goes `needs_user`; in an explore case it carries on to the next
  case.

### 6.3 Agent turns

An agent turn is `loop.run_turn` on the runner-owned session, headless: its
frames go to the event log, not to an SSE client. It runs with:

- **A stage instruction**: per-turn content, so the system prompt stays cached
  (contract 5). It comes from the profile's stage file:
  `harness/workflows/profiles/<profile>/<stage>.md`, read from the package only
  (contract 4).
- **A tool allowlist per stage**: read tools plus the stage's write tools. A
  short allowlist also eases the 128-tool offer (TOOLSEL-1), because the turn
  offers fewer tools.
- **The budget from the envelope**: tool calls per turn (budget.py's 25) and
  the run's token cap.
- **The same confirmation gate** as every turn (contract 6). The envelope only
  supplies the `policy` record (§7).

Who acts per agent point:

| Stage | Agent job | Allowed tool classes |
|---|---|---|
| 0 intake | Draft the brief and the envelope proposal; ask clarifying questions | reads, `get_site_template_form`, `get_eh_template`, `ask_user` |
| 2 inputs | Fill named gaps | reads, edit tools in the envelope, Library reads, `attach_tariff`, `set_weather_choice` |
| 2 research | §8 | provider web search/fetch (research turns only), reads |
| 3 L1 | Fix failed input rules | as stage 2, minus research |
| 5 L2 | Diagnose, then propose and apply ≤2 changes | triage reads, edit tools in the envelope |
| 6 explore | Propose ≤4 cases, ≤2 changes each, with sourced ranges (AI-558 Suggest-cases) | reads, `run_sensitivity_sweep`, experiment tools, decision-study tools |
| 7 report | Narrate chapters from admitted facts | report tools, reads |

### 6.4 The change ledger

```
ChangeRow = {"id", "stage", "loop", "round", "case"?, "rule": rule_id | null,
             "target": {"component", "name", "attr"}, "old", "new", "unit",
             "reason": str ≤ 240, "source": source_id | null,
             "approval": "envelope:<id>" | "card:<call_id>", "receipt": edit receipt id,
             "reverted": bool}
```

- Every write an agent turn makes inside a run appends a row. A write without a
  verified edit receipt (LC-04) appends nothing and fails the round.
- The hand-off offers "Revert the run's changes" as one preview (LC-16
  `preview_project_changes`), which is an ordinary carded write.

### 6.5 Budgets

Limits are set in the envelope. Defaults come from the profile, and the code
caps them:

| Budget | Default | Hard cap | On exhaustion |
|---|---|---|---|
| Solves (all stages) | profile, e.g. 6 | 20 | `needs_user` |
| Agent rounds (L1 + L2 + explore proposals) | 6 | 20 (= `goals.MAX_ROUNDS`) | `needs_user` |
| Wall time | 8 h | 72 h | `paused`; the envelope expires |
| Model output tokens | 200 k (budget.py session cap) | 1 M | `needs_user` |
| Model cost, total (GK B) | profile, e.g. €5 | owner value | `needs_user`; metered from provider usage (input, cached and output tokens, TOKEN-MEASURE-1), because cache reads and input dominate cost |
| Research cost | €2 (SH-RESEARCH-CAP-1) | €10 (SH-RESEARCH-CAP-1) | research stops; the stage goes on without it |
| Cases | 4 | 4 (AI-558) | refused at proposal |

`used` is counted by harness code: the solve queue for solves, budget.py for
tokens, the provider usage for research cost. It is never counted from model
claims. The tool-error kinds the harness adds (`invalid_envelope`,
`run_stale`, `run_not_runnable`, …) go in a `tool-error-kinds.d` fragment once
#798 lands (GK B).

### 6.6 The server-side runner (G2; absorbs live-voice 10's resumable advancement)

- **Where**: `harness/runner.py`. It lives in the harness because it calls
  `loop.run_turn`, and services never import the harness. It is started from
  the `main.py` lifespan after the solve queue.
- **One worker thread plus a lease per run**:
  - the run is driven by a lease (`runner.lease_until`, heartbeat every 30 s);
  - a second process, or a restarted one, takes a run over only after the
    lease has lapsed.
  - Each tick does at most one action:
    1. check the stop conditions;
    2. evaluate a gate;
    3. issue the next deterministic step;
    4. start an agent turn;
    5. or wait.
- **Wake-ups**: a job settling (the `continuation.follow_job` callback), a card
  decision, a user resume, a research confirmation, or a timer tick (60 s). The
  runner wakes on these; it does not poll the model.
- **Explicit context** (GK B, B1 settled as option b): the runner never uses
  `PyPSAService.get_active_context`.
  - It resolves the run's project as `services/study/runner.py` does:
    `hydrate_or_adopt` (`services/pypsa_service.py:1325`), `mutation_lock`
    then `solver_state_lock`, and the context bound through `contextvars`.
  - **Adopt means a shared context.** When the project is open in the
    foreground, `hydrate_or_adopt` adopts the same context, so the user and the
    runner edit one network. The run therefore holds the project's edit lock
    (`routers/projects.py:972` `_check_project_lock`, 409 `project_locked`)
    for its mutating stages. A foreground edit is refused with "a harness run is
    editing this project" rather than racing the runner. The foreign-drift pause
    (§7) stays as the backstop.
  - **Acting user**: the runner sets `_ACTING_USER_ID` to the run owner, so
    `chat_tools._acting()` (`services/chat_tools.py:4840`) resolves the owner,
    not the request user or no user. ACL is re-checked at each dispatch, and a
    revoked permission stops the run (SC-07, SC-16).
  - **Each dispatch re-resolves the project by uuid** and refuses on a
    mismatch.
  - **Bound-context completeness**: every tool and DISPATCHER in a stage's
    subset must resolve the project through the bound context, not through
    module state that ignores it. Issue 01 confirms each row of this sweep
    with a test before that tool joins a stage subset, and a tool that fails
    stays out:

    | Global | Where | Disposition |
    |---|---|---|
    | `_user_ts`, `_user_ts_meta` (`_Active*` proxies) | `services/user_timeseries.py:139, 210` | must read the bound context; a test writes a series from the runner and asserts it lands in the run's project only |
    | dirty state and autosave | `services/dirty_state.py` (reads the active context) | resolve through the bound context; a runner write marks the run's project dirty, never the foreground's |
    | change log | `services/change_log_service.py:60` `_active_org_id` | org from the bound context; rows carry the run's task id |
    | undo | `services/network_undo.py` | runner edits go on the run's project's undo stack; the hand-off revert uses the change ledger, not undo |
    | `chat_tools.chat_session()` | `services/chat_tools.py:6078` | returns the runner-owned session `run:<task_id>` inside a run |
    | `_acting()` / `_acting_session` | `services/chat_tools.py:4840, 4890` | the run owner, as above |
    | `_ActiveStateProxy` | `routers/simulation.py:270` | the solve path takes the bound context, as the B4.3 background-solve path does |

  - Tests (issues 01 and 02): a foreground switch mid-stage leaves the runner's
    writes in its own project and the foreground's in the foreground; a
    foreground PUT during a mutating stage gets 409 with no lost write.
- **Deterministic steps** go through the same dispatch path as an offered tool
  call:
  - `admit` → `begin_step` → the confirmation gate (envelope `policy` or a
    card) → `DISPATCHERS[tool]` → `finish_step`;
  - so there is one admission, lock and audit path, and nothing beside it.
- **Stop conditions**, checked each tick:
  - the run was cancelled;
  - the envelope expired or was revoked;
  - a budget is spent;
  - ACL or lock changed;
  - the inputs are stale;
  - a step is `uncertain`;
  - a card or question is waiting;
  - the runner itself errored twice in a row.
- **Cards while the user is away**:
  - a card that is outside the envelope, or destructive, is not issued live,
    because the 300 s TTL would expire unseen;
  - instead the run records `waiting = {kind: card, ref: <pending call>}`, goes
    `needs_user` and notifies;
  - when the user opens the companion, the plan card shows the waiting call,
    and the ordinary confirmation card is issued then, with its TTL starting at
    display;
  - declining it puts the run in `paused` with the reason.
- **Restart**: on startup, for each version-3 task with `run.status` in
  {`running`, `waiting_job`}:
  1. reconcile waiting steps. A queue step is settled from the solve-queue row
     (`settle_from_record`); a lost job settles `uncertain` (`_reconcile_lost`;
     SC-09: no requeue);
  2. any `uncertain` step means `needs_user` with "a job's outcome is unknown";
  3. otherwise, if the envelope is still valid, the run resumes from its stage
     pointer;
  4. increment `runner.restarts`. After 3 restarts in 1 h the run is `paused`
     (crash loop).
- **Notification** (G8):
  - Triggers: `needs_user`, `completed`, `failed`, `paused`, envelope expiring
    in under 1 h, and research values waiting for confirmation.
  - In-app: an attention record (`harness/attention.py`) that the companion
    shows on the plan card. Opening it opens the companion on the run.
  - Email: `email_service.send_email` when the deployment is configured
    (`get_status`) and the user opted in on the envelope. The text is built by
    the server, never by the model: the project name, the run title, the state
    and a link. It never carries values (research-sourced ones least of all),
    tokens or secrets. At most one email per state change and at most 10 per
    run.
- **Live-voice 10's share** (SH-FOLD-1):
  - Its "advance steps through shared admission/ACL/approval/lock/budget gates
    … reconcile uncertain outcomes before retry … verified cancellation/resume"
    is this runner.
  - Its typed cross-domain experiment adapters (plan step 4) are not runner
    work. They stay in live-voice 10 for re-triage after issue 02, and the
    explore stage uses them when they exist.

### 6.7 The explore stage and decision studies

- Network cases run through `run_sensitivity_sweep` (1–4 cases) or experiments
  (two runnable cases today: `brief_cases_not_runnable` above two, SC-10).
- Investment questions use the decision-study option runner
  (`start_decision_study` / `run_decision_study`, study-owned forks). The harness
  does not build a second case engine.
- A case that needs a domain experiments cannot run yet (commercial,
  reliability, gridspine) is `not_runnable` with that reason. It is never
  silently dropped.

## 7. The approval envelope (SH-ENVELOPE-1)

### Data model

```
Envelope = {
  "id", "task_id", "project_id", "owner_id", "version": 1,
  "granted_at", "expires_at",                  # ≤ granted_at + wall_time, hard cap 72 h
  "fingerprint": "<project input fingerprint at grant>",
  "chain": [{"after_change": change_id, "fingerprint"}],   # freshness chain (B2)
  "profile": "eh_site@1",
  "edits":  [{"tool": "<catalogue write tool>",
              "target": {"component": "Generator|StorageUnit|…", "name": "glob", "attr": "p_nom_max|…"},
              "bounds": {"min"?, "max"?, "rel"?: 0.5},       # absolute, or ±rel of the value AT GRANT
              "at_grant": <value recorded at grant>}],      # never relative to the current value (B2)
  "runs":   [{"tool": "run_simulation|solve_queue_enqueue|run_sensitivity_sweep|run_eh_study|…",
              "kind"?: "lopf|ac_pf|…"}],
  "budget": Budget,                            # §6.5
  "research": {"allowed": bool, "cost_cap_eur": float},
  "notify":  {"email": bool},
  "state":  "proposed" | "granted" | "expired" | "revoked" | "exhausted",
  "audit":  [{"at", "event": "granted|used|refused|expired|revoked", "call_id"?, "tool"?, "reason"?}]
}
```

### Grant

- The intake turn proposes the envelope from the profile: each profile lists
  the edits and runs its stages need (§9).
- The user sees it on the plan-review card and grants it with one click. That
  click is a card decision, recorded through `approvals.decided(by="user")`
  with the envelope id.
  So README contract 6 holds: a card was clicked, and it named everything the
  grant covers.
- The user can narrow the envelope before granting (remove an edit, lower a
  budget), but never widen it past the profile's list. Edits may name only the
  profile's `tunable` attrs (§6.0).
- The envelope binds owner, project uuid and task id.
- "Step by step" grants nothing and keeps COP-1 exactly.

### Use

- In `confirm.gate`, the runner's session asks the envelope before a card is
  issued. A call matches when:
  1. its tool is in `edits` or `runs`;
  2. its resolved target matches an entry and the new value is within bounds;
  3. its tier is not `destructive`;
  4. it is a native call: not external, MCP or unknown (`UNKNOWN_TOOL_TIER`);
  5. the envelope is `granted`, not expired, with budget left;
  6. it is the run's project (uuid), the owner still has the ACL, and no other
     user holds the edit lock;
  7. the project fingerprint equals the last link of the freshness chain:
     the fingerprint at grant, then the one after each ledgered change. Any
     other drift (a user edit, another session) pauses the run. An unreadable
     fingerprint counts as drift (fail closed; the SC-11 analogue).

  Admission is re-checked at every dispatch. Nothing is cached from the grant.

- **Glob targets** (GK B): `at_grant` is recorded per matched instance at the
  grant. An instance that did not exist at the grant, including one the run
  created, matches no entry and gets a card. A glob matches names within the
  entry's component class only.
- **Non-numeric attributes** (GK B): an edit entry carries numeric `bounds`
  or an explicit enum of allowed values. An entry with neither, for an
  attribute such as `carrier` or `bus`, is `invalid_envelope`.
- **Data-gap tools** (GK B): `attach_tariff`, `set_weather_choice` and series
  attaches are admitted only when listed in `edits` or `runs` with a target.
  Otherwise they get a card.
- A match is recorded as `approvals.decided(..., by="envelope:<id>")` plus an
  audit row `used`, and the call runs: this is the `policy` path, which
  "still needs a durable approval record", and fails closed when it cannot be
  recorded.
- No match: the card path (§6.6). The audit gets a `refused` row with the reason.

### What it can and cannot grant

| Can grant | Cannot grant (always a card, or refused) |
|---|---|
| Listed edits within bounds; listed runs; solves up to the budget; crossing stage boundaries (COP-1 card boundaries) inside the run | `destructive`-tier tools (delete, overwrite, publish); grid-code publish (always its own card, because others rely on the publication); external or MCP calls; unknown tools; edits outside the list or bounds; anything in §6.0; creating or deleting projects other than the run's own setup; writing a researched value into the model, including `ledger.apply_user_row` from a research value (SH-RESEARCH-1, always confirmed); anything after expiry, revocation, an ACL change, a project mismatch, foreign drift or `brief_stale` |

Issue 03's acceptance criteria are this refusal matrix, one test per row:
destructive tier, delete, MCP/external, unknown tool, outside the entry, out
of bounds, expired, revoked, ACL loss, wrong project, foreign drift.

### Scope and audit

- COP-1 is unchanged for ordinary chat and copilot plans: an envelope exists
  only on a version-3 harness run. The live chat session never consults one.
- Expiry: at `expires_at`, at budget exhaustion, at `cancel_task`, or at an
  explicit revoke (the plan card's "Stop" or `cancel_task`). Expiry is a state
  change: the runner pauses and notifies, and nothing is re-granted
  automatically.
- Audit: the envelope's own `audit` plus the approvals log in the session event
  log. The report's harness chapter prints the grant, how many calls it covered
  and every refusal.

## 8. The research step (SH-RESEARCH-1)

- **Tool**: the provider's own web search and fetch, offered as a server tool
  only in a research turn. In the code it is a provider capability (behind
  `harness/providers`; contract 1 keeps vendor words there). It is not a
  catalogue tool, so it never takes one of the 128 slots (TOOLSEL-1), and chat
  turns never see it.
  - A provider without web search marks the research step `not_available`, and
    the stage goes on with the Library and template defaults, listed as a gap.
- **Isolation** (GK B, B3):
  - research turns get a read-only tool subset plus the source-ledger write;
  - no envelope admission applies inside them, so a research turn cannot call
    `update_component` even when the envelope would allow it (issue 09 tests
    this);
  - fetched content is untrusted data: the prompt frames it as data, and
    nothing in it can widen the tool subset.
- **When research may run**:
  - in stage 2 for a gap the profile marks `researchable` (for example the
    tariff level, a fuel price, an electrolyser CAPEX range, a grid-code
    reference that the user still enters themselves);
  - in stage 6 for the sourced ranges of a case;
  - never in a loop round, never in chat.
- **Cost cap**: `envelope.research.cost_cap_eur`, metered server-side from
  provider usage (search calls plus tokens of the research turns), never from
  the model's count. At the cap, research stops and the remaining gaps are
  listed. Default €2 per run; the user may raise it on the envelope up to the
  €10 hard cap (owner decision SH-RESEARCH-CAP-1, 28 Oct).
- **Source ledger**:

  ```
  SourceRow = {"id", "for": {"component"?, "attr"?, "ledger_key"?}, "value", "unit",
               "url", "title", "quote": str ≤ 400, "fetched_at", "turn_id",
               "status": "proposed" | "confirmed" | "rejected", "confirmed_by"?, "confirmed_at"?}
  ```

- **Confirmation**:
  - a researched value enters the model only through the existing edit card,
    or through `ledger.apply_user_row` for a decision-study row, with the
    source row shown beside it (value, unit, quote, link);
  - this is always a card, even inside an envelope: the envelope cannot grant
    it;
  - while values wait, the run is `needs_user` (`waiting.kind =
    research_confirm`), and stages that do not read them may go on;
  - a rejected value stays in the ledger as `rejected`.
- **Unit check**: the confirmed value must carry the target's unit. A value in
  another unit is refused, not converted (as `apply_user_row`).
- **Source integrity** (GK B): the server checks every `SourceRow` the model
  writes:
  - its `url` must be one fetched in that turn;
  - its `quote` must appear verbatim in the fetched content.

  A row that fails is shown as "unverified source" on the card, never as
  sourced. Issue 09 tests a fabricated URL and a fabricated quote.
- **Currency**: every cap is in EUR. Provider usage billed in another currency
  is converted server-side at a stated rate kept in the harness settings, and
  the rate and its date are printed in the report's harness chapter.

## 9. Harness profiles

### 9.1 Schema

A profile is a Python data module, `services/run_profiles.py`, next to
`flow_scope.py`. It is data, not prompts: the stage instructions are markdown
under `harness/workflows/profiles/`.

```
Profile = {
  "id": "eh_site", "version": 1,
  "templates": ["eh_site"],                    # the template ids it serves
  "scope": "flow_scope.preset_for",            # cards/sub-options come from TEMPLATE_PRESETS, not copied
  "params": {"required": [...], "ask": [...]}, # creation params intake must settle
  "stages": {                                  # absent = generic default; "skip" with a reason
    "<stage>": {"actions": [{"tool"|"native": name, "args": {...}}],
                "agent": "profiles/<id>/<stage>.md" | null,
                "tools": [allowlist additions],
                "rules": [Rule], "loop": {"kind": "agent|native:<tool>|none", "rounds": n},
                "researchable": [gap ids]}},
  "tunable": [{"component", "attr", "bounds"}],   # the only things L1/L2 may change
  "envelope": {"edits": [...], "runs": [...], "budget": Budget},   # the proposal at intake
  "kpis": ["kpi.<key>", ...],                  # read back at hand-off
  "chapters": ["<report chapter id>", ...],
  "missing_tools": ["…"]                       # what blocks a full unattended run today
}
```

Every action is a catalogue tool or a **native action**. A native action is a
Python callable in the profile action registry, run through the same
admission, ACL, lock and audit path as a tool, but never offered to the model.
This is how the runner reaches studies that are GUI-only today without adding
catalogue tools (TOOLSEL-1). A study the agent should also offer in chat gets
one catalogue family tool with a `kind` enum, not one tool per study (GK B,
option A). Native actions get no bypass: they use the same admission, ACL,
lock, fingerprint and audit path as the DISPATCHERS.

### 9.2 Generic defaults (every profile inherits them)

| Stage | Actions | Rules | Loop |
|---|---|---|---|
| 1 setup | `create_project_from_template(profile.templates[0], params)` | `project.exists` | none |
| 2 inputs | `get_weather_choice`; `fetch_site_context` when a site is given | `readiness.ready` | agent fill |
| 3 input gate | `validate_network`, `project_readiness`, `diagnose_network` | the core input rules | L1, 3 rounds |
| 4 solve | `solve_queue_enqueue` (`await_job`) | `solve.status` | none |
| 5 result gate | the LC-20 rule set | the core result rules | L2, 2 rounds |
| 6 explore | `run_sensitivity_sweep` | case settled | ≤4 cases |
| 7 report | `generate_report` (LC-21 network chapters) + the harness chapter | `report.ready` | none |
| 8 hand-off | read back `kpis`, notify | `handoff.notified` | none |

### 9.3 Filled profiles

The facts below come from the code (`routers/projects.py:1522-1597`,
`project_templates/*`, `flow_scope.py:173-209`, `TEMPLATE_META`). "Missing"
lists what blocks the profile from running unattended end to end today.

**eh_site** (pilot, SH-PILOT-1; issue 07)
- Params, from `get_site_template_form`:
  - required: `site_type`, `stages`, `connection_mw`;
  - asked: `energization`, `critical_share`, `load_profile`, `existing`, `candidates`, `ride_through`, `fault_level_mva`, `horizon_end`.
- Scope: `SITE_TYPE_PRESETS[site_type]`. The hospital site type adds `stress`.
- Inputs: weather, tariff (`import_urdb_tariff`/`attach_tariff`), `fetch_site_context`. Researchable: tariff level, genset fuel price.
- Input rules: the core rules plus:
  - `eh.readiness.ready`;
  - `eh.review.blockers empty` (`review_eh_study`);
  - for each stage, `energization_mw ≥ 0`.
- Solve: the multi-period EH solve (the investment card), then `run_eh_study` (reliability).
- Result rules: the core rules plus:
  - `adequacy.lole_h_per_year ≤ goal` (h/yr basis; the engine loop gets `goal × horizon years`, §6.2);
  - `kpi.unserved_mwh == 0` in normal operation.
- Loop: L2 uses an engine loop for the LOLE rule (`run_margin_loop` for a firm-capacity lever, `run_coupling_loop` for an ENS-cap lever; §6.2), and the agent loop for the other rules. The two never run on the same rule.
- Tunable: candidate `max_mw` (genset, bess, pv), BESS hours. `connection_mw` is the user's to change.
- Explore: connection size against BESS against genset, ≤4 cases.
- Chapters: the EH reference design, reliability, investment.
- Missing: none for the core. The investment-case chapter needs `run_investment_case`, which exists.

**eh_datacenter / eh_datacenter_staged** (issue 10; GK A; SAGC-1, D3-KEEP, DC13-SCOPE)
- Params: `dc_load`, `planning`.
- Scope: `_DATACENTER` (grid_code, island_checks, goal_study, stress, tariffs, investment_case).
- Inputs:
  - a published grid-code profile for the project. D3-KEEP: no TSO profile ships, so the user enters the limits;
  - 60 Hz when SAGC (SAGC-1 G2);
  - `eh_sk_mva`/`eh_ibr_mva` set, exactly one `eh_poc`.
- Input rules:
  - `dc.gridcode.filled`;
  - `gridspine.poc.count == 1`;
  - `network.frequency == profile.frequency`.
- Result rules:
  - scorecard rows (`grid_readiness_for`: SCR, peak, ramp, flicker, TDD, unbalance, reactive, island, and the families of §5), gated by verdict: only `pass` passes, and a pass on assumed inputs is a screening pass (§5). The chat tool `campus_grid_readiness` already reads them. There is no wildcard: on the shipped template 19 of 20 rows are `unknown` by construction, so a wildcard over `block` rules would end every run `needs_user`. Issue 10 lists each row id or family with its severity (`block` or `warn`), a one-line basis and the run that produces its input. For a `warn` row, `unknown` is reported as not evaluated, never as a pass;
  - energised ≥ IT load per stage;
  - LOLE (h/yr basis, §6.2) ≤ goal;
  - the studies chosen under SAGC-1 (public SAGC, May 2026, and generic engineering only), built in `gridspine/static/` (DC 09–14 are done):
    - SCR/ESCR (`strength.py`, G4): screening, never certifying;
    - PQ: TDD, unbalance, harmonic impedance scan (G5): screening; "within the screening limit", never "meets IEEE 519"; resonance inside the clearance is `warn`;
    - planning contingencies (`contingency_set.py`, `campus_taps.py`, G6): stage 2 after an outage allows taps and shunts only, and redispatch is not a remedy (DC13-SCOPE);
    - P–V/Q–V margin (G7a): **deferred to [issue 18](issues/18-pv-qv-gate-fact.md)**. `gridspine/static/voltage_stability.py` exists, but no pypsa-gui backend module imports it, so it is not a gate fact today (GK A).
- Loop: agent L2 only on tunables. Nothing in §6.0 is tunable: grid-code limits, fault levels, `eh_poc`/`eh_ibr_mva`, contingency sets, IT load.
- Tunable: BESS and genset candidates, reactive support (`q` devices), within bounds.
- Missing for an unattended run:
  - the SAGC studies have no chat tool that starts one; they are read as grid-readiness rows after the campus or gridspine run (`campus_run_study`), and GK A confirms which run produces each;
  - grid-code draft/confirm/publish is GUI-only (`routers/campus_electrical.py:605-674`; `services/not_via_chat.py` `grid_code_publish`). It stays the user's: the run waits at stage 2 until a published profile exists (D3-KEEP);
  - grid-readiness limit edits are GUI-only (`:271`), and stay the user's;
  - the SCR gate also rides inside `run_eh_study` (`adequacy/scr_gate.py:142`).
  - The plan: native actions for the runner, plus at most one `grid_readiness_study(kind=…)` family tool when chat needs to start a study (a slice of issue 10, TOOLSEL-1).
- Staged: the same, per period (2027/30/35), with `period_years` and the energization schedule from `TEMPLATE_META`. The investment case (NPV/IRR) is in the result gate.

**eh_h2_hub / eh_h2_hub_staged** (issue 11)
- Params: `planning`.
- Scope: `_H2_HUB` (process_detail, contracts, investment_case). Archetype `strong_grid`.
- Result rules:
  - the H2 offtake is met (25 MW) in every hour;
  - electrolyser utilisation within the profile band (warn);
  - LOLE (h/yr basis, §6.2) ≤ goal, with the fuel cell as firm backup.
- Investment: the IC counterfactual with H2 offtake exists in the engine (H2e, #312: the baseline buys grey H2 at its reference price), and `eh_h2_hub_staged` ships it; the single-year hub's data and the gate fact are [issue 19](issues/19-h2-offtake-ic-counterfactual.md) (GK A). Until it lands it is not a gate: the investment rules are `warn` and stay `not_evaluated` with that reason. The offtake itself is demand and never tunable (§6.0).
- Tunable: wind/PV/BESS candidate max, electrolyser and H2 storage size within bounds.
- Missing: an H2 utilisation fact (no named reader), the IC counterfactual.

**eh_microgrid / eh_microgrid_staged** (issue 12; GK A for island rules)
- Params: `planning`. `SINGLE_YEAR=True`: the end year must be after the start year.
- Scope: `_MICROGRID` (island_checks, outage_screening, goal_study, stress, investment_case). Archetype `off_grid`.
- Input rules: the tie is open (`off_grid`); the critical (hospital) feeder is tagged.
- Result rules:
  - island survival by the verdicts of the scorecard's island rows (`ride_through:*`, `sustained_h` from `gridspine/schema/island.py`, the frequency judge);
  - LOLE (h/yr basis, §6.2) ≤ goal;
  - excluded on `off_grid`, with the reason and a test (GK A): SCR and every row built from the PoC's entered S_k'' (TDD, rapid voltage change, flicker, unbalance, motor start). With the tie open they mean nothing, and the fault level comes from the diesels.
- Investment baseline: the existing diesel fleet, which the engine's baseline re-solve already gives (#302, owner M2e (a)); the gate fact is [issue 20](issues/20-microgrid-diesel-ic-baseline.md) (GK A). It is not a gate yet; the rule is `warn`, `not_evaluated` until then.
- Tunable: battery size and GFM settings within bounds, PV max.
- Missing: the island tools are the `campus_*` set and their fit for a microgrid project is unverified; the IC baseline.

**eh_ammonia_staged** (issue 13; GK A; g2p 10–13)
- Params: `planning`. It builds on `eh_h2_hub_staged`: 200 MW connection, a 160 t/d Haber–Bosch loop.
- Result rules:
  - `binary_check.status` passes (`services/solver/process_uc.py:351`, automatic after a solve; the `flagged` and `periods` gaps are facts);
  - `nh3_delivery_tpd` ≥ target, with the shortfall shown as grey NH3 or penalty;
  - investment against the grey-NH3 counterfactual.
- **Pending g2p 10–13** (GK A): those issues are gated and unbuilt, so the ammonia-specific result gate is marked pending until they land. The coupling report (g2p 13) is a profile chapter then. It is not `run_coupling_loop`, which is the generic LOLE loop (§6.2).
- Loop: agent L2 on tunables. Process demand and NH3 offtake are never tunable (§6.0).
- Missing: a chat or native read for the binary check (it is only in the solve result payload); the process-unit form is guided-only (G2P-07).

**eh_ipp_hybrid** (issue 14; GK B for investment/finance)
- Params: `planning`. Ships `cases.json` and a market-series fetcher.
- Scope: `_IPP` (tariffs, contracts, investment_case). Grid, reliability and control are off.
- Stages:
  - inputs fetch the market series;
  - solve, then `run_investment_case`;
  - result rules: export ≤ 70 MW limit, `ic.min_dscr ≥ profile.dscr` (warn), `ic.project_pre_tax_irr` present;
  - explore runs the shipped `cases.json` (≤4 cases) and `solve_ppa_price` for a target IRR.
- Tunable: battery candidate size, PPA share within bounds.
- Missing: none. Every tool exists as a chat tool.

**Grid-only: 3bus, ieee14, belgium, ieee39** (issue 15; GK A for the N-1 rule)
- Params: none. No sidecars; preset `BLANK_PRESET`. `belgium` and `ieee39` may be absent when their source is missing (`_build.py:228, 247`): setup refuses with that reason.
- Stages:
  - setup;
  - input gate (core rules);
  - solve (LOPF, then `run_ac_pf_stage`);
  - result gate:
    - core rules;
    - PF convergence, voltage band and loading ≤ 100 % gated **only on AC PF results the engine labels as PF**, never on LOPF values (GK A, ACPF-BUG). Without an AC PF result these rules are `not_evaluated`;
    - N-1 optional (`warn`), when the solver config's `OWNED` option is on.
- Explore is skipped by default. Report: the LC-21 network chapters.
- Tunable: none by default (L2 diagnoses only and goes to `needs_user`).

**eh_concentrator_staged** (planned; issue 16, needs-triage until g2p 10 lands)
- Not in the registry. Spec: `.scratch/grid-to-process/design.md:402-518`; tickets g2p 10, 11, 14.
- Planned rules:
  - mass balance to 1e-6;
  - motor-start dip at the PoC (g2p 12, 18);
  - restart;
  - N-1 on the site transformer;
  - the investment case against "site as today".
- KPIs: lost tonnes of concentrate, lost-production EUR, shed level, NPV by stage.

## 10. Placement (UX-flow: one entry point)

- **Stage names** (the user's words; the ids stay internal): 0 Brief · 1 Set up · 2 Inputs · 3 Check inputs · 4 Solve · 5 Check results · 6 Explore options · 7 Report · 8 Hand-off.
- **Start**: the companion composer. The user states the goal, as in LC-23.
  There is no new Start button, and the hubDesign Start card is unchanged.
- **Approve**: the existing plan-review Choice card gains an **Envelope**
  section with the edits, runs, budgets, expiry and email opt-in. Its three
  verdicts are "Approve and run unattended", "Approve, step by step" (COP-1)
  and "Change".
- **Monitor**: the existing LC-23 plan card (`AssistantResultCard` in the
  companion) shows:
  - the stage rows;
  - the current stage expanded, with its gate rules pass or fail, loop round
    n/N, and budgets used;
  - "Waiting for you" items;
  - Pause and Stop.

  There is no new panel and no second progress line. The card-workspace Next
  row keeps its native next step.
- **Away**: the attention notice and the optional email open the companion on
  the plan card.
- **Report**: LC-21 chapters in the Reports panel. The plan card's last row
  links there.

UX verdict (28 Oct): GO on placement. Issue 08 carries its eight conditions:
both modes with a screenshot per state, plain-word envelope with units, exactly
three verdicts with a disabled reason, what Stop keeps, today's Confirmation
card for anything off-envelope, no automatic panel switch (`withReturnFrom`,
`withReturn`), wording rules, and failure states in words with a "›" fix.

## 11. Open questions

1. **LC-20's user-facing read** (GK B, in issue 05): a new read tool, or a
   `checks` argument on an existing results read.

Settled at plan review (28 Oct):
- Research cost cap: €2 default per run, raisable on the envelope up to a
  €10 hard cap (owner, SH-RESEARCH-CAP-1).
- Guided-only studies: option A, native actions plus at most one family tool
  later (GK B). Registry actions go through the same admission, ACL, lock,
  fingerprint and audit path as the DISPATCHERS, with no bypass for profile
  code. Grid-code publish is never envelope-admissible. The family tool sets
  its confirmation tier per `kind`. Engine semantics are GK A's.
- Gateable facts, engine loops and the never-tunable list: GK A (§5, §6.0,
  §6.2, §9.3).
- Execution context: option b, the explicit base context (GK B, §6.6).
- Engine issues (GK A): [18](issues/18-pv-qv-gate-fact.md) P–V/Q–V wiring into
  the backend; [19](issues/19-h2-offtake-ic-counterfactual.md) the H2-offtake IC
  counterfactual (the engine has it since H2e/#312; 19 adds the single-year hub's
  data and the `ic.counterfactual` fact); [20](issues/20-microgrid-diesel-ic-baseline.md)
  the microgrid diesel-fleet IC baseline (the engine has it since #302; 20 adds the
  fact).

## 12. What stays as it is

- COP-1 for chat and copilot.
- `goals.py` for chat.
- The `harness/workflows/*.md` files: hub-design, run-study and
  investment-decision become generated from profiles in issue 17 and are not
  edited by hand before then.
- Decision-study internals.
- DC-07 rolling optimisation, which stays later.
- Site-scene SC-07 (`.scratch/site-scene-generation/issues/08-ai-guided-project-flow.md`,
  needs-triage) overlaps issues 07 and 08 (AI guidance across the project
  flow). The coordinator proposed folding it into them. That is not an owner
  decision yet, and site-scene sessions are live, so its file is not edited
  here. Issue 08 names the overlap.
