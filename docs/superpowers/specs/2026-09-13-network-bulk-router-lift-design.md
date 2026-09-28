# Network bulk-update router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift the `PATCH /_bulk` cluster out of `routers/network.py` into
`services/network_bulk.py`. Strictly behaviour-preserving. First cut of the
`network.py` god-file chapter after the study / results surface polish.

## Problem

`routers/network.py` is still ~2380 LOC. After Phase 4 (pure helpers) and
Phase 5 (time-axis sibling), the deepest remaining function is `bulk_update`
(~270 LOC) plus its coerce / finite-default helpers (~200 LOC). The ~80 CRUD
routes stay put by design (`_STAYS` in `test_network_facade_surface.py`).

## Design

Same house style as the study / lost-load lifts:

```python
@router.patch("/_bulk")
def bulk_update(body: dict) -> dict:
    """…docstring unchanged…"""
    return apply_bulk_update(body)
```

| lives in | owns |
|---|---|
| `routers/network.py` | FastAPI decorator, thin handler, re-export façade |
| `services/network_bulk.py` | `_COMPONENT_ATTRS`, `_FINITE_DEFAULT_BOUNDS`, `_finite_input_meta`, `_finite_bound_default`, `_bool_input_default`, `_coerce_bulk_value`, `apply_bulk_update` |

Service may raise `HTTPException` (same as study runners). Never import
`routers.*`. Router re-exports the helpers as identical objects so existing
imports and bite comments stay valid.

## Proof

* `tests/test_bulk_update.py`, `test_nonfinite_bounds.py`, `test_nonfinite_inputs.py`,
  and includes-outages bulk cases — failing set unchanged.
* Extend `tests/test_network_facade_surface.py` `_MOVED` + `_LIFTED_HANDLERS`
  thin-handler tripwire.
* `ruff` F821 on `services` / `routers`.

## Out of scope

* Profile upload / template routes.
* Time-axis (already Phase 5).
* CRUD factory / `_STAYS` helpers.
* Deduplicating the coerce loop that is still inlined inside `apply_bulk_update`
  (the MERGE NOTE keeps `_coerce_bulk_value` as a sibling, not a call site).
* Consolidating `_COMPONENT_ATTRS` with `attribute_catalog` maps
  (documented intentional duplicate).
