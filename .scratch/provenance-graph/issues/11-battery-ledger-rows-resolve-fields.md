# Battery ledger rows: test the fields they walk, not only the key

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B.
Blocked by: none.

## What to build

Follow-up from the issue 03 landing (#1339). The battery rows of `services/study/asset_view.py` `LEDGER_FIELDS` are
covered only by the test that every ledger key resolves (X4 of issue 03). No test walks a battery row through
`provenance_graph.explain.explain_ledger_row` on a network with a battery and checks the fields and values it
returns.

## Acceptance

- A test walks each battery ledger row on a network that has the battery and checks the asset fields it compiles
  to, their values against the network columns, and the verdict state.
- A battery row on a network without the battery refuses (`why_target_unknown`), never a 500.

## Comments
