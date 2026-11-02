<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-10 — Preference comparison UI

Status: in-progress (PR 1 of 2 in review)
Type: task
Blocked by: 01, 06
Model: Sonnet
Reviewer: UX + GK B

Spec: [spec.md](../spec.md). Source: research note section 5, [RESULTS.md](../../datacenter-grid-readiness/research/prototype-as-loop/RESULTS.md).

## Scope

Run intents A–E as scenarios through the compare machinery (`BE/services/compare/`, `ADQ/eh_study.py`) and show the DC 05 table: capex, MW/MWh/Mvar per asset, transformer and STATCOM sizes, verdict per check, loop status (converged, not converged, unresolved). Available in Guided and Expert modes. Follow `docs/agents/review-ux.md`.

## Acceptance

- [x] Tests first: selecting two intents runs two scenarios and renders one row per intent.
- [x] A "not converged" or "unresolved" loop is visible, never shown as a pass.
- [x] Intent D shows the grid-code warning (AS-NOUPS-1); intent A shows the editable 4 h autonomy.
- [ ] Works in Guided and Expert; UX review checklist passes.

## PRs

## Comments

## Split (owner decision AS10-SPLIT-1)

- PR 1 (#1009): backend job. `POST/GET /api/projects/{name}/intent-comparison` and `/abort`, 2-5 distinct intents, 422 validation, the named project's own saved network, per-intent failed rows, abort, pass rule `converged and no failing and no unresolved`.
- PR 2: the UI (Guided and Expert, screenshots light and dark). A first cut is on branch `claude/as-10-ui-draft`; it must move to the job routes and take the UX conditions below.
- The issue stays open until both land. Acceptance boxes 1-3 are pinned in PR 1 on the backend and need the UI test in PR 2.

## Comments

GK B plan verdict (GO): background job with progress and abort; the named project's network; 422 validation (2-5 distinct, autonomy finite > 0 with an upper bound, no extra fields; a refusal becomes that row's failed status); mutation-test each conjunct of the pass rule; basis_note verbatim per row; route dispositions (not_via_chat row `intent_comparison`, ROUTE_POLICY rows, parity shadow). The job is a self-contained per-project record (`services/compare/intent_jobs.py`), not the global study `_state`, because that is bound to the active project.

UX plan verdict (GO) for PR 2: host panel and Guided home named before coding; labels from the backend table; per-row progress and abort; status words not colour; unit headers; autonomy saved per project; D warning only when the profile does not accept transfer, else say no profile is set; disabled reasons as text; `EditedSinceStudyBanner`; screenshots of six states in both modes, light and dark.

Open: cost column is the LP total (no capex split in the loop result).
