---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when running a sweep, merging a PR, refreshing the dashboard, or relaying between sessions and the owner.
---

The **coordinator** relays the owner's decisions to the work sessions, does every merge and keeps the owner's dashboard current. PR sessions follow `.claude/skills/steward/SKILL.md`. This skill is the coordinator's side of the same rules. Owner decisions that change a rule land here or in steward; the hourly trigger prompt only points at this file.

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

## Merging

Merge one PR at a time:

1. Run `scripts/coord/premerge.sh <pr>`. Done when it ends in `RESULT: ok to merge`.
2. Read the check runs on premerge's `head:` SHA. Every run must be success or skipped.
3. For gridspine and IC engine changes, get the gatekeeper's OK (session_01Fz66v1SpJnjGHky1RbUuqC), recorded in the state file.
4. Call `merge_pull_request` with `expectedHeadSha` set to the full 40-character `head:` line. Done when GitHub reports the PR merged.
5. Tell each dependent session the state file lists for this PR, then go on to the next PR. Each premerge run measures against the new master.

**A blocked PR:**
- **Code overlap or a conflict.** Ask the PR's session to merge master in, as a merge commit, then wait for CI on the new head.
- **The head moved** while the PR was green: a session pushed, even if the push was an unneeded master merge. The new head is the head, so wait for its CI. History stays as pushed: the coordinator asks sessions for merges and never asks for resets or force-pushes.
- **Two PRs both touch `harness/catalogue.py`.** The second merges master and reruns `smoke/assistant_inventory.py --write`. `capability-inventory.json` is regenerated each time, never hand-merged.
- **An environment-only failure.** In cloud containers `test_harness_provider_parity` fails 4 tests, because `~/.venv-pypsa-gui` lacks `requests`. Master fails the same way, so it doesn't block a merge.

## Dashboard

The dashboard is https://claude.ai/artifact/GCvn8s9zSM6JfwsJ4bsATk. Its boards (prs, merged, sessions) are JSON files kept in the scratchpad `board/` folder.

To refresh a board:
1. Edit its file.
2. Upload it with Artifact `publish`, `asset: true`, `url` set to the dashboard. JSON files go one per call.
3. In one `ArtifactData` batch, update `datasets/<id>` with `source.url` from the upload reply and `updated`.

## Time

Read the time; never estimate it.
- Stamp log entries with `date -u +%H:%MZ`.
- Give the owner `TZ=Europe/Berlin date +%H:%M` (CEST).
- Take a merge's time from its merge commit: `git log -1 --format=%cI <sha>`.
