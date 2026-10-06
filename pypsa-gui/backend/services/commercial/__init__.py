"""
Edge Investment Case — commercial layer (spec §5; plan P1/P2).

Tariffs, the billing-grade rating engine, contract settlement, connection
agreements and the dispatch-grade LP bindings. Nothing in this package imports
a router or `solver_service` (tripwire: tests/test_investment_case_tripwires.py);
this `__init__` re-exports nothing.
"""
