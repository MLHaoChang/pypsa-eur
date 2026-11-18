<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — No reliability verdict for strong-grid and generation-only templates

Status: needs-triage
Impact: high
Templates: H2 hub (both), ammonia, IPP
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Study card shows "certification: skipped" and "ENS cap 10‱ | Achieved 0.00‱" with no verdict. IPP: the accordion cannot open ("Reliability is outside the scope"; "Not offered for this site"). The user finishes the journey with no pass/fail and no next step.

## Why it matters

Journey ends at results without a decision for 4 of 10 templates.

## Suggested fix

Give each such template its own verdict (supply, delivery, revenue) or say plainly "no reliability verdict for this site type" and point to what to check instead.
