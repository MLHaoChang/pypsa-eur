<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 57 — The results workbook names a grid project's preset from its marker

Status: needs-triage
Gated: GK B + UX
Type: bug

## What is wrong

`services/results_workbook/__init__.py:153` writes the cover sheet's "Workflow" row as `flow_scope.preset_label(inp.template)`. `inp.template` is the project's template marker. A grid template project (3bus, ieee14, belgium, ieee39) has no marker, so the row reads the blank preset's label ("a blank project"), not the template's ("IEEE 14-Bus"). Issue 51 (#1616) records a grid project's template id in `metadata.json` and reads it through `flow_scope.preset_template(project_dir)`; the scope screens and the run use it, but the workbook does not. The "Template" row on the next line reads "none (a network without a site template)" for the same reason.

Found by GK B (65) in #1616's review; not blocking there.

## Acceptance

- The workbook's "Workflow" and "Template" rows for a project created from ieee14 name "IEEE 14-Bus" and `ieee14`, read through `flow_scope.preset_template`.
- An EH template project's rows are unchanged; a project with no template keeps today's words.
- A test builds the workbook for a grid template project created through the real route.

## Journey fit

Report step (the results workbook a user downloads after a run), Guided and Expert. Hand-off: creation → report, through the recorded template id.
