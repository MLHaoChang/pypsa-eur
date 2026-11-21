<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 02 — Economics › Discount rate is saved but changes nothing in the model or Results

Status: needs-triage
Impact: high
Templates: IPP (Economics › Rates); Ammonia and Concentrator: not tried
Found by: template walk, 2026-11-21 (master 6b421b06)
Shot: ../shots/ipp-e-econ2.png

## What

IPP, Expert › Economics › RATES: Discount rate 7 → 10 %, "Save rates", Run LOPF. The field keeps 10 afterwards. But the battery's Properties still say "Discount rate: Default 7 %" and "Capital cost (annualised) 63.04 EUR/kW/yr", and Results are identical to the run before (Total -4.52 M, New-build capital cost 1.28 M). The investment case then says "WACC gate: differs from the LP discount rate (discount rate differs ...)", so the project rate and the solve rate now disagree and nothing in Economics says so. The panel text says it "annualises each overnight cost over its lifetime", which a user reads as "this changes the asset costs".

## Why it matters

A user testing a 10 % rate sees no effect and no message. This is the one Expert edit that was saved and silently did nothing to the run.

## Suggested fix

Either apply the rate to assets that use the default, or state beside Save rates what it affects (investment case only) and warn when it differs from the rate the last solve used.
