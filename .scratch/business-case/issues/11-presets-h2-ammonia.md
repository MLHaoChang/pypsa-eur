<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — H2 hub and ammonia presets

Status: needs-triage (blocked by 06, 07)
Type: task
Blocked by: 06, 07
Gated: yes (GK B; GK A for the linked outputs)
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §6.3–§6.4 and [research.md](../research.md) §1–§2 first.

## What to build

Presets for `eh_h2_hub`, `eh_h2_hub_staged`, `eh_ammonia_staged` per §6.3–§6.4:
H2 offtake on `served_h2:h2_offtake` (kg), production premium (tenor), O2 off by
default on `derived_o2`, stack replacement and O&M defaults, NH3 sales on
`served_product:nh3` (t). Re-read each researched figure on its page first and
record its tag; only [V]/[S] figures become defaults or hints.

## Tests

Each preset runs on its built template; a shortfall lowers revenue once (G3); the
reference purchase is absent (G4); the identity test of served vs the built
network (GK A point 3).
