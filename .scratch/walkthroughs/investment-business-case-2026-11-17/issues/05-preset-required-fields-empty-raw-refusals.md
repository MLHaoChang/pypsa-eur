<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 05 — Preset leaves required fields empty; the refusals are raw strings

Status: done (#1683: refusals name the line and field in words; #1868: presets fill an empty contract indexation from the finance inputs' PPA escalation, chat apply keeps receivable days and lease-up)
Severity: medium
Group: Investment and business case. Walk 2026-11-17, master 9491878d.
Breaks: `.scratch/business-case/spec.md` §6.6 (insurance default from the pack) and G9; owner decisions RAW-NAMES-1, DECISION-GAPS-1.

## Repro

IPP template, solve, apply "IPP hybrid", enter the capacity price, GoO and land prices, a plant capex, then Run the business case.

## Actual

- Refusal 1: `business_case_refused:indexation_missing: lines[0].indexation`. The two revenue lines carry the "contract" escalation class and need an indexation rate; no preset row, hint or form field offers one.
- Refusal 2: `business_case_refused:quantity_missing: lines[5].quantity`. The "Insurance" row (`share_of:capex`) ships with no value; spec §6.6 says its default comes from the pack (the data-centre preset ships 0.005 from a sourced default).
- Each refusal is shown as the typed code with a line index, not as the row's name and a sentence.
- With the dc preset the same screen offers `Sga`, `Ppa` as category labels (see issue 06).

## Expected

The IPP insurance row ships a sourced assumed default like the data-centre one, or the spec is changed to say none exists. Indexation for a contract line has a visible field with the rate the finance inputs already hold as the default (the IPP finance block already holds 0.02 for opex, ppa and tariff). A refusal reads "Guarantees of origin: set an indexation rate" and focuses the cell.

## Acceptance

- Test: apply the IPP preset, type only the prices, run: it runs or names each blank row in words.
- No `lines[n].column` string in prose; the row name and the column label instead.
