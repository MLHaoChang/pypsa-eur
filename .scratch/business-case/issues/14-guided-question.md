<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 14 — Guided question: is this plant worth building?

Status: ready-for-agent (blocked by 08, 11, 12, 13; ../../study-harness/issues/10-profile-datacenter.md for the DC profile; Opus; GK B)
Type: task
Blocked by: 08, 11, 12, 13; ../../study-harness/issues/10-profile-datacenter.md for the DC profile
Gated: yes (GK B)
Model tier: opus
Size: S

Read [the spec](../spec.md) §7, the DC [07](../../dc-business-case/issues/07-guided-question.md)
it replaces and `harness/workflows/investment-decision.md` first.

## What to build

A `plant_business_case` question in `investment-decision`: apply the template's
preset, ask each empty revenue price with `ask_user` (never invented), offer the
assumed cost defaults with source and year, run the IC then the business case, and
give the verdict with EBITDA margin, NPV/IRR and the break-even price. The report
lists every default it relies on. A template with no preset yet uses the generic
offered rows.

## Comments

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-14: GK B (56) plan GO

Plan (session_01G7qCunGxgzvuAfagz4aKwV): a server-filled `apply_business_case_preset` write tool (the preset leaves "out of chat"), the report's `assumed` list and workbook table, a server-side verdict in the `get_business_case` summary, and the `plant_business_case` question in `investment-decision`. Q1 = A (the tool, defaults only from the server's pack read), Q2 = A (NPV sign only, break-even headroom as a figure, no "marginal" band), Q3 = A (empty cost prices are asked in the same prices card). Conditions:

- B1: the card shows the variant, every row with value, unit and source, every removed line, the kept capacity/kw_per_gpu and the break_even_line choice; lists never as a count; the digest binds that plus the pack id and version. A pack, template or if_match change since the card refuses (test the pack change).
- B2: only `variant` and `rows` come from the model, each a member of the server's list (an unknown one refuses with the list, never fuzzy); extra argument keys never reach the write (test).
- B3: the preset write runs every validator set_business_case_lines runs, through the same PUT (test a row a linked ledger stream also prices).
- B4: "Keep all" never turns a default into a user value; `assumed`, the report list, the workbook table and the summary keep the pack source and year; the list carries its true total.
- B5: worth_building needs project_npv ok, finite and ≥ 0 (NaN, inf, None, not-ok each not_established, tested); a verdict resting on assumed inputs says so and lists them; break-even headroom is finite or None, never inf.
- B6: run the route-disposition, ACL, write-lock, golden-coverage, lock-gate parity, run-monitor (NEVER_LISTED), family-manifest and error-kind tests, and the inventory, route-parity and control-matrix checks at the trial merge.
- B7: remove only the preset's not_via_chat entry; a test makes the preset ask a non-hit and the other business-case asks still hit.
- B8: the one prices ask_user turn is the citation; a price the user never gave is refused (test).
- B9: each refusal kind has its own killing test with its status; mutants on the tool, the verdict and the report hunk, survivors listed.

### 2026-11-14: GK B (56) Q4 = A, the `set` patch

Finding: every tool result is cut at 4000 chars (`harness/results.py`) and a stored preset is 3.9k–9.9k, so the read-whole/send-whole `set_business_case_lines` cannot store the asked prices on a preset. Ruling: an optional `set` argument patches the stored config server-side. Conditions:

- S1: `set` applies to the stored config bound to if_match; a change since the card refuses business_case_changed; the approval digest binds the if_match and the normalised `set`.
- S2: the card shows each changed cell (line, column, old → new); untouched lines unchanged, never removed; a two-cell `set` changes exactly those cells and leaves the rest == (test).
- S3: dropping `assumed[col]` needs a user citation for every column it covers (quantity, life_years, money_year, the *_by_year columns, not only price), also when the value equals the default; otherwise price_not_from_user (or a sibling) and nothing is written (test a non-price column).
- S4: typed refusals, never a 500, a test each: unknown line, column outside the whitelist, `set` with `business_case` or neither, empty `set` (empty_edit), non-dict `set`, non-finite value.
- S5: after the patch the same chat_edit.plan runs; on the smallest preset a `set` and the matching whole-object send store the same config and show the same card rows (test).
- S6: mutate each S4 guard and the S3 citation check; list survivors.
