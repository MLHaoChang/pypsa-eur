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

### 2026-11-17: GK B2 plan GO, 15a backend then 15b UI

- Q1 (b): a typed revenue or opex line under a factor below 100 % refuses with a typed kind naming the line (ruling 6: a typed line states an amount, not its volume class). Follow-up issue: a per-line volume class the user states (fixed / volume, unset refuses).
- Q2 (a): the stream map as planned. V1: the report and workbook state "volume-tied costs and revenue scaled pro rata by the factor; the dispatch was not re-solved at reduced load" whenever a year is below 100 %, absent at the default. V2: the PR body confirms demand_charge's basis (per peak kW, scaled as if the peak scales with f) and every `tax` row's basis; the map's docstring names each SCALE class's source. V3: the closed-map test is exact set equality.
- Q3 (a): 15a backend, 15b UI.
- C1 default byte-identity on every preset fixture against master's output. C2 hash gate tested on each part (in horizon, ≠ 1). C3 years outside the horizon do not refuse but the report and workbook name them as ignored; keys strict ints (refuse "2030.5", "x", bool), dict size capped, values finite 0–1, a test per bound. C4 a share on a scaled base scales once; a linked line gives q·s·f exactly. C5 the undetermined refusal fires only where the row is non-zero and f < 1, each part tested. C6 uneven factors across years. C7 chat line edit and preset apply keep the stored factor; get_business_case round-trips it if it returns inputs. mutants.py on business_case.py and the models module; the PR body lists each new guard with its killing test.
