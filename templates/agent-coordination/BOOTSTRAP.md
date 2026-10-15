# Bootstrap prompt

The owner does two things by hand. Everything else is done by the coordinator.

1. **Environment** (claude.ai, once per project):
   - Create a cloud environment for the new repo.
   - Add the secrets the project needs.
   - Allow network access for its package hosts.
2. **Coordinator.** Start one session in that environment on the strongest model, and paste this prompt with the `<…>` parts filled in:

```markdown
You are the coordinator for <owner/repo>. You start alone; you create the other sessions.

1. Add MLHaoChang/pypsa-eur with read access (add_repo) and clone it next to this repo.
   It holds the coordination template in templates/agent-coordination/.
2. Read its README.md and files/.claude/skills/coordinator/SKILL.md. Follow that skill's "Bootstrap" section for this repo, start to finish:
   - the setup PR;
   - the gatekeeper session;
   - the dashboard;
   - the sweep routine;
   - docs/agents/roles.md.
3. Then follow "Taking on tasks" with these tasks:
   <the task list, in my words; or "the specs under .scratch/">

Ask me every question as clickable options, the recommended one first. Give times in <time zone>.
```

Once the setup PR merges, the project's own `.claude/skills/coordinator/SKILL.md` takes over from the template's copy.
