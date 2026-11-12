<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 43 — Exporting an older report version uses the latest version's mapping plan

Status: needs-triage
Type: bug
Gated: yes (GK B)
Parent: [07f-report-lock-remaining-directory-operations.md](07f-report-lock-remaining-directory-operations.md) (GK B (46) E5)

## What

A report's meta keeps a single `mapping_plan` (and `template_mode`, `template_language`, `generation`), the latest version's. `export_report` (`routers/reports.py`, `body.version`) loads the requested version's document but always reads `load_meta(...).mapping_plan`. So exporting version k < latest with an untagged template renders version k through the mapping plan accepted for a later version. If the later version unbound or changed the template, version k gets a plan made for a different outline. Nothing says so.

07f-b fixed the same kind of borrowing for the latest version only: a job's unrecorded settings are marked `settings_unrecorded`, and export refuses `report_settings_unrecorded` when the latest version's mapping was not recorded.

## Acceptance criteria

1. Export of version k uses the mapping plan that applied to version k, or says plainly that none is recorded for it (a typed refusal or a visible note).
2. A test exports version 1 after version 2 changed the plan, and shows the outcome for version 1.
