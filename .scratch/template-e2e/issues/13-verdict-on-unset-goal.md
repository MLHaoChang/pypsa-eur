<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 — "Certified" is shown against an allowed shortfall the card calls "not set"

Status: done
Impact: high
Templates: eh_datacenter, hospital site, microgrid (Expert and Guided; extends 06 and 08)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Hospital: "Certified (pass vs 3 h/yr)" while the same card says "allowed shortfall not set". Draws: card says "draws 2000 (not converged)", Network tab says "500 (default)".

## Why it matters

A pass for a hospital rests on a number nobody chose and is not marked assumed.

## Suggested fix

Show the applied 3 h/yr (and draw count) as assumed values; ask before certifying; one draw count everywhere.

## Triage (2026-11-19, master 9f850d36)

Checked by code reading only; the app was not re-run. Acceptance: the applied 3 h/yr and the draw count show as assumed values wherever a verdict shows; no card says 'not set' beside a pass; one draw count in the study card and the Network tab. Overlaps 26 (assumed inputs unlabelled in results): do not duplicate the label; use its component. Gated: GK A and UX. Blocked by: none. Whether certification should ask first is not part of this issue.

## Comments

- 2026-11-23 status sweep: landed in #1645 (41318c60); #1735 (ae6ea750): goal and draw count now show as assumed. Remains: the Certified vs not-set card conflict and a single draw count across study card and Network tab.
