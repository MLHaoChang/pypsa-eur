<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# New-build capital cost reads 0 after a failed expansion compute

Status: needs-triage
Type: bug (found in review of 03 S1, PR #1343, GK B N7)
Reviewer: GK B.

## What is wrong

`pypsa-gui/backend/services/results/cost_breakdown.py` (the `except Exception`
after the expansion CAPEX loop, about line 541) logs the failure and leaves
`capex_expansion` at 0.00. The Results headline strip then shows the
"New-build capital cost" card as a computed zero (state `zero`), and it can
differ from the Capacity expansion tab, which falls back to its per-asset sum
when the payload's figure is 0.

## What to build

A failed expansion compute reports `capex_expansion` as not available (None),
never 0.00, so the strip's card reads "unavailable" with its sentence and the
tab's fallback rule is explicit.

## Done when

- A test makes the expansion compute raise and reads `capex_expansion is None`
  in the `/results/cost_breakdown` payload.
- The strip's `new_build_capital` card is `unavailable` in that case, and the
  Capacity expansion tab shows the same.
