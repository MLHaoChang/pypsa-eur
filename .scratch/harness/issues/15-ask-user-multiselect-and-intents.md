# 15 — `ask_user`: multi-select, a detail body, and presentation intents

Status: ready-for-agent
Type: task
Blocked by: 04 (done)

Source: assessment design 5; DeepSeek Harness `docs/subsystems/user-questions.md`
(single or multi-select, free text, presentation intents that "change
presentation only, never the protocol").

## What we have

`ask_user(title, question, options[{label, description, recommended?}],
allow_free_text)` in `services/chat_tools.py`; frame `choice_request` with
payload `(tool_use_id, title, question, options, allow_free_text)` in
`harness/events.py`; `ChoiceCard` in `ChatPanel.tsx`; non-blocking by owner
decision Q4 (the pick is the next user message). Timed questions with a
"pending" return do not apply: our turn ends at the card.

## What changes

1. Catalogue (`harness/catalogue.py`): optional `multi_select: boolean`
   (default false), `detail: string` (Markdown shown under the question,
   capped at 2,000 chars, rendered through the panel's existing Markdown
   path), `intent: "choice" | "plan_review"` (default `choice`).
2. `services/chat_tools.py`: validation for the three fields
   (`invalid_tool_args` as today); `plan_review` requires `detail` and
   exactly two options (approve, revise) unless given.
3. `harness/events.py`: the `choice_request` tuple gains `multi_select`,
   `detail`, `intent`; the AST tripwire and `ChatPanel.manifest.test.tsx` learn them.
4. `ChatPanel.tsx`: multi-select renders checkboxes and one Send button; the
   message sent is the chosen labels joined with "; " under the card title
   (same `sendRequest` path as today). `plan_review` renders `detail` as a
   document with the two buttons. Free text stays as today.
5. Stub endpoint (`smoke/stub_openai_endpoint.py`) and the parity battery
   (`smoke/run_chat_smoke.py --workflow`) gain a multi-select prompt (W1b)
   and the `check_start_menu`-style payload check.

## Done when

FakeProvider scripts for each intent yield `tool_request → tool_running →
choice_request → tool_result → turn_done`; vitest covers the three card
shapes; the parity battery passes 4/4 on the stub and live Anthropic wires;
`tool-error-kinds.json` unchanged.
