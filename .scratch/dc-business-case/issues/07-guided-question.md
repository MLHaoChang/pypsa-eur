<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 07 — Guided question: is this data centre worth building?

Status: needs-triage (blocked by 05 and study-harness 10)
Type: task
Blocked by: 05; .scratch/study-harness/issues/10-profile-datacenter.md
Gated: yes (GK B)
Model tier: opus
Size: S

Read [the spec](../spec.md) §7 and `harness/workflows/investment-decision.md` first.

## What to build

A `dc_business_case` question in `investment-decision`: ask the revenue model
and its prices with `ask_user` (never invented); offer the researched cost
defaults with source and year; run the IC, then the business case; give the
verdict with EBITDA margin, NPV/IRR and the break-even price as the driver that
could flip it. The report lists every default it relies on.
