# A finite check on the worked-back pack price

Status: needs-triage
Type: task
Spec: `.scratch/provenance-graph/spec.md`

Model tier: opus.
Gated: GK B.
Blocked by: none.

## What to build

Follow-up from the issue 03 landing (#1339). `services/provenance_graph/explain.py` `_pack_price` returns
`value / scale` after checking that the scale is a finite, non-zero number and the value is present. The quotient
itself is not checked: a very small recorded scale can overflow it to `inf`. Check the result and return
`not_established` when it is not finite (X3 of issue 03: the pack price is never inf or NaN).

## Acceptance

- A value and scale whose quotient is not finite read `not_established`, with a reason; one test.
- The mutant that drops the check is killed.

## Comments
