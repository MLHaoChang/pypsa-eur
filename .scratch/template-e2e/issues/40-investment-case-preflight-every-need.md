<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 40 — The investment case's start refuses with one need at a time

Status: needs-triage
Source: issue 22, slice S2 (#1761); GK B3 (4) on #1761, scoped by GK A
Severity: friction
Journey fit: results → decide (investment case).

## What

`POST /results/investment_case` (routers/results.py `post_investment_case`) and the runner's preflight
refuse at the first blocker they meet: `not_solved` (409), then one 422 code at a time
(`value_flows_not_configured`, `cod_missing`, `finance_inputs_missing`, …). The run panel
(`InvestmentCaseRunPanel`, S2) maps each code to a plain need and adds the needs other reads already
state, such as no site connection. Still, a user with three missing inputs learns of them one run at a time:
fix one, run, meet the next.

## Acceptance

- The preflight returns every need it can establish in one answer. It still refuses with the first
  blocker's own code and status, so the existing 409/422 contracts and their tests are unchanged.
- The extra needs ride in an additive field (for example `detail.needs: [code, …]`), each a code the
  parity table `pages/results/investment/investmentCaseNeeds.json` already names. The parity test
  (`tests/test_investment_case_needs_parity.py`) covers the new field.
- The run panel lists every returned need in its one ordered checklist (no client-side availability
  rule, UI-1).

## Notes

Backend (GK A area); the frontend part is small once the field exists.
