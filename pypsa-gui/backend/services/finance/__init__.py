"""
Edge Investment Case — finance layer (spec §6; plan P0/P4/P7).

Pure post-processing on solved results: cashflows, debt, tax, incentives,
tax equity, metrics, jurisdiction packs and the `InvestmentCaseReport`
assembler. Nothing in this package imports a router or `solver_service`;
this `__init__` re-exports nothing (same discipline as `services/results/`).
"""
