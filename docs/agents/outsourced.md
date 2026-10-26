<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced work

Tasks handed to coding apps outside our session coordination. Before opening a session on a task, check this table, and don't start one whose files an outsourced task touches. The brief lists those files.

An outsourced app opens one PR from its `outsource/<slug>` branch, with a body that starts with "Outsourced task <ID>". The coordinator finds it with `git ls-remote origin 'refs/heads/outsource/*'` and brings it in like any PR: the gatekeeper reviews harness changes, then premerge and the merge. When it merges, set the row's state to `merged <sha>` and the issue's `Status:` line to done.

| ID | Task | App | Branch | Brief | Issue | State |
|---|---|---|---|---|---|---|
| A | A tool the assistant just discovered is offered on its next request | Codex | `outsource/discovered-tools-offered` | `.scratch/harness/outsource/A-discovered-tools-offered.md` | `.scratch/harness/issues/23-discovered-tools-offered.md` | merged e8cb3f03 (#660) |
| B | A failing plan step stops after two retries | Codex | `outsource/task-step-retry-limit` | `.scratch/harness/outsource/B-task-step-retry-limit.md` | `.scratch/live-copilot/issues/25-task-step-retry-limit.md` | merged 53fac298 (#662) |
| C | Classify the `_refuse(...)` error kinds in the tool-error manifest | Codex | `outsource/refuse-kinds-manifest` | `.scratch/harness/outsource/C-refuse-kinds-manifest.md` | `.scratch/harness/issues/21-refuse-kinds-in-error-manifest.md` | merged 92453b35 (#661) |
| D | Opening the cost-reference card no longer throws away a solve (read-only preview) | Codex | `outsource/library-reference-preview-read-only` | `.scratch/harness/outsource/D-library-reference-preview-read-only.md` | `.scratch/harness/issues/22-library-reference-preview-is-a-write.md` | merged a52fadff (#674) |
| E | The assistant names what it cannot do from chat; every route has a chat disposition (LC-22) | Codex | `outsource/not-via-chat-list` | `.scratch/harness/outsource/E-not-via-chat-list.md` | `.scratch/live-copilot/issues/22-unsupported-by-chat.md` | merged f4e73540 (#675) |
| F | "hydro" and "hydrogen" generators stop filing under demand response | Codex | `outsource/gen-category-word-match` | `.scratch/timeseries-generators/outsource/F-gen-category-word-match.md` | `.scratch/timeseries-generators/issues/11-gen-category-word-match.md` | merged 24f6bc3e (#676) |
| G-a | Profile tuning, backend: tuning + schema route, compare service/route and `compare_profiles` tool (RP-05/07/12) | Codex | `outsource/profile-tuning-backend` | `.scratch/renewable-profiles/outsource/G-a-profile-tuning-backend.md` | `.scratch/renewable-profiles/issues/05-tuning-panel.md`, `07-before-after-compare.md`, `12-representative-weeks.md` | merged 7234bfbd (#694) |
| G-b | Profile tuning, frontend: Tune dialog, before/after compare, weather-bar window line | Codex | `outsource/profile-tuning-frontend` | `.scratch/renewable-profiles/outsource/G-b-profile-tuning-frontend.md` | same as G-a | ready: G-a merged 7234bfbd (26 Oct) |
| H | Results KPIs and the Curtailment tab stop counting hydrogen as renewable | Codex | `outsource/carrier-kpis-hydrogen` | `.scratch/timeseries-generators/outsource/H-carrier-kpis-hydrogen.md` | `.scratch/timeseries-generators/issues/13-carrier-kpis-hydrogen.md` | merged 763d8fcd (#699) |
| I | The assistant can show a snapshot, open an edit card and reach the hybrid/site canvas views (LC-14 items 1, 2, 3, 6) | Codex | `outsource/navigation-reach` | `.scratch/live-copilot/outsource/I-navigation-reach.md` | `.scratch/live-copilot/issues/14-navigation-reach.md` | PR #700, merged 2026-10-26 21:30 CET |
| J | Nine wording and label fixes in the frontend (finance flags, campus assets, island cuts, Back pill, limit bands, Grid plan tile) | Codex | `outsource/ui-wording-polish` | `.scratch/project-workflow/outsource/J-ui-wording-polish.md` | none | merged f82c8c9f (#706) |
| K | The assistant checks whether solved results make sense: first five plausibility rules and one read tool (LC-20) | Codex | `outsource/results-plausibility` | `.scratch/live-copilot/outsource/K-results-plausibility.md` | `.scratch/live-copilot/issues/20-results-plausibility-check.md` | ready: #694 merged 26 Oct; GK plan first (draft PR) |
| L | Reports list the project's template ledger in an assumptions appendix (E1) | Codex | `outsource/report-template-ledger` | `.scratch/project-workflow/outsource/L-report-template-ledger.md` | `.scratch/project-workflow/issues/E1-report-template-ledger.md` | merged 18ff198d (#704) |
| M | `scripts/coord/`: premerge counts only a comment's first line as a verdict; sessions.py prints `?` for unreported context | Codex | `outsource/coord-script-fixes` | `.scratch/coordination/outsource/M-coord-script-fixes.md` | none | merged d4ee749f (#705) |
| N | Fix stale `Status:` lines in `.scratch/` (docs only) | Codex | `outsource/stale-status-lines` | `.scratch/coordination/outsource/N-stale-status-lines.md` | none | merged dc065537 (#703) |
| #711 | Editable campuses and Blender exchange (spec only) | Codex | `claude/site-scene-generation-spec` | `.scratch/site-scene-generation/execution-guide.md` | — | merged 2026-10-26 20:53 CET (GK B + UX OK @fd485e50; fixes by our session, CODEX-711-OWN) |
| #712 | Optional asset footprint dimensions (width, depth, height) in Site 3D and Properties | Codex | `claude/asset-footprint-parameters` | PR body | — | merged 26 Oct 20:05 CET (UX OK @1f7a44ba) |
| #713 | Library gap research and implementation plan (19 tickets, LG-01…LG-19) | Codex | `claude/library-gap-implementation-plan` | PR body | — | draft; GK plan OK @1d79b630 with 9 conditions |

I and G-a both edit `harness/catalogue.py` and the capability inventory: whichever merges second merges master and regenerates the inventory with `--write`, never by hand. H touches no file G-a or I touches.

K also adds a tool to `harness/catalogue.py` and regenerates the capability inventory, so it starts only after G-a (PR #694) has merged, from the new `master`; it merges `master` and runs `--write` again before it opens for review. G-b waits for #694 as well (its row). J touches no file G-a, H, I or K touches; M and N touch no app code.

Ordering: if C merges before B, B adds its one new kind (`task_step_retry_limit`) to the manifest; both briefs say so. E (LC-22, the "not via chat" list) builds on A (merged), since both edit `find_capabilities`. Whichever of D and E merges second classifies the live preview path; both briefs say so.

Source: the PR #567 assessment, `.scratch/harness/pr-567-assessment.md`.
