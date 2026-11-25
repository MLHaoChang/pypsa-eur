<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05b Hall, PDU and rack breakdown of the IT load

Status: needs-triage
Blocked by: 05 (compute block)
Source: owner goal 2026-11-24 ("point of interconnection to the compute rack level"); split from DC review 05.
Severity: major

## Problem

The model reports one IT load per phase and a rack count from `rack_kw`. The owner wants the infrastructure visible down to the rack: halls, power distribution units per hall, racks per unit, and kW at each level.

## Acceptance criteria

- [ ] An optional breakdown input (halls, PDUs per hall, racks per PDU, kW per rack) per phase, reported next to the IT load and checked against it (counts × kW against the phase's IT MW, a note beyond 10 %, typed IT MW still wins).
- [ ] Reported only: no new network buses.
- [ ] A form section in the Project workflow's Load section, same fields in Guided and Expert, UX reviewed.
- [ ] Reuses the compute block's kW per accelerator and per-rack figures where they exist.
