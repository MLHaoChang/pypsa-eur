<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-16 DGS: line conductance written; shunt active power listed, never guessed

Status: ready-for-agent
Type: bug
Parent: `05-powerfactory-dgs.md` (#1580 review follow-up)
Gated: **yes** (GK A2, gridspine)

## What

#1580's DGS writer drops a line's shunt conductance (`g_us_per_km`) and a shunt's active power (`p_mw`), so a
lossy line or shunt reaches the tool without its losses, and nothing says so.

## Acceptance (GK A2 plan GO)

- Line: `TypLne.gline` in uS/km (in the ledger). The per-unit G equals pandapower's `net._ppc` within 1e-6.
  A line with g = 0 writes 0, so every row has the column. The golden header gains `gline(r)`.
- Shunt (option 1): no public source names the loss attribute, so no name is guessed. Each shunt with
  `p_mw` ≠ 0 is listed in `DgsExport.not_written` with its value and the reason. p = 0 and p = -0.0 are not
  listed. A non-finite p is refused by name.
- Queued: putting `not_written` into the campus study's export manifest needs `gridspine/drivers/campus_study.py`,
  which open PRs #1578, #1584 and #1589 change. That one line waits until they merge; tracked as GC-18 (`18-campus-manifest-not-written.md`).

## Comments

- 2026-11-17, GK A2 plan verdict: GO on line g and shunt option 1, with the tests above.
