<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 24 — The assistant's AC power-flow stage skips the run validation

Status: ready-for-agent
Type: task
Gated: yes (GK B reviews the PR)
Reported by: engine gatekeeper, 26 Oct 2026

The assistant tool `run_ac_pf_stage` (`harness/catalogue.py`, routed to
`POST /api/simulation/run_ac_pf`) starts an AC power-flow stage without
`validate_for_run`. `run_ac_pf()` in `routers/simulation.py` never calls it, while
`POST /api/simulation/run` does. A network that `/run` would refuse can still reach
the AC power-flow solver through the assistant.

The UX-flow R1 package removes the frontend's own `runAcPf` path, so after it merges
this assistant tool is the route's only caller.


## Triage (GK B, 2026-10-26)

Keep `run_ac_pf_stage` and fix the route. Don't add full `validate_for_run`.

- Full `validate_for_run` is the wrong gate. Skipping it is deliberate (the `preflight_validate(validate=False)` docstring in `routers/simulation.py` and `run_separability.loadflow_verdict`): it runs the LOPF-mode checks, which would refuse load flows that work on a solved network, and would split the route from the `loadflow` verdict, which is "one copy" through `ac_pf_refusal`.
- The real gap is narrower. A dispatch counts as fresh when the column set matches, so attribute edits after the solve still pass (e.g. a line set to r=0, x=0 gives a singular Y-bus). The bundled Stage 2 catches this through `validation_service._check_stage2_ac_pf` (`stage2_multi_period`, `stage2_zero_impedance`); the standalone `/run_ac_pf` never calls it.
- Retiring the tool is wrong: the standalone load flow is a run kind with its own verdict, and after R1 the assistant is its only caller.

Done when:
- `ac_pf_refusal(n, config)` also returns the first error-severity issue of `_check_stage2_ac_pf(n, config)` as `(code, message)`; warnings don't refuse.
- `loadflow_verdict` passes the config through, so the route and the verdict stay one function.
- New error kinds go into `tool-error-kinds.json`; the inventory is regenerated if it changes.
- Tests: `/run_ac_pf` returns 400 on a solved network after an edit to r=0, x=0, and with multi_investment_periods; `loadflow_verdict` gives the same blocker code for both; a valid solved network still starts.
- A release fragment.
