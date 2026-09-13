# Lost-load router lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-lost-load-router-lift-design.md`

1. Add `services/results/lost_load.py::compute_lost_load`.
2. Thin `get_lost_load` in `routers/results.py`.
3. Extend `_LIFTED` in `test_results_facade_surface.py`.
4. Update gui-backend-change skill.
5. Proof: facade + `test_results_range` / `test_adequacy_http` lost-load cases.
