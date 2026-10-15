---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when running a sweep, merging a PR, refreshing the dashboard, or relaying between sessions and the owner.
---

The **coordinator** relays the owner's decisions to the work sessions, does every merge and keeps the owner's dashboard current. PR sessions follow `.claude/skills/steward/SKILL.md`. This skill is the coordinator's side of the same rules. Owner decisions that change a rule land here or in steward; the hourly routine's prompt only points at this file.

Work from a checkout of current master. Before a sweep, run `git switch master && git pull --ff-only`. A stale checkout loads stale `AGENTS.md`, steward and `scripts/coord/`.

## Sweep

1. **Notifications.** Call `ReadNotifications` until it reports 0 remaining.
   Done when 0 remain and each one is either acted on or logged.
2. **Sessions.** Save the `list_sessions` output to a file, then run `python3 scripts/coord/sessions.py <file>`. Use `get_session` to look at one session.
   - A session waiting on an `AskUserQuestion` in its own window: fetch the question and bring it to the owner as options.
   - Done when every live session is in the dashboard's sessions board with its current state.
3. **Merge pass.** Run [Merging](#merging) on every PR the state file lists as pending.
   Done when each one is merged or has a named blocker in the log.
4. **Dashboard.** Do [Dashboard](#dashboard) if anything changed.
5. **Owner.** Message the owner only with what landed or what needs a decision, times in CEST. Ask every question as `AskUserQuestion` options, per `AGENTS.md`.
6. **Log.** Append each event to the state file with [Time](#time).

## Hourly check

An hourly routine opens a fresh `claude-sonnet-5-5` session for this check, so the coordinator's long context isn't re-read every quiet hour. The check session starts with no coordinator context and runs on current master:

1. **Open PRs.** Run `scripts/coord/premerge.sh <pr>` for every open PR (`gh api 'repos/mlhaochang/pypsa-eur/pulls?state=open'`).
2. **Master.** Read the check runs on master's head (`gh api repos/mlhaochang/pypsa-eur/commits/<sha>/check-runs`).
3. **Sessions.** Save the `list_sessions` output to a file and run `python3 scripts/coord/sessions.py <file>`.
4. **Report.** `send_message` the coordinator (session_013NDrjK8eU7A4n1bPW9m6Xj) only when something needs it:
   - a PR whose premerge ends in `RESULT: ok to merge`, with that output;
   - a master check that failed;
   - a session that is failed, blocked, or waiting on a question.
   Otherwise end without a message.

Done when every open PR, master and every live session has been checked. The check session reads and reports only. Merges, PR changes and owner messages stay with the coordinator.

## Opening a session

Each **work package** gets its own session: one feature or fix, ending when its last PR merges. Pass `model` on every `create_session`. A child left without it inherits the coordinator's model.

| Package | Model |
|---|---|
| Probes, key checks, link checks, summaries of logs or lists | `claude-haiku-5-5` |
| Frontend-only fixes, template data, CI and infra scripts, docs, walkthrough audits, landing stale PRs | `claude-sonnet-5-5` |
| Gridspine, investment/finance, adequacy and IC engine code; security; design-first features; gatekeeper review | `claude-opus-5-5` |
| Only when the owner asks for it | `claude-fable-5-1` |

The brief carries everything the session needs:
- the owner's decisions, with the owner's own words for the goal;
- file pointers and the master SHA it starts from;
- who reviews it (the gatekeeper for engine code);
- what "done" looks like.

When a package's last PR merges, read the session's hand-off note, `archive_session` it, and open the next package fresh from that note. Context stays short that way. Every tool call re-reads the whole context, and Sonnet prices cache reads the same as Opus, so a long-lived session costs more than its model tier saves.

## Merging

Merge one PR at a time:

1. Run `scripts/coord/premerge.sh <pr>`. It covers conflicts, overlap, the semantic tests on a trial merge and every check run on the head. Done when it ends in `RESULT: ok to merge <sha>`.
2. For gridspine and IC engine changes, get the gatekeeper's OK (session_01Fz66v1SpJnjGHky1RbUuqC) for that same SHA, recorded in the state file.
3. Call `merge_pull_request` with `expectedHeadSha` set to the full 40-character `head:` line. GitHub's mergeable state is the one check premerge leaves out; a refused merge reports it. Done when GitHub reports the PR merged.
4. Tell each dependent session the state file lists for this PR, then go on to the next PR. Each premerge run measures against the new master.

**A blocked PR:**
- **Code overlap or a conflict.** Ask the PR's session to merge master in, as a merge commit, then wait for CI on the new head.
- **The head moved** while the PR was green: a session pushed, even if the push was an unneeded master merge. The new head is the head, so wait for its CI. History stays as pushed: the coordinator asks sessions for merges and never asks for resets or force-pushes.
- **Two PRs both touch `harness/catalogue.py`.** The second merges master and reruns `smoke/assistant_inventory.py --write`. `capability-inventory.json` is regenerated each time, never hand-merged.
- **An environment-only failure.** In cloud containers `test_harness_provider_parity` fails 4 tests, because `~/.venv-pypsa-gui` lacks `requests`. Master fails the same way, so it doesn't block a merge.

## Dashboard

The dashboard is https://claude.ai/artifact/GCvn8s9zSM6JfwsJ4bsATk. All three boards share one dataset, `datasets/board`. Its file, `board.json`, is `{rows: [...]}`, and each row's `board` field (`prs`, `sessions` or `merged`) says which table shows it. The page splits the rows with one calc per board.

Each refresh is two calls:
1. Edit `board.json` in the scratchpad, then upload it with Artifact `publish`, `asset: true`, `url` set to the dashboard.
2. Make one `ArtifactData` update to `datasets/board`. It sets `source.url` from the upload reply and `updated: {at, by}`, with `if_version` taken from the last write's result.

## Time

Read the time; never estimate it.
- Stamp log entries with `date -u +%H:%MZ`.
- Give the owner `TZ=Europe/Berlin date +%H:%M` (CEST).
- Take a merge's time from its merge commit: `git log -1 --format=%cI <sha>`.
