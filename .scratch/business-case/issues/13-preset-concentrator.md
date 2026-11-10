<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 — Mining concentrator preset

Status: needs-triage (blocked by 06, 07; BCA-SCOPE-1 Q3: one net €/t line)
Type: task
Blocked by: 06, 07
Gated: yes (GK B; GK A for the linked outputs)
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §6.5 and [research.md](../research.md) §3 first.

## What to build

The `eh_concentrator_staged` preset: concentrate sales (net €/t) on
`shipped:shipments` with the build-up in its note (BCA-SCOPE-1 Q3), process plant cost per
t milled (typed), freight, staff, maintenance, insurance.

## Tests

Revenue reads shipped t, not mill output (LO-2); a shortfall lowers revenue once
(G3).
