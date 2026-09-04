"""
The `routers/results.py` decomposition — the arithmetic behind the read-only
`/results/*` endpoints. See
`docs/superpowers/specs/2026-09-04-backend-god-file-decomposition-design.md`,
"Phase 2 addendum".

Each `compute_*` takes the network, the solver config where it needs one, and
the handler's own parameters; result frames come through an injected
`result_df` callable rather than from router state. Returns the payload, or
`None` where the endpoint answers 204.

This `__init__` re-exports NOTHING, for the same reason `services/solver/`
does not: `routers.results` stays the single surface callers see, and a second
one would let the two drift apart. Nothing in this package imports a router.
"""
