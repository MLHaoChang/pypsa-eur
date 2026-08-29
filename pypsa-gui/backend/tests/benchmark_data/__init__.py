"""
Sourced published-benchmark fixtures for the adequacy engines (plan Task 7,
spec §7). Nothing in this package imports backend code — the fixtures are
plain data plus reconstruction helpers, so the COPT and the MC can be pointed
at identical inputs.

Files:
  rts79_units.csv / rts79_load.py — IEEE Reliability Test System (1979).
  rbts_units.csv  / rbts_load.py  — Roy Billinton Test System (1989/1990).

Every file carries its own provenance header: sources, retrieval date,
cross-check discrepancies, the published adequacy figures the gate tests
against, and the conventions those figures depend on.
"""
