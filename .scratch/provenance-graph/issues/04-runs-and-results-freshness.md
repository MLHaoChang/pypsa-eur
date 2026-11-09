# Runs and results freshness on the graph

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B; UI parts need the UX reviewer's OK.
Blocked by: 02.

## What to build

`run` nodes and `computed_in` edges for solves and load flows, carrying the input fingerprint. Move the checks in the
spec survey's "Runs and results" table onto `staleness`: one fingerprint instead of three
(`run_gates.fingerprint`, `run_envelope._fingerprint`, `run_evidence._input_fingerprint`), `flow_service`'s
freshness by kind, `dispatch_status.has_fresh_dispatch`, `edit_card._results_stale`, `chat_tools._with_freshness`,
and the client's `useStaleRunKinds` and `ReserveCommitments` reads.

## Acceptance

- The header sentence and the chat freshness stamp agree with the run's verdict for each kind.
- Unreadable fingerprint reads stale.
- One PR within PR-SIZE-1; the plan GO may split the client part off.

## Comments
