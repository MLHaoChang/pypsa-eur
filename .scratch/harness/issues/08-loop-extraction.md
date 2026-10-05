# 08 — The turn loop, session, confirmation and persistence move into the harness

Status: ready-for-agent (in progress 2026-10-05: the loop moved whole to `harness/loop.py`; `sse`, `fence`, `results`, `history`, `metrics`, `ratelimit`, `session`, `confirm` extracted under the README's splitting rule; budget gates, the stub loop, provider wiring, prompt assembly and the turn body remain)
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

## Comments

2026-10-05: Step 1 done — `git mv services/chat_service.py harness/loop.py`,
`services.chat_service` is a `sys.modules` alias, so every one of the 27
patched names (inventory in `harness/README.md`, "Splitting the loop")
and every lazy import keeps working; the path-reading tests (error-kind
manifest, hourly audit, layout) point at the new file; the loop's twelve
provider-word code sites are pinned in the layout test so they can only
move into providers/. Step 2 begun with `harness/sse.py` (`sse_frame`,
AST-selected). Remaining extractions, each a patch-surface move per the
README rule: result shaping, trim/summary, persistence + WAL, sessions,
confirmation, budget gates, the turn body.

2026-10-05 (gate for step 1, 773f009): 2908 passed, 5 skipped, 0 failed across the chat/provider/tool/Guided/harness/report files.

2026-10-05 (step 2): `fence.py`, `results.py`, `history.py`, `metrics.py`
extracted by AST selection of whole top-level nodes (the loop re-imports
every name; identity holds). Three tunables moved with their readers —
`MAX_TOOL_RESULT_CHARS_PER_TURN` → results, `SESSION_MESSAGES_MAX` and
`ROTATE_BYTES` → history — and the seven test sites that patched them
through `chat_service` now patch the new module; a tripwire in
`test_harness_layout.py` (`MOVED_TUNABLES`) keeps it that way. loop.py:
5,315 → ~4,040 lines.

2026-10-05 (gate for step 2, 5439066): 2910 passed, 5 skipped, 0 failed.

2026-10-05 (step 3): `ratelimit.py`, `session.py`, `confirm.py` extracted
(and the history sanitisers joined `history.py`). Six more tunables moved
with their readers; the loop now FORWARDS every moved tunable (PEP 562
`__getattr__`) instead of re-importing a copy, so `chat_service.<tunable>`
reads the home's live value; the one loop reader left
(`_dispatch_stub_call`'s ttl) reads `harness_session.CONFIRMATION_TTL_SECONDS`.
36 test sites repointed across confirmation_gate_seam, e2e, profile_binding,
sse and guided_write_confirmation. loop.py: 4,040 → 3,342 lines.
