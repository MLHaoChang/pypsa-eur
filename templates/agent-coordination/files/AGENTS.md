# Agent instructions

## Roles

The owner sets direction and makes decisions. Sessions work as one of three roles:
- the **coordinator** relays the owner's decisions and does every merge (`.claude/skills/coordinator/SKILL.md`);
- the **gatekeeper** reviews plans and PRs that touch {{GATED_AREAS}} (`.claude/skills/gatekeeper/SKILL.md`);
- a **work session** builds one work package and drives its PRs to green (`.claude/skills/steward/SKILL.md`).

`docs/agents/roles.md` lists the gatekeeper's session id and the dashboard link; `docs/agents/coordinator.md` holds the current coordinator id.

## Asking the owner

Ask every question as an interactive choice (the `AskUserQuestion` tool). Give 2–4 concrete answers, each with its consequence. Put your recommended answer first, mark it "(Recommended)" and give the reason in its description. The owner picks one or types their own.

Decisions the owner has made are in `docs/agents/owner-decisions.md`.

A work session sends each question in that same form to the coordinator with `send_message`. The coordinator bundles the questions for the owner.

## Credentials

- Do not commit credentials or paste secret values into logs, PRs, or scratch files.
- Read a key's presence, never its value.
- Never force-push or rewrite history; merge commits only.
- No model identifiers in commit messages, PR titles or PR bodies.

## Change discipline

- Change only what the task needs; leave unrelated code, comments and formatting alone.
- Before writing new code, reuse what the codebase, the standard library or an installed dependency already has.
- Never cut validation, error handling, security or accessibility to keep a change small.
- End your report by naming what you skipped and any risk it leaves.

## Release notes

Add a new file under `{{RELEASE_NOTES_DIR}}/`. See `{{RELEASE_NOTES_DIR}}/README.md` for the file name and format.

## Pull requests

Opening or driving a PR: read `.claude/skills/steward/SKILL.md` first. It holds the owner's merge, CI and reporting rules.

## Issue tracker

Issues and specs are local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`.

## Domain docs

`GLOSSARY-MAP.md` at the root points to each context's glossary. See `docs/agents/domain.md`.

## Project notes

<How to install, run and test this project in a cloud session; which credentials gate which features.>
