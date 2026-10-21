# The chat harness

Everything the in-app assistant needs that is **not** a language model, in
one folder. A provider (Claude, an OpenAI-compatible model, Kimi, Qwen, a
local endpoint) plugs in underneath; the harness is what makes the
assistant behave the same whichever one is active.

```
harness/
  protocol.py     the provider seam: LLMProvider, LLMRequest, LLMEvent, ERROR_KINDS
  catalogue.py    the tool declarations, their Safety tiers, TOOL_ROUTES
  toolsets.py     ordered provider-neutral catalogue views and always-available controls
  events.py       the closed vocabulary of frames the turn loop yields to the UI
  assurance.py    opt-in checked input/result drafts and publication gate
  assurance_scope.py  typed requested metrics, windows, cost basis and retained/comparison IDs
  assurance_conversation.py  bounded checked-scope carryover, cost clarification and previous-run selection
  assurance_recovery.py  private owned checkpoints, durable invalidation and native history recovery
  calculations.py  bounded formula/unit registry, raw masks and cost dependencies
  storage_calculations.py  raw StorageUnit power, state and chronological balance checks
  storage_costs.py  declared-basis direct StorageUnit discharge, holding and fixed costs
  durable.py      bounded network locks and cross-process owner-store transactions
  effects.py      exact confirmed input-write reservations, read-back and reconciliation
  navigation.py   issued view actions and owner/project/turn/generation-bound browser ACKs
  reviewer.py     optional single bounded read-only review of qualitative interpretations
  publication.py  JSON/ZIP from the same validated answer, facts and evidence
  rollout.py      capabilities, profile/effect rollback and bounded shadow observation
  pilot_evaluation.py  independent finite-suite cases, release scoring and live resource ceilings
  workflows/      the start menu and the step-by-step flows (Markdown + front matter)
  skills/         procedures the model loads on demand (<name>/SKILL.md)
  prompts/        the system-prompt fragments as Markdown, byte-identical to the old constants
  providers/      anthropic, openai_compat, openai_responses, usage (one token-usage meaning for both OpenAI wires), fake, and wiring (profile → provider, tools payload, history portability) — the only place a wire is named
  loop.py         the turn loop: the stream seam and tool dispatch (the former services/chat_service.py); `dispatch_tool_call` is the one per-call path (allowlist → guard → validators → card → handler) every caller drives
  solver_bridge.py  the solver log → tool_progress bridge for a running run_simulation / run_ac_pf_stage call
  job_adapters.py foreground (fg:…) and later domain job handles for wait_for_job without holding the turn
  activity_history.py bounded owned job-origin/status observations across worker loss; no execution, numerical or approval authority
  completion_observer.py bounded weak native-worker status observation while Activity is closed; no advancement or retry
  experiment_execution.py ordinary network-case admission and permanent no-replay receipts
  experiment_two_case.py sequential two-case admission with shared caps and failure policy
  experiment_reconciliation.py explicitly confirmed fenced native submission read-back; no execution or retry
  experiment_comparison.py current compatible objective comparisons and digest-bound export; no execution authority
  attention.py    bounded owned question/confirmation observations and versioned read-only inspection; no restored approval or answer authority
  owned_snapshots.py private atomic bounded snapshot storage shared by job and decision observations; schemas remain separate
  compose.py      the system prompt for a turn, the live network line, and the per-turn ui-context / Guided / workflow-step blocks
  budget.py       the per-turn / per-session / daily caps, the retry schedule, the per-tool timeout, the budget gate
  stub.py         the scripted stub loop behind StreamRequest.script
  session.py      ChatSession, PendingConfirmation, the session registry with its TTL and cap
  confirm.py      the confirmation gate: tiers, the Guided write rule, auto-approve, parallel-destructive
  access.py       authenticated read-only current scope, confirmation policy, locks and configured limits
  restrictions.py revisioned personal saved-project tool restrictions; fresh admission/pickup checks, no grants
  approvals.py    every card's asked/decided record per owner and session; an approval that cannot be recorded does not run
  eventlog.py     the session event log: every model-visible input, append-only per owner and session; derive_messages, fork, torn-tail repair; the source of truth (the deque is its fold, a chat not in memory is rebuilt from it, interrupted turns are read off it, chat.jsonl is its projection)
  replay.py       fork a recorded session before one turn and run that turn again on another provider (smoke/replay_session.py)
  mcp_server.py   local MCP exposure (issue 11): the protocol behind /api/mcp (routers/mcp.py), the exposed catalogue, one `mcp-` chat session per agent, the per-launch token, the cards waiting in the panel
  mcp_client.py   external tools from admin-listed MCP servers (issue 19): the server list, the stdio and HTTP JSON-RPC client, the per-session tool list and tiers, the dispatcher
  goals.py        a goal with a round budget: set/pause/complete, what stops it after a turn, the per-turn goal block (rounds run in loop.run_turn)
  ratelimit.py    the /stream token bucket
  sse.py          the SSE framing of a yielded (event, payload)
  fence.py        the untrusted-content delimiters and the neutraliser
  results.py      tool-result shaping: coercion, truncation, the per-turn result budget, error content
  spill.py        oversized results kept whole per owner and session; the model gets a preview and a locator for `read_result`
  history.py      the in-memory trim and turn summary, chat.jsonl persistence (a projection of the event log), lineage
  metrics.py      the process-wide chat metrics behind GET /api/chat/metrics
```

Borrowed designs (phase 5): `docs/superpowers/assessments/2026-10-06-deepseek-harness-adoption-assessment.md`; issues 13–20 under `.scratch/harness/issues/` (20 and 15 done, the rest re-scoped against the modules above on 2026-10-12, plan §9). Spec: `.scratch/harness/spec.md`. Plan:
`docs/superpowers/plans/2026-10-05-chat-harness.md`. Vocabulary:
`pypsa-gui/CONTEXT.md` (Harness, Workflow, Skill, Choice card, Start menu).

Activity's decision observations are read-only. `attention.py` captures admitted
Choice frames and audited native confirmation outcomes in a separate private
owner/project store (newest 256 origins, 1 MB, 30-day visibility, lazy expiry).
`GET /api/chat/decisions/{id}` rechecks project access and the listed revision/view
digest; a changed record needs a new Activity snapshot. The digest is not an
approval credential. Inspection never restores sessions or approval tokens,
answers questions, or certifies effects/results. A later native turn is only a
recorded follow-up, not proof of resolution. See
`.scratch/chat-companion/issues/04c-retained-decision-observations.md` for the
recovery, unavailable-history and remaining-scope contract.

Published checked-answer receipts with unavailable or clarification-required
result slots also retain a bounded `source_gap` observation in the same store.
Inspection shows the exact answer/turn origin and request descriptors with
missing metric reasons, never result values or evidence authority. These are
historical gaps: later turns do not silently resolve them, and the inspector
does not recheck evidence or execute work. Fully answered requests and non-scoped
refusals are not captured by this adapter. See
`.scratch/chat-companion/issues/04d-retained-source-gaps.md`.

An inspectable, fully specified source gap can prepare a separate checked
follow-up card in the current chat. Sending requires an explicit user click,
strict checking and an exact version/digest-bound source_gap_ref plus scope.
The native worker rechecks current owner/project access and the unchanged
request before provider work. Clarification-required scopes are not guessed.
Only an admitted answer_approved publication appends a bounded link (at most
eight) to the original gap. A receipt's request_complete flag describes that
turn, not current evidence or resolution; the gap remains historical Needs
review. Preparation never queues a send, alters the ordinary draft, restores an
old session or changes global opt-in. See the CP-04e contract in
`.scratch/chat-companion/issues/04e-explicit-gap-followups.md`.

Registered jobs with an established native Thread can also be observed while
Activity and native waits are closed. `completion_observer.py` uses one daemon,
at most 512 weak context bindings and a 24-hour watch limit. It reads only exact
native status without waiting for lifecycle locks, then journals outside those
locks to the registration's owned destination. Missing/replaced/rebound sources,
exited workers without terminal status and missed capture remain unknown/Needs
review; a completion observation is not engineering evidence or quiescence.
This best-effort observer cannot recover a terminal slot already replaced or a
process that died before persistence. It never advances or retries work. See
`.scratch/chat-companion/issues/04f-native-completion-observation.md`.

## The contract

In-page chat's access summary reads the same authenticated `/api/chat/access`
snapshot as the dock. ChatPanel supplies its actual next-turn checked selection
to both surfaces; opening the summary never sends a message or approves a card.
The summary hides observations during refresh/failure or a scope mismatch and
offers the existing full review and revisioned restriction editor. It is an
observation, not admission authority or proof that a running answer was checked.
See `.scratch/chat-companion/issues/06d-companion-access-summary.md`.

1. **Layering.** `services/* <- harness <- harness/providers`. Nothing in
   this folder outside `providers/` imports an SDK or names a wire
   (`anthropic`, `openai`, `cache_control`). `tests/test_harness_layout.py`
   greps for it. Cache intent is the `stable` flag, never a vendor word.
2. **Tools are data.** A tool is one entry in `catalogue.TOOLS`
   (`{name, description, input_schema}`), its handler one entry in
   `services.chat_tools.DISPATCHERS`, its route one entry in
   `catalogue.TOOL_ROUTES`. The `Safety: <tier>` text in the description IS
   the tier declaration (`catalogue.safety_tier_for`). The parity tests
   (`test_chat_tools_schema_match.py`, `test_chat_tools_endpoint_map.py`)
   keep the three in step.
3. **Frames are closed.** Every `(event, payload)` the loop yields is named
   in `events.FRAMES`; the tripwire test fails on a new name.
4. **Behaviour is Markdown.** A workflow or a skill is a file; adding one
   is a content change. Files are read from this package only — never from
   a project directory or an upload — because they are instructions. One
   exception (issue 14, owner decision Q16): user skills from
   `<app data>/skills/`, an install's own folder, ranked after the package;
   a user skill named like a package skill is refused, never shadowing it.
5. **Per-turn, not system.** Anything that changes with the user's place
   (the workflow step, the Guided rules, `ui_context`) travels as per-turn
   user content. The system prompt stays byte-identical and cached.
6. **The confirmation card is not negotiable.** A workflow may say "a card
   follows"; it cannot change which tier gets one.
7. **Old paths still work.** `services.chat_service`,
   `services.llm_provider`, `services.chat_tools_schema`,
   `services.llm_anthropic`, `services.llm_openai_compat` and
   `services.llm_fake` are `sys.modules` aliases of the harness modules:
   same objects, same monkeypatches. New code imports from `harness.*`.
8. **The loop's provider words only go down.** `loop.py` arrived carrying
   the seam spec's known leaks; the wiring (profile resolution, the
   provider factory, the history-portability filter) now lives in
   `providers/wiring.py`, and the layout test pins what is left in the loop
   (the capability refusals in the turn body). A new one goes into
   `providers/`, not into the loop.
9. **Model-visible means logged.** The session event log (`eventlog.py`,
   issue 13) is the source of truth: `ChatSession.messages` changes only
   through `eventlog.apply`, which records an event and folds it in with the
   step `derive_messages` uses (`append_history_message`, its trim,
   `rewind_session` and `reset_history` all go through it), so the deque IS
   the log's projection. A session not in memory is rebuilt from its log; the
   log's `turn/start` replaced the pending-turn WAL; each `chat.jsonl` record
   is a `turn/record` event first. A new writer to `session.messages` goes
   through `eventlog.apply` (`tests/test_harness_eventlog_truth.py` fails on
   one that does not).

## Current access visibility

`GET /api/chat/access` authenticates the current project and optional owned
session without creating a turn or connecting external servers. It projects the
same `confirm.confirmation_policy` used by the ordinary gate, current project
lock, checked selection and configured caps. The Guided/Expert and checked
query selections describe a next turn, not a running turn. The read-only view is
not an action capability, remaining-budget estimate, complete readiness check or
permission to incur charges. Unknown usage is unavailable rather than zero.
Locks are inspected without pruning expired rows; busy chat state returns 409
without waiting behind a turn's session lock. The response is `no-store`;
dispatch and effect commit keep their own current checks. The dock's **Access
and approvals** disclosure hides stale/failed or mismatched-scope reads. Existing
Confirmation/Choice cards and Activity remain the decision paths. See
`.scratch/chat-companion/issues/06a-effective-permission-visibility.md` for limits
and the remaining CP-06 contract.

Ordinary and checked dispatch snapshot finite JSON arguments before emitting
frames. Emitted request/card arguments and the pending token record are separate
copies. Approval cannot replace the action with a caller-mutated argument.
Current server confirmation-tier sets, native exemptions and token lifetime form
an effective configuration revision; a change while waiting revokes approval.
Dispatch also rechecks that revision, the tool's tier and its external server
identity before admission and at worker pickup. This is not a durable policy
history or an external-server reconnect/configuration revision (CP-13).

Access and approvals can save the authenticated user's personal rule for the
current saved project: standard server floors, an ordinary audited card for
every non-read tool, or refusal of non-read tools. This includes native and
external dispatch in both modes and checked turns; it grants no new tools or
checked adapters. Read-only tools can still involve inference and usage limits.
An additive owner/project database row stores the current mode and revision.
PUT `/api/chat/restrictions` requires the current project binding and expected
revision; concurrent or stale saves cannot overwrite another choice. Clearing
saves standard with a new revision. Missing rows preserve server floors;
unavailable/corrupt storage refuses admission. Fresh reads after approval and
at actual worker pickup invalidate changed revisions, including change-and-restore.
Saving does not cancel handlers already started. Rules are not imported with
project data, inherited by scenarios or shared with other users. This current
record is not durable policy history. See
`.scratch/chat-companion/issues/06c-saved-restrictions.md`.

## Component schema discovery

`get_component_schema` is an ordinary read-tier control, available in every
toolset and within the existing provider offer caps. It pages the actual
component request models, PyPSA defaults and asset descriptors: stored/display
units, declared JSON constraints, defaults, and conservative derived/output
editability. A page is at most3800 serialized characters; advance `offset` by
`returned` while `has_more`. An individually oversized field refuses rather
than silently clipping its schema. Query matches field names/labels, not values.

This is class metadata, including in a networkless planning context, never
proof that a project has an instance or supports an engine. Actual values stay
on-demand through existing owned component reads. The metadata hash is not an
input revision, validation-policy revision or approval. Native handlers still
validate every write, including cross-field rules absent from JSON Schema.
Personal read-only rules remain authoritative even for a field marked editable;
the strict checked pilot continues to refuse this unmapped tool. Non-component
service/domain schemas and exact target resolution remain LC-01 follow-ups.

## Edit cards (LC-04)

Owner decision Q3: every chat edit shows an old → new card before it applies,
in Expert as in Guided, settings included. `harness/edit_card.py` is the one
preview → confirm → receipt contract; the dispatcher (`loop.py`) calls it for
every tool `edit_card.is_edit` names, and the browser renders it with one
component (`frontend/src/components/EditCard.tsx`).

- **What is an edit.** A tool that writes the live network
  (`catalogue.tool_edits_network`) or a project setting (`SETTINGS_EDITS`).
  Every other write-tier tool is in `NOT_EDITS` with its reason;
  `tests/test_edit_card.py` fails on an unclassified new write tool. An edit
  is gated as Guided gates a write, so Expert shows the card too.
- **Preview.** `preview(tool, args)` returns `{version, kind, tool, rows,
  says, complete, digest}`, or refuses before any card (`unknown_field`,
  `derived_field`, `read_only_field`, `invalid_edit_value`,
  `component_not_found`, `edit_refused`). Component rows come from running the
  NATIVE handler on an isolated copy (`workflow_tools._isolated`, change log
  suppressed), so derived columns are exactly what the native derive writes;
  they appear as `role: 'derived'` rows. Values are in stored units; a field
  with a display scale also carries `display` (e.g. EUR/MWh stored, EUR/kWh
  shown). Nothing in the seam converts or annualises a value.
- **Approval binding.** The card rides the ordinary
  `tool_pending_confirmation` frame as `edit_card`. Just before the handler
  runs, `require_current` re-previews; a different digest (the inputs moved)
  refuses with `edit_preview_stale` and applies nothing.
- **Receipt.** The tool result gains `_edit_receipt`: each row read back
  natively, `outcome: verified` only when every value equals the card's `new`
  (otherwise `uncertain`), `results_stale` (the last solve's input fingerprint
  differs from now) and `undo: {tool: 'undo_last', available}`.
- **Adding an edit kind** (TS-04's series refinement, a new settings tool):
  `edit_card.register(tool, Adapter(kind, preview, readback))`, where
  `preview(args)` returns rows `{component_class, name, field, label, old,
  new, unit, role}` and `readback(args, rows)` the stored values in row order.
  An edit without an adapter still gets a card of kind `arguments`
  (`complete: false`): the requested values, no old value claimed, and its
  receipt is never `verified`.
- **Prompt.** `prompts/edit_card.md` (tools-on): ambiguity asks one Choice of
  component and app-described field with unit and basis; never a derived field.
- **Not covered here.** Checked-assurance turns keep their own effect path
  (`effects.py`) and do not get a card from this seam.

## Target and reference resolution (LC-01B, LC-10)

Two read-tier controls name a target before anything reads or changes it.
Neither activates, selects or opens anything, and neither grants authority:
every later use rechecks access natively.

- `resolve_project_target(project_name)` maps an exact project name to its
  canonical `project_id`: no trimming, case-folding, partial match or
  UUID guessing, and unknown and inaccessible names fail the same way.
- `resolve_reference(target, ...)` applies one ladder for "this", "the" and
  "latest": explicit, on screen, discussed last, the only one, otherwise
  candidates to ask about with one `ask_user` Choice. The on-screen rung
  reads `ChatSession.turn_ui_context`, which the loop replaces every turn
  with the request's UI context; it is a hint, re-read against the active
  network or the project's run records. `says` names what was resolved and
  `ignored` the hints that were not used. The tools-on prompt part
  `prompts/reference_ladder.md` tells the model when to call it.

## Local MCP exposure (issue 11)

On the desktop (local mode), an agent on this machine drives the app through
`POST /api/mcp` (`mcp_server.py`, `routers/mcp.py`). At startup the app writes
`<app data>/mcp-endpoint.json` with this launch's URL and bearer token. A
client that speaks Streamable HTTP can use them directly. A stdio client runs
`python pypsa-gui/backend/mcp_stdio.py`, which reads the file for every message
and forwards it, so the desktop's per-launch port never has to be configured.
The frozen desktop bundle deliberately has **no** `pypsa-gui mcp` subcommand
(owner decision, 2026-10-14): the script stays standalone, and a bundle-only
user connects an HTTP-capable client with the endpoint file instead.

* Every call goes through `loop.dispatch_tool_call` with write and above
  carded; the card waits in the panel's external-agent strip.
* One call at a time per session (a second answers 409, JSON-RPC -32002); at
  most `MAX_SESSIONS_PER_USER` sessions per user, the oldest idle one evicted
  for a new one, and 429 when every one is waiting on a card.
* The token is compared in constant time as bytes; a malformed header is a 401.
* The endpoint file is written with mode 0600. **On Windows `chmod` does not
  restrict it**: there it relies on the per-user app-data folder's ACL
  (`%LOCALAPPDATA%`, readable by the user and administrators only).

## The harness tools

### Checked-answer contracts

`PYPSA_GUI_ANSWER_ASSURANCE_MODE=strict` withholds model prose/thinking and gates
publication through `assurance.py`; the default remains `disabled`. `pilot`
admits an explicit version-1 strict request. The chat's **Check answers** option
uses the shared harness when the server enables pilot
mode. Checking defaults off for new pilot chats and project bindings; strict
server mode requires it. Browser publication is armed before dispatch and refuses
an omitted/downgraded acknowledgement. Retries, edits and clarification choices
preserve checked policy, including a conservative history hint when an earlier
answer is unavailable. That hint is never approval. Read the
[pilot activation and rollback guide](../../../.scratch/assistant-assurance/checked-opt-in-runbook.md).
The repeatable CI pilot evaluation runs 174 independent positive/adversarial cases
and rejects missing coverage or nonzero errors. `pixi run gui-pilot-evaluation`
writes a versioned report; optional live generation has fixed pre-send ceilings.
See the [evaluation runbook](../../../.scratch/assistant-assurance/pilot-evaluation-runbook.md).
This finite-suite result does not confer production or independent domain approval.
Offline `review_calibration.py` prepares blind interpretation judgments and
reconciles two independently attested domain reviewers. Incomplete/disputed
labels and provider errors prevent complete measurement. Synthetic cases cannot
produce population error estimates; identity and sampling attestations require
external verification. See the [calibration and review guide](../../../.scratch/assistant-assurance/calibration-runbook.md).
Shared scope and calculation modules own project/entity/window/metric constraints, required-slot dispositions, units,
formulas and coverage. Five bounded profiles support generator dispatch,
declared-basis generator costs, a linear network subset, StorageUnit dispatch
and declared-basis direct StorageUnit costs. A sixth, `carrier-energy/1`
(`carrier_calculations.py`), sums one exact carrier's generator energy, combined
peak and StorageUnit charge/discharge; it is opt-in (`calculations.OPT_IN_PROFILES`
run only when listed in `PYPSA_GUI_ANSWER_ASSURANCE_PROFILES`), excludes Links and
Stores and always discloses how many it excluded. `branch-loading/1` and
`network-loading/1` (`branch_calculations.py`, also opt-in) check one Line or
Transformer, or the whole network, against `loadflow_summary`'s LOPF definition:
P-only flow max(|p0|, |p1|) over rating·s_max_pu(t), overload above 1 + 1e-6. They
refuse whenever AC power-flow results exist. `lost-load/1` (`lost_load_calculations.py`,
opt-in) rebuilds one Load's or the network's unserved energy, peak, weighted shed
hours and VoLL cost from the solve's own VoLL-slack capture, which every solve
claim wipes and rewrites beside the input fingerprints. Monte Carlo LOLE (and
the EH study's per-year LOLE) stays withheld: no per-sample series is retained.
The storage profile validates separate charging/discharging power and the complete
predecessor state chain before calculating exact-window energy, peaks and final
stored energy. It uses storage duration weights and refuses cyclic units,
nonzero inflow/spill. The separate storage-cost profile uses objective weights
for discharge and end-of-snapshot holding expenditure, and an explicit allocation
of the annual installed-power charge. It excludes charging-electricity purchases,
revenues, overnight/FOM derivations, nonlinear costs and other storage models.
Missing cost basis,
unsupported components and quantities receive unavailable slots. Software renders
exact checked values; a model cannot replace them with narrative or its own numbers.

Live sessions retain at most two distinct checked run references for bounded
repeat/window/comparison follow-ups. Material cost ambiguity prompts a software
question with no provider call; only successfully checked results seed context.
Project/generation/selection changes, rewind, expiration or missing context
require a fresh explicit scope. Historical repeats keep their retained run.
A freshly recreated session can recover the same bounded scope from a private
owned checkpoint tied to its latest native approved history record. Recovery
revalidates every retained reference and admits one new browser generation;
subsequent generation changes retain the ordinary refusal. Imported histories,
gaps, source drift, unavailable checkpoints and unmatched anchors cannot resume
scope. Strict admission revokes the old checkpoint before execution; rewind
revokes it before dropping messages. Existing live history reads do not replace
context. Default/shadow modes do not recover checked context.
Queued corrections resolve their own checked scope; the previous utterance's
explicit scope is never forwarded to silently discard new constraints.

Immutable retained evidence and exact same-basis comparison IDs support replay
without rerunning a model. Optional qualitative interpretations need one bounded
read-only review; quantitative prose is withheld before review. The separately
opt-in effect adapter requires an exact confirmed static-input update contract,
durable ID reservation and independent input read-back. Live/uncertain writes
never silently retry. Supported navigation stays submitted until an issued,
current browser ACK arrives. Legacy version-1 ACKs certify a mounted view only.
Version-2 load-flow ACKs additionally require fetched/rendered matching source
binding and a current raw-data/input read-back; neither grants domain approval.
Authenticated JSON/ZIP exports and the
evidence card consume the same answer/receipt; existing PNG integrity is separate.

Profile rollback, effect rollback and bounded shadow observations live in the
shared harness. No new stream vocabulary or provider-specific guard path is used.
The actor-owned record store uses bounded cross-process transactions, but the live
project runtime still supports one application worker. These checks do not certify
all engineering domains, arbitrary request completeness or independent expert
approval. Unmapped strict tools fail closed: a checked turn runs only
`assurance.CHECKED_TOOLS`, and `tests/test_checked_results_dispositions.py` proves
every other catalogue tool is refused before its handler runs. Each result view
declares `checked_profiles` and a `checked_gap` (`services/result_views.py`; plan in
`.scratch/checked-results/audit.md`). Deployment remains opt-in. Read the
[implemented API and settings](../../../.scratch/assistant-assurance/harness-contracts.md),
[implementation boundary](../../../.scratch/assistant-assurance/implementation-status.md)
and [reliability design](../../../.scratch/assistant-assurance/reliability-contract.md)
before extending adapters.

### Existing workflow controls

Read-tier tools exist only for the harness (catalogue banner "Harness"):
`ask_user` (a Choice card; the pick is the next user message, the turn does
not block; optional `multi_select`, a Markdown `detail`, and `intent`
`choice` or `plan_review`), `use_skill` (a skill's body on demand), `start_workflow`,
`advance_workflow`, `end_workflow` (the session's `{id, step}`; the current
step's body rides each turn as per-turn user content, after the context
block and outside the untrusted fence), `read_result` (pages a result that
was too large to show whole: `spill.py` keeps it under the app-data dir per
owner and session and gives the model a preview and a locator; never in a
checked turn, never for a tool with its own `offset`/`page`/`cursor`, whose
cut names that argument instead; a control tool, so every toolset view and
the OpenAI tool selection keep it). `use_toolset` changes the session's
catalogue view for the next model request, including within the same turn.
`set_goal`, `pause_goal`, `complete_goal` (issue 17) keep a goal with a round
budget on the session (default 5, ceiling 20); after a turn, `run_turn` runs
the next round itself on the same stream, as the user message `Continue toward
the goal (round n of N).` admitted through utterance admission (a queued
correction goes first). It stops when the turn showed a Choice card (the
user's answer is the next turn, then rounds go on), a declined or lapsed card
(paused), an error, a budget refusal, a task step needing review, a changed
admission generation or a checked turn (blocked), or when the rounds are used
up (paused). Only a person resumes, at `POST /api/chat/{id}/goal`, with fresh
rounds. `goal_state` frames carry it to the panel's goal strip, before the
`error` or `session_done` that closes the stream; the event log has
`goal/set`, `goal/round` and `goal/phase`.
These controls do not change project access or confirmation rules.

The workflow helpers in `services/workflow_tools.py` reuse existing services:
`project_readiness` checks prerequisites; `wait_for_job` waits locally for an
authorized queue job for at most 25 seconds and emits existing progress frames;
`get_study_evidence` returns bounded, filtered evidence and software-computed
counts before paging/status filtering, cached by project/run/config/source
fingerprints. Stale or active study results are unavailable. Source freshness
uses file timestamps and saved config, not a scientific validity certificate.
Simulation mode reads saved results and optional baseline comparisons.
Wide study rows are explicitly clipped, retaining counts and page cursors;
simulation comparisons expose saved headline deltas, with detailed tables
available through existing result tools.

`run_sensitivity_sweep` is execution-tier: one normal confirmation authorizes
1–4 new scenarios and their queued solves. It preflights all typed component
changes on isolated contexts, preserves the baseline and active binding, and
reuses solved cases only with matching provenance and canonical model inputs.
PyPSA's deterministic slack-control assignment is normalized in the fingerprint;
solver outputs are excluded. It refuses dirty/conflicting cases, rechecks saved
baseline files before writes, and reports partial failures without deleting work.
Investment-period weights participate in reuse checks; active case jobs are
returned through queue deduplication rather than reported as completed reuse.
It changes static parameters; existing time-series overrides remain in force.
Large preparations remain subject to the standard per-tool execution deadline.
The `project-refinement` skill teaches the whole sequence on demand.

All five workflow tools use the shared catalogue and dispatcher. They are
available to any tool-capable agent using this harness: Claude through the
Anthropic adapter, or a configured remote/local model through the compatible
adapter. A new agent adapter implements `protocol.LLMProvider`; it receives
the same neutral tool schemas and returns neutral tool-call events to the
existing turn loop. The tool handlers do not construct model clients. Toolset
views belong to each chat session, so one agent's selection does not change
another agent's view. Project eligibility, authentication, confirmations and
dispatch allowlists apply on every provider.

`tests/test_workflow_provider_parity.py` exercises all five tools through the
real Anthropic SDK adapter and compatible remote/local adapters with mocked
HTTP streams, real project/queue handlers and HiGHS solves. It checks streamed
arguments, tool-result IDs, continuation, confirmation denial and independent
session views without API credentials or spending. These transport tests do
not certify that an arbitrary endpoint/model supports tool calling.

### Single network experiment advancement

`advance_experiment` arms one prepared case and offers an ordinary call; it does
not execute it. The confirmed sensitivity/queue handler is wrapped by
`experiment_execution.py` with an owner/project/input-bound durable reservation,
attempt/time caps and no automatic resubmission. `refresh_experiment` requires
terminal job success, exact saved/solver input identities and intact run evidence
before returning the recorded objective. `cancel_experiment` stops advancement,
not jobs. Explicit case activation and native evidence export remain ordinary
tools. Strict checked policy stays unchanged. See the
[case contract](../../../.scratch/live-voice/experiment-case-advancement.md).

## Splitting the loop

`loop.py` is one module on purpose for now. The suite patches 27 of its
names (`CONFIRMATION_TTL_SECONDS` eighteen times, the retry delays, the
rate and session caps, `ROTATE_BYTES`, `AUTO_APPROVE_TIERS`, a few
functions) through `chat_service.<name>`, and a function that moves to
another module stops reading the patched name. So a further extraction
follows one rule: **a tunable moves together with every reader, and the
tests that patch it move their target to the new module in the same
commit**, with the frame-recording gate
(`tests/test_chat_turn_frame_contract.py`, re-recorded only by
`tests/record_chat_turn_frames.py`) unchanged. Done so far: `sse`, `fence`, `results`, `history`, `metrics`, `ratelimit`,
`session`, `confirm`, `providers/wiring`, `stub`, `budget`, `compose`, `solver_bridge`. Nine tunables have moved with their readers
(`MOVED_TUNABLES` in the layout test lists them with their homes); a
reader that stays in the loop goes through the home's attribute
(`harness_session.CONFIRMATION_TTL_SECONDS`), the loop forwards every
moved tunable through `__getattr__` so a read via the alias is live, and
three tripwires hold it: no test patches a moved tunable through the alias,
the forwarded set equals the moved set, and the loop has no bare read of
one. The same rule covers a patched FUNCTION whose readers move
(`_build_anthropic_client`, intercepted by three tests, is forwarded and
patched on `providers/wiring`; `_profile_awareness_block` and `_skills_block`
on `compose`; `_neutralise_untrusted_delimiters`, whose readers all left the
loop, on `fence`, and every reader goes through `harness_fence.`). Still in
`loop.py`: the turn body itself — `run_turn`, `_run_turn_body`, the stream
seam, `_build_user_content`, tool dispatch and the real tool call (which reads
the budget through `harness_budget.<NAME>`).

## Measuring parity

The source capability inventory is generated by
`python pypsa-gui/backend/smoke/assistant_inventory.py --write` from the repository
root. Its `--check` command needs only the Python standard library and runs in
CI on catalogue/toolset/results-tab/panel changes. Regenerate and review the
committed inventory after changing these contracts. It verifies shared navigation
schema parity and source metadata, not behavioral/tool/GUI parameter completion.
The assistant can navigate all current Results tabs and panel targets, including
adequacy/FMEA, workspace/settings/GridSpine/reports/campus electrical. Invalid
navigation IDs/types are refused by the shared handler before a UI event emits.

Official OpenAI requests offer at most 128 tools per turn. Wiring selects from
the eligible registry using explicit names, always-available controls, recent
calls, and query terms, then retains catalogue order for prefix caching. The
exact selected set supplies the dispatch allowlist; `session_init.tool_count`
describes the first request. After a toolset switch or intentional project
rebinding the next request receives the refreshed catalogue and allowlist;
tools issued in the earlier batch are still checked against that batch's offer.
Controls retain priority even if a query explicitly names more than 128 tools.
Anthropic and other compatible endpoints keep their eligible catalogues, with
the same optional provider-neutral toolset views.

GridSpine studies bind a networkless backend project context on activation;
the session pointer survives cold resolution. Tool selection reads that active
context, so the study tools follow the project shown in the browser. A study
needs its registered config; ordinary projects still need their saved network.

The opt-in paid suites and coverage levels are documented in
`docs/superpowers/plans/2026-10-09-openai-comprehensive-tests.md`. Reuse a ledger
within the same test plan to resume completed cases without duplicate spend.
It is a run checkpoint, not a persistent substitute for regression tests after
changing their schemas, prompts, fixtures or relevant implementation.

`backend/smoke/run_chat_smoke.py --workflow --profile <id>` drives the same
battery (start menu, a single-pick and a multi-select Choice card, a
workflow started and ended, one reasoning prompt) on any profile and prints each turn's usage, with the same
meaning on every wire, and whether it showed reasoning;
`docs/superpowers/runbooks/harness-parity-probe.md` has the recipe and the
runs. Run it on every wire you can reach before calling a harness
change done.

## Adding a workflow

Create `workflows/<id>.md`:

```markdown
---
id: <id>                 # equals the file stem
title: <menu label>
intent: <one sentence shown as the chip tooltip and to the model>
when: [unbound | expert | guided]
order: <menu position>
status: active | planned  # planned = validated but never offered
preamble_when: [guided]   # optional: contexts the preamble applies to (default: `when`)
opening_request: <the message the chip sends>
steps:
  - id: <step>
    title: <step title>
    done_when: <checkable completion criterion>
---
<optional preamble: rules that hold for every step>

## Step: <step>
<what the assistant does on this step; name tools in backticks>
```

Rules: tool names go in backticks and component attributes in bold (the
test reads every backticked snake_case word as a tool claim); every step in
the front matter has a `## Step:` section and vice
versa; every backticked tool name in an `active` workflow exists in the
catalogue (the test checks). Write the step for the assistant: what to
call, what to say, what to ask with `ask_user`, and the completion
criterion. Keep the user's confirmation on every write or run.

## Adding a skill

Create `skills/<name>/SKILL.md` with `name` (equals the folder) and
`description` (when to load it) front matter, then the procedure. The
description is what the model sees in its catalogue; it decides when the
skill is loaded, so front-load the trigger. The same layout as
`.claude/skills/`, so a procedure can be copied between the developer
agents and the app.

Two optional flags, both default `true`: `model_invocable: false` keeps a
skill out of the prompt's catalogue and out of `use_skill`;
`user_invocable: false` keeps it out of `GET /api/chat/skills`, the listing
for a future `/skill` command in the panel.

A user can add their own skill to `<app data>/skills/<name>/SKILL.md` (the
same layout) and pick it up with `POST /api/chat/skills/reload` or a
restart. A user skill that is malformed, or named like a package skill, is
refused with a logged reason and shown under `refused` in the listing; it
never breaks the package skills. The listing shows each skill's provenance
(`package` or `user`); the prompt never does. User skills are not part of
`find_capabilities`' tool index.

## What consumes this today, and what will

| Part | Consumed by (today) | Wired by |
|---|---|---|
| `protocol` | `services/chat_service.run_turn`, `services/reports/generator.py`, `providers/*` | — |
| `prompts` | `services/chat_service._build_system_prompt` (the constants are loaded from here) | — |
| `providers` | `chat_service._provider_for_profile`, the connection test in `routers/chat.py` | — |
| `catalogue` | `chat_service._tools_payload`, `chat_tools`, the schema tests, the smoke scripts | — |
| `events` | the tripwire test; `choice_request` is what `ask_user` emits, `workflow_state` what the workflow tools emit | — |
| `workflows` | `GET /api/chat/workflows` (the start menu the panel renders), the `start_workflow` / `advance_workflow` / `end_workflow` tools, `chat_service._workflow_addendum` (the per-turn step), `_guided_mode_addendum` (the `hub-design` preamble) | — |
| `skills` | `chat_service._skills_block` (the catalogue in the tools-on prompt), the `use_skill` tool | — |

## Durable assistant tasks and file delivery

Dictation is independent of the turn loop and chat profiles. Authenticated
`/api/dictation/config` and `/api/dictation/transcribe` accept temporary recorded
audio through ordinary session/CSRF checks; the isolated `providers/transcription`
adapter reads a server-managed credential at call time. The neutral validation
service bounds bytes, language/glossary inputs, concurrency and per-user attempts.
No audio, glossary or project context enters tool dispatch or chat history.
Only the user's reviewed insertion enters the composer, then the normal chat
provider receives it. Browser dictation remains an explicit fallback. See
`docs/superpowers/plans/2026-10-10-multilingual-dictation.md` and the results
assessment for the automated, real-browser and live-API validation boundaries.

`find_capabilities(goal)` searches the shared catalogue and explains required
arguments, safety, project eligibility and reduced-toolset visibility.
Discovery is advisory: dispatch still checks the exact catalogue offered to
that model request. Metadata searches cache the catalogue index; normal tool
selection keeps original catalogue order for prefix reuse.

`start_experiment` persists bounded version-2 plans without executing cases.
`prepare_experiment` checks current static network targets, schema units/ranges,
private candidate validity and authorized baseline evidence, then stores an exact
input preflight digest. Corrupt checkpoints and changed-payload request retries
refuse. Legacy plans remain readable; unsupported domains stay persistence-only.
Preparation is not execution approval: `execution_available` remains false and
Guided write confirmation still applies. See the
[prerequisite contract](../../../.scratch/live-voice/experiment-prerequisites.md).

`start_task(title, steps, request_id)` persists an owner/project-scoped plan
with 1–12 ordinary calls. Create/activate/import a project first: rebinding
and task-control tools cannot be steps. `list_tasks`, `get_task` and
`resume_task` discover and recover plans across chat sessions and providers.
`resume_task` returns the next exact call; it does not execute hidden actions.
The normal dispatch seam reserves and checkpoints matching calls. Completed
steps are retained, and `$earlier_step.path` references resolve their results.
Defaults compare canonically. Each step's arguments are limited to 1800
characters; large results should be referenced through existing file/result
IDs. Task views stay within the ordinary result budget.

A denied confirmation leaves a step pending. An interrupted effectful call
requires review; native exports count as effectful despite their legacy read
tier. `resolve_task_step` requires ordinary destructive confirmation to
record verified completion or an explicit retry. Reconciliation refuses a
still-running originating session or detached timeout worker. `cancel_task`
stops future task steps and retains checkpoints; it never aborts a queued job.
Tasks live in the authorized project directory, are atomically replaced,
and recheck project access/account status/owner on every operation.

`inspect_import` samples CSV/Excel columns, missing values and time axes.
Units and mappings are not guessed. `import_uploaded_network` reads native
network files by upload ID and invokes the existing NC/CSV/Excel/MATPOWER
importers through normal destructive confirmation and project rebinding.
Native binary files are tool-accessible attachments, represented as metadata.

`preview_project_changes` validates typed engineering inputs on an isolated
copy and stores an owner/project-bound preview. `apply_project_changes`
requires normal execution confirmation, unchanged inputs, an idle project,
and the existing foreign-lock/study/undo gates. It commits a validated private
candidate, retains unsaved work, and refuses interrupted application replay.
Previews are limited to a complete review of at most 20 values fitting a
3800-character card; larger changes must be split. Legacy/clipped records need
a new preview. The card renders an old/new/unit table on both surfaces and
disables approval when its review is incomplete. A server-only execution context
binds the reviewed record's digest to the handler. The handler rechecks the
record, source fingerprint and independently reconstructed diff under the
network lock before committing; a consistent replacement checksum cannot
substitute for the approved record. Checksums detect drift, not storage trust
or domain correctness. Other effect families retain their existing cards;
their exact-preview adapters remain separate work. See
`.scratch/chat-companion/issues/06b-bound-confirmations.md`.
Candidate preparation also reads each assigned native scalar back as a Python
value and refuses rounding or overflow before a preview is saved. A finite JSON
integer is not necessarily exactly representable in a native floating-point
field; representable large values remain supported.
Topology operations remain the existing CRUD/batch tools.

`create_chart` renders bounded numeric input/result data as PNG. Result
charts require aligned dispatch and matching solve-input provenance;
parameter edits invalidate the fingerprint even when component axes match.
The solve service records that provenance under the existing solve lock and
netCDF preserves it. Older imported solved files need a new solve before
result charting. Private cloning skips the native solver model without
changing the live model. `build_delivery` bundles selected authorized
artifacts and a content-hash/provenance manifest within the 25 MB limit.
All existing agent exports now include authenticated download metadata.
The frontend renders task progress, ordinary resume/cancel requests and safe
file links from existing `tool_result` frames; no new stream vocabulary or
provider-specific handlers are introduced.

A terminal failed/aborted job leaves its wait step incomplete. Explicit failure
payloads are not counted as successful steps, and partial writes require
review. Verified failed/uncertain steps may be explicitly reconciled through
the normal confirmation gate. Interactive `ask_user` answers are user messages,
so choice requests run outside durable action steps. Preview application
confirmation includes the immutable old/new/unit diff while the actual handler
still receives only its catalogue arguments; chat renders the preview table.

Sequential durable tasks wait for a terminal solver outcome before advancing
chart/export steps, bounded by the per-tool deadline and cancellation. Ordinary
foreground solver calls retain the detached start receipt. A start or
contradictory still-running receipt cannot complete a task; fully asynchronous
durable-task step resumption remains outside the checked-scope recovery tranche.

## Recorded runs, load flow, and experiment plans

These tools live in `services/run_evidence.py`, `services/loadflow_summary.py`,
`services/experiments.py`, and `services/durable_conversation.py`. Each is a
catalogue entry dispatched from `services/chat_tools.py`. Each name except
`get_loadflow_summary` is in `toolsets.CONTROL_TOOLS`, so a reduced toolset
still offers it; `get_loadflow_summary` reaches a reduced toolset only through
the `results` domain (keyword `loadflow`). They require
an activated saved project (`run_project_required` or
`experiment_project_required` otherwise). The model supplies ids and typed
fields. It does not supply filesystem paths, and it does not supply the
numbers these tools publish.

### Run evidence

One run is the directory `<project>/run_evidence/<run_id>/`. Section artifacts
are written first, under `sections/<name>.json`, at most 16 KiB each. The
terminal `manifest.json` is written last, atomically, and is not rewritten.
Its presence is what makes the run terminal. A crash before that write leaves
at most a `running.json` marker and orphan sections. Each manifest section
entry stores a SHA-256 and a byte length. A later read re-checks both. A
mismatch, a missing file, an unknown section, or a still-running run returns
`evidence_unavailable` for **that** `run_id`, with a reason
(`artifact_missing`, `artifact_corrupt`, `section_not_recorded`,
`run_not_finished`). The answer stays on the requested id. The newest run and
the live network stay out of it.

Retention keeps section artifacts for the 20 newest manifests and manifest
records for 200 runs. Older section directories are removed; a later read of
a pruned section is `evidence_unavailable`.

Publishers are best-effort. An unsaved scratch network produces no record, and
a recording failure does not fail the solve.

| Source | When a record opens | Run id |
|---|---|---|
| Foreground `run_simulation` / `run_ac_pf_stage` | `start_foreground_run`, then `publish_foreground_run` | `fg-<job hex>` |
| Solve queue / GridSpine | `start_queue_run` on claim, `publish_queue_run` on terminal finish | `q-<job id, dashes removed>` |
| Adequacy / study mesh | `watch_study_run` from `routers/results.py` `_publish_study` | `ad-<study key>-<nonce>` |
| Report jobs | `start_report_run` when the report job starts running, `publish_report_run` on terminal finish | `rpt-<report id>` |

The study watcher polls for up to six hours. If the study is still running at
the deadline, the running marker stays and no terminal status is invented.

Statuses are `running`, `completed`, `failed`, and `interrupted`.
`list_project_runs` pages newest first (default 10, maximum 50) and accepts
an optional status filter. `get_run_evidence` returns warnings, the error,
software-computed metrics as `{value, unit}`, and each section's
availability. Pass `section` (lowercase snake_case, from that list) for the
stored JSON. An unknown id is `run_not_found`.

`compare_project_runs` computes `delta = run − baseline` from recorded
metrics only. Both runs must be `completed`, share a mode, and share an
input fingerprint. `allow_input_changes=true` still compares when the
fingerprints differ and sets `inputs_differ=true`. Any other mismatch
returns `comparable=false`, an empty `deltas` object, and
`incomparable_reasons` (`run_not_completed`, `mode_mismatch`,
`mode_unknown`, `fingerprint_mismatch`, `fingerprint_unknown`). A metric
missing on one side, or stored in different units, is listed in
`unmatched_metrics` and left unestimated. `delta_pct` is null when the
baseline value is zero. The two ids must differ. Optional `metrics` accepts
1–20 recorded names. Comparing a run whose section artifacts fail
verification returns `evidence_unavailable` for that run.

### Discussed run

`discuss_project_run` binds the chat session's `discussed_refs` after
`get_run_evidence` succeeds for `run_id` (and for `baseline_run_id` when
that argument is set). The binding stores project, run, status, mode,
fingerprint, and whether evidence is still available. Optional `results_tab`
is stored as `source`. Optional `section` is stored as given.

`open_ui` defaults to true and emits a `ui_event` with `kind=open_run_result`
so the Results chrome selects that run and the optional tab. The event
carries identifiers. It leaves the live network's inputs where they are.
`open_ui=false` returns `{ok: true, discussed, ui_navigated: false}`.

When the episode has a durable parent thread, the same identifiers are
patched onto `refs.discussed` with generation compare-and-swap
(`durable_conversation.set_discussed_run`). That patch does not bump the
thread generation. Omitting `baseline_run_id` clears a baseline previously
stored on the thread.

`resume_episode` drops the previous episode and its approvals, mints a new
session, and copies `refs.discussed` onto that session's `discussed_refs`.
When the run is still readable under the current project ACL, status, mode,
fingerprint, and `evidence_available` are refreshed from `get_run_evidence`.
When that read fails, the durable identifiers alone are restored. Export and
later turns keep that run without another `discuss_project_run`.

`export_run_evidence` writes one run as a JSON agent-export chip. Omit
`run_id` to export the discussed run. With no `run_id` and no discussed run
the error is `discussed_run_required`. Download the chip with
`get_file_delivery`.

`discuss_project_run` and `export_run_evidence` are write-tier. Expert mode
leaves write-tier tools uncarded. Guided mode cards every write, and
`AUTO_APPROVE_TIERS` never includes `write`.

### Current load flow

`get_loadflow_summary` ranks lines and transformers on the current solve. It
uses the same source selection, dispatch-freshness gate, rating (`s_nom_opt`
for an extendable branch, otherwise `s_nom`), and snapshot weights as the
Load flow tab. The default list is 5 branches, the maximum is 25, and the
payload is trimmed to stay under 3800 characters.

The native `GET /api/results/loadflow-view` uses this same oracle for branch
tables, loading chart points and CSV. It requires recorded exact solve-input
provenance; input drift or missing provenance requires a new solve. Both ordinary
chat summaries and native projections refuse that drift. The UI projection caps
rows and unavailable entries at 500 and chart series at ten branches × the first
800 selected snapshots, with visible truncation; full-window statistics are not
downsampled. Unknown ratings and unaligned AC convergence are unavailable.

`GET /api/results/run-view` reads owned retained evidence and an optional owned
baseline reference. Missing/corrupt sections remain unavailable; no live fallback,
input restore, arbitrary historical slicing or implicit comparison is performed.
The Results chrome shows the source; per-turn UI context carries only source
identifiers/window positions, fenced as untrusted hints. Tools re-read evidence.
The source-bound current walkthrough adapter covers branch loading in Expert;
historical walks use available sections and explicit Next. Other current tabs and
historical harness completion ACKs need their own source adapters. Guided tours
report that gap without switching modes. See the
[CP-03 implementation boundary](../../../.scratch/chat-companion/issues/03-source-navigation.md).

Peak utilisation is the larger terminal flow divided by `rating * s_max_pu`.
`source=lopf` (the default) is an active-power proxy: the larger of `|p0|`
and `|p1|`. `source=ac_pf` uses apparent power where reactive flows exist
and excludes unconverged snapshots. `claims.full_ac_thermal_compliance` is
false: the summary has no N-1 check and no voltage limits. An unsolved,
stale, running, infeasible, or failed solve returns `status: unavailable`
with a typed `unavailable_kind`. Branches with a missing or zero rating, or
with no flow data, appear in `skipped` and stay out of the ranking.

A `run_id` argument reads a stored `loadflow` section when that artifact
exists. With no such section the result is
`unavailable_kind=historical_loadflow_unavailable`. Omit `run_id` for the
live results. `window_from` and `window_to` are snapshot indexes;
`window_from` must not exceed `window_to`.

### Current energy flows

`get_energy_flows` (`services/energy_flows_read.py`) reads the current solve's
Energy flow tab through the same `services/results/energy_flows.py` payload
the tab draws: Source → Via → Destination MWh, the largest rows first (default
12, maximum 40, trimmed under 3800 characters), the conservation check and the
window. It computes nothing of its own. `not_delivered` carries
`unserved_product_mwh` (process shortfall per load) and `unserved_mwh` (shed
load) as energy NOT delivered; neither is ever a row or a source. Before a
solve it is `unavailable_kind=not_solved`; an attribution the module cannot
prove to conserve energy is `not_established`, with no rows. Current results
only; `inputs_changed_since_solve` says when the inputs moved after the solve.
The `flows` result view (`services/result_views.py`) goes live with this tool.

### Current load shape

`get_load_shape` (`services/load_shape_read.py`) reads the current solve's Load
shape tab through the same `services/results/load_shape.py` payload the tab
draws, one view per call (`summary`, `heatmap`, `week`, `monthly`, `annual`),
trimmed under 3800 characters; `period`, `window_from` / `window_to` (the
heatmaps), `week` and `series` pick what is returned. It computes nothing of
its own. A group the network does not model, or the run cannot establish,
keeps its `not_modelled` / `unavailable` state and reason, never a missing key
that reads as zero. Before a solve it is `unavailable_kind=not_solved`; a run
whose dispatch cannot be read as energy is `not_established`. Current results
only. The `loadshape` result view (`services/result_views.py`) goes live with
this tool.

### Experiment plans

`start_experiment` writes `<project>/experiments/<uuid>.json` plus
`experiments/index.json`. The returned view has `status: "planned"`,
`executed: false`, `execution_available: false`, empty `job_ids`, and no case-execution receipts. The call
does not queue a solve or apply a change. A later slice is what advances a
case, through ordinary harness tools and approvals.

- `domain` is one of `network`, `commercial`, `reliability`, `gridspine`.
- One to eight cases. Each `id` matches `^[A-Za-z][A-Za-z0-9_-]{0,31}$` and is unique inside the plan. An optional `label` is 1–120 characters.
- Each case has 1–12 changes: `{path, value, value_type}` and an optional `unit` of 1–24 characters.
- `value_type` is `number` (finite, and not a boolean), `boolean`, `string`, or `enum`. String and enum values are 1–120 characters.
- `path` starts with an identifier of at most 32 characters, then one to six dotted segments.
- `title` is 1–120 characters. `request_id` is 1–64 characters. The same owner/project/request returns the existing canonical plan; changed payloads return `experiment_request_conflict`.
- `baseline_run_id`, when set, is stored as a string of 1–64 characters. This call does not look the run up.
- `limits` contains bounded `max_cases`, `max_tool_attempts`, `max_elapsed_seconds`, `max_cost_usd` and `failure_policy`; omitted fields receive explicit defaults. Unknown fields and invalid caps refuse. These are caps, not permission to run cases or incur charges; see the prerequisite contract above.
- `list_experiments` (`limit` 1–20) returns the newest owned plans, without case bodies, and the complete owned count. The project store caps at 100 records without discarding old plans. `get_experiment` returns cases, limits, `job_ids` and preflight state. Bad ids/missing or foreign records return `experiment_not_found`; corrupt/uncertain checkpoints or inconsistent indexes return `experiment_checkpoint_unreadable` rather than creating replacements.

```json
{
  "title": "Battery sensitivity",
  "request_id": "req-1",
  "domain": "network",
  "baseline_run_id": "run-base",
  "cases": [
    {
      "id": "case-hi",
      "label": "Higher battery cap",
      "changes": [
        {"path": "Generator.battery.p_nom", "value": 7.5, "value_type": "number", "unit": "MW"}
      ]
    }
  ]
}
```

`prepare_experiment` validates supported network prerequisites on private copies,
stores exact current/candidate input digests and warning counts, and leaves
execution unavailable. Legacy reads remain compatible; successful explicit
preflight upgrades compatible records. Old ambiguous caps must be replanned.

`start_experiment` and `prepare_experiment` are write-tier, with the same Guided confirmation rule as
`discuss_project_run`. `list_experiments` and `get_experiment` are read-tier.

New single-case submissions also carry a native fence and bounded submission
checkpoints. `reconcile_experiment` is a destructive-tier, exact-receipt-bound
metadata control: current owner/project access precedes edit-lock checks and
ordinary confirmation is mandatory. Software derives the outcome by reading
the matching native child/job/input/evidence records. A native child receipt
binds the original job/attempt; a later same-input job cannot substitute.
No-effect settlement requires a positive handler-close checkpoint before the
first effect; a free lease alone cannot prove it. Legacy/missing/corrupt proof,
lost queue receipts and source drift stay unresolved. The original reservation
and caps remain; no retry, solve, abort, activation or deletion is executed.
This does not add a checked-answer profile or a two-case adapter. See
`.scratch/chat-companion/issues/05a-uncertain-case-reconciliation.md` and the
single-case contract for the native proof and remaining-scope boundaries.

`compare_experiments` reads two distinct owned single-network-case plans in the
active baseline project without executing or refreshing their receipts. Each
current result must pass the existing exact saved/job/run oracle. Deltas require
intact referenced baseline evidence matching the frozen preflight digest and
the same prepared baseline, full horizon, snapshot/investment weights, solver
configuration, mode and objective units. Declared candidate changes are included;
failed/unavailable sources retain their own state without a fabricated delta.
The sole metric is the recorded solver objective, not certified full cost.
`export_experiment_comparison` re-reads sources and requires the inspected report
SHA256, then uses the ordinary write/lock/authenticated-download contract. An old
export is a historical report; it does not certify current evidence. No strict
profile expansion, shared two-case advancement or stop/continue scheduling is
added. See `.scratch/chat-companion/issues/05b1-case-comparison.md`.


Two prepared network cases can now share one explicitly armed plan. The version-2
parent execution ledger binds two version-1 case receipts without migrating old
single-case ledgers. Each next_call is still one separately admitted ordinary
sweep or bounded wait, never an internal execution loop. Combined attempts and
one start time/cancellation cover both cases and survive recreated chats.
A verified terminal job failure follows the frozen stop/continue policy;
uncertain, partial or unavailable effects block both policies. Explicit fenced
case reconciliation can establish matching native receipts or positive no-effects
proof, but never resets a reservation or authorizes retry. Native submissions
share one parent lease; separate case proof files preserve their exact origins.

For two-case plans, reconcile_experiment requires the inspected case_id, and
compare_experiments/export_experiment_comparison require explicit case_id and
baseline_case_id selectors for their respective two-case plans. Two distinct
cases of the same plan are allowed. The existing exact source/basis/digest export
checks still apply. No strict checked-profile expansion or broader domain adapter
is introduced. See `.scratch/chat-companion/issues/05b2-shared-two-case.md`.
