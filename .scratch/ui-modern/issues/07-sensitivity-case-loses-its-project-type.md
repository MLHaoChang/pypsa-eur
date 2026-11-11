<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# A sensitivity case shows the plain-network headline cards, not its base's

Status: ready-for-agent (UM07-KIND-1)
Type: bug (found while taking the 03 S3 shots)
Reviewer: GK B.

## What is wrong

A sensitivity case made by `run_sensitivity_sweep` from an energy-hub project
has no `eh_template.json` in its folder, so `headline_kpis.project_kind` reads
it as a plain network. The base shows LCOE, system cost, new-build capital
cost, curtailment rate and unserved energy; its case shows system cost,
new-build capital cost, curtailment rate and "Peak line loading: Not
applicable". Shots: `.scratch/ui-modern/shots-03/s3-base-expert-dark.png`
beside `s3-case-expert-dark.png`.

The deltas are right for the cards both show (they are matched by card id);
the case simply never gets an LCOE or unserved-energy card.

## To decide

Whether a case should take its base's project type for the strip (and for
whatever else reads the template marker), or the sweep should copy the marker.

## Decided (UM07-KIND-1, owner 2026-11-11 23:52Z)

A case takes its base's project type, read-side: one helper resolves a
project's template marker, following a sensitivity case to its base, and
`headline_kpis.project_kind` and every other reader of the template marker
use it. Existing cases are fixed with no migration; the sweep does not copy
`eh_template.json`. GK B reviews (TDD plan first).

## Also seen, not this package's

- A template project solved once through `/api/simulation/queue` reads as out
  of date after it is reloaded from disk (the recorded solve fingerprint
  differs from the saved network's); a second queued solve clears it. Every
  case of such a base then reads "the base has no current results to compare
  with". Script: `.scratch/ui-modern/shots-03/s3_resolve.py`.
- Sensitivity cases are not listed on the Projects page.
- In a multi-period run the source line shows the snapshot window
  ("2030-07-01 → 2030-07-07 · all periods") while the trend spans the
  investment periods (2027 to 2035).
