# Network CRUD-factory lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift the generic CRUD factory out of `routers/network.py` into
`services/network_crud.py`. Behaviour-preserving. Follow-on to #37 (global
constraints). Reopens the factory split previously left in `_STAYS`.

## Problem

After bulk / profiles / undo / buses / lines / global constraints, `network.py`
is ~1108 LOC. The remaining depth is the generic CRUD factory (~370 LOC):
serialize / get / meta / create / merge / series detach-reattach / rename /
update / delete. Facade `_STAYS` currently pins the seven public helpers on the
router; that pin is lifted here.

## Design

```python
# routers/network.py — identity re-exports (import surface unchanged)
from services.network_crud import (  # noqa: F401
    _serialize_component,
    _get_component,
    _meta_payload,
    _create_component,
    _merge_partial_update,
    _update_component,
    _delete_component,
    # internals also re-exported only if anything still imports them;
    # otherwise they stay private to the service.
)
```

| lives in | owns |
|---|---|
| `services/network_crud.py` | all 12 factory helpers (public + internals) |
| `routers/network.py` | ~80 thin CRUD routes; identity re-exports; injects into bus/GC specials |

`project_network.py` and `chat_tools` keep importing from `routers.network`.
Bus/GC injects continue: `update_component=_update_component`,
`merge_partial_update=_merge_partial_update`.

`_filter_transient_names` stays as the Phase-5 alias on the router (already
defined in `services.transient_rows`).

## Proof

* Facade: move the seven `_STAYS` names into `_MOVED` → `services.network_crud`.
* Profiles / time-axis surface: stop asserting `__module__ == "routers.network"`
  for factory helpers; require they remain exported from `routers.network`.
* `test_lost_load_and_custom_attrs` / adequacy merge / chat_tools update dispatch.
* `ruff` F821.

## Out of scope

* Changing remove+add / partial-PUT / series-preserve semantics.
* Moving the thin CRUD routes themselves.
* Further splitting the factory.
