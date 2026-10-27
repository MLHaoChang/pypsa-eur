---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when turning tasks into sessions, running a sweep, rotating to a fresh coordinator, merging a PR, refreshing the dashboard, running a retro, or relaying between sessions and the owner.
---

The **coordinator** relays the owner's decisions to the work sessions, does every merge and keeps the owner's dashboard current. PR sessions follow `.claude/skills/steward/SKILL.md`. This skill is the coordinator's side of the same rules. Owner decisions that change a rule land here or in steward; the sweep routine's prompt only points at this file.

At start, `touch ~/.claude-coordinator`: the context-guard hook warns the coordinator at 300k (owner decision COORD-CTX-1); every other session has warned at 300k too since USE-2.

Work from a checkout of current master. Before a sweep, run `git switch master && git pull --ff-only`. A stale checkout loads stale `AGENTS.md`, steward and `scripts/coord/`.

## Sweep

1. **Notifications.** Call `ReadNotifications` until it reports 0 remaining; act on or log each one.
2. **Sessions.** Have a Haiku subagent ([Tool hygiene](#tool-hygiene)) save the `list_sessions` output and return the `python3 scripts/coord/sessions.py <file>` summary.
   - A session waiting on an `AskUserQuestion` in its own window: fetch the question and bring it to the owner as options.
   - Check [Rotation](#rotation)'s trigger.
   - A session flagged `ROTATE` (over 300k context, owner decisions USE-1, USE-2): ask it for its hand-off note (steward's Rotation), `archive_session` it and open the rest fresh from the note.
   - Done when every live session is on the sessions board with its current state.
3. **Merge pass.** Run [Merging](#merging) on every PR the state file lists as pending.
   Done when each one is merged or has a named blocker in the log.
4. **Capacity.** Count the builders actually building, not landing, held or waiting on a merge chain. The **builder target** is 10 unless the owner sets another. While the count is below it, run `python3 scripts/coord/frontier.py --open-prs <prs> --sessions <sessions>` (inputs below) and open each package that shares no likely files with running work, per [Taking on tasks](#taking-on-tasks). A listed package you hold back gets its blocker in the log.
   - `<prs>`: one `<pr> <changed path>` line per open PR, from `git diff --name-only origin/master...pr/$n | sed "s/^/$n /"`.
   - `<sessions>`: the `sessions.py` output, or one issue path per line.
   Done when builders ≥ target, the frontier is empty, or every remaining package names its blocker.
5. **Owner.** Do [Dashboard](#dashboard) if anything changed, then message the owner only with what landed or what needs a decision, times in CEST. State "builders N/target, frontier M". Ask every question as `AskUserQuestion` options, per `AGENTS.md`, and write the same question and options in the turn's final reply text, so a worker restart cannot lose it.

Log each event in the state file as `- HH:MMZ ...` ([Time](#time)); `board.py` logs its own edits.

## Wakes

Every wake re-reads the whole context, so fewer wakes cost less.
- Merges follow the sessions' `premerge ok <sha>` reports. Never `subscribe_pr_activity`: a subscription costs about 3 wakes and 3k tokens of boilerplate per PR.
- The sweep routine fires every 6 hours, as a safety net.
- Call `list_triggers` with `enabled: true`; the full list is large.
- With no live session, `update_trigger` the routine to `enabled: false`. The next `create_session` re-enables it.
- A routine created with `create_new_session_on_fire` starts without the repo and without the session tools, so the sweep stays in this session.

## Rotation

A long context is this session's largest cost. Rotate to a fresh coordinator when `get_session` (no id) shows `context_usage.used_tokens` above 300k (owner decision COORD-CTX-1), or once a day, whichever comes first. The scratchpad is per session, so the new coordinator gets the state as content, not as files.

1. **Brief.** Write a coordinator brief at the top of the state file (`## Coordinator brief`, with [Time](#time)'s stamp). It holds live state only; the standing rules are in this skill, which the new coordinator reads:
   - pending PRs, each with its head SHA and the gatekeeper's OK for that SHA;
   - live sessions, each with its id and package;
   - owner decisions not yet in `docs/agents/owner-decisions.md`, in the owner's words;
   - open owner questions;
   - each dashboard's link and its datasets' last `ArtifactData` `if_version`, and the routine's trigger id.
2. **Create.** `create_session` the new coordinator on this session's model, with a prompt that carries the brief's full text and says: "You are the coordinator for MLHaoChang/pypsa-eur. Read `.claude/skills/coordinator/SKILL.md`. First, save the brief below as the top of `coordinator-state.md` in your scratchpad, rebuild `board/board.json` from the dashboard's published file, put your own id (`get_session`) in `docs/agents/coordinator.md` through a tiny PR, and `send_message` me when the brief is saved."
   The new coordinator runs `touch ~/.claude-coordinator` (see the top of this skill).
   The new coordinator also runs Artifact `open` on each dashboard in [Dashboard](#dashboard). The app lists only the artifacts a session has published or opened, so without it the owner's panel shows no dashboard after a rotation.
3. **Hand over the routine.** `update_trigger` the sweep routine with `persistent_session_id` set to the new id. If `update_trigger` has no such field, `delete_trigger` and `create_trigger` again with the same cron and prompt.
4. **Tell the sessions.** `send_message` each live session the new id. When any session rotates, reviewer or builder, tell every peer that works with it the new id at once.
5. **Archive** this session once the new coordinator confirms the brief is saved. Relay anything that reaches this session before then.

When this coordinator's children would hit the session-chain depth limit, the owner starts the successor at depth 0 (owner decision ROTATE-DEPTH).

Done when the routine fires into the new session, every live session has the new id, and `docs/agents/coordinator.md` on master holds it.

## Deciding or asking

Decide yourself when the answer follows from a rule or an earlier owner decision, then tell the owner what you decided and why. Ask the owner when the answer changes scope, cost, what users see, or a rule. Escalate a disagreement between a session and the gatekeeper only when the gatekeeper holds its position.

Every `AskUserQuestion` is also written in the turn's final reply text, in the same options form: a worker restart drops a pending question, and the text survives it.

Append every owner decision to `docs/agents/owner-decisions.md`, in the PR that lands the next coordination change or as its own tiny docs PR. Briefs cite the row.

**Permission requests (owner decision PERM-1).** A session's `PERMISSION ...` message goes to the owner as one `AskUserQuestion`, with the exact command pattern and what it reaches. On a yes, add exactly that pattern to `permissions.allow` in `.claude/settings.json` (e.g. `Bash(npx impeccable:*)`, never broader than asked) in a tiny OWN-PR, add an owner-decisions row, merge and tell the session to merge master. Never widen a pattern on your own.

Report only what has happened. Tell the owner a message went out after `send_message` returns, not before.

Verify an option against its issue file (`frontier.py`) before offering it to the owner: an issue with an open `Blocked by` cannot open.

Verify a session's facts before acting on them: re-run premerge rather than trusting a reported SHA, overlap or green.

## Taking on tasks

The owner hands over tasks: in a message, or as specs under `.scratch/`.

1. **Split.** Turn the tasks into **work packages**: one feature or fix each, ending when its last PR merges. For each package, write down:
   - the goal, in the owner's words;
   - the model, from [Opening a session](#opening-a-session);
   - whether it touches engine code the gatekeeper reviews;
   - the files it will likely touch;
   - the packages it depends on.
2. **Confirm.** Where a split changes scope or order, ask the owner as one `AskUserQuestion` with the recommended plan first. Otherwise go ahead and tell the owner the plan.
3. **Open.** Open a session for each package that is ready. A package is ready when no dependency is unmerged and it shares no likely files with a running package. Queue the rest in the state file. See [Opening a session](#opening-a-session).
4. **Track.** Add each session to the dashboard's sessions board and to the log.
5. **Refill.** When a package hands off, archive its session. Then open every queued package that is now ready.

Done when every package is running, queued with a named reason, or merged.

## Opening a session

Each **work package** gets its own session: one feature or fix, ending when its last PR merges. Pass `model` on every `create_session`. A child left without it inherits the coordinator's model.

Title and tag every session per owner decision NAMING: pass `title` as "<Team> · <role/package> — <current work>" (teams Lead, Review, Assurance, Build) and set the matching `team:<team>` tag with `set_session_tags`. Retitle a session when its current work changes.

| Package | Model |
|---|---|
| Probes, key checks, link checks, summaries of logs or lists | `claude-haiku-5-5` |
| Frontend-only fixes, template data, CI and infra scripts, docs, walkthrough audits, landing stale PRs | `claude-sonnet-5-5` |
| Gridspine, investment/finance, adequacy and IC engine code; security; design-first features; gatekeeper review | `claude-opus-5-5` |
| Only when the owner asks for it | `claude-fable-5-1` |

After `create_session`, re-enable the sweep routine if [Wakes](#wakes) disabled it.

Leave `outcome_branch` unset, so the session can open each PR on its own `claude/*` branch. A session bound to one push branch has to hand its fixes over as patches.

Always pass `source_url` `https://github.com/MLHaoChang/pypsa-eur`, `model`, `title` and `tags` (lowercase `team:<lead|review|build|assurance>`) on `create_session`. Before opening a package, check `git log origin/master` for it: it may have landed.

Build the brief with `python3 scripts/coord/brief.py` (`--help`; template `docs/agents/session-brief.md`) and send the file it writes as the first message. From flags it takes the task (the owner's own words for the goal, and what "done" looks like), decisions, file pointers, the master SHA, the reviewer and the stream; it builds "Stay out of" from the open PRs' files and the live sessions (the inputs of Sweep's Capacity step); and it appends Context budget (hand off at 300k, USE-2), Process, and for stream DC the CONFIDENTIALITY text (SAGC-1). Paste a rotating session's hand-off note into the task verbatim. When the brief is long, write it to the scratchpad and have a Sonnet subagent call `create_session` with it verbatim.

Never tell a session to disregard an auto-mode or classifier flag; it sends the coordinator a `PERMISSION` message. A builder with uncommitted helper scripts pushes them before you archive it.

When a package's last PR merges, read the session's hand-off note, `archive_session` it, and open the next package fresh from that note. Context stays short that way. Every tool call re-reads the whole context, and Sonnet prices cache reads the same as Opus, so a long-lived session costs more than its model tier saves.

## Merging

Merge one PR at a time. The coordinator merges a PR whose premerge ends in `ok to merge` with each needed reviewer OK current or carried (owner decision MERGE-AUTH-1):

1. Start from a session's `premerge ok <sha>` report. The session drives its PR's CI, so run `scripts/coord/premerge.sh <pr>` once, here; it covers conflicts, overlap, the semantic tests on a trial merge, template sync and every check run on the head. Done when it ends in `RESULT: ok to merge <sha>`.
   - On "TEMPLATE NOT UPDATED", ask the session to port the change to `templates/agent-coordination/`.
   - When the change is specific to this project, rerun with `PREMERGE_TEMPLATE_SYNC=skip` instead and log why.
   - Run premerge from a detached origin/master worktree. Once: `git worktree add --detach <scratch>/wt-pm origin/master`, then `ln -sfn /home/user/pypsa-eur/pypsa-gui/frontend/node_modules <scratch>/wt-pm/pypsa-gui/frontend/node_modules`. Before each run: `git fetch -q origin && git checkout -q --detach origin/master`, then `PREMERGE_PYTHON=/root/.venv-pypsa-gui/bin/python scripts/coord/premerge.sh <pr> > <scratch>/pmN.txt 2>&1` and `grep -E '^head:|^RESULT|OK @|NOT OK' <scratch>/pmN.txt`. Never leave a trial merge half-done there (`git merge --abort`): a dirty wt-pm broke ciwait once.
2. For gridspine and IC engine changes, the gatekeeper's OK must be on the PR; for a PR that changes UI, the UX reviewer's too (owner decision VERDICT-PR). Reviewers post `GK OK @<sha>` / `UX OK @<sha>` as PR comments and the coordinator relays nothing. Premerge's "reviewer verdicts" section prints each as `current`, `carries (clean master merge)` or `stale`. Missing and stale OKs don't block premerge, so you decide: a gated PR needs a current or carried OK from each reviewer it needs. A `NOT OK` newer than the last OK blocks. Two gatekeepers review by area (owner decision GK-SPLIT-1): GK A for gridspine, DC, grid-to-process and IC/adequacy engine code, GK B for the harness, app, finance and outsourced work. A PR spanning both gets one lead GK, named by the coordinator. Any push after a GK or UX OK needs a re-OK, unless it is a clean base merge whose tree equals `git merge-tree --write-tree <old-head> origin/master`. A hand-resolved conflict always needs a re-OK.
3. Call `merge_pull_request` with `merge_method` merge, `expectedHeadSha` set to the full 40-character `head:` line and `commit_title` "Merge pull request #N from MLHaoChang/<head.ref>". Read `head.ref` from `pull_request_read get`, or `git ls-remote origin | grep <sha>`; never a placeholder (#767 merged as ".../PLACEHOLDER"). GitHub's mergeable state is the one check premerge leaves out; a refused merge reports it. Done when GitHub reports the PR merged. Retry once on a GitHub 500.
4. Tell each dependent session the state file lists for this PR, then go on to the next PR. Each premerge run measures against the new master.

**A blocked PR:**
- **Code overlap or a conflict.** Ask the PR's session to merge master in, as a merge commit, then wait for CI on the new head. Premerge waives a code overlap whose files all have tests that pass on a clean trial merge; only the rest blocks.
- **The head moved** while the PR was green: a session pushed, even if the push was an unneeded master merge. The new head is the head, so wait for its CI. History stays as pushed: the coordinator asks sessions for merges and never asks for resets or force-pushes.
- **A JSON-chain PR behind master**, until TOOLSEL-1 (#771) lands: premerge's semantic tests miss `tests/test_vs2_results_walk_journey.py`. In wt-pm: `git merge -q --no-commit <head>`; `(cd pypsa-gui/backend && /root/.venv-pypsa-gui/bin/python -m pytest -q -o addopts='' tests/test_vs2_results_walk_journey.py | tail -1)` (~3.5 min, 14 tests); `git merge --abort`. A carried OK checks `[ "$(git merge-tree --write-tree <old-head> <master-sha>)" = "$(git rev-parse <new-head>^{tree})" ]`.
- **Two PRs both touch `harness/catalogue.py`.** The second merges master and reruns `smoke/assistant_inventory.py --write`. `capability-inventory.json` is regenerated each time, never hand-merged.
- **A runner-less macOS job** (owner decision 2026-10-21). When `Integration (macos)` has been queued for over 30 minutes with no runner and every other check is green, cancel that workflow run (`actions_run_trigger`, `cancel_workflow_run`). Then rerun premerge: it waives the cancelled job, and the PR merges under that waiver.
- **A README or error-kinds overlap** (owner decisions 2026-10-21). Premerge itself treats `harness/README.md` as docs, and waives a `tool-error-kinds.json` overlap when both sides only add lines, the trial merge is clean and the manifest test passes. Don't ask for a master merge for these.
- **An environment-only failure.** In cloud containers `test_harness_provider_parity` fails 4 tests, because `~/.venv-pypsa-gui` lacks `requests`. Master fails the same way, so it doesn't block a merge.

## Dashboard

The owner has three dashboards. Each keeps its data, and any state needed to rebuild it, in the artifact itself (its uploaded files and `ArtifactData` docs), never only in the scratchpad: the scratchpad goes with the session at rotation.

| Dashboard | Link | Datasets | Refresh |
|---|---|---|---|
| Coordinator board | https://claude.ai/artifact/GCvn8s9zSM6JfwsJ4bsATk | `datasets/board` | With each owner report that changes a PR or a session |
| Feature progress | https://claude.ai/artifact/4LgN19kQBYxwMNnDnnoybL | `datasets/tasks` | After a merge that finishes an issue, and on every sweep |
| Token usage | https://claude.ai/artifact/Y4gNYH7a7Ns43GaGfNmg68 | `datasets/daily`, `datasets/sessions`, `state/usage` | On every sweep (owner decision USAGE-REVIVE-1) |

Every refresh uploads a file (Artifact `publish`, `asset: true`, `url` set to that dashboard) and points the dataset at it: an `ArtifactData` update that sets `source.url` from the upload reply and `updated: {at, by}`, with `if_version` taken from the last write's result (after a rotation, a `get` returns it).

**Coordinator board.** All three boards share one dataset, `datasets/board`. Its file, `board.json`, is `{rows: [...]}`, and each row's `board` field (`prs`, `sessions` or `merged`) says which table shows it. The page splits the rows with one calc per board.

A row is `{board:'merged',pr,title,stream,merged:'HH:MM'}`, `{board:'prs',pr,title,stream,state,head}` or `{board:'sessions',session,doing,waiting_on,needs_you}`.

Each refresh is three calls, made at most once per owner report:
1. One Bash call, `python3 scripts/coord/board.py` (`--help` lists the flags). From flags it upserts or removes session rows, adds merged rows, adds or removes pr rows, appends one line to the state file and prints the path of `board.json`, ready for upload.
2. Upload that path with Artifact `publish`, `asset: true`, `url` set to the dashboard.
3. One `ArtifactData` update to `datasets/board`. It sets `source.url` from the upload reply and `updated: {at, by}`, with `if_version` taken from the last write's result.

**Feature progress.** One row per subcomponent: each `.scratch/<feature>/issues/*.md` on master, plus the polish backlog, with a `stage` (Done, In review, In progress, Ready, Blocked by NN, Needs triage, Needs owner, Won't do) and a `note`. The owner wants it high level: core feature, area, percentage built and the names of what is missing. A `.scratch` row's `stage` comes from `python3 scripts/coord/frontier.py --stages --open-prs <prs> --sessions <sessions>` (inputs: Sweep's Capacity step), never by hand; only `note` and the polish rows are edited by hand. To refresh, download the current file (Artifact `read`, `path` set to the asset id in `datasets/tasks`'s `source.url`), set the stages from that output and the notes with a short Python edit, upload it and update `datasets/tasks`.

**Token usage.** Tokens and API-equivalent cost per Berlin day, over every Claude Code session on the account (Cowork sessions are not listed).

`list_sessions` returns no usage totals, but `get_session <id>` does (`external_metadata.usage`: cache read/write, input and output tokens, `cost_usd`), so each session is read one at a time (owner decision USAGE-REVIVE-1, which replaced USAGE-FREEZE-1).
1. A Haiku subagent pages `list_sessions` (`mine: true`, `limit: 25`, following `after_id`) for ids and `updated_at` only, and writes the ids of the sessions updated since the last run (`state/usage`'s last run time) into 3 files. Don't keep the `list_sessions` pages in `<scratchpad>/usage/`: they carry no usage field, and `usage.py` would zero those sessions' snapshots.
2. Three Haiku subagents, one per file, call `get_session` per id and append one compact JSON line each to `<scratchpad>/usage/gs-N.jsonl`: `{id, title, created_at, updated_at, session_context.model, external_metadata.{last_served_model, usage}}`. Combine the lines into `<scratchpad>/usage/page-gs.json` as `{"data": [...]}`.
3. Unless this session already ran it, restore the history: `ArtifactData get` of `state/usage`, download its `snapshots` asset (Artifact `read`, `path` set to the id) to `<scratchpad>/usage/snapshots.json`, and write `{"first_run": "<its first_run>"}` to `<scratchpad>/usage/meta.json`. Without this, the run starts the history again and credits each session's whole total to its last active day.
4. `python3 -I scripts/coord/usage.py <scratchpad>/usage` writes `daily.json`, `sessions.json` and the new `snapshots.json`.
5. Upload the three files, then one `ArtifactData batch`: `datasets/daily`, `datasets/sessions`, and `state/usage` with the new `snapshots` url.

## Tool hygiene

- Read check runs through `premerge.sh`, which prints only the runs that block, never through one-off check-run reads per PR.
- Start a long command with the tool's own background option. A `&` inside a background command detaches it, and its completion notice never arrives.
- Session admin runs in a Haiku subagent (Agent tool, `model: haiku`) that returns one line, so the JSON dumps (~2.5k tokens each) stay out of this context:
  - retire a session: rename it "Done · …", then `archive_session` (direct calls are fine too);
  - `list_sessions`: save it and return the `sessions.py` summary;
  - `get_session` of another session.
- A PR with no session (the coordinator's own, or one whose driver rotated or is Codex, when only CI and premerge are left): start `scripts/coord/ciwait.sh 118 <pr>` with the Bash tool's `run_in_background` and `timeout` 7200000, one PR per call. It prints READY, FAIL, MACOS or TIMEOUT on the first hit: rerun it after a push. Backend shards take ~60 min; when the wait would pass ~60 min, run premerge directly instead. Sessions drive the CI of their own PRs. Push small mechanical fixes yourself; your own branch's hook blocks force pushes.
- Remote branch deletion is blocked by the classifier: it is the owner's job.
- A verdict sent to an archived session id is lost: relay it to the successor.
- `list_sessions` with `limit: 100` overflows a subagent: use `limit: 25` and have it copy the saved tool-results JSON to the file. `list_pull_requests` overflows too: parse its saved file with python.
- `list_events` output is large too. Save it and read it with `python3 scripts/coord/events.py <file> --chars 200`: each assistant event's time, text and last tool call. Read a session's events only that way, from a saved file.
- Read a PR's file list with `git diff --stat origin/master...<head>`; `pull_request_read get_files` returns hundreds of thousands of characters on a large PR (684k on #491).

## Retro

Run a retro when the owner asks, or when a day's cost or rework looks high.

1. **Read the sources:**
   - the state file;
   - the `usage` in `list_sessions`;
   - the transcript;
   - the CI run times.
2. **Find the candidates.** Name the costs: restarted CI runs, stale rules, wrong reported facts, the largest tool calls, long contexts. Prefer a check or a script over a new rule.
3. **Write it up** as `retro-<date>.md` in the scratchpad, with the candidates in order of severity.
4. **Ask the owner** which candidates to land, as one `AskUserQuestion`.
5. **Land the chosen fixes** in one PR. When a fix changes the coordination setup, the same PR also changes `templates/agent-coordination/` and adds a dated entry to its `LEARNINGS.md`. premerge.sh's "template sync" section checks this.

## Time

Read the time; never estimate it.
- Stamp log entries with `date -u +%H:%MZ`.
- Give the owner `TZ=Europe/Berlin date +%H:%M` (CEST).
- Take a merge's time from its merge commit: `git log -1 --format=%cI <sha>`.
