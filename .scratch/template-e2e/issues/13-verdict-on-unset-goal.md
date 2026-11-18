<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 13 — "Certified" is shown against an allowed shortfall the card calls "not set"

Status: needs-triage
Impact: high
Templates: eh_datacenter, hospital site, microgrid (Expert and Guided; extends 06 and 08)
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Hospital: "Certified (pass vs 3 h/yr)" while the same card says "allowed shortfall not set". Draws: card says "draws 2000 (not converged)", Network tab says "500 (default)".

## Why it matters

A pass for a hospital rests on a number nobody chose and is not marked assumed.

## Suggested fix

Show the applied 3 h/yr (and draw count) as assumed values; ask before certifying; one draw count everywhere.
