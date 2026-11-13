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

### 2026-11-13: GK B plan GO for 04a (session 53)

Sliced: **04a** (backend: one fingerprint wrapper, `run` nodes and `computed_in` edges, the flow, edit card and chat
stamp read the verdict) and **04b** (reserves `reserve_inputs_changed_since_solve` onto a `run:reserves` node, and the
client wording for `uncompared`, with its own plan and UX review).

Decisions:
- D1 (a): optimisation freshness includes the input fingerprint; a value edit keeping the dispatch shape reads stale.
- D2 (a): `uncompared` goes on the wire (`freshness_by_kind`, `freshness`), and `FlowFreshness` gains it. 04b gives it
  its own header wording with UX: a blank header reads as current.
- D3 (a): a study record with no captured revision reads uncompared, never fresh.

Conditions: V1 one wrapper behaviour (`or None` everywhere, tested), each alias keeps its ctx source, P4 hex pins;
V2 a recorded None against a current None is never unchanged or fresh, one test per touched comparer, red under the
None == None mutant; V3 a "before" column per consumer, no row moves toward fresh, how each boolean consumer maps
uncompared, `run_gates._fresh` treats None as not fresh; V4 one read, an edit landing right after it (#1384 N1);
V5 measure the flow's cost on a full-year network, cache on (ctx identity, dirty revision) if over ~50 ms, with an
invalidation test; V6 every consumer of `freshness_by_kind` handles `uncompared`, tsc, route-parity and
control-matrix checks; V7 no stored digest or meta key changes; V8 `solve_freshness` callers that saw the raise;
V9 mutants on `runs.py` and each migrated reader, including the graph propagation.

### 2026-11-13: 04a built (session_01G7qCumghRZMeJ12hfk8vfU)

04a is on `claude/provenance-graph-04a-runs-freshness`. The PR body records V1–V9 and the follow-ups: the readers still calling `_network_input_fingerprint` directly. 04b (the `run:reserves` node, and the client wording for `uncompared` with UX) is still open, so this issue stays open until it lands.
