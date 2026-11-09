# Design plan for the provenance graph

Status: ready-for-agent
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B (session_01G7qCunT8cyHdQxBmNV35J8). Send the plan to GK B and start no code before the plan GO.
Blocked by: none.

## Goal

A plan that GK B can say GO to, which pins the module, its types and its two queries, and names the slices
02–07 with their files and size. No code lands under this issue; its output is the plan text in `## Comments`, plus
any edit to the spec or the slice issues the plan GO asks for.

## The plan answers

1. **Where it lives.** Proposed: `pypsa-gui/backend/services/provenance_graph/` (types, builder, the two queries),
   with a thin read tool in the harness for slice 03 (`pypsa-gui/backend/harness/README.md` is the contract).
   It reuses `harness/calculations.py` (metric definitions and `source`), `harness/fact_identity.py` (window identity)
   and `services/asset_schema/provenance.py` (stamps and `staleness`). It writes nothing of its own.
2. **Types.** Frozen dataclasses for `Node` (kind, id, value, unit) and `Edge` (kind, from, to, recorded). Node kinds
   `input`, `derived`, `run`, `surface`, plus a reserved `memory`. Edge kinds `derived_from`, `priced_through`,
   `stamped_with`, `computed_in`, `shown_in`, plus a reserved `recalled_from`. Unknown kinds are refused.
3. **Building.** Which producer adds which nodes and edges, and when the graph is built: per request from
   `n.meta` and the study records, or cached per network version. Producers declare their edges next to the code that
   records them today, so nothing re-derives a relationship.
4. **Current value.** Who answers "what is this input now?" for each edge kind. For `priced_through` it is
   `provenance._verdict` (TCU-05's comparison) moved behind the graph, not copied. Unreadable means stale.
5. **`Verdict` and `Walk`.** Their exact fields, and how `staleNote` / `staleBody` in
   `frontend/src/components/ProvenanceChip.tsx` format a verdict, so the wording stays the same on every surface.
6. **Wire shape.** The `stale` field the routes return today (`routers/network.py:802`, `:818`) must keep its shape
   in slice 02, or the plan names every reader that changes with it.
7. **Slices.** Confirm or change 02–07 in the spec: files, the checks each moves (the spec's survey), the tests that
   pin each, and an estimate under 1,500 changed lines each (PR-SIZE-1). Say which cache keys (adequacy, time
   aggregation, previews) stay out.
8. **Tests.** A unit test per edge kind (fresh, changed, unreadable), a test that every migrated surface reads the
   graph and keeps no comparison of its own, and a test that the reserved kinds build an empty set today.

## Acceptance

- GK B's plan GO (or NO-GO with reasons) is recorded below under `## Comments`, with the date.
- The spec and the slice issues match the plan GO.

## Comments
