<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — "Checked figures are off on this installation" replaces LCOE and Curtailment rate with no way forward

Status: needs-triage
Impact: low
Templates: all three (Expert Results › Summary)
Found by: template walk, 2026-11-21 (master 6b421b06); raised by UX review of PR 1797
Shot: ../shots/amm-e-res.png

## What

On the IPP, concentrator and ammonia the "LCOE (real)" and "Curtailment rate" cards read "Not available for this run — Checked figures are off on this installation; an administrator can turn them on." The dev admin account used here is an administrator, yet nothing says where to turn it on. The LCOE card adds "The Investment case … gives the finance-consistent LCOE" with a button, which works where the investment case establishes it (not on ammonia, where LCOE is "not established").

## Why it matters

Two of five headline cards are dead for every user on this install, and the text names a person who is not the reader.

## Suggested fix

Say who and where ("Settings › Checked figures"), or hide the cards when the feature is off.
