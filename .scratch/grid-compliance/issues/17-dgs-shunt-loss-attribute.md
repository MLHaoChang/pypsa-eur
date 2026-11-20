<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-17 DGS: write a shunt's active power once its loss attribute is confirmed

Status: needs-info
Type: task
Parent: `16-dgs-shunt-p-and-line-g.md`
Gated: **yes** (GK A2, gridspine)

## What

GC-16 lists a shunt's non-zero `p_mw` instead of writing it, because no public source names ElmShnt's
active-power or loss attribute. Once someone with the tool confirms it, write the loss there. That could be a
capacitor loss factor (p / |q|) and its reactor equivalent, or a conductance; the confirmed attribute decides
which. Then drop the listing and pin the per-unit value against pandapower.

## Blocked by

A confirmed attribute name and unit, from the tool's reference or from an export by the tool.

## Comments

**2026-11-17 triage (SCALE-1).** Cannot start: it needs a confirmed ElmShnt active-power or loss attribute name and unit from the tool's reference or an export by the tool. Nothing on master or in an open PR supplies it. Needs the owner (or whoever has PowerFactory) to provide a DGS export or the reference page. Set ready-for-agent once the attribute is in the file.
