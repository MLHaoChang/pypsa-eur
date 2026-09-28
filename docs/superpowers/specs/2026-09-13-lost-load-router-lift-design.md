# Lost-load router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation
**Scope:** lift `get_lost_load` out of `routers/results.py` into `services/results/lost_load.py` (`compute_lost_load`). Strictly behaviour-preserving. Follow-on to PR #29.

## Problem

After the study-controller and COPT/modes lifts, the largest remaining fat results GET that still does real work in `routers/results.py` is `get_lost_load` (~74 LOC): capture read, VoLL recovery, bus carriers, shed-hours, optional snapshot window.

## Design

Phase-2 style (same as curtailment / loads):

```python
@results_router.get("/lost_load")
def get_lost_load(from_=..., to_=...):
    from services.results.lost_load import compute_lost_load
    payload = compute_lost_load(
        PyPSAService.get_network(),
        _state.get("last_lost_load"),
        from_, to_,
    )
    return Response(status_code=204) if payload is None else payload
```

Handler keeps Query params, capture lookup, HTTP map. Service never imports `routers.*`. Capture dict is injected.

## Proof

* Existing lost-load / range / adequacy HTTP suites — failing set unchanged.
* `test_results_facade_surface.py` `_LIFTED` entry + thin-handler tripwire.
* Handler name/params stay on `routers.results` for `chat_tools`.

## Out of scope

* `get_ac_pf_status` and other state readers.
* Behaviour / payload key changes.
* Abort DRY / study GET lock symmetry.
