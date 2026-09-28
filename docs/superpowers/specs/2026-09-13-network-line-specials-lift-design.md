# Network line-specials lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift `recalculate_line_lengths` and `rescale_impedances` out of
`routers/network.py` into `services/network_lines.py`. Behaviour-preserving.
Follow-on to #35 (bus specials).

## Problem

After bulk / profiles / undo / buses, the deepest non-CRUD remaining cluster in
`network.py` is the line specials: `recalculate_line_lengths` (~38 LOC) and
`rescale_impedances` (~27 LOC). Geometry primitives already live in
`services/network_geometry.py`; these handlers own lock + mutation + changelog.

## Design

```python
@router.post("/lines/recalculate_lengths")
def recalculate_line_lengths():
    return apply_recalculate_line_lengths()

@router.post("/lines/rescale_impedances")
def rescale_impedances(req: ImpedanceRescaleRequest):
    return apply_rescale_impedances(req)
```

| lives in | owns |
|---|---|
| `routers/network.py` | FastAPI handlers |
| `services/network_lines.py` | `apply_recalculate_line_lengths`, `apply_rescale_impedances` |
| `services/network_geometry.py` | haversine / `_impedance_preview` (unchanged) |

## Proof

* `test_line_lengths` / `test_line_rescale` — failing set unchanged.
* Facade `_LIFTED_HANDLERS` pins for both handlers.
* `ruff` F821.

## Out of scope

* CRUD factory / plain line create/update/delete.
* Moving geometry helpers (already Phase 4).
* Changing consent / preview semantics of the rescale flow.
