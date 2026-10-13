"""
Edge Investment Case — the org-scoped Library (spec decision 17; plan P1/P2).

Versioned, content-addressed series (price curves, envelopes, meter history,
forecast pairs) and, from P2, tariffs and contracts. Nothing in this package
imports a router or `solver_service`; this `__init__` re-exports nothing.
"""
