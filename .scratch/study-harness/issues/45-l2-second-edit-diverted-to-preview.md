<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 45 — A second `update_component` in one L2 turn is diverted to `preview_project_changes`

Status: needs-triage
Type: bug
Gated: yes (GK B; harness)
Parent: [13-profile-ammonia.md](13-profile-ammonia.md) (found while building #1429)

## What

In an L2 (result-stage) agent round, the model may change up to 2 levers per
round. When it emits a second `update_component` in the same response (or after
one already applied this turn), the chat edit routing
(`harness/edit_routing.py`, LC-16 slice C) refuses it with
`use_project_preview` toward `preview_project_changes`. The L2 stage allowlist
does not offer `preview_project_changes`, so the second change never lands: a
round applies one change, below its 2-change cap.

This affects every staged profile with L2 levers (the H2 hubs and
`eh_ammonia_staged@1` alike), not only ammonia.

## Expected

An L2 round can land up to its cap of changes. Either the routing does not
divert inside a run stage whose allowlist lacks the preview, or the run's
envelope admits the preview/apply pair for lever edits.

## Acceptance criteria

- [ ] A real staged run where L2 proposes two lever changes in one round applies both (or the cap is lowered to 1 and the design says so).
- [ ] Chat (non-run) turns keep the LC-16 one-card routing.
