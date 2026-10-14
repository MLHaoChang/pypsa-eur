# Agent instructions

## Asking the owner

Ask every question as an interactive choice (the `AskUserQuestion` tool): 2–4 concrete answers, each with its consequence, your recommended answer first and marked "(Recommended)", and the reason in its description. The owner picks or types their own.

A coordinated session (one commissioned through the coordinator session, `session_013NDrjK8eU7A4n1bPW9m6Xj`) sends each question in that same form to the coordinator with `send_message`; the coordinator bundles them for the owner.

## Release notes

Release notes: add a new file under `doc/release-notes.d/`; never edit `doc/release_notes.md` directly.
See `doc/release-notes.d/README.md` for the file name and format.

## Pull requests

Opening or driving a PR: read `.claude/skills/steward/SKILL.md` first; it holds the owner's merge, CI and reporting rules.

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

## Cursor Cloud specific instructions

- Moonshot / Kimi: the `moonshot` preset reads `MOONSHOT_API_KEY`. This
  environment may inject the same credential as `KIMI_KPI_KEY`.
  `app_secrets.bootstrap_environment` aliases `KIMI_KPI_KEY` → `MOONSHOT_API_KEY`
  when the latter is unset. Prefer naming the Cursor secret `MOONSHOT_API_KEY`
  for new setups. LiveKit / STT / TTS remain separate credential gates.
- Do not commit credentials or paste secret values into logs, PRs, or scratch files.
- Python, Node, and npm for the GUI come from `.pixi/envs/default` (pixi 0.68.1). Install with `pixi install --frozen -e default -e test`.
- `pixi run gui-tests` uses the `test` environment, not `default`.
- The environment's boot script starts the API on `http://127.0.0.1:8000` (tmux session `gui-backend`) and Vite on `http://127.0.0.1:5173` (tmux session `gui-frontend`).
- Sign in as `admin@example.com`. The password is the single line in `~/.local/share/PyPSA Studio/dev-admin-password`, created on first boot. That account owns a personal workspace, so it can create projects.
- Leave `pypsa-gui/backend/.env` absent unless you mean to point `DATABASE_URL` at Postgres. With no `.env`, the API uses the SQLite file under the app-data directory.
- The chat panel stays off until `ANTHROPIC_API_KEY` is set; the app still boots without it.
