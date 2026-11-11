<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# G2P-13: N-1 bound wording, the report's empty "()" source, em dashes in the disclosures

Status: done (#1338)
Type: task
Found by: UX (13) review of G2P #22 (#1249); pre-existing, not asks there.

## Problem

- The N-1 line's own Notes read "some units are not known, so this is a lower bound" when its bound comes from an outage longer than the period (the sub-rows no longer do, #1249).
- The report table's Source cell shows an empty "()" when a line's stamp has no words (`chapters._stamp_words` returns "").
- The grid-risk report disclosures use em dashes ("Grid risk to production — …").

## Where to look

`services/results/grid_risk_production.py` (`_n_1` line flags), `services/reports/chapters.py` (`grid_risk_table` source, `grid_risk_disclosures`), `GridRiskTab.tsx` (`FLAG_WORDS`).
