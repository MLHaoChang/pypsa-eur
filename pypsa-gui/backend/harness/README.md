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
  providers/      anthropic, openai_compat, fake — the only place a wire is named
  loop ...        (phase 3) session, confirmation, budget, history, loop, sse
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
7. **Old paths still work.** `services.llm_provider`,
   `services.chat_tools_schema`, `services.llm_anthropic`,
   `services.llm_openai_compat` and `services.llm_fake` are `sys.modules`
   aliases of the harness modules: same objects, same monkeypatches. New
   code imports from `harness.*`.

## The harness tools

Five read-tier tools exist only for the harness (catalogue banner "Harness"):
`ask_user` (a Choice card; the pick is the next user message, the turn does
not block), `use_skill` (a skill's body on demand), `start_workflow`,
`advance_workflow`, `end_workflow` (the session's `{id, step}`; the current
step's body rides each turn as per-turn user content, after the context
block and outside the untrusted fence). None of them touches a project.

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
