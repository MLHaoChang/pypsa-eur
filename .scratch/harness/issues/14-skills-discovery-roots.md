# 14 — User-authored skills: discovery roots and invocation policy

Status: ready-for-agent (done 2026-10-13: `<app data>/skills/` root, invocation flags, `/api/chat/skills` and reload)
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

## Comments

2026-10-12 (re-scoped against master after 143 commits; nothing of this issue exists there yet):
`harness/skills/registry.py` is unchanged (package-only); master added a
second package skill, `project-refinement`. Also amend `harness/README.md`
contract rule 4 ("from this package only"), not only spec D12. Keep user
skills out of `find_capabilities`' catalogue index
(`services/assistant_tools.py`); `use_skill` is already in
`toolsets.CONTROL_TOOLS`, so it stays visible in every toolset view.

2026-10-13 (done): `harness/skills/registry.py` reads two roots, `package`
then `user` (`<app data>/skills/`, via `app_paths.app_data_dir()`); `roots()`
names exactly those two. Package files still raise `SkillError`; a user file
that is malformed or named like a package skill is refused with a warning on
`pypsa_gui.chat` and kept in `refusals()`. `Skill` carries `source`,
`model_invocable`, `user_invocable`; the catalogue and `use_skill` see
model-invocable skills only (`unknown_skill` otherwise). `GET
/api/chat/skills` (signed in) lists user-invocable skills with
`name`, `description`, `source`, `model_invocable` and the refusals, never a
body; `POST /api/chat/skills/reload` re-reads both roots. User skills are not
in `find_capabilities`' index (it indexes `TOOLS` only). Tests:
`tests/test_harness_user_skills.py` (15, red first), plus a never-fail load at startup (`main._load_skills_at_startup`) so a refusal is logged when the app starts. Spec D12, harness README
rule 4 and "Adding a skill", CHATBOT.md and CONTEXT.md amended.
