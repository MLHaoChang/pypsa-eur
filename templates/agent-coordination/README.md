# Agent coordination template

The multi-session setup this repo runs on, packaged for a new project:
- one **owner** sets direction;
- one **coordinator** session sets the project up, turns the owner's tasks into work packages, opens a session per package, relays decisions and does every merge;
- one **gatekeeper** session reviews the risky code;
- each **work session** follows the steward rules from branch to green, then hands off.

A new project starts with the coordinator alone. It creates everything else.

## Start a new project

Follow `BOOTSTRAP.md`. The owner creates the cloud environment and starts one
coordinator session with the prompt there. The coordinator then:
1. applies this template with `apply.sh` and merges the setup PR;
2. creates the gatekeeper session, the dashboard and the 3-hourly sweep routine;
3. records them in `docs/agents/roles.md`;
4. splits the owner's tasks into work packages and opens a session for each ready one, on the model its task table picks.

The coordinator skill holds the steps, under "Bootstrap" and "Taking on tasks".

## What you get

| Path in the new repo | What it does |
|---|---|
| `AGENTS.md`, `.claude/CLAUDE.md` | Rules every session loads: roles, asking the owner, credentials, release notes, PRs |
| `.claude/skills/coordinator/SKILL.md` | Bootstrap, task intake, sweep, merge, dashboard, retro |
| `.claude/skills/steward/SKILL.md` | How a work session drives its PR to green and reports it |
| `.claude/skills/gatekeeper/SKILL.md` | Plan checks, repeated-failure help and head-exact reviews |
| `.claude/settings.json`, `.claude/hooks/session-start.sh` | SessionStart hook skeleton for cloud sessions |
| `.claude/hooks/block-dangerous-git.sh`, `test-block-dangerous-git.sh` | PreToolUse hook on Bash: allows a normal `git push`, blocks force-push, `reset --hard`, `rebase`, `commit --amend`, `filter-branch`/`filter-repo`, `clean -f`, `branch -D`; with its matcher tests |
| `docs/agents/roles.md` | The one place for session ids, the dashboard link, the routine id and the template home |
| `docs/agents/session-brief.md` | The first message the coordinator sends each new session |
| `docs/agents/issue-tracker.md`, `triage-labels.md`, `domain.md` | Local markdown issues under `.scratch/`, triage roles, glossary layout |
| `scripts/coord/premerge.sh` | The whole merge gate: conflicts, overlap, optional semantic tests, every check run |
| `scripts/coord/sessions.py` | One line per live session from a saved `list_sessions` result |
| `scripts/coord/board.example.json` | The dashboard's row shape |
| `{{RELEASE_NOTES_DIR}}/README.md`, `.github/workflows/release-notes-guard.yaml` | One release-note fragment per PR, so notes never conflict, plus a guard against editing the collated file |
| `.github/pull_request_template.md` | PR body headings, including the fragment line |

`files/` keeps `.claude/` and `.github/` as `dot-claude/` and `dot-github/`, so the harness doesn't load the template's skills as this repo's own. `apply.sh` writes them back under their dot names.

These stay in this folder and are not copied:
- `BOOTSTRAP.md`: the owner's one prompt;
- `LEARNINGS.md`: why each rule exists, by retro;
- `dashboard/index.html`: the dashboard page;
- `template.env.example`: the placeholder values.

## apply.sh

`templates/agent-coordination/apply.sh --env <values-file> <target-repo-dir>`

- The values file is `template.env.example`, filled in.
- `apply.sh` never overwrites a file that already exists. It lists what it skipped, and every `{{...}}` placeholder it couldn't fill; it exits 1 when any are left.
- After it runs, set the project-specific parts:
  - `.claude/hooks/session-start.sh`: install the project's dependencies.
  - `scripts/coord/premerge.sh`: `docs_only_re`, `semantic_re` and `semantic_cmd`. Point `semantic_cmd` at the interpreter the hook builds.
  - `.claude/skills/coordinator/SKILL.md`: the model table and the known environment failures.
  - `AGENTS.md`: "Project notes".
  - `.gitignore`: if it ignores `.claude/hooks/*`, add exceptions for `block-dangerous-git.sh` and `test-block-dangerous-git.sh`, or the guardrail files are never committed.

| Placeholder | Example | Used in |
|---|---|---|
| `{{REPO}}` | `MLHaoChang/pypsa-eur` | premerge.sh, session brief, bootstrap |
| `{{BASE_BRANCH}}` | `master` | steward, coordinator, premerge.sh, guard |
| `{{TZ}}` | `Europe/Berlin` | coordinator time commands |
| `{{TZ_LABEL}}` | `CET/CEST` | owner-facing times |
| `{{GATED_AREAS}}` | `engine, finance and security code` | steward, gatekeeper, coordinator |
| `{{SLOW_CI_JOB}}` | `GUI backend` | steward (the job a push restarts) |
| `{{RELEASE_NOTES_DIR}}` | `doc/release-notes.d` | AGENTS.md, steward, fragments, guard |
| `{{RELEASE_NOTES_FILE}}` | `doc/release_notes.md` | guard |
| `{{TEMPLATE_HOME}}` | `MLHaoChang/pypsa-eur` | roles, coordinator bootstrap and retro |

Session ids, the dashboard link and the routine id are not placeholders. They
live only in `docs/agents/roles.md`, and every other file points there.

## CI

From the retros (see `LEARNINGS.md`):
- **Shard the slowest test job** into a matrix. Add one aggregate job under the old check name that `needs:` the shards, so branch protection still matches it. Every push restarts CI, so a shorter job makes everything cheaper.
- **Always upload junit XML and the full test log** as artifacts. A session can then read a failure without a re-run.
- **Keep the release-notes guard.** It ended the top-of-file conflicts that restarted CI.

## Keeping it current

This repo runs the live instance of the setup. When a rule changes here, from a retro, an owner decision or a fix, the same PR changes the template, and `LEARNINGS.md` gets a dated entry.

`scripts/coord/premerge.sh` enforces this. A PR that changes one of these live coordination files must also touch `templates/agent-coordination/`:
- `.claude/skills/steward/`
- `.claude/skills/coordinator/`
- `.claude/skills/retro/`
- `scripts/coord/`
- `docs/agents/session-brief.md`

When a change is specific to this project, such as a pypsa-gui test path in `semantic_tests`, the coordinator runs `PREMERGE_TEMPLATE_SYNC=skip scripts/coord/premerge.sh <pr>` and logs why.

**Intentional differences** between the live files and the template:
- The live files name pypsa-gui paths, tests, models and session ids. The template uses placeholders and `docs/agents/roles.md`.
- The live premerge.sh also has the semantic test list and the assistant-inventory check; the template has an empty `semantic_re`.
- Bootstrap exists only in the template, because this repo is already set up. The live gatekeeper runs from its first message rather than from a skill file.

Learnings from another project that used the template come back here by hand: that project's coordinator sends them to this repo's coordinator, or the owner opens a PR.
