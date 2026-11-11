<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — The LOLE gate names the loop's fleet notes; the Link refusal text goes

Status: needs-triage
Type: task
Gated: yes (GK A)
Model tier: sonnet
Raised by: [01](01-loops-sample-link-outages.md) (GK A (20) AL-2). Waits for #1333, which owns `services/run_*.py`.

## What

Since 01 the coupling and margin loops sample Links that supply an electrical bus. Their record carries `fleet_notes`: what the supplying-Link walk decided, e.g. "grid import taken as never failing: no outage data for grid_import", or a store-fed fuel cell not credited. The gate does not show them yet. `run_gates.LOOP_FIELDS` drops `fleet_notes`, so the LOLE rule's note cannot say that its verdict rests on a grid import taken as firm (AL-2).

`run_gates.NOTHING_TO_SAMPLE_DETAIL` still says "Outages carried on Links, as on the H2 hubs, cannot be sampled yet". After 01 that sentence is false.

## Acceptance

- `LOOP_FIELDS` keeps `fleet_notes`, and the LOLE rule's reason names each "grid import taken as never failing" note. A test checks the words on eh_h2_hub with grid_import's outage data cleared.
- `NOTHING_TO_SAMPLE_DETAIL` names only what the refusal establishes (#1318 F1): no electrical generator and no supplying Link with outage data.
- Study-harness 11's acceptance 1 is then closable (the loop half landed in 01).
