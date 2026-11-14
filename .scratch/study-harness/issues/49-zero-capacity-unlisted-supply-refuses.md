<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 49 — A zero-capacity unlisted supply still refuses the island tie

Status: needs-triage
Gated: GK A
Type: bug
Parent: SH 46 (#1461) hand-off follow-up

## Problem

After SH 46 (#1461), an unlisted supply with zero capacity on a sidecar unit's bus still makes `tie_state` return `unit_on_mainland`, so the gate refuses although that supply can deliver nothing.

## Ask

Leave out supplies that cannot deliver (zero nominal capacity, not extendable), with a test, and keep the detection for any supply that can.
