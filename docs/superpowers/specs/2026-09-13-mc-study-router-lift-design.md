# MC study router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation (continues the backend god-file decomposition)
**Scope:** lift `post_mc` out of `routers/results.py` into `services/adequacy/mc_loop_runner.py`, restoring the thin-router invariant for the remaining largest FMEA study controller. Strictly behaviour-preserving. Follow-on to PR #27.

## Problem

After the planning-loop lift (#27), `routers/results.py` is still 2,190 lines. The largest remaining study POST is:

| handler | LOC | shape |
|---|---:|---|
| `post_mc` | 274 | validation + snapshot under lock + worker + publish |
| `post_frontier` | 95 | same shape (later cut) |
| `post_fmea_sweep` | 85 | same shape (later cut) |

`post_mc` already calls pure engines in `services/adequacy/mc.py` / `elcc.py`. What remains in the router is request validation, the one locked snapshot, the study record, and the worker — same seam as the coupling/margin lifts.

`services/chat_tools.py` imports `McRequest` and `post_mc` from `routers.results`, so the **handler name, signature and request model stay on the router** (re-exported).

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

The runner accepts:

* `solver_state` — today's `_state` (solver_config, last_reserve_margin)
* `publish_study` — today's `_publish_study`

No `state_update` is needed (MC never mutates solver settings). The runner never imports `routers.*`. Pure engines in `mc.py` / `elcc.py` stay untouched.

### Constants and request models

`McRequest` (and any MC-only wire constants) move with the runner and are **re-exported from `routers/results.py`**.

### Proof

* Existing `tests/test_adequacy_mc_endpoint.py` failing set unchanged.
* New tripwire: thin handler LOC; runner never imports routers; surface names stay on the router.
* F1n2-style source scans that named `post_mc` in `routers/results.py` still resolve the thin handler (ALLOWED exemption stays valid).

## Out of scope (this PR)

* `post_frontier` / `post_fmea_sweep` — same shape, separate or follow-on commits if time allows.
* Behaviour changes.
