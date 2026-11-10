<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Linked model outputs and unit conversions

Status: ready-for-agent after 01 merges and 02 (GK A LO-1..LO-10; L7 waits on DC 03)
Type: task
Blocked by: 02; ../../dc-business-case/issues/03-it-energy-series.md (L7 only)
Gated: yes (GK A)
Model tier: opus
Size: M

Read [the spec](../spec.md) §3 G5 and §5, and GK A's conditions LO-1..LO-10 below,
first. Send the PR head to GK A when built.

## What to build

`services/business_case/linked_outputs.py`:

1. `catalogue(n) -> list[OutputSpec]`: the outputs this project offers (id, label,
   units), from its network; the catalogue version constant.
2. `read(n, result, output_id, unit) -> per-year series | not_evaluated`: L1
   served product, L2 served H2, L2b shipped concentrate, L3 derived O2, L4 served
   load, L5 export, L6 capacity, L7 the DC 03 IT energy and contracted kW.
3. Unit conversions beside the reads (G5): H2 MWh ↔ kg by `lhv_kwh_per_kg` from
   `models.commercial` or the line's stated LHV; MWh ↔ t by the bus's `mwh_per_t`
   or `eh_ammonia.MWH_PER_T`; MWh ↔ GPU-h by the DC row's kW per GPU. No new
   literals (LO-1).
4. Resolution of 02's `linked:<id>@<version>` into a series; an id this project
   does not offer is `linked_output_missing:<id>`.

## Tests

LO-1..LO-10 each have at least one; plus a test that every reader accepts each
catalogue version (#1132 F1).

## Not in this slice

The engine, double-count refusal (06), recoverable heat (out of v1).

## Comments

### 2026-11-10: GK A (19) plan GO, conditions LO-1..LO-10

Answers: (1) served = Σ (p_set − shortfall) per year is right even with stores: a
PyPSA Load always withdraws exactly p_set and the shortfall slack is what was not
produced; sum per year, never clip per snapshot (a slack filling a store, nh3_tank
or concentrate, can exceed the hour's load); store drawdown (e_initial − e_final)
is product sold, so it counts as served. Read the shortfall with the reader
ProductShortfall uses (case.py:554): only involuntary or product-shortfall slack
(slack.involuntary_slack_mask and the product-shortfall carriers), never
curtailment or DSR. (2) ProductShortfall's convention exactly: Σ p ×
snapshot_weightings.generators over the period ÷ the period's investment years;
call or lift its helper, don't re-derive it. (3) Heat out of v1 (it needs a share
and an offtaker, and templates that model heat would double-count it: DC
`waste_heat_export`, any heat bus or link). O2 acceptable as `derived`, quantity
only, never in the LP, at 7.94 kg/kg of H2 produced (the physical constant, not
the preset's 8 "assumed").

- LO-1 Units reuse the existing constants: eh_ammonia.MWH_PER_T (5.166), and
  models.commercial lhv_kwh_per_kg (33.33) or the line's own stated LHV. No new
  literals. A units test per conversion.
- LO-2 L1/L2 product reads go through process_products.is_product_bus.
  h2_offtake served = p_set − shortfall, never electrolyser p1. Concentrate is
  shipped t from the `shipments` load. Test, for each, a case where mill or
  electrolyser output ≠ served.
- LO-3 L4: a bus slack is allocated pro rata across that bus's loads by p_set(t),
  as the DC Q4 rule does on dc_mv. Exclude non-involuntary carriers. Unserved earns
  0 and is never priced (no VoLL).
- LO-4 Double count (GK B G2 is the owner): a linked served-product or export
  quantity on a stream the IC ledger already values (product `lost_production`,
  export, or bill streams) is refused `double_count`. Test each ledger stream that
  could clash.
- LO-5 L5: state which end of the export link and its sign. Read at the grid side,
  after link losses, and test with efficiency < 1.
- LO-6 L6: capacity per period from `get_active_assets(period)`
  (adequacy/activity.py), never hand-rolled build_year/lifetime. An extendable
  asset reads p_nom_opt; a non-solved one is not_evaluated, never p_nom.
- LO-7 Every linked output binds to the run's own solve (project, revision). Stale
  or unsolved data reads not_evaluated, never 0 (as SH-07 F6). Test it.
- LO-8 Non-unit snapshot_weightings and a multi-period network in the tests. Also a
  store-bearing bus where an hour's shortfall exceeds its load.
- LO-9 Post-solve only: nothing in linked_outputs enters the LP, the IC, the IC
  hash or adequacy facts. A pin test that the IC is byte-equal with and without BC
  lines.
- LO-10 L7 is DC 03 as merged (it_energy). No second IT reader.

### 2026-11-10: GK A (19) LO-4 aligned to GK B G2/G3

For product `lost_production`, follow GK B G3: when a revenue line reads served
product, the BC drops lost_production rather than refusing the line; test that
the shortfall's loss is counted exactly once, as lower served revenue. For export
and bill streams LO-4's refusal stays as G2 says. Where G2/G3 and LO-4 differ,
G2/G3 win. LO-1..3 and LO-5..10 unchanged. GK A (20, session
`session_01G7qCuj9VQMra1QJZv5J1s3`) confirms the GO stands as narrowed.

Also: the catalogue ids are a stored contract (lines link to them). Version them,
and add a test that every reader accepts each version (#1132 F1).

GK A standing points on every template: (1) revenue reads the spec or the stated
series, never network loads, unless the plan shows the load IS the billed
quantity; (2) unserved is only the involuntary slack, converted to the billed
unit, a quantity, never priced; (3) one load: the billed series uses the build's
own rule, with an identity test against the BUILT network; (4) no change to any
template's IC basis, and no BC line in the IC or adequacy facts.

### 2026-11-10: from DC 03 (#1319, merged 394bdae2), via the coordinator

- The IT energy read is `it_energy(spec, snapshots, generators weightings, unserved_facility_mw(n), timezone=_dc_timezone(tid))`. On a single index it uses the week-start phase (GK A F1 on #1319).
- Unserved IT MWh is a quantity only: 04 needs its own test that no BC line prices it.
