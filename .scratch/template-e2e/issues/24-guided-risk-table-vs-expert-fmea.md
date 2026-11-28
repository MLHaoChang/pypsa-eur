<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 24 — Guided risk table disagrees with Expert FMEA and the report

Status: done
Source: decide-report-journeys.md (walk 2026-11-17, master 0ab07339)
Severity: misleads
Journey fit: results: numbers must agree between Guided, Expert and report.

## What

Cost, LOLE and the top risk agree across all three on every template (PASS). The Guided risk table does not: DC gensets EUR 0/yr against EUR 760/yr in Expert FMEA, genset_new (EUR 12,529) only in Expert; microgrid diesel EUR 0 against EUR 302 k each; hospital genset_1..3 at EUR 0 and critical_feeder omitted; ammonia 'Nothing to show yet' while Expert lists EUR 17,915 and Improve says grid import is 100% of the risk. Raw names show (genset_new, diesel_1, critical_feeder, weak_flexible; see 05, 11). Hospital: the 50% must-stay-on share is stated only in section 4, never in results or report; Expert LCOE EUR 413/MWh has no caveat.

## Acceptance

- Guided risk rows equal Expert FMEA rows.
- Results state the critical share and MW kept on.
- Implausible LCOE carries a caveat.

## Triage (2026-11-20, master b7e5dee3)

Checked by code reading only; the app was not re-run. Acceptance as above: Guided risk rows equal Expert FMEA rows, results state the critical share and MW kept on, implausible LCOE carries a caveat. Overlaps the FMEA-unpriced work already merged (#1662): re-check the EUR 0 rows in the app first. Gated: GK A. Blocked by: none.

## Comments

- 2026-11-23 status sweep: landed in #1662 (a3e82685): unpriced FMEA rows no longer show EUR 0. Remains: Guided-versus-Expert row equality, critical share and MW kept on, LCOE caveat.
- 2026-11-27: slice 1 merged in #2092 (d1799373). The Guided risk table, Expert FMEA, the reference-design panel, the report and the results workbook all read the EH study's fmea_top rows. A failed study with no report no longer serves the old stored report (GK A M1).
- 2026-11-27: slice 2, part 1 merged in #2097 (84c2274b). The Guided Results card states the load kept on in an outage, in the Site card's words.
- Remains: the LCOE caveat, held until the owner sets the plausibility bound (UX 32 gate: no placeholder bound; caveat on the investment "LCOE (real)" in both modes; the cause sentence only when the site has a critical share and backup exists).
- Follow-up (GK C1 on #2092): put a network fingerprint on the stored EH report, so that a stored report from an edited network is not served as current.
- Known gap (named in #2092): the Guided ResultsCard "Biggest risks" list and the headline read fmeaTopRows, which holds class-B rows only.
- 2026-11-28: the LCOE caveat lands in #2117 (owner decision LCOE-CAVEAT-1: EUR 300/MWh). With it, all three acceptance lines are met, and Status is done once #2117 merges.
- Follow-ups (UX 33 on #2117, not blocking):
  - Money notation: the LCOE figure reads "413.2 €/MWh" while its caveat reads "€ 300/MWh", two notations on one card.
  - The caveat's "rarely runs" is not checked against dispatch.
  - Investment's "Project NPV … -2,400,000.00" row (on master already) is not in money()/signFirstEuro style (ruling (ad)). AA 36 (#2090) may already cover it.
