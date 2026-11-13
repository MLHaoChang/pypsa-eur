<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 — Mining concentrator preset

Status: done
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

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-13: done

PR #1462 merged at 624386dc (GK A OK @88727860 carried, GK B OK @b40eb071). Left open: no pack defaults for the maintenance and insurance shares (no sourced figure); no offered rows; the process-plant line does not scale in a shipment shortfall (the note says so).
