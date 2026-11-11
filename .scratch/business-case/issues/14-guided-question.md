<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 — Guided question: is this plant worth building?

Status: ready-for-agent (blocked by 08, 11, 12, 13; ../../study-harness/issues/10-profile-datacenter.md for the DC profile; Opus; GK B)
Type: task
Blocked by: 08, 11, 12, 13; ../../study-harness/issues/10-profile-datacenter.md for the DC profile
Gated: yes (GK B)
Model tier: opus
Size: S

Read [the spec](../spec.md) §7, the DC [07](../../dc-business-case/issues/07-guided-question.md)
it replaces and `harness/workflows/investment-decision.md` first.

## What to build

A `plant_business_case` question in `investment-decision`: apply the template's
preset, ask each empty revenue price with `ask_user` (never invented), offer the
assumed cost defaults with source and year, run the IC then the business case, and
give the verdict with EBITDA margin, NPV/IRR and the break-even price. The report
lists every default it relies on. A template with no preset yet uses the generic
offered rows.

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.
