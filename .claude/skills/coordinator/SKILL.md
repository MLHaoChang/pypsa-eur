---
name: coordinator
description: The coordinator session's own procedure (the other side of steward). Use in the coordinator session when turning tasks into sessions, running a sweep, merging a PR, refreshing the dashboard, running a retro, or relaying between sessions and the owner.
---

The **coordinator** relays the owner's decisions to the work sessions, does every merge and keeps the owner's dashboard current. PR sessions follow `.claude/skills/steward/SKILL.md`. This skill is the coordinator's side of the same rules. Owner decisions that change a rule land here or in steward; the sweep routine's prompt only points at this file.

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

## Sweep timing

The routine wakes this session for a [Sweep](#sweep) every 3 hours, as a safety net. Between sweeps, merges follow the sessions' green reports.

A routine created from a session (`create_trigger` with `create_new_session_on_fire`) starts without the repo and without the session tools. It can't read PRs or reach the coordinator, so the sweep stays in this session.

## Deciding or asking

Decide yourself when the answer follows from a rule or an earlier owner decision, then tell the owner what you decided and why. Ask the owner when the answer changes scope, cost, what users see, or a rule. Escalate a disagreement between a session and the gatekeeper only when the gatekeeper holds its position.

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

Leave `outcome_branch` unset, so the session can open each PR on its own `claude/*` branch. A session bound to one push branch has to hand its fixes over as patches.

Send `docs/agents/session-brief.md`, filled in, as the first message. The brief carries everything the session needs:
- the owner's decisions, with the owner's own words for the goal;
- file pointers and the master SHA it starts from;
- who reviews it (the gatekeeper for engine code);
- what "done" looks like.

When a package's last PR merges, read the session's hand-off note, `archive_session` it, and open the next package fresh from that note. Context stays short that way. Every tool call re-reads the whole context, and Sonnet prices cache reads the same as Opus, so a long-lived session costs more than its model tier saves.

## Merging

Merge one PR at a time:

1. Run `scripts/coord/premerge.sh <pr>`. It covers conflicts, overlap, the semantic tests on a trial merge, template sync and every check run on the head. Done when it ends in `RESULT: ok to merge <sha>`.
   - On "TEMPLATE NOT UPDATED", ask the session to port the change to `templates/agent-coordination/`.
   - When the change is specific to this project, rerun with `PREMERGE_TEMPLATE_SYNC=skip` instead and log why.
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

## Tool hygiene

- Read check runs through `premerge.sh`, which prints only the runs that block, never through one-off check-run reads per PR.
- Start a long command with the tool's own background option. A `&` inside a background command detaches it, and its completion notice never arrives.
- `list_sessions` output is large. Save it and summarize it with `sessions.py`; use `get_session` for a single session.

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
