# 14 — User-authored skills: discovery roots and invocation policy

Status: ready-for-agent (Q16 decided 2026-10-06: the app-data root only; D12 to be amended in the same change)
Type: task
Blocked by: 05 (done)

Source: assessment design 6; DeepSeek Harness `docs/subsystems/skills.md`
(five discovery roots ranked by priority, `skill()` tool, catalogue of name
and description only, per-skill `modelInvocable` / `userInvocable`).

## What we have

`harness/skills/registry.py` loads `harness/skills/*/SKILL.md` from the
package only (D12), caches the registry, and `catalogue_block()` puts one
line per skill into the tools-on system prompt; `use_skill` returns the body.

## What changes

1. A second root under the app data directory (`<PYPSAGUI_APP_DATA_DIR>/skills/<name>/SKILL.md`),
   never a project folder or bundle: a project can arrive from someone else
   and a skill is instructions the model obeys. Package skills rank first; a
   user skill with the same name is refused at load (the loader test and a
   startup warning), not shadowed.
2. Front matter gains `model_invocable` and `user_invocable` (default true).
   `catalogue_block()` lists only model-invocable skills; `GET /api/chat/skills`
   (new, read-only) lists user-invocable ones for a future `/skill` command
   in the panel. Provenance (`package` | `user`) is carried in the registry
   and shown in that listing, never in the prompt.
3. The prompt pins: the catalogue block is already outside the pinned
   constants (issue 05); a user skill changes the block for that install
   only. `test_harness_skills_and_workflows` gains the shadowing and policy
   cases.

## Done when

A skill dropped into the app-data root appears in the catalogue after a
restart (and after `POST /api/chat/skills/reload`), `use_skill` loads it, a
same-named skill is refused with a logged reason, and D12 in the spec is
amended to name the one extra root and why project folders are excluded.
