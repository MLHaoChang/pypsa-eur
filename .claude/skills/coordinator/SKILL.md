---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when turning tasks into sessions, running a sweep, rotating to a fresh coordinator, merging a PR, refreshing the dashboard, running a retro, or relaying between sessions and the owner.
---

The **coordinator** relays the owner's decisions to the work sessions, does every merge and keeps the owner's dashboard current. PR sessions follow `.claude/skills/steward/SKILL.md`. This skill is the coordinator's side of the same rules. Owner decisions that change a rule land here or in steward; the sweep routine's prompt only points at this file.

Work from a checkout of current master. Before a sweep, run `git switch master && git pull --ff-only`. A stale checkout loads stale `AGENTS.md`, steward and `scripts/coord/`.

## Sweep

1. **Notifications.** Call `ReadNotifications` until it reports 0 remaining; act on or log each one.
2. **Sessions.** Save the `list_sessions` output to a file, then run `python3 scripts/coord/sessions.py <file>`.
   - A session waiting on an `AskUserQuestion` in its own window: fetch the question and bring it to the owner as options.
   - Check [Rotation](#rotation)'s trigger.
   - A session flagged `ROTATE` (over 300k context): ask it for its hand-off note (steward's Rotation), `archive_session` it and open the rest fresh from the note.
   - Done when every live session is on the sessions board with its current state.
3. **Merge pass.** Run [Merging](#merging) on every PR the state file lists as pending.
   Done when each one is merged or has a named blocker in the log.
4. **Owner.** Do [Dashboard](#dashboard) if anything changed, then message the owner only with what landed or what needs a decision, times in CEST. Ask every question as `AskUserQuestion` options, per `AGENTS.md`, and write the same question and options in the turn's final reply text, so a worker restart cannot lose it.

Log each event in the state file as `- HH:MMZ ...` ([Time](#time)); `board.py` logs its own edits.

## Wakes

Every wake re-reads the whole context, so fewer wakes cost less.
- Merges follow the sessions' `premerge ok <sha>` reports. Never `subscribe_pr_activity`: a subscription costs about 3 wakes and 3k tokens of boilerplate per PR.
- The sweep routine fires every 6 hours, as a safety net.
- With no live session, `update_trigger` the routine to `enabled: false`. The next `create_session` re-enables it.
- A routine created with `create_new_session_on_fire` starts without the repo and without the session tools, so the sweep stays in this session.

## Rotation

A long context is this session's largest cost. Rotate to a fresh coordinator when `get_session` (no id) shows `context_usage.used_tokens` above 150k, or once a day, whichever comes first. The scratchpad is per session, so the new coordinator gets the state as content, not as files.

1. **Brief.** Write a coordinator brief at the top of the state file (`## Coordinator brief`, with [Time](#time)'s stamp):
   - pending PRs, each with its head SHA and the gatekeeper's OK for that SHA;
   - live sessions, each with its id and package;
   - owner decisions in force, in the owner's words;
   - open owner questions;
   - the dashboard's last `ArtifactData` `if_version`, and the routine's trigger id.
2. **Create.** `create_session` the new coordinator on this session's model, with a prompt that carries the brief's full text and says: "You are the coordinator for MLHaoChang/pypsa-eur. Read `.claude/skills/coordinator/SKILL.md`. First, save the brief below as the top of `coordinator-state.md` in your scratchpad, rebuild `board/board.json` from the dashboard's published file, put your own id (`get_session`) in `docs/agents/coordinator.md` through a tiny PR, and `send_message` me when the brief is saved."
3. **Hand over the routine.** `update_trigger` the sweep routine with `persistent_session_id` set to the new id. If `update_trigger` has no such field, `delete_trigger` and `create_trigger` again with the same cron and prompt.
4. **Tell the sessions.** `send_message` each live session the new id.
5. **Archive** this session once the new coordinator confirms the brief is saved. Relay anything that reaches this session before then.

Done when the routine fires into the new session, every live session has the new id, and `docs/agents/coordinator.md` on master holds it.

## Deciding or asking

Decide yourself when the answer follows from a rule or an earlier owner decision, then tell the owner what you decided and why. Ask the owner when the answer changes scope, cost, what users see, or a rule. Escalate a disagreement between a session and the gatekeeper only when the gatekeeper holds its position.

Every `AskUserQuestion` is also written in the turn's final reply text, in the same options form: a worker restart drops a pending question, and the text survives it.

Append every owner decision to `docs/agents/owner-decisions.md`, in the PR that lands the next coordination change or as its own tiny docs PR. Briefs cite the row.

Report only what has happened. Tell the owner a message went out after `send_message` returns, not before.

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

| Package | Model |
|---|---|
| Probes, key checks, link checks, summaries of logs or lists | `claude-haiku-5-5` |
| Frontend-only fixes, template data, CI and infra scripts, docs, walkthrough audits, landing stale PRs | `claude-sonnet-5-5` |
| Gridspine, investment/finance, adequacy and IC engine code; security; design-first features; gatekeeper review | `claude-opus-5-5` |
| Only when the owner asks for it | `claude-fable-5-1` |

After `create_session`, re-enable the sweep routine if [Wakes](#wakes) disabled it.

Leave `outcome_branch` unset, so the session can open each PR on its own `claude/*` branch. A session bound to one push branch has to hand its fixes over as patches.

Send `docs/agents/session-brief.md`, filled in, as the first message. The brief carries everything the session needs:
- the owner's decisions, with the owner's own words for the goal;
- file pointers and the master SHA it starts from;
- who reviews it (the gatekeeper for engine code);
- what "done" looks like.

When a package's last PR merges, read the session's hand-off note, `archive_session` it, and open the next package fresh from that note. Context stays short that way. Every tool call re-reads the whole context, and Sonnet prices cache reads the same as Opus, so a long-lived session costs more than its model tier saves.

## Merging

Merge one PR at a time:

1. Start from a session's `premerge ok <sha>` report. Run `scripts/coord/premerge.sh <pr>`; it covers conflicts, overlap, the semantic tests on a trial merge, template sync and every check run on the head. Done when it ends in `RESULT: ok to merge <sha>`.
   - On "TEMPLATE NOT UPDATED", ask the session to port the change to `templates/agent-coordination/`.
   - When the change is specific to this project, rerun with `PREMERGE_TEMPLATE_SYNC=skip` instead and log why.
2. For gridspine and IC engine changes, get the gatekeeper's OK (session_01Fz66v1SpJnjGHky1RbUuqC) for that same SHA, recorded in the state file.
   - For a PR that changes UI, also get the UX reviewer's OK (`docs/agents/coordinator.md`) for that same SHA. The reviewer checks it against the approved project workflow (`.scratch/project-workflow/design.md`). When a PR needs both OKs, both must name the same SHA. A clean master merge carries an OK when merge-tree equals the head tree.
3. Call `merge_pull_request` with `expectedHeadSha` set to the full 40-character `head:` line. GitHub's mergeable state is the one check premerge leaves out; a refused merge reports it. Done when GitHub reports the PR merged.
4. Tell each dependent session the state file lists for this PR, then go on to the next PR. Each premerge run measures against the new master.

**A blocked PR:**
- **Code overlap or a conflict.** Ask the PR's session to merge master in, as a merge commit, then wait for CI on the new head. Premerge waives a code overlap whose files all have tests that pass on a clean trial merge; only the rest blocks.
- **The head moved** while the PR was green: a session pushed, even if the push was an unneeded master merge. The new head is the head, so wait for its CI. History stays as pushed: the coordinator asks sessions for merges and never asks for resets or force-pushes.
- **Two PRs both touch `harness/catalogue.py`.** The second merges master and reruns `smoke/assistant_inventory.py --write`. `capability-inventory.json` is regenerated each time, never hand-merged.
- **A runner-less macOS job** (owner decision 2026-10-21). When `Integration (macos)` has been queued for over 30 minutes with no runner and every other check is green, cancel that workflow run (`actions_run_trigger`, `cancel_workflow_run`). Then rerun premerge: it waives the cancelled job, and the PR merges under that waiver.
- **A README or error-kinds overlap** (owner decisions 2026-10-21). Premerge itself treats `harness/README.md` as docs, and waives a `tool-error-kinds.json` overlap when both sides only add lines, the trial merge is clean and the manifest test passes. Don't ask for a master merge for these.
- **An environment-only failure.** In cloud containers `test_harness_provider_parity` fails 4 tests, because `~/.venv-pypsa-gui` lacks `requests`. Master fails the same way, so it doesn't block a merge.

## Dashboard

The dashboard is https://claude.ai/artifact/GCvn8s9zSM6JfwsJ4bsATk. All three boards share one dataset, `datasets/board`. Its file, `board.json`, is `{rows: [...]}`, and each row's `board` field (`prs`, `sessions` or `merged`) says which table shows it. The page splits the rows with one calc per board.

Each refresh is three calls, made at most once per owner report:
1. One Bash call, `python3 scripts/coord/board.py` (`--help` lists the flags). From flags it upserts or removes session rows, adds merged rows, adds or removes pr rows, appends one line to the state file and prints the path of `board.json`, ready for upload.
2. Upload that path with Artifact `publish`, `asset: true`, `url` set to the dashboard.
3. One `ArtifactData` update to `datasets/board`. It sets `source.url` from the upload reply and `updated: {at, by}`, with `if_version` taken from the last write's result.

## Tool hygiene

- Read check runs through `premerge.sh`, which prints only the runs that block, never through one-off check-run reads per PR.
- Start a long command with the tool's own background option. A `&` inside a background command detaches it, and its completion notice never arrives.
- `list_sessions` output is large. Save it and summarize it with `sessions.py`; use `get_session` for a single session.
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
