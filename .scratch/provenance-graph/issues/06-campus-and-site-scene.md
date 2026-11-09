# Campus and site-scene checks on the graph

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: sonnet.
Gated: GK B; UI parts need the UX reviewer's OK.
Blocked by: 02.

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
