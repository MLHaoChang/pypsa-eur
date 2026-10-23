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
| D | Opening the cost-reference card no longer throws away a solve (read-only preview) | Codex | `outsource/library-reference-preview-read-only` | `.scratch/harness/outsource/D-library-reference-preview-read-only.md` | `.scratch/harness/issues/22-library-reference-preview-is-a-write.md` | ready to hand out (GK plan OK 2026-10-23) |
| E | The assistant names what it cannot do from chat; every route has a chat disposition (LC-22) | Codex | `outsource/not-via-chat-list` | `.scratch/harness/outsource/E-not-via-chat-list.md` | `.scratch/live-copilot/issues/22-unsupported-by-chat.md` | ready to hand out (GK plan OK 2026-10-23) |
| F | "hydro" and "hydrogen" generators stop filing under demand response | Codex | `outsource/gen-category-word-match` | `.scratch/timeseries-generators/outsource/F-gen-category-word-match.md` | `.scratch/timeseries-generators/issues/11-gen-category-word-match.md` | ready to hand out (GK triage + plan OK 2026-10-23) |
| G-a | Profile tuning, backend: tuning + schema route, compare service/route and `compare_profiles` tool (RP-05/07/12) | Codex | `outsource/profile-tuning-backend` | `.scratch/renewable-profiles/outsource/G-a-profile-tuning-backend.md` | `.scratch/renewable-profiles/issues/05-tuning-panel.md`, `07-before-after-compare.md`, `12-representative-weeks.md` | handed out 2026-10-23 16:06 CEST (GK plan OK 2026-10-23) |
| G-b | Profile tuning, frontend: Tune dialog, before/after compare, weather-bar window line | Codex | `outsource/profile-tuning-frontend` | `.scratch/renewable-profiles/outsource/G-b-profile-tuning-frontend.md` | same as G-a | waits for G-a to merge (GK plan OK 2026-10-23) |

Ordering: if C merges before B, B adds its one new kind (`task_step_retry_limit`) to the manifest; both briefs say so. E (LC-22, the "not via chat" list) builds on A (merged), since both edit `find_capabilities`. Whichever of D and E merges second classifies the live preview path; both briefs say so.

Source: the PR #567 assessment, `.scratch/harness/pr-567-assessment.md`.
