<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 06 — Ledger guards: double count, lost production, reference purchases

Status: ready-for-agent (blocked by 05; Opus; GK B and GK A)
Type: task
Blocked by: 05
Gated: yes (GK B, GK A)
Model tier: opus
Size: S

Read [the spec](../spec.md) §3 G2–G4 and GK A's LO-4 ([04](04-linked-outputs.md) ##
Comments) first.

## What to build

In `build_business_case` (05):

1. G2: a line `linked` to an output a ledger stream already prices (export,
   PPA/CfD settlement, bill streams) is refused `double_count:<stream>`, never
   warned. A product bus is never refused: G3 (item 2) handles it (GK A F1).
2. G3: when a revenue line reads a product bus's served quantity, that bus's
   `lost_production` lines leave the business-case case; kept otherwise. The
   line is not refused (GK A LO-4 aligned to G3, 2026-11-10). Part of a bus
   (G3-4): when the bus has a load no triggering line reads, only the read loads'
   pro-rata share by p_set(t) is dropped (the LO-3 rule), never refused.
3. G4: no reference-purchase value reaches the business case.
4. The locked "from the energy model" rows: a read-only list of the ledger streams
   the case carries, for 09 to show.

## Tests

- Each ledger stream that can clash (LO-4) refused, with its kind.
- A product bus with a shortfall: revenue drops by the unserved quantity × price
  exactly once (a mutant that keeps `lost_production` fails it).
- No line, no drop: `lost_production` stays when no line reads the bus.
- H2 hub and ammonia: no reference-purchase value in the business case (pin).
- IC byte-equal (BC1).

## Comments

### 2026-11-10: GK A (20) plan GO for G3/G4, conditions (verbatim, PR #1322 issuecomment-6759149723)

- **G3-1 One helper.** Map slack → bus with `finance_case._lost_production_slacks` (covers a captured slack with no Generator), never by stream class: `lost_production` and `fuel` share escalation class `fuel`. Test: a real `fuel` line, and a second product bus with no sale line, both stay.
- **G3-2 Trigger set.** Only a *revenue* line whose quantity is linked to L1 `served_product`, L2 `served_h2` or L2b `shipped` on that bus. An opex line on the same quantity (a royalty per t) and a `share_of` line never trigger the drop. Test each.
- **G3-3 Per period.** Drop the bus's `lost_production` only in the periods where a triggering line is active. A line that covers some years keeps the penalty in the others. Test it on a multi-period network with non-unit weightings.
- **G3-4 Part of a bus.** When the bus has a load that no triggering line reads (L2 is per load), drop only that load's pro-rata share by p_set(t), the LO-3 rule, or refuse with a typed kind. State which in the plan, and test a two-load bus with one line.
- **G3-5 Exactly once.** Business-case NPV with the sale line = the same case without `lost_production` and without the line, plus Σ served × price. Mutants that keep `lost_production`, or that drop it with no line, must each fail a test. Use a store-bearing bus where an hour's shortfall exceeds its load (LO-8).
- **G3-6 Shown.** The locked "from the energy model" rows (item 4) list each dropped `lost_production` as dropped, with the line that superseded it, so a €0 sale line can't remove the penalty silently.
- **G4-1 Pin by value.** On the H2 hub and on ammonia, the business case is byte-equal when the reference price changes (for example grey H2 2.0 → 9.0 €/kg). No business-case line carries a `reference_supply:`/`reference_purchase` asset or stream.
- **G4-2 Digest.** `reference_purchases` doesn't change the business case's values, so leave it out of the business-case hash domain, with a test that changing it leaves that hash unchanged. Or say why it must stay in. The IC hash is untouched either way (BC1, the IC byte-equal pin).
- **G-A1 VoLL check.** Show by a test that a business case with an L4 `served_load` line on a bus with VoLL shed has no project-side line pricing that shed. If one exists, the G3 rule covers it too.

G3-4 choice: the pro-rata drop (LO-3 rule), not a refusal (item 2).

### 2026-11-11: triage

Checked on master: `services/finance/business_case.py` and `project_templates/business_case_presets/` do not exist; 01 and 02 are done (#1322, #1320); business-case 04 is in progress in another session and was left alone. Verdict: ready-for-agent, held by its Blocked by line. Likely files, tests and acceptance are in the body.

### 2026-11-11: GK B (47) ruling on served_load against the bill streams (BC 04 F1, PR #1383)

Ruling (a): a linked `served_load.<load>` meets no bill stream. Against a valued
`eaas_fee` it is refused `double_count:eaas_fee` (fail closed). Conditions:

- **C1 Pin the premise.** The ruling holds only while every bill stream prices grid
  import. Pin that with a test; a future bill item that prices the load joins
  `served_load`'s clash set, and the `ledger_clash` docstring says so.
- **C2 Tests here in 06.** One per stream: `served_load` against each bill stream is
  admitted; against `eaas_fee` refused; with both valued, refused on `eaas_fee`. Then
  mutate the `eaas_fee` branch and show the mutant killed.
- **C3 Help text.** The microgrid preset's typed "Availability / EaaS fee" row's help
  says the locked rows and the help are what stop duplication.

04's `ledger_clash` (PR #1383) returns None for `served_load` against every stream;
06 adds the `eaas_fee` branch, the C1 pin and the C2 tests.
