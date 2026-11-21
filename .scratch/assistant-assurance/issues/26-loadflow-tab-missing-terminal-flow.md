<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Load flow tab reads one terminal when the other is missing

Status: needs-triage
Type: task
Blocked by: 13 (required warning slots, #1831)

## Problem

`loadflow_summary._summarise_class` (`pypsa-gui/backend/services/loadflow_summary.py`)
takes a branch's flow as `np.fmax` of the two terminals. `np.fmax` ignores NaN, so
when `p1` (or `q1`) is missing at a snapshot, the tab shows terminal 0 alone as the
branch flow. The KPI rule (#1424 A1–A5, B1–B3) forbids that: a missing flow reads
as unavailable, never as 0 or as a neighbour's value.

AA-13's `ac-branch-loading/1` (`harness/ac_branch_calculations.py`) follows the rule
(GK B4 (2) ask 1). A missing p or q at either terminal makes its flow metrics
unavailable. A branch with no reactive flow answers its rating only
(`reactive_flow_unavailable`), with no P proxy. The tab and the profile therefore
differ in those cases. `test_ac_branch_loading_assurance.py` pins parity only when
every flow is present.

## Ask

- Decide whether the tab should show a missing-terminal snapshot as unavailable
  instead of using `np.fmax`.
- Decide whether the tab's `active_power_proxy` basis for branches without q stays.
  It is labelled in the tab today, but the profile answers no loading for them.
- If the tab changes, extend the parity test to the missing-terminal case.
