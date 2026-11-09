# `commercial.provenance` cells on the graph

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B.
Blocked by: 02.

## What to build

`input` nodes for the `commercial.provenance` cells (`services/document_values_commercial.py:584` `provenance`,
`:653` `_live`), keyed `<target>:<entity>/<path>`, with `stamped_with` edges from their approval stamps. Costs that
read them stop listing them as `uncompared` (GK B ruling (b) in issue 01).

## Acceptance

- A cell whose item, period, tier or contract no longer exists reads stale, never fresh.
- Conditions P1, P3, P5 and P8 of the plan GO apply.
- One PR within PR-SIZE-1.

## Comments
