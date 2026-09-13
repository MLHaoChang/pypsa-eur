# MC / frontier / FMEA-sweep study router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation (continues the backend god-file decomposition)
**Scope:** lift `post_mc`, `post_frontier`, and `post_fmea_sweep` out of `routers/results.py` into `services/adequacy/{mc_loop,frontier_loop,fmea_sweep}_runner.py`, restoring the thin-router invariant for the remaining FMEA study controllers. Strictly behaviour-preserving. Follow-on to PR #27.

## Problem

After the planning-loop lift (#27), `routers/results.py` is still 2,190 lines. The largest remaining study POSTs are:

| handler | LOC | shape |
|---|---:|---|
| `post_mc` | 274 | validation + snapshot under lock + worker + publish |
| `post_frontier` | 95 | same shape |
| `post_fmea_sweep` | 85 | same shape |

`post_mc` already calls pure engines in `services/adequacy/mc.py` / `elcc.py`; frontier and sweep call `frontier.py` / `sweep.py` / `stress.py`. What remains in the router is request validation, the study record, and the worker — same seam as the coupling/margin lifts.

`services/chat_tools.py` imports the request models and handlers from `routers.results`, so the **handler name, signature and request model stay on the router** (re-exported).

## Design

### Thin handler, fat runner

```python
@results_router.post("/mc")
def post_mc(body: McRequest | None = None):
    _refuse_if_mesh_busy("mc")
    from services.adequacy.mc_loop_runner import start_mc
    return start_mc(body, solver_state=_state, publish_study=_publish_study)
```

Mesh refusal stays in the router. Everything from the synchronous 422 set through `_publish_study` moves.

### Injected state, no router import

The runners accept:

* `solver_state` — today's `_state` (solver_config, last_reserve_margin)
* `publish_study` — today's `_publish_study`
* `state_update` — today's `_state_update` (frontier and sweep only; closing restore). MC does not take it.

The runners never import `routers.*`. Pure engines stay untouched.

### Constants and request models

`McRequest` / `FrontierRequest` / `FmeaSweepRequest` (and any study-only wire constants) move with their runners and are **re-exported from `routers/results.py`**.

### Proof

* Existing `tests/test_adequacy_{mc_endpoint,frontier,sweep,fmea_sweep*}.py` failing sets unchanged.
* Tripwires: thin handler LOC; runners never import routers; surface names stay on the router (`test_mc_study_facade_surface.py`, `test_study_runners_facade_surface.py`).
* F1n2/F1j-style source scans retargeted at the runners when a results.py scan goes vacuous.

## Included follow-on cuts

* `post_frontier` → `services/adequacy/frontier_loop_runner.py` (`start_frontier`, `FrontierRequest`); injects `state_update` (closing restore).
* `post_fmea_sweep` → `services/adequacy/fmea_sweep_runner.py` (`start_fmea_sweep`, `FmeaSweepRequest`); injects `state_update`.

## Out of scope

* Behaviour changes.
