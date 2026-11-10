<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# UX follow-ups from the 10 Nov reviews

Status: needs-triage
Type: polish (wording, consistency)
Found by: UX (15) and UX (16) reviews on 2026-11-10, relayed by the coordinator.

## Items

- Microgrid ledger defaults (horizon, dates) have no Expert home (#1313).
- The Expert EditCard table shows raw `s_nom` where Guided says "Rating" (#1325).
- The Guided grid-study jump shows two Back controls (#1328).
- `ic_counterfactual.reason_words` joins with "; ", not " · " (#1333).

## Already owned elsewhere

- Results header says "optimal" / "period all" / "window …T00:00" while chart source lines say "solved" / "all periods" (#1336), and Build KPI tiles say "(annualised)" while charts say "per year": both are in ui-modern 03 slice 2.
- `.scratch/live-copilot/issues/44` is done by #1325 (Status line updated here).
