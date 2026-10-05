# 04 — `ask_user` tool and the Choice card

Status: ready-for-agent
Type: task
Blocked by: 01

Catalogue: `ask_user(title, question, options[{label, description,
recommended?}], allow_free_text=true)`, `Safety: read`, route sentinel
`_ui_event_`. Dispatcher returns `{"_ui_event": True, "kind": "choice",
...}`; the loop's `_ui_event` path emits a new frame `choice_request`
(`harness.events`). Tool description tells the model to end the turn after
presenting and to wait for the user's pick. Frontend: a `ChoiceCard`
component next to the confirmation card; a click sends the option label
through `sendRequest` with the card title as `label`; a free-text field is
present when allowed. `tool-error-kinds.json` is unchanged (no new error
kind); `ChatPanel.manifest.test.tsx` learns the frame.

Done when: a `FakeProvider` script that calls `ask_user` yields
`tool_request → tool_running → choice_request → tool_result → turn_done`;
the card renders and sends; the smoke's stub model has a scripted branch
that calls it.
