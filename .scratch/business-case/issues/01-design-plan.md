<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Design plan: a business case on every template

Status: ready-for-agent (GK B plan GO G-C1..G-C8, GK A plan GO LO-1..LO-10, 2026-11-10; this PR)
Type: task
Blocked by: —
Gated: yes (GK B finance plan GO; GK A for the template and IC reads)
Model tier: opus
Size: S (docs only)

Read [the spec](../spec.md) first.

## What this PR holds

The spec, the research note and the slice files 02, 04–14. No code. There is no
03 here: the IT energy series stays [DC 03](../../dc-business-case/issues/03-it-energy-series.md),
unchanged, and becomes the catalogue's L7. The DC issues 02 and 04–07 are marked
superseded (spec §8).

## Done when

- GK B has given a plan GO on spec §3–§4 and GK A on §5 and G3–G4.
- The owner's answers (BCA-SCOPE-1, A on all four) are recorded in spec §9 and the slices.
- This PR merges.

## Comments

### 2026-11-10: GK B (40) plan GO for BC-ALL 01 (finance side), conditions G-C1..G-C8

Verbatim:

> GK B (40): plan GO for BC-ALL 01 (finance side), with conditions G-C1..G-C8. GK A (19) still owes its GO on the G2–G4 template/IC reads. Record this in issue 01 ## Comments verbatim.
>
> Answers:
> (a) One stream per (kind, category), not a generic stream. Conditions:
>   - the category list is closed, and each category maps to one STREAM_CLASS row: revenue → contract or export class, opex → opex, capex never a stream;
>   - the line name goes in TemplateLine.key;
>   - a test that every category has its row, and a pin test that the IC's stream set and IC results are unchanged (B3/BC1).
> (b) Refuse. double_count:<stream> is a typed refusal, never a warning: a warning would let a double count into NPV. This is the same rule as #1320 F2.
> (c) Split it. 02 = the line model and its validation:
>   - kinds, categories, required capex life;
>   - Finite on every number (#1320 F1);
>   - G9 typed refusals naming line and column;
>   - the G6 share_of graph (one level, cycles refused);
>   - the unit vocabulary as an enum, with the rule that price unit = currency per quantity unit and no conversions.
>   The G5 conversion factors (H2 LHV, bus mwh_per_t) go to 04, beside the linked outputs they convert.
>
> Conditions:
> - G-C1 (money year): G1's "money_year = base year" is wrong for defaults. LineItem gets a money_year column, defaulting to the base year. A preset or pack default carries its source's money_year (the DC facility default is 2025), and compile passes it to TemplateLine.money_year. Test with a non-base money year.
> - G-C2 (tax column): the engine has no per-line taxable/exempt or non-deductible switch, so G1 adds no new tax logic. For v1, drop the per-line tax column: revenue is taxable, opex deductible and capex depreciated through its DepreciationClass, as the engine does today. If a template needs a switch, it is an engine change in its own slice, GK B-gated, with BC5's tie and a mutant.
> - G-C3 (capex the model does not own): presets declare `excludes` beside `requires`. A capex line in an excluded category is refused capex_not_owned:<category> (#1320 F2 generalised). Example: the DC colocation preset excludes it_hardware.
> - G-C4 (pass-through): a pass-through of the energy bill is refused under any preset whose revenue price includes energy (DC GPU-hour; my #1320 ruling 2). Presets declare it.
> - G-C5 (G2 double counting): a revenue or opex line with source `linked` to an output that a ledger stream already prices is refused, as planned. Typed lines cannot be checked, so the energy-model rows show locked beside the line table, and the spec says so. Revenue category `tariff` needs a one-line definition (what is sold), so it cannot be read as the energy bill.
> - G-C6 (hash): the BC hash covers preset_id@version, the lines and receivable_days; the IC hash and every other digest stay byte-identical (BC1 pin test).
> - G-C7 (BC carried over): BC2 (gross and net of any pass-through), BC3 (working capital only in the cash bridge), BC4 (P&L from per-stream op, never op_inc, with a year where net > 0 and gross ≠ net), BC5 (one tax and a tie with a mutant), BC8 (bracketed break-even, so G8 is on the BC8 rule; DSCR n/a without debt) and BC9 (pack rows appended, previous version byte-equal) bind on 05 and 06 for every template.
> - G-C8 (TDD plans): slices 02, 05 and 06 each bring their TDD plan to GK B before building.
>
> Size: if 05 grows beyond M with G2–G4 inside it, split the double-count and lost-production rules into their own slice rather than raise the size.

Earlier GK B points from #1320 (2026-11-10), carried into 02: F1 every unbounded
float is `Finite` with an inf test per field; F2 a capex line the revenue model
does not own is a typed refusal; a pass-through is refused under a price that
includes energy; a weakly sourced cost default may ship if assumed, with a source
and a range, and a cost with no source gets no default; each default's
`money_year` flows to its line.

Note on numbering: GK B's G-C7/G-C8 name slices by the plan message's numbers
(05 engine, 06 presets). In this spec the presets are 07 and the ledger guards
06, so G-C7 binds on 05 and 07 and G-C8 on 02, 05 and 07.

### 2026-11-10: GK A (19) plan GO for slice 04, conditions LO-1..LO-10

Recorded in [04](04-linked-outputs.md) ## Comments with GK A's answers and
standing points. GK A's GO on G3/G4 (slice 06) asked 2026-11-10.

### 2026-11-10: GK B (40) rulings on the addendum

Reusing #1320's branch is fine. (1) One kind per refusal, no aliases:
`pass_through_price_includes_energy` and `capex_not_owned:<category>` (category in
the field path), for DC too; `it_capex_missing` and `facility_capex_missing` stay
because the DC spec names them; `pass_through_not_colocation` never shipped and
does not survive. Every kind that reaches a tool is registered once in its PR's
error-kind fragment. (2) The re-scoped #1320's delta review needs the TDD plan
first (G-C8), the 2026-11-10 pack rows unchanged or re-proved by byte prefix, the
F1/F2 tests on the LineItem columns, and the `money_year` column in 02's model.

### 2026-11-10: owner decision BCA-SCOPE-1

A on all four (04:27Z, via the coordinator); recorded in spec §9.

### 2026-11-10: GK A rotation

GK A (19) handed off; GK A (20) is `session_01G7qCuj9VQMra1QJZv5J1s3`. The plan GO
stands as narrowed (LO-4 aligned to GK B G2/G3, [04](04-linked-outputs.md) ##
Comments). The slice 06 G3/G4 GO is asked of GK A (20).
