"""
The `solver_service` decomposition.

`services/solver_service.py` is carved into this package; see
`docs/superpowers/specs/2026-09-04-backend-god-file-decomposition-design.md`.

This `__init__` deliberately re-exports NOTHING. `services.solver_service` is
the single import surface for everything in here — 40-plus call sites already
import from it, and a second export surface would let new call sites drift onto
the package while the old ones stay on the façade, which is how one module
becomes two half-migrated ones.

Modules here never import from `solver_service`. Dependencies run one way, and
`tests/test_solver_facade_surface.py` enforces it.
"""
