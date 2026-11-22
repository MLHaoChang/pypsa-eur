<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Economics › Asset costs shows "0 €/kW/yr" annualised capital cost for priced candidates

Status: needs-triage
Impact: medium
Templates: Ammonia (multi-period; the single-period IPP shows its battery cost correctly)
Found by: template walk, 2026-11-21 (master 6b421b06)
Shot: ../shots/amm-e-econ.png

## What

Ammonia, Expert › Economics › Asset costs. Rows such as `wind_new_2027` (overnight 1,383 €/kW, 30 y), `solar_new_2027` (482 €/kW), `electrolyser_new_2030` (1,886 €/kW, 25 y) and `h2_storage` (60.05 €/kWh) show "Capital cost (annualised) 0 €/kW/yr" (and 0 €/kWh/yr), next to a non-zero fixed O&M. The batteries on the same page show 95.63 €/kW/yr. The model does charge the electrolyser: after the run Capacity expansion lists `electrolyser_new_2030` +12.0 MW with CAPEX EUR 2.8 M (about 233 €/kW), and the wind and solar candidates are not built, which a zero cost would not give. The header line also says "9 not priced: the model builds them for free" (grid supply, existing plants, Haber-Bosch).

## Why it matters

A user reading the table concludes the candidates are free, or that the table is wrong, and cannot check the figures the run used.

## Suggested fix

Show the cost the solve charges per period (or say "per-period: see the card"), and keep the "not priced" and "0" cases visibly different.
