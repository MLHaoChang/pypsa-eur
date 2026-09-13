# Network bulk-update router lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-network-bulk-router-lift-design.md`

1. Add `services/network_bulk.py` with helpers + `apply_bulk_update` (byte-for-byte from `routers/network.py`).
2. Thin `bulk_update` in `routers/network.py`; re-export helpers from the façade block.
3. Extend `_MOVED` + add `_LIFTED_HANDLERS` thin tripwire in `test_network_facade_surface.py`.
4. Update gui-backend-change skill (“Where network helpers go”).
5. Proof: facade + bulk / nonfinite suites; `ruff` F821.
