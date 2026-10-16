# Matt Pocock's agent skills (vendored)

Source: https://github.com/mattpocock/skills, commit `49dd158` (2026-10-16), plugin version 1.3.1.
License: MIT, see `MATTPOCOCK-LICENSE`.

Installed: exactly the promoted set the upstream Claude Code plugin ships
(`.claude-plugin/plugin.json`), flattened from `skills/engineering/` and
`skills/productivity/` into this folder. Not installed: upstream's `in-progress/`
(beta), `misc/` and `deprecated/` buckets.

## Local changes

- `code-review` is installed as **`mp-code-review`**, because the Claude Code harness
  already has a built-in `code-review` skill. References in `ask-matt`, `implement`,
  `implement-spec` and `tdd` point at the new name.
- `retro` has a step 5: a picked fix that changes the coordination setup also lands in
  `templates/agent-coordination/` and its `LEARNINGS.md`.

- The steward skill calls four of them: `diagnosing-bugs` after the same failure twice,
  `tdd` for a gated bug fix (failing test first), `mp-code-review` before a gatekeeper OK on
  a gated PR. The gatekeeper template expects the diagnosis and the self-review.

## Git guardrail (not vendored)

`.claude/hooks/block-dangerous-git.sh` is adapted from upstream
`skills/misc/git-guardrails-claude-code`, wired as a PreToolUse hook on Bash in
`.claude/settings.json`. Upstream blocks every `git push`; ours allows a normal push and
blocks force-push in any form, `reset --hard`, `rebase`, `commit --amend`,
`filter-branch`/`filter-repo`, `clean -f` and `branch -D`. It matches by token, so
`git push origin claude/force-fix` passes. Tests: `.claude/hooks/test-block-dangerous-git.sh`.
Upstream's `misc/` bucket is otherwise not installed.

## Where to start

- `/ask-matt`: router that tells you which skill or flow fits the situation.
- `/setup-matt-pocock-skills`: run once per repo before `to-spec`, `to-tickets`,
  `triage` and `wayfinder`; it records the issue tracker, triage labels and domain
  doc layout under `docs/agents/`.

| Flow | Skills |
|---|---|
| Plan and design | `grill-me`, `grill-with-docs`, `grilling`, `to-questionnaire`, `wayfinder`, `prototype`, `research` |
| Spec to code | `to-spec`, `to-tickets`, `implement`, `implement-spec`, `tdd` |
| Quality | `mp-code-review`, `diagnosing-bugs`, `improve-codebase-architecture`, `codebase-design`, `domain-modeling` |
| Delivery and session | `pr`, `retro`, `handoff`, `wait-what`, `wizard`, `triage` |
| Writing and learning | `writing-for-agents`, `teach` |

## Updating

```bash
git clone --depth 1 https://github.com/mattpocock/skills /tmp/mp-skills
# re-copy each path listed in /tmp/mp-skills/.claude-plugin/plugin.json into .claude/skills/,
# then re-apply the code-review -> mp-code-review rename and retro step 5 above, and bump
# the commit here.
```
