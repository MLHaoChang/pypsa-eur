# Memory items as graph nodes

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: sonnet.
Gated: GK B.
Blocked by: 02, and the assistant memory build (MEMORY-1, `.scratch/assistant-memory/spec.md`).

## What to build

Fill the reserved `memory` node kind and `recalled_from` edge: a value the user accepted from a memory item ("Used
before: 7% · project A ›") links to that item, so `why(node)` can say where an offered value came from. A memory
item is only ever offered, never applied silently (COPILOT-1), so the edge exists only after the user accepts it.

## Acceptance

- `why` on an accepted remembered value names the memory item and its source project or run.
- Retiring a memory item does not make the accepted value stale; the value keeps its own stamp.
- One PR within PR-SIZE-1.

## Comments
