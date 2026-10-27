---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when bootstrapping a project, turning tasks into sessions, running a sweep, rotating to a fresh coordinator, merging a PR, refreshing the dashboard, running a retro, or relaying between sessions and the owner.
---

The **coordinator**:
- relays the owner's decisions to the work sessions;
- does every merge;
- keeps the owner's dashboard current.

PR sessions follow `.claude/skills/steward/SKILL.md`, and this skill is the coordinator's side of the same rules. When an owner decision changes a rule, the change lands here or in steward. The sweep routine's prompt only points at this file. `docs/agents/roles.md` holds the session ids, the dashboard link and the routine id.

At start, `touch ~/.claude-coordinator`: the context-guard hook warns at 300k, the same limit as every other session (owner decision COORD-CTX-1).

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
4. **Sweep routine.** `create_trigger` firing into this session every 6 hours, with the prompt `Run a sweep: follow .claude/skills/coordinator/SKILL.md from "Sweep".`
5. **Roles.** Fill `docs/agents/coordinator.md` with your id, and `docs/agents/roles.md` with the gatekeeper id, the dashboard link and the routine id. Merge it.
6. **Tasks.** Go on to [Taking on tasks](#taking-on-tasks).

Done when `docs/agents/roles.md` and `docs/agents/coordinator.md` on `{{BASE_BRANCH}}` have no `<…>` left, and the owner has the dashboard link.

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

1. **Notifications.** Call `ReadNotifications` until it reports 0 remaining; act on or log each one.
2. **Sessions.** Have a Haiku subagent ([Tool hygiene](#tool-hygiene)) save the `list_sessions` output and return the `python3 scripts/coord/sessions.py <file>` summary.
   - A session waiting on an `AskUserQuestion` in its own window: fetch the question and bring it to the owner as options.
   - Check [Rotation](#rotation)'s trigger.
   - A session flagged `ROTATE` (over 300k context): ask it for its hand-off note (steward's Rotation), `archive_session` it and open the rest fresh from the note.
   - Done when every live session is on the sessions board with its current state.
3. **Merge pass.** Run [Merging](#merging) on every PR the state log lists as pending.
   Done when each one is merged or has a named blocker in the log.
4. **Capacity.** Count the builders actually building, not landing, held or waiting on a merge chain. The **builder target** is 10 unless the owner sets another. While the count is below it, run `python3 scripts/coord/frontier.py --open-prs <prs> --sessions <sessions>` (inputs below) and open each package that shares no likely files with running work, per [Taking on tasks](#taking-on-tasks). A listed package you hold back gets its blocker in the log.
   - `<prs>`: one `<pr> <changed path>` line per open PR, from `git diff --name-only origin/{{BASE_BRANCH}}...pr/$n | sed "s/^/$n /"`.
   - `<sessions>`: the `sessions.py` output, or one issue path per line.
   Done when builders ≥ target, the frontier is empty, or every remaining package names its blocker.
5. **Owner.** Do [Dashboard](#dashboard) if anything changed, then message the owner only with what landed or what needs a decision, times in {{TZ_LABEL}}. State "builders N/target, frontier M". Ask every question as `AskUserQuestion` options, per `AGENTS.md`, and write the same question and options in the turn's final reply text, so a worker restart cannot lose it.

Log each event in the state log as `- HH:MMZ ...` ([Time](#time)); `board.py` logs its own edits.

## Wakes

Every wake re-reads the whole context, so fewer wakes cost less.
- Merges follow the sessions' `premerge ok <sha>` reports. Never `subscribe_pr_activity`: a subscription costs about 3 wakes and 3k tokens of boilerplate per PR.
- The sweep routine fires every 6 hours, as a safety net. A second "dashboard refresh" routine (for example every 2 hours, `9 */2 * * *`) refreshes the dashboards; a dashboard with no change since its last refresh is skipped and logged. Refresh a costly dashboard such as token usage once a day.
- Call `list_triggers` with `enabled: true`; the full list is large.
- With no live session, `update_trigger` the routine to `enabled: false`. The next `create_session` re-enables it.
- A routine created with `create_new_session_on_fire` starts without the repo and without the session tools, so the sweep stays in this session.

## Rotation

A long context is this session's largest cost. Rotate to a fresh coordinator when `get_session` (no id) shows `context_usage.used_tokens` above 300k (owner decision COORD-CTX-1), or once a day, whichever comes first. The scratchpad is per session, so the new coordinator gets the state as content, not as files.

1. **Brief.** Write a coordinator brief at the top of the state log (`## Coordinator brief`, with [Time](#time)'s stamp). It holds live state only; the standing rules are in this skill, which the new coordinator reads:
   - pending PRs, each with its head SHA and the gatekeeper's OK for that SHA;
   - live sessions, each with its id and package;
   - owner decisions not yet in `docs/agents/owner-decisions.md`, in the owner's words;
   - open owner questions;
   - each dashboard's link and its datasets' last `ArtifactData` `if_version`, and the routine's trigger id.
2. **Create.** `create_session` the new coordinator on this session's model, with a prompt that carries the brief's full text and says: "You are the coordinator for {{REPO}}. Read `.claude/skills/coordinator/SKILL.md`. First, save the brief below as the top of `coordinator-state.md` in your scratchpad, rebuild `board/board.json` from the dashboard's published file, put your own id (`get_session`) in `docs/agents/coordinator.md` through a tiny PR, and `send_message` me when the brief is saved."
   The new coordinator runs `touch ~/.claude-coordinator` (see the top of this skill).
   The new coordinator also runs Artifact `open` on each dashboard in [Dashboard](#dashboard). The app lists only the artifacts a session has published or opened, so without it the owner's panel shows no dashboard after a rotation.
3. **Hand over the routines.** `update_trigger` each routine (the sweep and the dashboard refresh) with `persistent_session_id` set to the new id. If `update_trigger` has no such field, `delete_trigger` and `create_trigger` again with the same cron and prompt.
4. **Tell the sessions.** `send_message` each live session the new id. When any session rotates, reviewer or builder, tell every peer that works with it the new id at once.
5. **Archive** this session once the new coordinator confirms the brief is saved. Relay anything that reaches this session before then.

When this coordinator's children would hit the session-chain depth limit, the owner starts the successor at depth 0.

Done when the routine fires into the new session, every live session has the new id, and `docs/agents/coordinator.md` on `{{BASE_BRANCH}}` holds it.

## Deciding or asking

Decide yourself when the answer follows from a rule or an earlier owner decision, then tell the owner what you decided and why. Ask the owner when the answer changes scope, cost, what users see, or a rule. Escalate a disagreement between a session and the gatekeeper only when the gatekeeper holds its position.

Every `AskUserQuestion` is also written in the turn's final reply text, in the same options form: a worker restart drops a pending question, and the text survives it.

Append every owner decision to `docs/agents/owner-decisions.md`, in the PR that lands the next coordination change or as its own tiny docs PR. Briefs cite the row.

**Permission requests (owner decision PERM-1).** A session's `PERMISSION ...` message goes to the owner as one `AskUserQuestion`, with the exact command pattern and what it reaches. On a yes, add exactly that pattern to `permissions.allow` in `.claude/settings.json` (e.g. `Bash(npx impeccable:*)`, never broader than asked) in a tiny OWN-PR, add an owner-decisions row, merge and tell the session to merge master. Never widen a pattern on your own.

Report only what has happened. Tell the owner a message went out after `send_message` returns, not before.

Verify an option against its issue file (`frontier.py`) before offering it to the owner: an issue with an open `Blocked by` cannot open.

Verify a session's facts before acting on them: re-run premerge rather than trusting a reported SHA, overlap or green.

## Opening a session

Each **work package** gets its own session: one feature or fix, ending when its last PR merges. Pass `model` on every `create_session`. A child left without it inherits the coordinator's model.

Title and tag every session the way the owner's naming decision says (for example "<Team> · <role/package> — <current work>" plus a `team:<team>` tag), so the session list reads by team. Retitle a session when its current work changes.

| Package | Model |
|---|---|
| Probes, key checks, link checks, summaries of logs or lists | `claude-haiku-5-5` |
| Frontend-only fixes, data, CI and infra scripts, docs, walkthrough audits | `claude-sonnet-5-5` |
| {{GATED_AREAS}}; design-first features; gatekeeper review | `claude-opus-5-5` |

After `create_session`, re-enable the sweep routine if [Wakes](#wakes) disabled it.

Leave `outcome_branch` unset, so the session can open each PR on its own `claude/*` branch. A session bound to one push branch has to hand its fixes over as patches.

Always pass `source_url` `https://github.com/MLHaoChang/pypsa-eur`, `model`, `title` and `tags` (lowercase `team:<lead|review|build|assurance>`) on `create_session`. Before opening a package, check `git log origin/{{BASE_BRANCH}}` for it: it may have landed.

Build the brief with `python3 scripts/coord/brief.py` (`--help`; template `docs/agents/session-brief.md`) and send the file it writes as the first message. From flags it takes the task (the owner's own words for the goal, and what "done" looks like), decisions, file pointers, the `{{BASE_BRANCH}}` SHA, the reviewer (the gatekeeper for {{GATED_AREAS}}) and the stream; it builds "Stay out of" from the open PRs' files and the live sessions (the inputs of Sweep's Capacity step); and it appends Context budget, Process and any stream paragraph the template defines. Paste a rotating session's hand-off note into the task verbatim. When the brief is long, write it to the scratchpad and have a cheap-model subagent call `create_session` with it verbatim.

Never tell a session to disregard an auto-mode or classifier flag; it sends the coordinator a `PERMISSION` message. A builder with uncommitted helper scripts pushes them before you archive it.

When a package's last PR merges, read the session's hand-off note, `archive_session` it, and open the next package fresh from that note. That keeps context short. Every tool call re-reads the whole context, so a long-lived session costs more than its model tier saves.

## Merging

Merge one PR at a time:

1. Start from a session's `premerge ok <sha>` report. The session drives its PR's CI, so run `scripts/coord/premerge.sh <pr>` once, here; it checks conflicts, overlap, the semantic tests on a trial merge and every check run on the head. Done when it ends in `RESULT: ok to merge <sha>`.
   - Run premerge from a detached origin/{{BASE_BRANCH}} worktree. Once: `git worktree add --detach <scratch>/wt-pm origin/{{BASE_BRANCH}}` (link any untracked dependency directories into it). Before each run: `git fetch -q origin && git checkout -q --detach origin/{{BASE_BRANCH}}`, then `PREMERGE_PYTHON=<the project's venv python> scripts/coord/premerge.sh <pr> > <scratch>/pmN.txt 2>&1` and `grep -E '^head:|^RESULT|OK @|NOT OK' <scratch>/pmN.txt`. Never leave a trial merge half-done there (`git merge --abort`): a dirty wt-pm broke ciwait once.
2. For {{GATED_AREAS}}, the gatekeeper's OK must be on the PR, and where the project has a UX reviewer a PR that changes UI needs its OK too. Reviewers post `GK OK @<sha>` / `UX OK @<sha>` as PR comments and the coordinator relays nothing. Premerge's "reviewer verdicts" section prints each as `current`, `carries (clean {{BASE_BRANCH}} merge)` or `stale`. Missing and stale OKs don't block premerge, so you decide: a gated PR needs a current or carried OK from each reviewer it needs. A `NOT OK` newer than the last OK blocks. Any push after a GK or UX OK needs a re-OK, unless it is a clean base merge whose tree equals `git merge-tree --write-tree <old-head> origin/{{BASE_BRANCH}}`. A hand-resolved conflict always needs a re-OK.
3. Call `merge_pull_request` with `merge_method: merge`, `expectedHeadSha` set to the full 40-character `head:` line and `commit_title` "Merge pull request #N from <owner>/<head.ref>". Read `head.ref` from `pull_request_read get`, or `git ls-remote origin | grep <sha>`; never a placeholder. premerge leaves out one check, GitHub's mergeable state; a refused merge reports it. Done when GitHub reports the PR merged. Retry once on a GitHub 500.
4. Tell each dependent session the state log lists for this PR, then go on to the next PR. Each premerge run measures against the new `{{BASE_BRANCH}}`, so run it again after every merge.

**A blocked PR:**
- **Code overlap or a conflict.** Ask the PR's session to merge `{{BASE_BRANCH}}` in as a merge commit, then wait for CI on the new head. Where `overlap_tests_script` is set, premerge waives a code overlap whose files all have tests that pass on a clean trial merge; only the rest blocks.
- **The head moved** while the PR was green, because a session pushed. The new head is now the head, so wait for its CI and, where needed, a fresh gatekeeper OK. History stays as pushed. Ask sessions for merges, never for resets or force-pushes.
- **A runner-less job.** When a platform job (for example `Integration (macos)`) has been queued for over 30 minutes with no runner and every other check is green, cancel that workflow run. Then rerun premerge: it waives a job cancelled before it got a runner, and the PR merges under that waiver.
- **Known environment failures.** <List failures that also happen on `{{BASE_BRANCH}}` in cloud containers and so don't block a merge.>

## Dashboard

The dashboard is a Dashboard-type artifact (link in `docs/agents/roles.md`). It keeps its data, and any state needed to rebuild it, in the artifact itself (uploaded files and `ArtifactData` docs), never only in the scratchpad: the scratchpad goes with the session at rotation. Its page is `templates/agent-coordination/dashboard/index.html` in the repo the template came from. All three boards share one dataset, `datasets/board`. Its file, `board.json`, is `{rows: [...]}`, and each row's `board` field says which table shows it:

| `board` | Fields |
|---|---|
| `prs` | `pr`, `title`, `stream`, `state` (Testing, Re-running, Queued, Rebuilding, Approved, Ready), `eta`, `head`, `unblocks`, `note` |
| `sessions` | `session`, `doing`, `waiting_on`, `needs_you` (`yes`/`no`) |
| `merged` | `pr`, `title`, `stream`, `merged` (HH:MM, {{TZ_LABEL}}). Oldest first; append new ones |

Each refresh is three calls, made at most once per owner report:
1. One Bash call, `python3 scripts/coord/board.py` (`--help` lists the flags). From flags it upserts or removes session rows, adds merged rows, adds or removes pr rows, appends one line to the state log and prints the path of `board.json`, ready for upload.
2. Upload that path with Artifact `publish`, `asset: true`, `url` set to the dashboard.
3. One `ArtifactData` update to `datasets/board`. It sets `source.url` from the upload reply and `updated: {at, by}`, with `if_version` taken from the last write's result.

## Tool hygiene

- Read check runs through `premerge.sh`, which prints only the runs that block, never through one-off check-run reads per PR.
- Start a long command with the tool's own background option. A `&` inside a background command detaches it, and its completion notice never arrives.
- Session admin runs in a Haiku subagent (Agent tool, `model: haiku`) that returns one line, so the JSON dumps (~2.5k tokens each) stay out of this context:
  - retire a session: rename it "Done · …", then `archive_session` (direct calls are fine too);
  - `list_sessions`: save it and return the `sessions.py` summary;
  - `get_session` of another session.
- A PR with no session (the coordinator's own, or one whose driver rotated or is Codex, when only CI and premerge are left): start `scripts/coord/ciwait.sh 118 <pr>` with the Bash tool's `run_in_background` and `timeout` 7200000, one PR per call. It prints READY, FAIL, MACOS or TIMEOUT on the first hit: rerun it after a push. Backend shards take ~60 min; when the wait would pass ~60 min, run premerge directly instead. Sessions drive the CI of their own PRs. Push small mechanical fixes yourself; your own branch's hook blocks force pushes.
- A `ci-only <pr> <sha>` hand-off from a session: archive the session, run `ciwait.sh` on the PR, push small mechanical fixes yourself, and open a fresh session from its hand-off note for anything bigger.
- Remote branch deletion is blocked by the classifier: it is the owner's job.
- A verdict sent to an archived session id is lost: relay it to the successor.
- `list_sessions` with `limit: 100` overflows a subagent: use `limit: 25` and have it copy the saved tool-results JSON to the file. `list_pull_requests` overflows too: parse its saved file with python.
- `list_events` output is large too. Save it and read it with `python3 scripts/coord/events.py <file> --chars 200`: each assistant event's time, text and last tool call. Read a session's events only that way, from a saved file.
- Read a PR's file list with `git diff --stat origin/{{BASE_BRANCH}}...<head>`; `pull_request_read get_files` returns hundreds of thousands of characters on a large PR (684k on #491).

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
