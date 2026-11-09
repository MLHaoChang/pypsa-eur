# Runs and results freshness on the graph

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B; UI parts need the UX reviewer's OK.
Blocked by: 02. Send the slice plan to GK B before building (P4).

## What to build

`run` nodes and `computed_in` edges for solves and load flows, carrying the input fingerprint. Move the checks in the
spec survey's "Runs and results" table onto `staleness`: one wrapper instead of three
(`run_gates.fingerprint`, `run_envelope._fingerprint`, `assistant_tasks._fingerprint`; P4: no stored digest changes,
`_network_input_fingerprint` byte-identical with a pin test), `flow_service`'s
freshness by kind, `dispatch_status.has_fresh_dispatch`, `edit_card._results_stale`, `chat_tools._with_freshness`,
and the client's `useStaleRunKinds` and `ReserveCommitments` reads.

## Acceptance

- The header sentence and the chat freshness stamp agree with the run's verdict for each kind.
- Unreadable fingerprint reads stale.
- One PR within PR-SIZE-1; the plan GO may split the client part off.

## Comments
