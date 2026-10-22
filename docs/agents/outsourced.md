<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Outsourced work

Tasks handed to coding apps outside our session coordination. Before opening a session on a task, check this table, and don't start one whose files an outsourced task touches. The brief lists those files.

An outsourced app opens one PR from its `outsource/<slug>` branch, with a body that starts with "Outsourced task <ID>". The coordinator finds it with `git ls-remote origin 'refs/heads/outsource/*'` and brings it in like any PR: the gatekeeper reviews harness changes, then premerge and the merge. When it merges, set the row's state to `merged <sha>` and the issue's `Status:` line to done.

| ID | Task | App | Branch | Brief | Issue | State |
|---|---|---|---|---|---|---|
| A | A tool the assistant just discovered is offered on its next request | Codex | `outsource/discovered-tools-offered` | `.scratch/harness/outsource/A-discovered-tools-offered.md` | `.scratch/harness/issues/23-discovered-tools-offered.md` | handed out 2026-10-22 |
| B | A failing plan step stops after two retries | Codex | `outsource/task-step-retry-limit` | `.scratch/harness/outsource/B-task-step-retry-limit.md` | `.scratch/live-copilot/issues/25-task-step-retry-limit.md` | handed out 2026-10-22 |
| C | Classify the `_refuse(...)` error kinds in the tool-error manifest | Codex | `outsource/refuse-kinds-manifest` | `.scratch/harness/outsource/C-refuse-kinds-manifest.md` | `.scratch/harness/issues/21-refuse-kinds-in-error-manifest.md` | handed out 2026-10-22 |

Ordering: if C merges before B, B adds its one new kind (`task_step_retry_limit`) to the manifest; both briefs say so. LC-22 (the "not via chat" list) waits for A, because both edit `find_capabilities`.

Source: the PR #567 assessment, `.scratch/harness/pr-567-assessment.md`.
