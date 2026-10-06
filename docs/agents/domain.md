# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY-MAP.md`** at the repo root: this repo is multi-context. The map names each context and where its glossary lives. Read the glossary of every context relevant to the topic. In `pypsa-gui` the glossary file is called `CONTEXT.md`, not `GLOSSARY.md`.
- **ADRs**: `docs/adr/` at the repo root for repo-wide decisions, and the context's own ADR folder (`pypsa-gui/docs/adr/` for the GUI) for decisions scoped to that context. Read the ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved. A context without a glossary yet gets one at `<context>/GLOSSARY.md`, added to the map.

## File structure

```
/
├── GLOSSARY-MAP.md                    ← lists the contexts
├── docs/adr/                          ← repo-wide decisions (created lazily)
├── pypsa-gui/
│   ├── CONTEXT.md                     ← the GUI glossary
│   └── docs/adr/                      ← GUI decisions
└── gridspine/                         ← no glossary yet
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_
