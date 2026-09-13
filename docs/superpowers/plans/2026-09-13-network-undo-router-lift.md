# Network undo router lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-network-undo-router-lift-design.md`

1. Add `services/network_undo.py` (`push_undo_snapshot`, `get_undo_info`, `apply_undo`).
2. Thin handlers + identity re-export of `_push_undo_snapshot` in `routers/network.py`.
3. Facade / profiles / time-axis tripwires; retarget study-guard callsite scan.
4. Update gui-backend-change skill.
5. Proof: undo suites + facade + ruff F821.
