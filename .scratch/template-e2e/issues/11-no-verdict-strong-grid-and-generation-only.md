<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 — No reliability verdict for strong-grid and generation-only templates

Status: ready-for-agent
Impact: high
Templates: H2 hub (both), ammonia, IPP
Found by: Expert-mode walk, 2026-11-17 (master 0ab07339)

## What

Study card shows "certification: skipped" and "ENS cap 10‱ | Achieved 0.00‱" with no verdict. IPP: the accordion cannot open ("Reliability is outside the scope"; "Not offered for this site"). The user finishes the journey with no pass/fail and no next step.

## Why it matters

Journey ends at results without a decision for 4 of 10 templates.

## Suggested fix

Give each such template its own verdict (supply, delivery, revenue) or say plainly "no reliability verdict for this site type" and point to what to check instead.

## Triage (2026-11-19, master 9f850d36)

Checked by code reading only; the app was not re-run. Decision (owner, 2026-11-19, TE2E-11-PLAIN-1): show 'no reliability verdict for this site type' plus what to check instead, for all four templates (H2 hub both, ammonia, IPP). Acceptance: no 'certification: skipped' or unopenable accordion on these templates; the plain statement and the check-instead pointer show in Guided and Expert; a test per template. Per-template verdicts are not part of this issue. Gated: UX. Blocked by: none.
