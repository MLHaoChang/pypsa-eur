# Network global-constraints lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-network-global-constraints-lift-design.md`

1. Add `services/network_global_constraints.py` with the three `apply_*` bodies.
2. Thin handlers in `routers/network.py`; inject `_merge_partial_update` on update.
3. Facade `_LIFTED_HANDLERS` + skill row.
4. Proof: facade + chat_tools GC dispatch; ruff F821.
