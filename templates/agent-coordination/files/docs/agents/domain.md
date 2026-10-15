# Domain docs

How sessions use this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY-MAP.md`** at the repo root. It names each context and where its glossary lives. Read the glossary of every context relevant to the topic.
- **ADRs**: `docs/adr/` for repo-wide decisions, and a context's own `docs/adr/` for decisions scoped to it. Read the ADRs that touch the area you are about to work in.

If any of these files don't exist, proceed silently. They are created when terms or decisions actually get resolved; a new context's glossary goes at `<context>/GLOSSARY.md` and is added to the map.

## Use the glossary's vocabulary

When your output names a domain concept (an issue title, a proposal, a test name), use the glossary's term. If the concept isn't in the glossary yet, either you are inventing language the project doesn't use, or there is a real gap to add.

## Flag ADR conflicts

If your output contradicts an existing ADR, say so explicitly rather than silently overriding it:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
