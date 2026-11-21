<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — "Total system cost EUR -4.52 M" has no basis beside the developer's Net on another basis

Status: needs-triage
Impact: medium
Templates: IPP
Found by: template walk, 2026-11-21 (master 6b421b06); raised by UX review of PR 1797
Shot: ../shots/ipp-e-capexp.png

## What

IPP, Expert Results › Summary: "Total system cost -€4.52 M … in this solve (revenue exceeds cost)". The parenthesis says why it is negative but not what is in it (annualised capex, opex, market revenue) or that it is a system view. Commercial & finance on the same run lists the IPP developer's Net as EUR 4,141,318.07 (Paid 11.6 M, Received 15.7 M, raw numbers, no unit). The two are on different bases and neither says so. The Guided status bar shows "Obj: -€ 4.6 m" and a second figure "€1.4".

## Why it matters

A user comparing "cost -4.52 M" with "Net +4.14 M" cannot tell whether they are the same quantity with opposite signs or two different quantities.

## Suggested fix

For a generation-only site label the card as a profit/revenue figure ("Net system result +€4.52 M"), list what it contains, and state the basis next to the developer Net.
