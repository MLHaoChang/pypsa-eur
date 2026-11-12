# Memory items as graph nodes

Status: done (PR: provenance-graph 07)
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
- Retiring or editing a memory item does not make the accepted value stale; the value keeps its own stamp.
- The Verdict shows such a value in its own state ("from memory v<n>, user-approved"), never fresh against a project
  input (P1).
- One PR within PR-SIZE-1.

## Comments

### 2026-11-09 (~19:35 CET): memory-side contract, from GK B

GK B relayed this from the assistant-memory plan (#1305, session_01G7qCumE33c6N9CfRbBAj3f) so both sides match:

- When the user accepts a parameter chip, the memory side writes
  `provenance.record(status="customised", source="memory:<id>@v<n>", approved_by, approved_at, replaces)`
  (memory slice 03). It also exposes `memory_store.node(id, version)`, which returns the summary, kind, owner scope and evidence.
- Versions are immutable and confirmed items are never deleted, so `mem_<id>@v<n>` always resolves.
- This slice builds the `memory` node and the `recalled_from` edge from those two. Retiring or editing the item does
  not make the value stale.
- Under P1 the Verdict shows the value in its own state ("from memory v<n>, user-approved"), never fresh against a
  project input.

### 2026-11-11: triage

Checked on master: `memory_store.py` has no `node(id, version)` yet and `memory` / `recalled_from` are still reserved in `provenance_graph/types.py`. The contract from GK B (comment below) needs assistant-memory 03 (open as #1347). Verdict: ready-for-agent, waiting for it.

- Blocked by: assistant-memory 03 (#1347).
- Likely files: `services/provenance_graph/`, `services/memory_store.py`, tests. Reviewer: GK B.

The acceptance in the body stands.

### 2026-11-12: GK B plan GO, conditions

P1 fail-closed source parse in one reader (`memory.recall_of`); P2 list every writer that can write a `memory:` source (only `memory_parameters.apply`; the other approved writers build `upload:`/`pdf:`/reference ids); P3 a later edit ends the recall (`mark_customised` drops `approved_by`/`approved_at`, so no edge); P4 item shown only as the viewer may read it, else "not visible to you"; P5 an unreadable version says "memory item could not be read", state stays `from_memory`; P6 `from_memory` replaces only `fresh`; P7 byte-identity without memory sources; P8 every recorded field is read through `money.add_asset`; P9 new state `from_memory`: no frontend consumer of the why verdict, catalogue text updated. Mutants: drop the approved_by check; drop the approved_at check; remove recalled_from from walked; from_memory wins over stale; unresolvable version raises; skip the visibility check.
