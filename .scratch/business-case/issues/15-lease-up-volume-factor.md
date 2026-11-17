<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 15 — Lease-up volume factor

Status: ready-for-agent
Gated: GK B
Type: enhancement
Parent: business-case 08 (#1467), GK B question Q1

## Problem

GK B's review of business-case 08 raised Q1: the workbook applies no lease-up (ramp-in) factor to served volume, so the first years' revenue assumes full volume from day one.

## Ask

Triage: confirm the gap against master after #1467, and decide whether a per-year volume factor belongs in the business-case inputs.

## Comments

### 2026-11-17: triage (session_01G7qCuo557GzjqFm3CFxX1D)

Confirmed on master at 1a626c94: no lease-up or volume factor anywhere in the business case. `services/finance/business_case.py:146` `_quantity` reads a linked line as the solved series × `linked_share_at(year)` (a commercial sold share, #1430 F2), and the IC template rows (`_templates`, :389) carry the solve's full dispatched volume every year, so year 1 books full volume from COD. Owner decision BC15-BUILD-1: build a per-year volume factor in the business-case inputs, default 100 %, validated 0–100 %. Ruling 6 (`docs/agents/review-gk.md:171`) binds it: the factor scales the cost lines tied to dispatched volume with the revenue lines, never revenue alone, and a line whose volume class is unknown refuses. Verdict: ready-for-agent; plan to GK B2 before building.
