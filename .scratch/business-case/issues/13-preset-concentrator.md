<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 — Mining concentrator preset

Status: in-review (PR pending; Sonnet; GK B, GK A for linked outputs)
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

### 2026-11-13: hand-off (builder)

Built: `project_templates/business_case_presets/eh_concentrator_staged.json` (variant `concentrate_sales`), `eh_concentrator_staged` in `presets.py` VERSIONS; tests in `tests/test_business_case_presets_concentrator.py` (rows, linked ids exist, LO-2 shipped t, G3 shortfall once, share bases).

Choices to review:
- Concentrate sales and Freight both link `shipped.shipments` (freight is a cost, so G3 adds no second revenue share). Freight's note says to delete it if the net price is already net of freight.
- Process plant cost is typed per t milled. The US$5.6-11.3/t figure sits in the note, not a `hint`, because the preset schema allows hints on revenue rows only.
- A typed "Plant capex" lump is the base of both Maintenance and Insurance shares (one maintenance share, so no capex line sits in two maintenance bases). No pack defaults for either share.
- No offered rows.
