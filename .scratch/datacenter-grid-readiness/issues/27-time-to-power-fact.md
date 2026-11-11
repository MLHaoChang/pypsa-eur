<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A checked "time to power" fact for data-centre projects

Status: needs-triage
Type: feature (engine)
Reviewer: GK A.
Found by: ui-modern 03 (KPI headline strip); owner decision TTP-1 (10 Nov 2026).

## Problem

The Results headline strip is meant to show "time to power" for data centres (DASHBOARD-1, UI-MODERN-1). No run computes it. The only related data is the staged template's energisation schedule (`pypsa-gui/backend/project_templates/eh_templates.py`, `DC_ENERGIZATION_MW`, and `eh_datacenter_staged/eh_template.json`), which is an input, not a result.

## What must be defined

- Which date the figure means: first energisation (the first MW served) or full load (the whole contracted MW served).
- Where it comes from: the staged build (the first stage whose capacity serves the load) or the connection queue (the grid connection's energisation date), and which wins when both exist.
- Its unit and start point (months from the project's start date, or a calendar date).
- What a project without a staged build or a connection date reads: a state with its reason, never 0.

## What done looks like

A checked formula with its own `formula_id`, read through `harness.calculations` like the other checked facts, with tests for a staged data centre, a single-stage one and a project with no date.

## Comments
