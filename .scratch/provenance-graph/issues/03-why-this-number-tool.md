# "Why this number": the assistant's read tool over the graph

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B.
Blocked by: 02.

## What to build

A read-only assistant tool that takes a number the user points at (an asset field, an Economics cost, a Guided
ledger row, a calculation metric) and returns `why(node)`: each step's value, unit, source stamp, the edge to the next
step, and whether the step is current. Follow the harness contract (`pypsa-gui/backend/harness/README.md`) for the
tool's registration, catalogue entry, error kinds and route parity. Calculation metrics reuse
`harness.calculations.source` and `harness.fact_identity.window` for their `derived_from` steps.

## Acceptance

- "Why is this PV cost what it is?" walks to the pack price, the DC/AC ratio it was scaled by and each stamp, and
  says when the ratio has changed since.
- The tool reads only; it never changes a value (COPILOT-1).
- One PR within PR-SIZE-1.

## Comments
