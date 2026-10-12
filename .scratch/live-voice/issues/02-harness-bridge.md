# Shared harness delegation bridge

Status: claimed
Type: task
Blocked by: 08, 09 (integrated acceptance; bridge extraction can start earlier)

Implement steps 1 and 4's shared coordinator path in
[the plan](../../../docs/superpowers/plans/2026-10-10-live-voice-conversation.md).
Start from typed requests independently of media. Use the shared admission/turn
pipeline, stable speech context, single turn
lease, bounded queue and generation-aware result projection. No raw browser
tool dispatch or duplicate authorization implementation. Preserve existing cards
and closed SSE events. Cover Claude/OpenAI parity, complete eligible-tool
reachability and interrupted corrections. One model plans; speech synthesis reads
its answer. Carry exact project/run/source/window/asset references for follow-ups.

Done when ordinary harness gates and transport-equivalence fixtures pass without
duplicate actions. See [spec](../spec.md).
Integrate issue 08's full inventory/discovery and issue 09's durable owner/thread
contract. The pending queue handles overlapping utterances; issue 11 must release
long-job turns and provide independent observation before workflow acceptance.

## Comments

2026-10-11: C-U1 unit slice — `services/utterance_admission.py` admits a stable
utterance once, holds one turn lease per session, queues at most one pending
correction (latest wins), rejects blank/stale-generation input, and marks
obsolete lease+generation projections. No `run_turn`/SSE/media wiring yet.
Tests: `tests/test_utterance_admission_c_u1.py` (8 passed). Next: wrap stream
admission so only admitted leases start turns; wire Live busy path to queue
instead of dropping; sync generation with issue 09 rebind.

2026-10-11: C-I1 stream gate — optional `StreamRequest.utterance_id` +
`generation` (both required together) admit via the coordinator inside
`run_turn` before the `#19` in-flight guard. Non-admitted statuses yield
existing `error` + `session_done` frames (`duplicate` / `queued` /
`stale_generation` / `rejected_empty` classified inline in
`tool-error-kinds.json`). Lease release uses `promote=False` so pending is
not left hanging without a turn. Omitting fields preserves prior behaviour.
Tests: `tests/test_utterance_admission_stream_c_i1.py` (6 passed). Still open:
auto-drain pending after release; Live busy path; generation sync with issue 09.

2026-10-11: Live busy→queue — Companion Live queues one pending correction via
`sendRequest({ source: 'live', openDock: false })` instead of dropping; latest
wins; console stays closed. Project mismatch still refuses. Frontend tests in
`AssistantDock.test.tsx`. Still open: wire Live stream with utterance_id/
generation; backend auto-drain after promote.

2026-10-11: Live stream IDs — idle `companion:live-utterance` and queued
`source: 'live'` drains send optional `utterance_id` + `generation` on
`ChatStreamRequest` (store `utteranceGeneration`, default 1). Typed and
hub-card sends omit both. Tests: `ChatPanel.liveUtteranceAdmission.test.tsx`
(4 passed). Still open: backend auto-drain after promote; frontend sync of
generation from durable rebind.

2026-10-11: Durable rebind → Live generation — `api/durableConversations.ts`
create/get/rebind/resume call `applyDurableThreadGeneration`, which sets
`durableThreadId` and `utteranceGeneration` from the thread response. Tests:
`durableConversations.test.ts`. Still open: backend auto-drain after promote;
UI entry that creates/rebinds a durable thread from Live.

2026-10-11: Backend auto-drain — after an admitted turn ends, `release_lease`
promotes the pending correction and `run_turn` continues on the same stream
with `_held_lease` (no re-admit of a seen id). Tests:
`test_utterance_admission_stream_c_i1.py::test_queued_pending_auto_drains_after_turn`.
Still open: Live UI durable create/rebind entry.

2026-10-11: Live durable bind — opening Companion Live calls
`ensureLiveDurableThread()` (create if unbound, else rebind). Generation sync
reuses `applyDurableThreadGeneration`. Tests: `durableConversations.test.ts`,
`AssistantDock.test.tsx`. Still open: LiveKit credentials/deployment smoke.
