<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Off-grid constraints: minimum renewable fraction and operating reserve

Status: needs-triage
Type: task
Gated: yes (GK B; GK A if it touches the IC/adequacy engine)
Spec: [spec.md](../spec.md) (HOMER-GAPS-1)

## What

HOMER Pro lets an off-grid design require a minimum renewable fraction of served energy and an operating (spinning) reserve: dispatchable headroom held each hour as a share of load plus a share of the solar and wind output, so a cloud or a gust does not drop load. A keyword pass found neither constraint in GridSpine; `reserve_margin` in `services/adequacy/` is a capacity margin, not per-hour headroom.

## Likely shape

Two optional constraints on an energy-hub or microgrid study, with typed inputs (percent of load, percent of PV, percent of wind; minimum renewable fraction), shown in the results with the hours where the reserve binds. Check the microgrid profiles (SH-12c) and study-harness 40 (microgrid templates fail the frequency screen) for overlap.

## Triage

Confirm on master that neither constraint exists (also under other names), name the files and the reviewer, and set the model tier.
