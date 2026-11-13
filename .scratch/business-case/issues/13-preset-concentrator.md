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

### 2026-11-13: hand-off (builder)

Built: `project_templates/business_case_presets/eh_concentrator_staged.json` (variant `concentrate_sales`), `eh_concentrator_staged` in `presets.py` VERSIONS; tests in `tests/test_business_case_presets_concentrator.py` (rows, linked ids exist, LO-2 shipped t, G3 shortfall once, share bases).

Choices to review:
- Concentrate sales and Freight both link `shipped.shipments` (freight is a cost, so G3 adds no second revenue share). Freight is priced in the Freight row only; the sales note says not to deduct it (GK B F1).
- Process plant cost is typed per t milled. The US$5.6-11.3/t figure sits in the note, not a `hint`, because the preset schema allows hints on revenue rows only; it is sourced to Project Blue [16] (secondary), the TC/RC figure to S&P/SMM [14] (GK B F3). The note says the line does not scale in a shortfall: type the milled t from the results (F2).
- A typed "Plant capex" lump is the base of both Maintenance and Insurance shares (one maintenance share, so no capex line sits in two maintenance bases). No pack defaults for either share.
- No offered rows.

### 2026-11-13: done

PR #1462 merged at 624386dc (GK A OK @88727860 carried, GK B OK @b40eb071). Left open: no pack defaults for the maintenance and insurance shares (no sourced figure); no offered rows; the process-plant line does not scale in a shipment shortfall (the note says so).
