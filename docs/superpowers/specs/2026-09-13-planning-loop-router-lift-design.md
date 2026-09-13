# Planning-loop router lift — design

**Date:** 2026-09-13
**Status:** approved for implementation (continues the backend god-file decomposition)
**Scope:** lift `post_coupling_loop` and `post_margin_loop` out of
`routers/results.py` into `services/adequacy/`, restoring the Phase-2 thin-router
invariant that Solution FMEA undid. Strictly behaviour-preserving.

## Problem

Phase 2 of the god-file decomposition cut `routers/results.py` from 4,106 to
~911 lines by lifting arithmetic into `services/results/`. Solution FMEA (#5)
then added planning-loop HTTP controllers that grew the router back to **3,661
lines**. The two largest offenders:

| handler | LOC | shape |
|---|---:|---|
| `post_margin_loop` | 898 | validation + nested closures + worker thread |
| `post_coupling_loop` | 516 | same shape, energy-cap lever |

Both already call the pure controller in `services/adequacy/coupling.py`. What
remains in the router is request validation, the study record, `solve_at` /
`evaluate` / restore bindings, and the worker — none of which is HTTP-layer
work beyond the initial 422/409 mesh.

`services/chat_tools.py` resolves `post_coupling_loop` by importing it from the
router, so the **handler name, signature and request model stay on the router**.

## Design

### Thin handler, fat runner

```python
@results_router.post("/coupling_loop")
def post_coupling_loop(body: CouplingLoopRequest | None = None):
    _refuse_if_mesh_busy("coupling_loop")
    from routers.simulation import _state_update
    from services.adequacy.coupling_loop_runner import start_coupling_loop
    return start_coupling_loop(
        body,
        solver_state=_state,
        state_update=_state_update,
        publish_study=_publish_study,
    )
```

Same shape for the margin loop. Mesh refusal stays in the router (it is shared
study-mesh policy owned by the results router). Everything from the synchronous
422 set through `_publish_study` moves.

### Injected state, no router import

`services/adequacy/coupling.py` stays pure (no `_state`, no locks). The new
runners accept:

* `solver_state` — the live solver-state dict (today `_state`)
* `state_update` — today's `_state_update` callable
* `publish_study` — today's `_publish_study` (claims the mesh and starts the thread)

So the runners never import `routers.*`. Layering matches Phase 2/3/4.

### Constants and request models

Wire-facing constants (`LOOP_WARNING_V1`, `NEVER_BOUND_COPY_V1`,
`MARGIN_LOOP_PANEL_LABEL`, …) and pydantic request models move with the runners
and are **re-exported from `routers/results.py`** so existing test and
`chat_tools` imports keep working without edits (house rule: never edit an
existing test for a refactor).

### Proof

* AST identity of each moved block against the pre-cut source (text move, then
  structural check after dependency injection rewrites).
* Existing endpoint suites unchanged failing set:
  `tests/test_adequacy_coupling_endpoint.py`,
  `tests/test_adequacy_margin_loop.py`.
* New tripwire: handler LOC and that runners never import `routers`.

## Out of scope

* `post_mc` / frontier / sweep — same shape, separate cuts.
* Behaviour changes, including tempting copy edits in verdict strings.
* Touching `services/adequacy/coupling.py` (the pure controller).
