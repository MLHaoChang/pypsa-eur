# Session brief

The coordinator sends this as a new session's first message. `scripts/coord/brief.py` fills the first block from flags, appends the stream block when `--stream` names one, and writes the brief to a file for `create_session`; edit this file and the script reads the change. Fields the script leaves unset read "none".

```markdown
You are a session for MLHaoChang/pypsa-eur, commissioned by the owner through the coordinator session (the id in `docs/agents/coordinator.md`).

**Task.** <What to build or fix, and the outcome that marks it done.>

**Owner decisions.** <keys only, e.g. `USE-1`, `OWN-PR`. Look each up with `grep -F '| KEY |' docs/agents/owner-decisions.md`; never read the whole file.>

**Read first.**
- <file or doc>: <why>

**Stay out of.** <Files or areas another session owns right now, and which session. brief.py builds it from the open PRs' changed files and the live sessions.>

**Dependencies.** <PRs that must merge first, or PRs this one must not conflict with; "none".>

**Review.** <Who reviews it: the gatekeeper for engine code, else "none required".>

**Context budget.** Hand off at 300k context (owner decision USE-2). Keep tool output short: `| tail -8`, `grep -n` then `sed -n` of at most 60 lines, never whole files or whole diffs; background runs under 580 s.

**Process.** Follow `.claude/skills/steward/SKILL.md`: one PR on its own `claude/*` branch, a release-note fragment if the repo's guard requires one for the path, drive its CI, report `premerge ok <full head SHA>` and the `scripts/coord/premerge.sh` output to the coordinator (`docs/agents/coordinator.md`) once green, and send questions to the coordinator as 2–4 options. A clean master merge carries a reviewer's OK, so do not ask for a re-OK (owner decision RETRO-QUIET-1).
```

## Stream DC

Appended for `--stream dc`.

```markdown
**CONFIDENTIALITY (owner decision SAGC-1).** DC issues 09–19 derive from a client's confidential documents. In code, tests, PR text, commits and messages cite only the public SAGC (May 2026) and generic engineering; never a client name, network, MW figure or SOW text.
```
