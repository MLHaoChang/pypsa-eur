# Network bus-specials lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift `update_bus`, `delete_bus_cascade`, and `rename_bus` out of
`routers/network.py` into `services/network_buses.py`. Behaviour-preserving.
Follow-on to #33 (profiles) / #34 (undo, in flight).

## Problem

After bulk / profiles / undo, the deepest non-CRUD remaining cluster in
`network.py` is the bus specials: `update_bus` (~55 LOC, coord-change line
recompute), `delete_bus_cascade` (~25), `rename_bus` (~22). Plain
`create_bus` / `delete_bus` stay as one-line factory wrappers.

## Design

```python
@router.put("/buses/{name}")
def update_bus(name: str, bus: BusCreate):
    return apply_update_bus(name, bus, update_component=_update_component)

@router.delete("/buses/{name}/cascade", status_code=204)
def delete_bus_cascade(name: str):
    apply_delete_bus_cascade(name)

@router.post("/buses/{name}/rename")
def rename_bus(name: str, body: dict):
    return apply_rename_bus(name, body)
```

| lives in | owns |
|---|---|
| `routers/network.py` | FastAPI handlers, inject `_update_component` |
| `services/network_buses.py` | `apply_update_bus`, `apply_delete_bus_cascade`, `apply_rename_bus` |

`apply_update_bus` takes `update_component=` so the service never imports
`routers.*`. Geometry stay in `network_geometry._recompute_lengths_for_bus`.

## Proof

* `test_line_lengths` / `test_line_rescale` / chat-tools bus dispatch — failing set unchanged.
* Facade `_LIFTED_HANDLERS` pins for the three handlers.
* `ruff` F821.

## Out of scope

* CRUD factory / `create_bus` / `delete_bus` one-liners.
* Moving `_recompute_lengths_for_bus` (already in geometry).
* Changing rename / cascade semantics.
