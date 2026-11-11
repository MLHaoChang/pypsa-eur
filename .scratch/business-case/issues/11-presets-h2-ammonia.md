<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — H2 hub and ammonia presets

Status: ready-for-agent (blocked by 06, 07; Sonnet; GK B, GK A for linked outputs)
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

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.
