# COPT / FMEA-modes router lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-copt-modes-router-lift-design.md`

## Steps

1. Add `services/adequacy/copt_endpoint.py` with `build_copt_payload` and `build_fmea_modes_payload` (bodies lifted from `routers/results.py`; inject `get_lock` / `sweep_record`; raise `ValueError` for 422; return `None` for 204).
2. Thin `get_copt` / `get_fmea_modes` to network + state + builder + HTTP map.
3. Tripwire: `tests/test_copt_endpoint_facade_surface.py`.
4. Update `.cursor/skills/gui-backend-change/SKILL.md`.
5. Proof: existing copt/modes/activity suites; facade tripwire; `ruff` F821 on touched modules.

## Done when

- Both handlers ≤ 40 LOC; endpoint never imports `routers.*`.
- Handler names remain on `routers.results`.
- Behaviour-preserving: no payload key / status-code changes.
