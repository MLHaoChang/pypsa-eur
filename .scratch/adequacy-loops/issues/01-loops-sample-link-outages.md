<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — run_coupling_loop / margin loop cannot sample outages carried on Links (firm Link backup, grid-import Link)

Status: needs-triage
Type: task
Gated: yes (GK A)
Model tier: opus
Raised by: [study-harness 11](../../study-harness/issues/11-profile-h2-hub.md) (GK A (19) ruling SH11-L4, 9 Nov)

## What

The adequacy loops sample a fleet built from **electrical Generators** only (`snapshot_inputs`). A network whose outage data sits on Links has nothing to sample, and every loop refuses before it starts with the same message:

> nothing to sample: no electrical generator carries resolvable occurrence data (unavailability + MTTR), so the sampled fleet is empty

The refusal is shared by three call sites:

- `pypsa-gui/backend/services/adequacy/coupling_loop_runner.py:250` (`run_coupling_loop`)
- `pypsa-gui/backend/services/adequacy/margin_loop_runner.py:223` (`run_margin_loop`)
- `pypsa-gui/backend/services/adequacy/eh_stages.py:1150` (the EH study's loop stage)

Both H2-hub templates (`eh_h2_hub`, `eh_h2_hub_staged`) carry their outage data only on Links: `grid_import`, `electrolyser` and the firm backup `fuel_cell`. Their harness profiles' LOLE rule (`eh.adequacy.lole`, `run_coupling_loop`) therefore can never be evaluated, and an H2-hub run stops at the result gate with a typed NoEvidence (SH11-L1).

The refusal itself is correct: an empty fleet would report the whole horizon as loss of load. The gap is that a firm Link (a fuel cell feeding the electrical bus) and a grid-import Link are supply the loop should sample.

For reference only: the EH study's two-area Monte Carlo (`mc_certify`) already samples Link-borne outages; F9 (study-harness 07, owner Q3 = A) keeps its LOLE out of the gate.

## Acceptance

- The coupling and margin loops sample the outages of Links that supply an electrical bus (at least a firm backup Link and a grid-import Link), each with its own occurrence data; the refusal stays for a network with no sampled supply at all.
- The three refusal sites share one rule for what is sampleable (no drift between them).
- On both H2-hub templates `run_coupling_loop` runs to a verdict; the H2-hub profiles' LOLE rule then evaluates, and study-harness 11's acceptance 1 can be closed.
- A test per Link role (firm backup, grid import), plus the existing Generator-only fixtures byte-unchanged.
