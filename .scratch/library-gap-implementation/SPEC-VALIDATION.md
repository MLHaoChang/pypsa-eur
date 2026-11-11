<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Specification validation — 2026-10-25

Validated this documentation package against master `aba252d2384f55116bbd15516a5b025951ba265e`:

- JSON/TOML parse checks passed.
- 19 unique tickets, all with research questions, code anchors, implementation steps, stop conditions and planned acceptance cases.
- 95 unique planned cases, each present once in the matrix and assigned to one ticket; all marked planned.
- All dependency targets exist, are ordered and form an acyclic graph.
- 97 native path anchors exist in the inspected tree.
- 188 relative Markdown links in the package resolved at validation time. Adding this note does not change those links.
- Historical JUnit and copied summary reconcile: 379 existing tests, zero failures/errors/skips. This is prior master validation, not new implementation testing.

These are structural/content checks of the specification. They do not validate new data, source rights, future parsers, electrical/financial accuracy, UX journeys or domain calibration. No new runtime test was executed because this PR changes documentation only. Applicable GitHub checks, current review verdicts and premerge must be recorded against the actual pushed head; this file does not pre-claim their success.

The package preserves the original assessment and evidence, adds no dataset and changes no runtime code. Exact commands/output from the temporary validator are retained locally in `/workspace/scratch/library-gap-plan-20261025/` for the current session; the checked properties and machine-readable inputs are recorded here and in `plan.json` for independent repetition.
