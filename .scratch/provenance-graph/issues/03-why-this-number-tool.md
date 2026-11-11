# "Why this number": the assistant's read tool over the graph

Status: done (#1339; PG-03b metric target is a follow-up)
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

### GK B (42), 2026-11-10: plan GO with changes

Split: this PR ships the asset, Economics and ledger targets. The calculation-metric target goes to a follow-up,
03b, with its own plan: it reads assurance evidence records, which need their own access and binding design, and
`computed_in` stays uncompared until slice 04.

- X1 Read-only: the network's meta, every touched column and `user_ts_meta` are byte-equal before and after,
  through both the tool and the route. No store or record write, and no revision bump.
- X2 States: a step reads fresh only when its comparator compared the edge's recorded value with the current one;
  anything uncompared reads `uncompared`, never fresh (#1170). The Broken wire lists recorded vs current only for
  the fields it compared and names the rest as uncompared. One test per state (fresh, stale, broken, uncompared).
- X3 Arithmetic: the implied pack price (value ÷ recorded scale) reads `not_established` when the scale is missing,
  zero, non-finite or not a number. It is never inf, NaN or 0, and it is labelled as arithmetic. One test per bad
  scale. Units and `currency_year` come from the stamp; a stamp that lacks one names it, never a filled default.
- X4 Targets: an unknown component, name or field, or a field with no provenance, is a typed refusal
  (`why_target_unknown`, 404/422), never a 500. `LEDGER_FIELDS` tested both ways (every key resolves, an unknown
  key refuses). `capital_cost` walks both money fields, and its value equals the network column.
- X5 Size: the step count is capped and a capped result is marked truncated; it never reads as complete. The
  outcome block comes first in the tool result.
- X6 Route and tool: the tool is tested through the real dispatcher signature. At the trial merge: the route-policy
  trio, the smoke `--check`s (inventory, route_parity, control_matrix), `test_tool_error_kind_manifest` and
  `ChatPanel.manifest.test.tsx`.
- X7 Mutants: hand-mutate each compare in the state classifier, the scale guard, the target refusal and the
  ledger map, plus `scripts/coord/mutants.py` on `explain.py`. Survivors and their reasons go in the PR body.
