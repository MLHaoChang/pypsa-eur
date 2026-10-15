# Agent coordination template

The multi-session setup this repo runs on, packaged for a new project. One
**owner** sets direction. One **coordinator** session relays decisions and does
every merge. One **gatekeeper** session reviews the risky code. Each **work
package** gets its own session, which follows the steward rules from branch to
green.

`apply.sh` copies `files/` into the new repo and fills in the placeholders.
This repo's own files (`AGENTS.md`, `.claude/skills/steward`,
`.claude/skills/coordinator`, `scripts/coord/`) are the live instance the
template was taken from. When a rule changes there, port it here too.

## What you get

| Path in the new repo | What it does |
|---|---|
| `AGENTS.md`, `.claude/CLAUDE.md` | Rules every session loads: asking the owner, credentials, release notes, PRs |
| `.claude/skills/steward/SKILL.md` | How a work session drives its PR to green and reports it |
| `.claude/skills/coordinator/SKILL.md` | The coordinator's sweep, merge, dashboard and session procedure |
| `.claude/skills/gatekeeper/SKILL.md` | The gatekeeper's plan checks and head-exact reviews |
| `.claude/settings.json`, `.claude/hooks/session-start.sh` | SessionStart hook skeleton for cloud sessions |
| `docs/agents/roles.md` | The one place for session ids, the dashboard link and the routine id |
| `docs/agents/session-brief.md` | The first message the coordinator sends each new session |
| `docs/agents/issue-tracker.md`, `triage-labels.md`, `domain.md` | Local markdown issues under `.scratch/`, triage roles, glossary layout |
| `scripts/coord/premerge.sh` | The whole merge gate: conflicts, overlap, optional semantic tests, every check run |
| `scripts/coord/sessions.py` | One line per live session from a saved `list_sessions` result |
| `scripts/coord/board.example.json` | The dashboard's row shape |
| `{{RELEASE_NOTES_DIR}}/README.md` | Release-note fragments, one file per PR, so notes never conflict |
| `.github/pull_request_template.md` | PR body headings, including the fragment line |

These stay in this folder and are not copied:
- `dashboard/index.html`: the page for the owner's dashboard artifact.
- `template.env.example`: the placeholder values.

## Apply it

1. Copy `template.env.example` to a file outside the target repo and fill it in.
   Leave the session ids for step 5.
2. Run `templates/agent-coordination/apply.sh --env <file> <target-repo-dir>`.
   It never overwrites a file that already exists. It lists what it skipped, and
   any `{{...}}` placeholder it couldn't fill.
3. In the target repo, set the project-specific parts by hand:
   - `.claude/hooks/session-start.sh`: install the project's dependencies.
   - `scripts/coord/premerge.sh`: `docs_only_re`, `semantic_re` and `semantic_cmd`.
   - `.claude/skills/coordinator/SKILL.md`: the model table and the "known environment failures" list.
   - `AGENTS.md`: the "Project notes" section.

   Commit on a `claude/*` branch and open a PR.
4. Vendor the mattpocock skills, if wanted: copy `.claude/skills/` from this repo
   and run `/setup-matt-pocock-skills` once. Its answers go to
   `docs/agents/issue-tracker.md` and `docs/agents/triage-labels.md`.
5. **Owner, once per project** (claude.ai):
   - Create a cloud environment for the repo. Add the secrets the project needs
     and network access for its package hosts.
   - Start the coordinator session and run `/coordinator`. Start the gatekeeper
     session on the strongest model, with
     `Read .claude/skills/gatekeeper/SKILL.md; you are the gatekeeper.`
   - Put both session ids in `docs/agents/roles.md` and merge that.
   - Have the coordinator create the dashboard: the Dashboard artifact type, one
     `datasets/board` file dataset, with `files/index.html` from
     `dashboard/index.html`. Put its link in `docs/agents/roles.md`.
   - Have the coordinator create the sweep routine. It fires into the coordinator
     session every 3 hours, with this prompt:
     `Run a sweep: follow .claude/skills/coordinator/SKILL.md from "Sweep".`

## Placeholders

| Placeholder | Example | Used in |
|---|---|---|
| `{{REPO}}` | `MLHaoChang/pypsa-eur` | premerge.sh, session brief |
| `{{BASE_BRANCH}}` | `master` | steward, coordinator, premerge.sh |
| `{{TZ}}` | `Europe/Berlin` | coordinator time commands |
| `{{TZ_LABEL}}` | `CET/CEST` | owner-facing times |
| `{{GATED_AREAS}}` | `engine, finance and security code` | steward, gatekeeper |
| `{{SLOW_CI_JOB}}` | `GUI backend` | steward (the job a push restarts) |
| `{{RELEASE_NOTES_DIR}}` | `doc/release-notes.d` | AGENTS.md, steward, fragment README |

Session ids, the dashboard link and the routine id are not placeholders. They
live only in `docs/agents/roles.md`, and every other file points there, so
replacing a session changes one line.
