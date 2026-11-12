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
