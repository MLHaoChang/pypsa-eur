<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Ledger guards: double count, lost production, reference purchases

Status: needs-triage (blocked by 05; GK A GO on G3/G4 asked 2026-11-10)
Type: task
Blocked by: 05
Gated: yes (GK B, GK A)
Model tier: opus
Size: S

Read [the spec](../spec.md) §3 G2–G4 and GK A's LO-4 ([04](04-linked-outputs.md) ##
Comments) first.

## What to build

In `build_business_case` (05):

1. G2: a line `linked` to an output a ledger stream already prices (export,
   PPA/CfD settlement, bill streams, a product bus valued by the IC) is refused
   `double_count:<stream>`, never warned.
2. G3: when a revenue line reads a product bus's served quantity, that bus's
   `lost_production` lines leave the business-case case; kept otherwise.
3. G4: no reference-purchase value reaches the business case.
4. The locked "from the energy model" rows: a read-only list of the ledger streams
   the case carries, for 09 to show.

## Tests

- Each ledger stream that can clash (LO-4) refused, with its kind.
- A product bus with a shortfall: revenue drops by the unserved quantity × price
  exactly once (a mutant that keeps `lost_production` fails it).
- No line, no drop: `lost_production` stays when no line reads the bus.
- H2 hub and ammonia: no reference-purchase value in the business case (pin).
- IC byte-equal (BC1).
