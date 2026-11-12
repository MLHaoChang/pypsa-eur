<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Linked model outputs and unit conversions

Status: done (#1383)
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

### 2026-11-11: GK A (22) plan GO, conditions P1-P8

Answers: Q1 one named constant `O2_KG_PER_KG_H2 = 7.94`, the only new literal. Q2
this slice ships the two refusal kinds and the pure `ledger_clash` rule with its
tests; 06 wires it. Export against a PPA or CfD settlement is a refuse case too.
Q3 a series is keyed per investment period; a flat network has the one key `None`.
Q4 every electric Load is offered: the catalogue offers, the preset bills.

- P1 L3 is offered for the built electrolyser (carrier `electrolysis`, `eh_role`
  `electrolyser`), and not for another Link into the H2 bus.
- P2 The own-solve check is `harness.calculations.solve_matches_inputs`: inputs
  re-hashed now, completed and optimal, no AC power flow or LOPF frames. Never the
  network's own stamp.
- P3 An absent lost-load capture is never "zero unserved": not evaluated, unless
  the capture is explicitly empty or the solve's VoLL is 0.
- P4 Each slack MW counts once across the per-Load capture, the bus slack and
  `process_products.shortfalls`; served + ProductShortfall's number = contracted.
- P5 MWh to t only through `process_products.t_per_unit` on the bus.
- P6 `eh_ammonia_staged` offers `served_product.nh3` and no `served_h2`.
- P7 Export is −p1 (grid side, after losses); the existing billed export is p0.
- P8 `active_mask` with `"ALL"` on a flat axis; IT energy read once; contracted kW
  × 12 per year.

### 2026-11-11: as built

- Ids use a dot, not a colon (`served_product.nh3`, `capacity.Link.electrolyser`),
  because 02's `QUANTITY_SOURCE_PATTERN` allows only `[A-Za-z0-9_.-]` in an id. A
  component whose name has other characters (a space) is not offered.
- Capacity reads in the line units `MW_year`, `kW_year` and `kW_month`; the unit
  enum has no bare MW or kW.
- `LinkedSolve` carries the solve: `bind(ctx)` builds it; the caller passes the DC
  spec and timezone for L7 (`dc=`), and the line's LHV or the preset row's kW per
  GPU to `read`.
- Follow-up for the route slice (08): register the 22 `REFUSAL_KINDS` in a
  `tool-error-kinds.d` fragment.
- `ledger_clash` refuses an export quantity against `energy_export`, `ppa_settlement`
  or `cfd_settlement`, and drops `lost_production` for a product quantity. A served
  electric load meets no stream: the bill streams price the site's grid import, which
  is no catalogue output, and a served load is what a microgrid sells (spec §6.7).
  For GK B to confirm in 06.
- L7 passes `it_energy` the facility's unserved MW by DC 03's Q4 rule, read from
  the solve's lost-load capture as well as the Generator rows: the per-Load VoLL
  slacks are removed after the solve, so `unserved_facility_mw(n)` alone reads 0 on
  a real solve. Follow-up for the DC owner: `unserved_facility_mw` has the same gap
  for any other caller.
- GK B (47) ruled on `served_load` against the bill streams: admitted, except
  against a valued `eaas_fee` (refused `double_count:eaas_fee`). Recorded with its
  conditions C1-C3 in 06's comments; 06 implements it.
- GK A OK @6c4c9c6b with one non-blocking point: pin F7's three-term identity
  (served + ProductShortfall + the Load's own VoLL slack = contracted) by a test.
  Done here, with the flat-network F3 test the full mutation run asked for.
