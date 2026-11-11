# "Why this number": the calculation-metric target (03b)

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B.
Blocked by: none.

## What to build

The calculation-metric target of the `why_this_number` tool, split out of issue 03 by GK B (42): it reads assurance
evidence records, which need their own access and binding design. Metrics reuse `harness.calculations.source` and
`harness.fact_identity.window` for their `derived_from` steps. `computed_in` stays uncompared until slice 04.

The plan is owed to GK B before any code.

## Acceptance

- A metric target walks to the facts it was computed from; anything not compared reads `uncompared`, never fresh.
- The tool still reads only (COPILOT-1).
- Conditions X1–X7 of issue 03's plan GO apply.
- One PR within PR-SIZE-1.

## Comments
