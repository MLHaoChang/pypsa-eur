# COPT / FMEA-modes router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation (continues the backend god-file decomposition)
**Scope:** lift `get_copt` and `get_fmea_modes` out of `routers/results.py` into `services/adequacy/copt_endpoint.py`. Strictly behaviour-preserving. Follow-on to PR #28.

## Problem

After the study-controller lifts (#27, #28), all five FMEA study POSTs are thin. The largest remaining FMEA work in `routers/results.py` is the sync adequacy GETs:

| handler | body ≈ | shape |
|---|---:|---|
| `get_copt` | ~121 | lock + fleet/residual + screening + payload |
| `get_fmea_modes` | ~49 | calls `get_copt()`, merges sweep rows, sorts |

`get_fmea_modes` calls `get_copt()` directly, so they move together. Pure engines in `services/adequacy/copt.py` stay untouched.

## Design

### Thin handler, fat endpoint builder (Phase-2 style — not a runner)

```python
@results_router.get("/copt")
def get_copt():
    from services.adequacy.copt_endpoint import build_copt_payload
    try:
        out = build_copt_payload(
            PyPSAService.get_network(),
            _state.get("solver_config"),
            get_lock=PyPSAService.get_lock,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if out is None:
        return Response(status_code=204)
    return out
```

No worker, no `publish_study`. HTTP mapping (422 / 204 / 200) stays in the router.

### Injected lock, no router import

`build_copt_payload(n, cfg, *, get_lock)` acquires the mutation lock around the same reads as today (`fleet_and_residual`, `must_take_generators`, `activity_summary`). Screening and payload assembly stay outside the hold.

`build_fmea_modes_payload(..., sweep_record=..., get_lock=...)` calls `build_copt_payload` (not the HTTP handler). The service never imports `routers.*`.

### Handler names stay on the router

`services/chat_tools.py` resolves `get_copt` / `get_fmea_modes` by name. Signatures stay empty-arg FastAPI handlers on `routers.results`.

### Proof

* Existing copt / modes / activity / includes_outages / profiled_units suites that call `R.get_copt` / `R.get_fmea_modes` — failing set unchanged.
* Tripwire: surface names; thin handler LOC; `copt_endpoint` never imports routers.
* Retarget any source-scan that assumes the COPT body lives in `results.py`.

## Out of scope

* Behaviour changes (VoLL messaging, sort key, abort-status merge).
* `get_lost_load` / abort DRY / GET lock symmetry (U1).
* Rewriting `copt.py` engines.
