"""
The compare engine — the nine `_compute_*_summary` functions behind
`/api/projects/{name}/compare-state` and `/results-summary`, plus their
shared helpers. See the decomposition spec's "Phase 3 addendum".

Functions here take the network and explicit arguments; the solver config and
the LP-stage result lookup arrive as keyword-only parameters where a function
needs them, never from router state. `routers.compare` keeps the two routes,
re-exports the pure functions under their old names, and wraps the four that
used to resolve state inline.

This `__init__` re-exports NOTHING, for the same reason `services/solver/` and
`services/results/` do not. Nothing in this package imports a router.
"""
