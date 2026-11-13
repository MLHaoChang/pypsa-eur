# Campus and site-scene checks on the graph

Status: done
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: sonnet.
Gated: GK B; UI parts need the UX reviewer's OK.
Blocked by: 02, and #1295 merged (P7).

## What to build

Move the person-facing checks in the spec survey's "Campus, site scene and other" table onto `staleness`: the campus
electrical study and library hashes (`campus_electrical_service.py`), `CampusDipsRestartSection`'s `outOfDate`,
`DcArchitectureSection`'s key compare, the site binding uid checks (`site_bindings.stale_keys`,
`site_scene/associations._stale`) and the assistant task brief (`assistant_tasks._stale`). Leave the cache keys and
the checks the spec marks "leave alone".

## Acceptance

- Each moved check reads its verdict from the graph and keeps its current wording.
- One PR within PR-SIZE-1.

## Comments

### 2026-11-11: triage

Checked on master: #1295 is merged (the P7 condition) and 02 is done; the checks named still read their own staleness. Verdict: ready-for-agent.

- Blocked by: none.
- Likely files: `campus_electrical_service.py`, `site_bindings.py`, `site_scene/associations.py`, `assistant_tasks.py`; client `CampusDipsRestartSection`, `DcArchitectureSection`. Reviewers: GK B plan first, UX for the client part.

The acceptance in the body stands. Add: [ ] a test per moved check asserts the same wording before and after.

### 2026-11-12: built

Moved onto `provenance_graph.pins` (a `Pin(label, recorded, read)` on a `derived_from` edge; a read that raises or returns `UNREADABLE` is stale): `campus_electrical_service` `_stale`, `_library_stale`, `_milp_stale` (so `_least_cost_fresh`), `_grid_code_changed`, `_dips_out_of_date`; `site_bindings.stale_keys`; `site_scene/associations._stale`; `assistant_tasks._stale`. Left alone: the hash functions that produce the recorded values, the cache keys (time aggregation, adequacy, previews), the "leave alone" rows. `CampusDipsRestartSection` renders the backend's `out_of_date` list, so it has no client change. `DcArchitectureSection`'s key is in-memory UI state with nothing recorded on the server: left alone (no UI part, so no UX plan). Tests: `tests/test_provenance_graph_pins.py`.

### 2026-11-12: GK B plan GO, conditions P1–P8

GK B (session_01G7qCukZouPBT6iQi3vk3jy) gave GO. The DC compare stays alone (client state, nothing recorded on the server).

- **P1** `pin_verdict` with no pins reads `uncompared`; each caller maps it to today's answer (dips with no units: empty list). Tested.
- **P2** Fail-closed changes are named in the PR body, each with a test. No fail-open is kept to stay equal.
- **P3** An absent record keeps today's answer; a record that exists but lacks a pinned field breaks the edge (`not recorded`). One test per check family.
- **P4** The dst walk is skipped only for a `derived_from` edge that records a `Pin`; a test shows a compared edge whose dst is itself stale reads fresh from the pin.
- **P5** `why`/explain emit a pin's label and recorded value only (bytes as sha256, NaN as text); JSON-safe under `allow_nan=False`.
- **P6** `read()` runs where today's compare ran; no new IO under a lock.
- **P7** Mutants of `compare_pin`, the dst walk and one pin per moved check; each killed by a test named in the PR body. The `_PROVENANCE_STORE` pin test is in.
- **P8** The dips and MILP moves change only the stale compare: no note to GK A needed.

### 2026-11-12: GK B NOT OK @198107cc, K1–K5 addressed

K1 network-digest raise → stale tested; K2 grid-code, site-binding and association raises → stale tested and listed in the PR body; K3 `assistant_tasks._stale` stops at the first broken pin (requirements thread not read once the fingerprint is broken), tested; K4 `tests/test_provenance_graph_sweep.py` compares money and commercial verdicts and walks with a dump written on master `432655af` (byte-equal); K5 `pin_verdict` refuses duplicate labels; `why` reports a pin edge's own state.
