<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 51 — A scope preset for the grid templates

Status: needs-triage
Gated: GK B + UX
Type: task
Parent: SH 15 (grid_only@1) follow-up; owner decision SH15-SCOPE (A, under PR-SIZE-1)

## Problem

The four grid templates (`3bus`, `ieee14`, `belgium`, `ieee39`) are not in `flow_scope.TEMPLATE_PRESETS`, so `flow_scope.preset_for` gives them `("eh_generic", _OWN_SITE)`. A `grid_only@1` run (and the project's scope cards) is then labelled "eh_generic": it lists the EH reliability goal study and investment-case cards and marks the grid card "not part of this template's study", although setup switches Load flow on (SH15-PF-1). `("grid", "load_flow")` is also `NOT_YET` in `flow_scope.AVAILABILITY`, so the scope card shows Load flow unavailable. Issue 15 asked for `BLANK_PRESET` scope.

## Ask

Add a grid preset for the four templates to `TEMPLATE_PRESETS` (`BLANK_PRESET` plus `grid: ("load_flow",)`) and make `("grid", "load_flow")` available for them, so the run and project scope cards show the grid study. Test the preset id and cards `run_profiles.scope_for` returns for each grid template.
