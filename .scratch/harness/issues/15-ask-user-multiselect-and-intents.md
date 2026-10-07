# 15 — `ask_user`: multi-select, a detail body, and presentation intents

Status: ready-for-agent (done 2026-10-07: multi_select, detail, intent; W5 in the parity battery)
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

## Comments

2026-10-07 (done). One change from the spec above: a `plan_review` card
takes the options the model gives (the tool description suggests Approve and
Revise) instead of forcing exactly two; what the validator enforces is that
it has a detail and is single-pick, since a review is one verdict. The model
also receives `multi_select` and an `answer_format` in the `presented`
result, so it knows a reply may be several labels joined by "; ". The detail
keeps its line breaks (it is Markdown) and is capped at 2,000 characters;
the panel renders it with the chat's Markdown renderer in a scrolling box.
The frame handler normalises an older or malformed frame to a plain
single-pick card. Tests: 10 backend (red first), 5 card and 2 frame-handler
tests in the panel, a stub branch and a battery prompt (W5) with its check.
Parity: stub 5/5, live Anthropic 5/5. Gates: the full chat regression 3834
passed, 0 failed; the frontend 291 files / 3372 tests, tsc clean.
