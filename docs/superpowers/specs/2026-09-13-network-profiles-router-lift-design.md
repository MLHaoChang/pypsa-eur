# Network profiles router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift load / generator / link profile routes (+ `_xlsx_response`,
`_apply_profile_upload`) out of `routers/network.py` into
`routers/network_profiles.py`. Behaviour-preserving. Follow-on to #32 (bulk).

## Problem

After the bulk lift, `network.py` is ~1894 LOC. The deepest remaining cluster is
the three profile blocks (~501 LOC) plus shared `_xlsx_response` /
`_apply_profile_upload`. CRUD stays put (`_STAYS`).

## Design

Phase 5 pattern (time-axis sibling):

* New `routers/network_profiles.py` owns its `APIRouter` and the moved handlers.
* `routers/network.py` `include_router`s it and re-exports every handler name
  (`chat_tools` imports them by name).
* Sibling never imports `routers.network` (no cycle). Shared pure helpers come
  from `services/` (`profile_shapes`, `user_timeseries`, `transient_rows`, …).

| moves | stays |
|---|---|
| `get_*_profiles`, `download_*_template`, `upload_*_profile`, `aggregate_load_profile` | ~80 CRUD routes / factory |
| `_xlsx_response`, `_apply_profile_upload`, `_LOAD_SHAPES` | undo stack, attribute catalog, bus/line specials |

## Proof

* Existing profile / upload / nonfinite-bounds suites — failing set unchanged.
* New `tests/test_network_profiles_surface.py` (mirror time-axis).
* Update facade `_STAYS` + time-axis stays list so `_xlsx_response` /
  `_apply_profile_upload` are allowed to leave.

## Out of scope

* CRUD factory split.
* Undo stack.
* Deduplicating profile GET boilerplate across load/gen/link.
