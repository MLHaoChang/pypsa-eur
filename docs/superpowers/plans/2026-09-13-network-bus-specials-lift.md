# Network bus-specials lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-network-bus-specials-lift-design.md`

1. Add `services/network_buses.py` with the three apply_* bodies.
2. Thin handlers in `routers/network.py`; inject `_update_component` into `apply_update_bus`.
3. Extend facade `_LIFTED_HANDLERS`; update skill.
4. Proof: line-length / rescale / facade / chat dispatch; ruff F821.
