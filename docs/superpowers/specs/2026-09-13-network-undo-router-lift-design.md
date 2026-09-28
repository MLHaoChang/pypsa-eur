# Network undo router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift `_push_undo_snapshot`, `undo_info`, and `undo_last` out of
`routers/network.py` into `services/network_undo.py`. Behaviour-preserving.
Follow-on to #33 (profiles sibling).

## Problem

After bulk (#32) and profiles (#33), `network.py` is ~1399 LOC. The deepest
non-CRUD cluster left is the undo stack (~120 LOC): middleware push + info GET
+ undo POST. Stack storage already lives in `services/undo_service.py`; the
router still owns the netcdf / user-ts capture and restore path.

## Design

Bulk-style (not a sibling router — only two routes + one middleware helper):

```python
@router.get("/undo/info")
def undo_info():
    return get_undo_info()

@router.post("/undo")
def undo_last():
    return apply_undo()

# main.py middleware keeps importing this name from routers.network
_push_undo_snapshot = push_undo_snapshot  # identical re-export
```

| lives in | owns |
|---|---|
| `routers/network.py` | FastAPI decorators, thin handlers, re-export of `_push_undo_snapshot` |
| `services/network_undo.py` | `push_undo_snapshot`, `get_undo_info`, `apply_undo` |
| `services/undo_service.py` | stack / coalesce / byte budget (unchanged) |

Service uses `user_timeseries` helpers and `PyPSAService` directly — never
`routers.*`. Study precheck stays inside `apply_undo` (before `pop`); retarget
`test_adequacy_swap_guard_callsites` at the service so the scan is not vacuous.

## Proof

* Existing undo / unsaved-results / per-project undo suites — failing set unchanged.
* Facade: `_push_undo_snapshot` moves from `_STAYS` to `_MOVED`; thin-handler pins for `undo_info` / `undo_last`.
* Update profiles / time-axis surface stays that still require `_push_undo_snapshot` in `routers.network`.

## Out of scope

* CRUD factory / bus specials.
* Changing coalesce window, MAX_STEPS, or MAX_BYTES.
* Moving `undo_service` itself.
