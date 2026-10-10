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
  workflows/      the start menu and the step-by-step flows (Markdown + front matter)
  skills/         procedures the model loads on demand (<name>/SKILL.md)
  prompts/        the system-prompt fragments as Markdown, byte-identical to the old constants
  providers/      anthropic, openai_compat, openai_responses, fake, and wiring (profile → provider, tools payload, history portability) — the only place a wire is named
  loop.py         the turn loop: the stream seam and tool dispatch (the former services/chat_service.py)
  solver_bridge.py  the solver log → tool_progress bridge for a running run_simulation / run_ac_pf_stage call
  compose.py      the system prompt for a turn, the live network line, and the per-turn ui-context / Guided / workflow-step blocks
  budget.py       the per-turn / per-session / daily caps, the retry schedule, the per-tool timeout, the budget gate
  stub.py         the scripted stub loop behind StreamRequest.script
  session.py      ChatSession, PendingConfirmation, the session registry with its TTL and cap
  confirm.py      the confirmation gate: tiers, the Guided write rule, auto-approve, parallel-destructive
  ratelimit.py    the /stream token bucket
  sse.py          the SSE framing of a yielded (event, payload)
  fence.py        the untrusted-content delimiters and the neutraliser
  results.py      tool-result shaping: coercion, truncation, the per-turn result budget, error content
  history.py      the in-memory trim and turn summary, chat.jsonl persistence, the pending-turn WAL, lineage
  metrics.py      the process-wide chat metrics behind GET /api/chat/metrics
```

Borrowed designs under consideration: `docs/superpowers/assessments/2026-10-06-deepseek-harness-adoption-assessment.md` (issues 13–20). Spec: `.scratch/harness/spec.md`. Plan:
`docs/superpowers/plans/2026-10-05-chat-harness.md`. Vocabulary:
`pypsa-gui/CONTEXT.md` (Harness, Workflow, Skill, Choice card, Start menu).

## The contract

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
   a project directory or an upload — because they are instructions.
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

## The harness tools

Read-tier tools exist only for the harness (catalogue banner "Harness"):
`ask_user` (a Choice card; the pick is the next user message, the turn does
not block), `use_skill` (a skill's body on demand), `start_workflow`,
`advance_workflow`, `end_workflow` (the session's `{id, step}`; the current
step's body rides each turn as per-turn user content, after the context
block and outside the untrusted fence). `use_toolset` changes the session's
catalogue view for the next model request, including within the same turn.
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
battery (start menu, one Choice card, a workflow started and ended) on any
profile; `docs/superpowers/runbooks/harness-parity-probe.md` has the recipe
and the runs. Run it on every wire you can reach before calling a harness
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
