# 05 — `use_skill` tool, the skill catalogue block, and the `grill` skill

Status: ready-for-agent (done 2026-10-05: `use_skill`, `_skills_block`, `unknown_skill`)
Type: task
Blocked by: 04

Catalogue: `use_skill(name)` (`Safety: read`, `_service_call_`) returns the
skill body from `harness.skills.registry`; unknown names return
`error_kind='unknown_skill'` (classify it `inline` in
`tool-error-kinds.json`). System prompt: a tools-on-only block listing
`name — description` for every skill, built like `_profile_awareness_block`
(stable within a turn). The `grill` skill (already written in 01) runs
rounds of numbered questions through `ask_user`.

Done when: a scripted turn that calls `use_skill('grill')` receives the
body; the prompt block is absent on tools-off profiles; the pinned hashes
are unchanged (the block is appended, not inserted into a pinned constant).
