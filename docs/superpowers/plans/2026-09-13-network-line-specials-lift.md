# Network line-specials lift — plan

**Spec:** `docs/superpowers/specs/2026-09-13-network-line-specials-lift-design.md`

1. Add `services/network_lines.py` with the two `apply_*` bodies.
2. Thin handlers in `routers/network.py`; re-export apply names if useful.
3. Facade `_LIFTED_HANDLERS` + skill row.
4. Proof: line_lengths / line_rescale + facade; ruff F821.
