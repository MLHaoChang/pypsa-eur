"""
The asset parameter schema (plan `docs/superpowers/plans/2026-10-05-asset-schema-s0.md`).

How an asset's investment is typed (`schema`), how PyPSA's cost columns are derived from it (`derive`), and how
every reader gets the upfront cost back (`access`). Nothing in this package imports a router or
`solver_service`; this `__init__` re-exports nothing.
"""
