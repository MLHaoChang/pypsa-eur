<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Guided 'Next: Goal' names a step the stepper does not have and skips sections 4–7

Status: needs-triage
Impact: low
Templates: DC (seen on the connection step; same button on the others)
Found by: template walk, 2026-11-20 (master 1f6905e3)
Shot: ../shots/gdc-afterrun.png (stepper)

## What

In Guided, section 3 Connection & grid ends with a button 'Next: Goal'. The stepper has no step called Goal: the goal card lives in section 8, 'Scenarios & optimisation'. Clicking it opens section 8 and skips 4 Loads & demand, 5 Assets & sizing, 6 Time series & profiles and 7 Commercial & parties. Other steps say 'Next: Assets & sizing ›' or 'Next: Readiness and run ›', so the button names differ in kind.

## Why it matters

A first-time user taking the 'Next' buttons in order never sees the loads, assets, profiles or tariff, and looks for a section called Goal that is not there.

## Suggested fix

Label the button with the stepper's name ('Next: Scenarios & optimisation (Goal)') and either go to section 4 or say that 4–7 are skipped because they are complete.
