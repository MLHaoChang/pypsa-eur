<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 24 — The assistant's AC power-flow stage skips the run validation

Status: needs-triage
Type: task
Gated: yes (gatekeeper triages)
Reported by: engine gatekeeper, 26 Oct 2026

The assistant tool `run_ac_pf_stage` (`harness/catalogue.py`, routed to
`POST /api/simulation/run_ac_pf`) starts an AC power-flow stage without
`validate_for_run`. `run_ac_pf()` in `routers/simulation.py` never calls it, while
`POST /api/simulation/run` does. A network that `/run` would refuse can still reach
the AC power-flow solver through the assistant.

The UX-flow R1 package removes the frontend's own `runAcPf` path, so after it merges
this assistant tool is the route's only caller.

Triage questions:
- Should `/run_ac_pf` run `validate_for_run` (and refuse on `has_errors`) the same way `/run` does?
- Or should `run_ac_pf_stage` be retired from the catalogue once R1 merges?

Done when the route refuses a network `/run` would refuse, with a test, or the tool
is retired with the inventory regenerated.
