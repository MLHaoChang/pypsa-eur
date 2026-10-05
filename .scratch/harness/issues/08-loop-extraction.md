# 08 — The turn loop, session, confirmation and persistence move into the harness

Status: needs-triage
Type: task
Blocked by: 02, 06, 07

Continuation of `docs/superpowers/plans/2026-09-09-chat-turn-loop-decomposition.md`
(Phase E's result-shaping join is still open). Target modules:
`harness/session.py` (ChatSession, registry, eviction), `harness/confirm.py`
(PendingConfirmation, `_confirm_destructive_tool`, tiers),
`harness/budget.py` (caps, rate limit, daily spend), `harness/history.py`
(chat.jsonl, WAL, rotation, lineage, trim/summary), `harness/loop.py`
(`run_turn`, `_run_turn_body`, stream and dispatch seams),
`harness/sse.py` (`sse_frame`). `services/chat_service.py` becomes the shim.
AST-driven moves only; the recorded frame-sequence gate from the
decomposition plan is the contract.

Done when: the frame recording is unchanged for the scripted set;
`test_tool_error_kind_manifest.py` scans the new module paths; the
vision sub-call's direct SDK use is either moved behind the protocol or
recorded as a finding.
