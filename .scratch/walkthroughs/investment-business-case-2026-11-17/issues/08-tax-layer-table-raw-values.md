<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 08 — The investment case's tax-layer table shows raw values

Status: needs-triage
Severity: low (Expert, detail table)
Group: Investment and business case. Found in the issue 02 re-walk, 2026-11-23, master 443ec313.
Breaks: owner decision RAW-NAMES-1.

## What

Expert › Results › Commercial & finance › Investment case, the Tax section's detail table (rendered by `toTable`, InvestmentCaseView.tsx) prints payload values as stored: "tax pack id: eu_de", "tax losses: offset_other_income", "financing fee tax: not_deducted", "flags: remaining_basis_written_off:gewst, remaining_basis_written_off:kst", and the footer "Case investment_case; … packs eu_de …".

## Repro

Data Center Energy Hub from the template, defaults → Readiness and run › solve → run the investment case → Expert › Investment case › Tax.

## Expected

Values in words (the tax pack's name, "offset against other income", "not deducted"). The flags line reuses `projectFlagWords`, which has words for `remaining_basis_written_off` since issue 02.
