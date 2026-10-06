# The chat harness

Everything the in-app assistant needs that is **not** a language model, in
one folder. A provider (Claude, an OpenAI-compatible model, Kimi, Qwen, a
local endpoint) plugs in underneath; the harness is what makes the
assistant behave the same whichever one is active.

```
harness/
  protocol.py     the provider seam: LLMProvider, LLMRequest, LLMEvent, ERROR_KINDS
  catalogue.py    the 182 tool declarations, their Safety tiers, TOOL_ROUTES
  events.py       the closed vocabulary of frames the turn loop yields to the UI
  workflows/      the start menu and the step-by-step flows (Markdown + front matter)
  skills/         procedures the model loads on demand (<name>/SKILL.md)
  prompts/        the system-prompt fragments as Markdown, byte-identical to the old constants
  providers/      anthropic, openai_compat, fake, and wiring (profile → provider, tools payload, history portability) — the only place a wire is named
  loop.py         the turn loop: the stream seam, tool dispatch, the solver bridge (the former services/chat_service.py)
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

Spec: `.scratch/harness/spec.md`. Plan:
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

Five read-tier tools exist only for the harness (catalogue banner "Harness"):
`ask_user` (a Choice card; the pick is the next user message, the turn does
not block), `use_skill` (a skill's body on demand), `start_workflow`,
`advance_workflow`, `end_workflow` (the session's `{id, step}`; the current
step's body rides each turn as per-turn user content, after the context
block and outside the untrusted fence). None of them touches a project.

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
`session`, `confirm`, `providers/wiring`, `stub`, `budget`, `compose`. Nine tunables have moved with their readers
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
`loop.py`: the solver bridge and the turn body itself (which reads the budget
through `harness_budget.<NAME>`).

## Measuring parity

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
