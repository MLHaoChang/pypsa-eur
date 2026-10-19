# Session brief

The coordinator fills in this template when it spawns a session and sends it as the first message. Replace every `<…>`; write "none" rather than deleting a section.

```markdown
You are a session for MLHaoChang/pypsa-eur, commissioned by the owner through the coordinator session (the id in `docs/agents/coordinator.md`).

**Task.** <What to build or fix, and the outcome that marks it done.>

**Read first.**
- <file or doc>: <why>

**Stay out of.** <Files or areas another session owns right now, and which session.>

**Dependencies.** <PRs that must merge first, or PRs this one must not conflict with; "none".>

**Process.** Follow `.claude/skills/steward/SKILL.md`: one PR on its own `claude/*` branch, a release-note fragment, report `premerge ok <full head SHA>` and the `scripts/coord/premerge.sh` output to the coordinator (`docs/agents/coordinator.md`) once green, and send questions to the coordinator as 2–4 options.
```
