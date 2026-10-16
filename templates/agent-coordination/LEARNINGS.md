# Learnings behind the template

Why each rule exists, and where it lives now. Add a dated entry whenever a
retro or an owner decision changes the setup (see "Keeping it current" in
`README.md`). Newest last.

## 13 Oct 2026: coordination retro

- **One ~2 h test job multiplied every restart.** About 15 restarted runs in a day.
  - Fix: shard the slowest job into a matrix, and keep one aggregate job under the old check name so branch protection still matches it.
  - Lives in: README, "CI".
- **Release-note conflicts restarted CI six times.**
  - Fix: one fragment file per PR, enforced by a guard workflow.
  - Lives in: the fragment README, `.github/workflows/release-notes-guard.yaml`.
- **Rules lived in messages.** Sessions created before a rule changed kept the old one.
  - Fix: the rules live in the steward skill, which the cloud harness makes every PR session read from the head branch.
  - Lives in: steward.
- **Sessions reported wrong SHAs, overlap without a fetch, and green on cancelled runs.**
  - Fix: one committed gate script, whose output goes in every green report.
  - Lives in: `scripts/coord/premerge.sh`, steward "Report green".
- **Two semantic conflicts had no file overlap.**
  - Fix: run the semantic tests on a trial merge for risky paths.
  - Lives in: premerge.sh `semantic_re` / `semantic_cmd`.
- **Failures hid behind thousands of log lines.**
  - Fix: always upload junit XML and the full log as CI artifacts.
  - Lives in: README, "CI".
- **Older sessions asked questions in their own windows.** And a session bound to one push branch couldn't open its own PRs.
  - Fix: the session brief, and leave `outcome_branch` unset.
  - Lives in: `docs/agents/session-brief.md`, coordinator "Opening a session".
- **Owner-facing details.** Times go in the owner's zone, and every question is clickable options.
  - Lives in: AGENTS.md, steward "Questions".

## 14 Oct 2026: setup retro

- **The coordinator ran on a checkout 435 commits behind,** so it loaded stale rules.
  - Fix: sweep from a fresh pull of the base branch.
  - Lives in: coordinator intro.
- **The coordinator asked a session for a force-push** to undo an unneeded merge.
  - Fix: the moved head is the head; never reset.
  - Lives in: coordinator "A blocked PR".
- **The routine prompt duplicated the rules and went stale.**
  - Fix: the prompt only points at the coordinator skill.
  - Lives in: coordinator "Sweep timing", README.
- **The gate script and hand-run checks drifted.**
  - Fix: one test list, inside the script; the script is the whole gate.
  - Lives in: premerge.sh.
- **Check-run reads were the largest avoidable cost.**
  - Fix: premerge prints only the blocking runs.
  - Lives in: premerge.sh "checks", coordinator "Tool hygiene".
- **A dashboard refresh cost 2–3 calls per change.**
  - Fix: one `board.json` dataset holding all three boards.
  - Lives in: coordinator "Dashboard".
- **Typed timestamps drifted from the clock.**
  - Fix: read `date`, and take merge times from the merge commit.
  - Lives in: coordinator "Time".
- **A `&` inside a background command lost its completion notice.**
  - Lives in: coordinator "Tool hygiene".

## 15 Oct 2026: model-tiering retro

- **87% of cost sat in nine long sessions,** mostly cache reads of 300–700k-token contexts. Sonnet prices cache reads the same as Opus.
  - Fix: one session per work package, archived at hand-off; the next package starts fresh from the note.
  - Lives in: coordinator "Opening a session" and "Taking on tasks", steward "Finishing a package".
- **Children inherited the coordinator's model.**
  - Fix: pass `model` from a task table on every `create_session`.
  - Lives in: coordinator "Opening a session".
- **Read-heavy helper work ran on the parent's model.**
  - Fix: helpers run on the smallest model.
  - Lives in: steward "Helper agents".
- **A fresh-session routine for the sweep was tried and dropped.** Such a session starts without the repo and the session tools.
  - Fix: the routine fires into the coordinator.
  - Lives in: coordinator "Sweep timing".

## 15 Oct 2026: owner decisions

- **Review came only at the end.** The gatekeeper now advises before a plan for gated areas, and after the same failure twice.
  - Lives in: steward "Gatekeeper checkpoints", gatekeeper skill.
- **Coordinator slips.** It reported a relay before sending it, and miscounted approved PRs.
  - Fix: report only what has happened, and verify facts with premerge.
  - Lives in: coordinator "Deciding or asking".
- **A new project starts from one coordinator session,** which creates the gatekeeper, the dashboard and the routine, then opens sessions from the task list.
  - Lives in: coordinator "Bootstrap" and "Taking on tasks", `BOOTSTRAP.md`.

## 16 Oct 2026: owner decisions (Matt Pocock skills)

- **Skills that fit the workflow were not wired in.** The owner chose five changes:
  - after the same failure twice, run `diagnosing-bugs`, then send the gatekeeper the diagnosis and both attempts;
  - a gated bug-fix plan names the failing test, which the session writes first and watches fail (`tdd`);
  - on gated PRs, run `mp-code-review` against the issue or spec before asking for an OK;
  - a PreToolUse hook blocks force-push and history rewrites (adapted from upstream `git-guardrails-claude-code`, which blocks every push; ours allows a normal one);
  - the vendored skills move to upstream commit `49dd158`.
  - Lives in: steward "Gatekeeper checkpoints", gatekeeper skill, `files/dot-claude/hooks/block-dangerous-git.sh`.
