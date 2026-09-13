# Network profiles router lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-network-profiles-router-lift-design.md`

1. Add `routers/network_profiles.py` (profiles + `_xlsx_response` + `_apply_profile_upload`).
2. Strip bodies from `routers/network.py`; re-export + `include_router`.
3. Add `tests/test_network_profiles_surface.py`; update facade / time-axis stays.
4. Update gui-backend-change skill.
5. Proof: profiles surface + bulk/nonfinite/upload suites; ruff F821.
