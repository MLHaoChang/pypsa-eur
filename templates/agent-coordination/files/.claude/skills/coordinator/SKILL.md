---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when bootstrapping a project, turning tasks into sessions, running a sweep, merging a PR, refreshing the dashboard, running a retro, or relaying between sessions and the owner.
---

The **coordinator**:
- relays the owner's decisions to the work sessions;
- does every merge;
- keeps the owner's dashboard current.

PR sessions follow `.claude/skills/steward/SKILL.md`, and this skill is the coordinator's side of the same rules. When an owner decision changes a rule, the change lands here or in steward. The sweep routine's prompt only points at this file. `docs/agents/roles.md` holds the session ids, the dashboard link and the routine id.

Work from a checkout of current `{{BASE_BRANCH}}`. Before a sweep, run `git switch {{BASE_BRANCH}} && git pull --ff-only`, because a stale checkout loads stale rules.

Keep two files in the session's scratchpad:
- the **state log**, `coordinator-state.md`: one `- HH:MMZ ...` line per event. It records pending PRs and the dependent sessions of each, gatekeeper OKs with their SHAs, and decisions;
- the **board**, `board/board.json`: the dashboard's rows (see [Dashboard](#dashboard)).

## Bootstrap

The owner starts only this session. On a project where `docs/agents/roles.md` still holds `<session_…>`, do these steps once, in order. Find your own session id with `get_session` (no id).

1. **Setup PR.** If the template files are still missing:
   1. `add_repo` the template's home repository, {{TEMPLATE_HOME}}, with read access, and clone it.
   2. Ask the owner for the `template.env.example` values as one `AskUserQuestion`. Read defaults from the repo: its default branch, its CI job names, its release-notes layout.
   3. Run `templates/agent-coordination/apply.sh --env <file> <this repo>`.
   4. Fill in the project-specific parts the README lists. Read the repo's CI workflow and lockfiles for them.
   5. Open the setup PR on a `claude/*` branch.

   Merge it per [Merging](#merging) once it's green.
2. **Gatekeeper.** `create_session` on the strongest model, with no `outcome_branch`. Prompt it with: "You are the gatekeeper for {{REPO}}. Read `.claude/skills/gatekeeper/SKILL.md`. The coordinator is `<your session id>`."
3. **Dashboard.** Run Artifact `quickstart` for a dashboard and create it from the Dashboard type, titled after the project. Write:
   - `dash/meta`;
   - one file dataset, `datasets/board`, from `scripts/coord/board.example.json` with its rows emptied;
   - `files/index.html`, from the template's `dashboard/index.html`.
4. **Sweep routine.** `create_trigger` firing into this session every 3 hours, with the prompt `Run a sweep: follow .claude/skills/coordinator/SKILL.md from "Sweep".`
5. **Roles.** Fill `docs/agents/roles.md` with the coordinator and gatekeeper ids, the dashboard link and the routine id. Merge it.
6. **Tasks.** Go on to [Taking on tasks](#taking-on-tasks).

Done when `docs/agents/roles.md` on `{{BASE_BRANCH}}` has no `<…>` left, and the owner has the dashboard link.

## Taking on tasks

The owner hands over tasks: in a message, or as specs under `.scratch/`.

1. **Split.** Turn the tasks into **work packages**: one feature or fix each, ending when its last PR merges. For each package, write down:
   - the goal, in the owner's words;
   - the model, from [Opening a session](#opening-a-session);
   - whether it touches {{GATED_AREAS}};
   - the files it will likely touch;
   - the packages it depends on.
2. **Confirm.** Where a split changes scope or order, ask the owner as one `AskUserQuestion` with the recommended plan first. Otherwise go ahead and tell the owner the plan.
3. **Open.** Open a session for each package that is ready. A package is ready when no dependency is unmerged and it shares no likely files with a running package. Queue the rest in the state log. See [Opening a session](#opening-a-session).
4. **Track.** Add each session to the dashboard's sessions board and to the log.
5. **Refill.** When a package hands off, archive its session. Then open every queued package that is now ready.

Done when every package is running, queued with a named reason, or merged.

## Sweep

1. **Notifications.** Call `ReadNotifications` until it reports 0 remaining.
   Done when 0 remain and each one is either acted on or logged.
2. **Sessions.** Save the `list_sessions` output to a file, then run `python3 scripts/coord/sessions.py <file>`. Use `get_session` to look at one session.
   - A session might be waiting on an `AskUserQuestion` in its own window. Fetch the question and bring it to the owner as options.
   - Done when every live session is on the dashboard's sessions board with its current state.
3. **Merge pass.** Run [Merging](#merging) on every PR the state log lists as pending.
   Done when each one is merged or has a named blocker in the log.
4. **Dashboard.** Do [Dashboard](#dashboard) if anything changed.
5. **Owner.** Message the owner only with what landed or what needs a decision, with times in {{TZ_LABEL}}. Ask every question as `AskUserQuestion` options, per `AGENTS.md`.
6. **Log.** Append each event to the state log with [Time](#time).

## Sweep timing

The routine wakes this session for a [Sweep](#sweep) every 3 hours, as a safety net. Between sweeps, merges follow the sessions' green reports.

A routine that starts a fresh session on each firing begins without the repo and without the session tools. It can't read PRs or reach the coordinator, so the routine fires into this session.

## Deciding or asking

Decide yourself when the answer follows from a rule or an earlier owner decision, then tell the owner what you decided and why. Ask the owner when the answer changes scope, cost, what users see, or a rule. Escalate a disagreement between a session and the gatekeeper only when the gatekeeper holds its position.

Report only what has happened. Tell the owner a message went out after `send_message` returns, not before.

Verify a session's facts before acting on them: re-run premerge rather than trusting a reported SHA, overlap or green.

## Opening a session

Each **work package** gets its own session: one feature or fix, ending when its last PR merges. Pass `model` on every `create_session`. A child left without it inherits the coordinator's model.

| Package | Model |
|---|---|
| Probes, key checks, link checks, summaries of logs or lists | `claude-haiku-5-5` |
| Frontend-only fixes, data, CI and infra scripts, docs, walkthrough audits | `claude-sonnet-5-5` |
| {{GATED_AREAS}}; design-first features; gatekeeper review | `claude-opus-5-5` |

Leave `outcome_branch` unset, so the session can open each PR on its own `claude/*` branch. A session bound to one push branch has to hand its fixes over as patches.

Send `docs/agents/session-brief.md`, filled in, as the first message. The brief carries everything the session needs:
- the owner's decisions, with the owner's own words for the goal;
- file pointers and the `{{BASE_BRANCH}}` SHA it starts from;
- who reviews it (the gatekeeper for {{GATED_AREAS}});
- what "done" looks like.

When a package's last PR merges, read the session's hand-off note, `archive_session` it, and open the next package fresh from that note. That keeps context short. Every tool call re-reads the whole context, so a long-lived session costs more than its model tier saves.

## Merging

Merge one PR at a time:

1. Run `scripts/coord/premerge.sh <pr>`. It checks conflicts, overlap, the semantic tests on a trial merge and every check run on the head. Done when it ends in `RESULT: ok to merge <sha>`.
2. For {{GATED_AREAS}}, get the gatekeeper's OK for that same SHA and record it in the state log. A clean `{{BASE_BRANCH}}` merge on top of an OK needs no re-review when the gatekeeper says so.
3. Call `merge_pull_request` with `merge_method: merge` and `expectedHeadSha` set to the full 40-character `head:` line. premerge leaves out one check, GitHub's mergeable state; a refused merge reports it. Done when GitHub reports the PR merged.
4. Tell each dependent session the state log lists for this PR, then go on to the next PR. Each premerge run measures against the new `{{BASE_BRANCH}}`, so run it again after every merge.

**A blocked PR:**
- **Code overlap or a conflict.** Ask the PR's session to merge `{{BASE_BRANCH}}` in as a merge commit, then wait for CI on the new head.
- **The head moved** while the PR was green, because a session pushed. The new head is now the head, so wait for its CI and, where needed, a fresh gatekeeper OK. History stays as pushed. Ask sessions for merges, never for resets or force-pushes.
- **Known environment failures.** <List failures that also happen on `{{BASE_BRANCH}}` in cloud containers and so don't block a merge.>

## Dashboard

The dashboard is a Dashboard-type artifact (link in `docs/agents/roles.md`). Its page is `templates/agent-coordination/dashboard/index.html` in the repo the template came from. All three boards share one dataset, `datasets/board`. Its file, `board.json`, is `{rows: [...]}`, and each row's `board` field says which table shows it:

| `board` | Fields |
|---|---|
| `prs` | `pr`, `title`, `stream`, `state` (Testing, Re-running, Queued, Rebuilding, Approved, Ready), `eta`, `head`, `unblocks`, `note` |
| `sessions` | `session`, `doing`, `waiting_on`, `needs_you` (`yes`/`no`) |
| `merged` | `pr`, `title`, `stream`, `merged` (HH:MM, {{TZ_LABEL}}). Oldest first; append new ones |

Each refresh is two calls:
1. Edit `board.json` in the scratchpad, then upload it with Artifact `publish`, `asset: true`, `url` set to the dashboard.
2. Make one `ArtifactData` update to `datasets/board`. It sets `source.url` from the upload reply and `updated: {at, by}`, with `if_version` taken from the last write's result.

## Tool hygiene

- Read check runs through `premerge.sh`, which prints only the runs that block, never through one-off check-run reads per PR.
- Start a long command with the tool's own background option. A `&` inside a background command detaches it, and its completion notice never arrives.
- `list_sessions` output is large. Save it and summarize it with `sessions.py`; use `get_session` for a single session.

## Retro

Run a retro when the owner asks, or when a day's cost or rework looks high.

1. **Read the sources:**
   - the state log;
   - the `usage` in `list_sessions`;
   - the transcript;
   - the CI run times.
2. **Find the candidates.** Name the costs: restarted CI runs, stale rules, wrong reported facts, the largest tool calls, long contexts. Prefer a check or a script over a new rule.
3. **Write it up** as `retro-<date>.md` in the scratchpad, with the candidates in order of severity.
4. **Ask the owner** which candidates to land, as one `AskUserQuestion`.
5. **Land the chosen fixes** in a PR. When a fix changes the coordination setup, also make it in the template's home repository (`docs/agents/roles.md` names it), as its `templates/agent-coordination/README.md` describes under "Keeping it current".

## Time

Read the time; never estimate it.
- Stamp log entries with `date -u +%H:%MZ`.
- Give the owner `TZ={{TZ}} date +%H:%M`.
- Take a merge's time from its merge commit: `TZ={{TZ}} git log -1 --date=format-local:%H:%M --format=%ad <sha>`.
