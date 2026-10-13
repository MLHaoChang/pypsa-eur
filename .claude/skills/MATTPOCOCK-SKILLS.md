# Matt Pocock's agent skills (vendored)

Source: https://github.com/mattpocock/skills, commit `4588b32` (2026-10-05), plugin version 1.3.1.
License: MIT, see `MATTPOCOCK-LICENSE`.

Installed: exactly the promoted set the upstream Claude Code plugin ships
(`.claude-plugin/plugin.json`), flattened from `skills/engineering/` and
`skills/productivity/` into this folder. Not installed: upstream's `in-progress/`
(beta), `misc/` and `deprecated/` buckets.

## Local changes

- `code-review` is installed as **`mp-code-review`**, because the Claude Code harness
  already has a built-in `code-review` skill. References in `ask-matt`, `implement`,
  `implement-spec` and `tdd` point at the new name.

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
# then re-apply the code-review -> mp-code-review rename above and bump the commit here.
```
