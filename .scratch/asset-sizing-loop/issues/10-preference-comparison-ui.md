<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-10 — Preference comparison UI

Status: ready-for-agent
Type: task
Blocked by: 01, 06
Model: Sonnet
Reviewer: UX + GK B

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Run intents A–E as scenarios through the compare machinery (`BE/services/compare/`, `ADQ/eh_study.py`) and show the DC 05 table: capex, MW/MWh/Mvar per asset, transformer and STATCOM sizes, verdict per check, loop status (converged, not converged, unresolved). Available in Guided and Expert modes. Follow `docs/agents/review-ux.md`.

## Acceptance

- [ ] Tests first: selecting two intents runs two scenarios and renders one row per intent.
- [ ] A "not converged" or "unresolved" loop is visible, never shown as a pass.
- [ ] Intent D shows the grid-code warning (AS-NOUPS-1); intent A shows the editable 4 h autonomy.
- [ ] Works in Guided and Expert; UX review checklist passes.

## PRs

## Comments
