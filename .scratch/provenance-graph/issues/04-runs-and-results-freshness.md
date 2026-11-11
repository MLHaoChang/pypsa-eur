# Runs and results freshness on the graph

Status: ready-for-agent (Opus; GK B plan first, UX for UI parts)
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

### 2026-11-11: triage

Checked on master: `provenance_graph/types.py` reserves `run` and `computed_in` ("built by the runs slice (04)"); 01-03 are done (#1302, #1311, #1339). Verdict: ready-for-agent.

- Blocked by: none (02 is done).
- Likely files: `services/provenance_graph/`, `run_gates.py`, `run_envelope.py`, `assistant_tasks.py`, `flow_service`, `dispatch_status.py`, `edit_card.py`, `chat_tools.py`; client `useStaleRunKinds`, `ReserveCommitments`. Reviewers: GK B plan first (P4), UX for the client part.

The acceptance in the body stands. Add: [ ] a pin test shows `_network_input_fingerprint` and every stored digest byte-identical (P4).
