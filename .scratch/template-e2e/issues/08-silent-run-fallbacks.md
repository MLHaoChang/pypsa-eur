<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — Run inputs that fall back to a default without saying so

Status: ready-for-agent
Size: M
Gated: GK B; UX
Likely files: pypsa-gui/backend/services/reliability_goal.py (mc_draws ~l.35, site_archetype l.34); pypsa-gui/backend/services/adequacy/eh_stages.py (~l.479-487, efficiency 1.0); pypsa-gui/backend/copt_endpoint.py (l.54, VOLL 0.0); pypsa-gui/backend/services/adequacy/occurrence.py (l.214, carrier outage defaults); pypsa-gui/backend/services/flow_service.py (assumption items ~l.855-899); Results "Assumptions behind this result"
Journey fit: set up (sections 5, 8) → run → results → report. The default must be visible before the run and in what the run reports.

## What

The run reads these inputs and silently uses a default when they are missing (code, decisions.md finding 8):

| Input | Default | Read at |
|---|---|---|
| `mc_draws` (simulation runs per check) | 500 | reliability_goal.py ~l.35 |
| `site_archetype` | strong_grid | reliability_goal.py l.34 |
| link / conversion efficiency | 1.0 | eh_stages.py ~l.479 |
| VOLL (backend) | 0.0 | copt_endpoint.py l.54 |
| forced-outage rate / MTTR per carrier | carrier defaults | occurrence.py l.214 |

None of them shows as "assumed" in its section, in Results or in the report.

## Acceptance

- Each defaulted run input shows as "Assumed" in its own section, and in the Results group "Assumptions behind this result", and in the report.
- The backend VOLL fallback refuses the run instead of using 0.0 (06 carries the user-facing half).
- A test runs each template's study with the input missing and checks that the assumption appears in the result.
