# Agent instructions

## Agent skills

The skills from mattpocock/skills are vendored in `.claude/skills/` (see `.claude/skills/MATTPOCOCK-SKILLS.md`).

### Issue tracker

Issues and specs are local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default role strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), recorded as a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Multi-context: `GLOSSARY-MAP.md` at the root points to each context's glossary (`pypsa-gui/CONTEXT.md` today). See `docs/agents/domain.md`.
