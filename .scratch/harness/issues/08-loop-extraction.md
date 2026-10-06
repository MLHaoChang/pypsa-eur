# 08 — The turn loop, session, confirmation and persistence move into the harness

Status: ready-for-agent (in progress 2026-10-05: the loop moved whole to `harness/loop.py`; `sse`, `fence`, `results`, `history`, `metrics`, `ratelimit`, `session`, `confirm`, `providers/wiring`, `stub`, `budget`, `compose`, `solver_bridge` extracted under the README's splitting rule; the turn body remains)
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

2026-10-05 (gate for step 3, 5180c4c): 2912 passed, 5 skipped, 0 failed.

2026-10-06 (step 4): the provider wiring (`llm_config_module`,
`_resolve_turn_profile`, the portability filter, `_anthropic_client_for_profile`,
`_provider_for_profile`, the tools payload) moved to `harness/providers/wiring.py`
— where a wire may be named — and the scripted stub loop to `harness/stub.py`.
`_build_anthropic_client` (a function three tests intercept) is forwarded and
patched on wiring; the loop's vendor-word pin came down accordingly.
loop.py: 3,342 → 2,872 lines.

2026-10-06 (step 5): the budget and retry tunables, the per-tool timeout and
`_turn_budget_block` moved to `harness/budget.py`; the turn body, the stream
seam and the dispatch read them as `harness_budget.<NAME>` (rewritten by AST
position), ten more names are forwarded, 33 test sites repointed across
seven files. Gate: the full chat regression, 3407 passed, 5 skipped, 1 failed
— a test patching `chat_service.os.fsync` once the loop's unused `import os`
went; repointed to `harness_history.os`, pushed as b5564df.

2026-10-06 (step 6): the prompt assembly and ui-context formatting (the
bound prompt fragments, `_build_system_prompt`, the profile and skill blocks,
`_format_live_network_meta`, `_sanitise_ui_value`, `_format_ui_context`, the
Guided and workflow-step addenda, `_workflow_state_payload`) moved to
`harness/compose.py` as one contiguous range (the `_p = …` reassignments
defeat the node extractor). `_profile_awareness_block` and `_skills_block`
(patched by the pre-P25 snapshot test) are forwarded and patched on compose;
`_neutralise_untrusted_delimiters` (patched by the fence-integrity suite) is
forwarded to fence now that its last loop reader moved, and compose, results
and the loop read it as `harness_fence.<name>`. loop.py: 2,872 → 2,288 lines.
Gate: the full chat regression, 3408 passed, 5 skipped, 0 failed.

2026-10-06 (step 7): `_classify_solver_line` and `solver_log_bridge` moved to
`harness/solver_bridge.py` by the node extractor; nothing patches them, so
nothing is forwarded. loop.py: 2,288 → 2,224 lines. What remains is the turn
body itself.
