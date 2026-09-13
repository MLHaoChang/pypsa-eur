# Network global-constraints lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift `create_global_constraint`, `update_global_constraint`, and
`delete_global_constraint` out of `routers/network.py` into
`services/network_global_constraints.py`. Behaviour-preserving. Follow-on to
#36 (line specials).

## Problem

After bulk / profiles / undo / buses / lines, the deepest non-factory remaining
cluster in `network.py` is the global-constraint mutators (~80 LOC). GET stays
as a one-line `_get_component` wrapper. Update must keep using the shared MERGE
helper and must **not** go through `_update_component` (carrier injection).

## Design

```python
@router.post("/global_constraints", status_code=201)
def create_global_constraint(body: GlobalConstraintCreate):
    return apply_create_global_constraint(body)

@router.put("/global_constraints/{name}")
def update_global_constraint(name: str, body: GlobalConstraintCreate):
    return apply_update_global_constraint(
        name, body, merge_partial_update=_merge_partial_update,
    )

@router.delete("/global_constraints/{name}", status_code=204)
def delete_global_constraint(name: str):
    apply_delete_global_constraint(name)
```

| lives in | owns |
|---|---|
| `routers/network.py` | FastAPI handlers; inject `_merge_partial_update` |
| `services/network_global_constraints.py` | `apply_create_*`, `apply_update_*`, `apply_delete_*`, `_GC_OPTIONAL` |

## Proof

* chat_tools GlobalConstraint dispatch / partial-PUT mitigation — failing set unchanged.
* Facade `_LIFTED_HANDLERS` pins for the three mutators.
* `ruff` F821.

## Out of scope

* CRUD factory / `_merge_partial_update` itself.
* Moving `get_global_constraints` (already one line).
* Changing partial-PUT semantics.
