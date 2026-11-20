<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Answering a checked-answer clarification card is refused in the real UI

Status: done
Type: bug
Found by: S3 screenshots (issue 10), 2026-11-17, against master's ChatPanel.

## Problem

`frontend/src/components/ChatPanel.tsx:2766` (`const generation = ++streamGenerationRef.current`) increases `binding_generation` on every send. `harness/assurance_conversation._state` requires the follow-up turn's binding `(owner, project_id, binding_generation)` to equal the clarification turn's. Otherwise it drops the context, and the answer reads `conversation_scope_changed`: "I couldn't verify that answer…".

So in the browser, every pending card answer fails:
- cost quantity
- explicit cost basis
- capacity meaning
- S3's objective-or-cost card

The backend tests always send the same `binding_generation` (3), so none of them catches it. The S3 shots passed only because the driver kept every request on the first generation.

## Acceptance

- One end-to-end test that sends a card answer with the generation the real client sends, and gets the checked answer.
- Fix on one side, chosen in triage:
  - the client keeps the generation for a same-project follow-up, or
  - the server compares only what the generation guards: a project or binding change, not a new send.
- `binding_generation`'s other uses (`ui-ack`, stale-stream guards at ChatPanel 2553 and 2769) keep their meaning.

## Triage

Fixed on the server side, option (b). The client's `binding_generation` is a per-stream epoch: it goes up on every send, error, abort, new chat and project switch. `isCurrentStream`, the ui-ack guard and `navigation.py` rely on it, so the client keeps it as it is. `assurance_conversation` now compares only the owner and project id with the turn that set the context. A project switch changes the project id, a new chat starts a new server session, and the anchor check still refuses an answer once any other turn has run in between. Test: `pypsa-gui/backend/tests/test_aa20_card_answer_generation.py`.
