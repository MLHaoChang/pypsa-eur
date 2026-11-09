# Design plan for the provenance graph

Status: done (#1302)
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

### 2026-11-09 (~18:25 CET): GK B plan GO

GK B (session_01G7qCunT8cyHdQxBmNV35J8) gave the plan GO, reviewed from the plan message. Rulings on the three questions:

- (a) The module goes in `pypsa-gui/backend/services/provenance_graph/`, since it is domain code. The harness gets only slice 03's thin read tool.
- (b) `commercial.provenance` gets its own later slice. Until then a Verdict lists each input the graph does not model as `uncompared`, never as fresh (#1170). Slice 02's card must not read "fresh" for a cost whose commercial input is not in the graph.
- (c) Cache keys stay out. A test holds that no cache key reads a graph node, so a cache never invalidates on graph shape.

Conditions. Each slice's PR is checked against them:

- **P1 Fail closed, no vacuous fresh.** Unreadable counts as stale. A node with no recorded input compared nothing, so it gets its own state (`unanchored`/`uncompared`), never fresh (#1170). The Verdict names each uncompared field.
- **P2 Legacy writers stay covered** (#1281 T5-3): Library reference applies (`customised` + `approved_by`) and pack prices that recorded no input. Slice 02 tests each.
- **P3 Byte-equal on the wire.** Slice 02 keeps the `stale`/`provenance` shape at `routers/network.py:802`/`:818`. A test compares the old `provenance.staleness` output with the graph-backed output, row for row, on an edited network and on an in-memory network, not only on a fixture loaded by the same reader (#1175 E1). `provenance._verdict` moves and is not copied. A guard test checks that no migrated reader calls `provenance.staleness`.
- **P4 No stored digest changes.** Slice 04's "one fingerprint" may only unify the wrappers `run_gates.fingerprint`, `run_envelope._fingerprint` (its `or None`) and `assistant_tasks._fingerprint`. `_network_input_fingerprint`'s value stays byte-identical, with a pin test. Each wrapper keeps its exception→None and ""→None behaviour. `run_envelope`'s freshness chain (grant, then settle) and every grant, envelope and manifest digest stay unchanged. Slice 04's plan goes to GK B before building.
- **P5 Read-only and consistent.** The graph never caches across revisions: it rebuilds per query, or keys the cache on every revision it reads. A query that reads several files that share no lock re-reads, and settles stale on a change (#1083).
- **P6 Slice 03 follows the harness contract:** README, inventory, route parity and control matrix `--check`; an error-kind fragment for any new kind; a project-scoped read whose `project_id` is the active project, never another owner's records. `why()` puts its verified-outcome block first and caps a long walk with a `truncated` total.
- **P7 Slice 05's client stops regex-parsing reasons.** The server's typed reason is the only source, and a 409 shape change is checked against every frontend consumer (#1261 R1). Wording and formatters belong to the UX reviewer. Slice 06 (site scene, `assistant_tasks._stale`) waits for #1295 to merge.
- **P8** Each slice's PR body lists the hand-rolled checks it migrates and the ones it leaves alone. Each per-edge-kind test (fresh/changed/unreadable) gets a mutant of the edge's comparison, and the tests kill it.

Each slice sends GK B its plan delta and its PR.
