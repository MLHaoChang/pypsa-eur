# Agent instructions

## Asking the owner

Ask every question as an interactive choice (the `AskUserQuestion` tool): 2–4 concrete answers, each with its consequence, your recommended answer first and marked "(Recommended)", and the reason in its description. The owner picks or types their own.

## Agent skills

The skills from mattpocock/skills are vendored in `.claude/skills/` (see `.claude/skills/MATTPOCOCK-SKILLS.md`).

### Issue tracker

Issues and specs are local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five default role strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), recorded as a `Status:` line in each issue file. See `docs/agents/triage-labels.md`.

### Domain docs

Multi-context: `GLOSSARY-MAP.md` at the root points to each context's glossary (`pypsa-gui/CONTEXT.md` today). See `docs/agents/domain.md`.

### The chat harness

Working on the in-app assistant (chat, tools, prompts, provider profiles, Guided flows): read `pypsa-gui/backend/harness/README.md` first; it is the contract, and the moved modules keep their old `services.*` names as aliases.
