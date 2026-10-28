<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 17 — Generate the hub-design, run-study and investment-decision workflow files from profiles

Status: ready-for-agent
Type: task
Blocked by: 07
Gated: yes (GK B)
Model tier: sonnet
Owner decisions: SH-1, SH-FOLD-1
Design: [design.md](../design.md) §2, §12

## What to build

`harness/workflows/hub-design.md`, `run-study.md` and `investment-decision.md` become generated from the profiles (stage order, gates as plain-language "done when", the tools per step), so chat guidance and harness runs cannot drift. A generator script plus a test that fails when a checked-in file differs from the generated one. The workflow registry and `advance_workflow` stay as they are.

## Acceptance criteria

- [ ] Generated files are checked in; the drift test passes; editing a profile without regenerating fails it.
- [ ] Existing workflow tests pass; each step's `done_when` names the gate rule ids it mirrors.

## Likely files

`pypsa-gui/backend/harness/workflows/`, `pypsa-gui/backend/harness/workflows/registry.py`, `scripts/` or `pypsa-gui/backend/tools/` (generator), `pypsa-gui/backend/tests/test_workflows_from_profiles.py` (new)

## Comments
