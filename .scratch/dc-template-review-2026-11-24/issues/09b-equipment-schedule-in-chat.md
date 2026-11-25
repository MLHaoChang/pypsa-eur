<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 09b The equipment schedule in chat

Status: needs-triage
Blocked by: none
Source: issue 09. The schedule shipped without a chat tool (not_via_chat entry `equipment_schedule`, disposition `unbuilt`).
Severity: minor
Model: opus. Review: GK B.

## Problem

`GET /api/campus-electrical/{name}/equipment-schedule` serves the electrical equipment schedule: every transformer, switchgear, cable and declared unit with its rating, governing criterion, flags and capex. The assistant cannot read it yet. A user who asks in chat which switchgear rating was chosen, or why a cable is flagged, is pointed to Results › Hand-off.

## Fix

Add a read-only chat tool that returns the schedule's rows and assumptions in the same words as the table. Then drop the `held-by-spec` route-parity declaration that points here, and move the not_via_chat entry `equipment_schedule` off `unbuilt`. The `.xlsx` route stays a binary transfer.

## Comments
