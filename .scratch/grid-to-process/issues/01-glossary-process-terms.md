# Glossary: the process terms in CONTEXT.md

Status: needs-triage
Type: task
Blocked by: none
Gated: no
Model tier: sonnet
Size: S

Read [the design](../design.md) §1 first.

## What to build

Add these six terms to `pypsa-gui/CONTEXT.md`, using the `domain-modeling` skill:

- Process unit
- Product
- Inventory
- Production target
- Lost-production value
- Criticality tier

Also add Restart time if the owner keeps it as its own term.

Each term takes its definition, its `_Avoid_` line and its PyPSA mapping from design §1.2.

## Acceptance

- Each term cites the code that defines it. Before tickets 02 and 08 land, the citation names
  the design section. The ticket that builds the code updates the citation to file and line.
- Each term follows the file's existing style.
- No term redefines an existing one (Component, Asset, Baseline, Investment case).
