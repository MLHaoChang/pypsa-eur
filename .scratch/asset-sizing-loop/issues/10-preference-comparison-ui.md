<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# AS-10 — Preference comparison UI

Status: in-progress (PR 1 merged #1009; PR 2 UI in review)
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
- PR 2: the UI (Guided and Expert, screenshots light and dark), branch `claude/as-10-ui`: section "Intent comparison" in the grid study panel (`campusStudy`), polling the job routes. Small backend delta: `POST` takes `grid_code` (profile id, null = none set) instead of a raw profile.
- The issue stays open until both land. Acceptance boxes 1-3 are pinned in PR 1 on the backend and need the UI test in PR 2.

## Comments

GK B plan verdict (GO): background job with progress and abort; the named project's network; 422 validation (2-5 distinct, autonomy finite > 0 with an upper bound, no extra fields; a refusal becomes that row's failed status); mutation-test each conjunct of the pass rule; basis_note verbatim per row; route dispositions (not_via_chat row `intent_comparison`, ROUTE_POLICY rows, parity shadow). The job is a self-contained per-project record (`services/compare/intent_jobs.py`), not the global study `_state`, because that is bound to the active project.

UX plan verdict (GO) for PR 2: host panel and Guided home named before coding; labels from the backend table; per-row progress and abort; status words not colour; unit headers; autonomy saved per project; D warning only when the profile does not accept transfer, else say no profile is set; disabled reasons as text; `EditedSinceStudyBanner`; screenshots of six states in both modes, light and dark.

Open: cost column is the LP total (no capex split in the loop result).

PR 2 skipped, by decision: backup autonomy is per run, not saved per project (a project-file write needs an edit-lock decision; AS-AUTONOMY-1 asks per project). Open for the owner.

GK B PR 2 plan verdict (GO): not_run shown next to a converged row; non-converged, unresolved, failed and stopped never render Pass and their sizes are "not final"; abort wired; vitest for polling ending on done, aborted, failed and for a 409 on start (shows the running job, no second job). Ruling 1: `grid_code` id on POST, resolved server-side from the project's own grid-code store, unknown id 422, raw `profile` field removed. Ruling 2: per-project autonomy goes in its own small backend PR (existing per-project store preferred; a new file needs `_BUNDLE_FILES`, `_SAVE_WRITTEN_FILES`, snapshot, copy, atomic write under the write lock, route rows, 422 validation, round-trip tests); PR 2 sends autonomy per run (default 4 h) until then. Still to check against ruling 1: a profile without `ride_through_transfer_counts`, and an omitted id, both read as "not checked"; the UI uses UX's wording for the omitted id ("No grid code set: ...").

## PR 2 notes (owner decision AS10-AUTONOMY-2)

- Backup autonomy (AS-AUTONOMY-1) is per run for now ("Per run now, store later"). Per-project storage is a separate backend PR afterwards, with GK B's conditions; it is not in #1017.
- Screenshots: `evidence/10/`, eight states, Guided and Expert, light and dark, from `pypsa-gui/frontend/scripts/intent-comparison-shots.mjs` (route-mocked harness, invented numbers). The Guided-home-to-study-part path shot is waived by UX: Guided reaches the panel in one click through the hub chapter.
- Chat disposition (not_via_chat row `intent_comparison`, held until the owner decides on a chat tool): `GET /api/projects/{name}/intent-comparison`, `POST /api/projects/{name}/intent-comparison`, `POST /api/projects/{name}/intent-comparison/abort`.

GK B (16) plan verdict (GO) for AS-AUTONOMY-1 storage (owner decision AS10-AUTONOMY-2): store in the existing `settings` table of `metadata.json` (`PATCH /api/projects/{name}/settings`, `ProjectInfo.settings.backup_autonomy_h`). Conditions: (1) one per-field validator shared by `services/project_settings.read_settings` and `ProjectInfo.settings`, bool refused, a parametrised test that both agree on every corrupt value; (2) JSON int 4 stored as 4.0, a re-save byte-stable, `derive_lengths_from_geometry` stays bool-only; (3) the Save-As fix also changes `derive_lengths_from_geometry` (release note says so); a source with no bound directory writes no settings; own-directory saves keep theirs; (4) PATCH under the context's `mutation_lock` after the project lock, as `_save_context`; a concurrent save and PATCH lose neither edit; (5) freshness: each comparison run records the autonomy its request carried and nothing on that path reads the stored value, so a later PATCH cannot relabel a result; the setting is inert for `_network_input_fingerprint` (pinned); (6) `DesignIntentSpec` and the PATCH body share the constants; 720 accepted, 720.0001 refused through both.
