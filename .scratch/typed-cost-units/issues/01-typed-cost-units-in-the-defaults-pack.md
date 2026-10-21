# Typed cost units in the defaults pack: `capacity_basis`, `includes_grid_connection`, `money_year`, `currency`

Status: ready-for-agent
Type: task
Blocked by: GS-2 regional cost packs (#499) merged. Paused until after the usage reset, Mon 26 Oct 2026 18:00 CEST
(owner decision 2026-10-21, "finish in-flight, pause new").

Gated: send the gatekeeper (session_01Fz66v1SpJnjGHky1RbUuqC) the plan first. Own PR. It replaces and includes the
earlier "electrolysis Link scaling" follow-up. Align with GS-EU (session_01LpGW1j7CpjcgocdpGEpt5V), whose #501
`CapacityBasisCode` holds the same vocabulary.

## Problem

`defaults_pack.pricing.price_from_pack` scales every Link's overnight cost by its efficiency (PyPSA-Eur's convention:
a Link's `p_nom` is its input). That is right for the fuel cell, whose row is per kW of electrical **output**, and
wrong for an electrolysis row, which is per kW of electrical **input**: an electrolysis Link priced this way comes out
about 38 % too cheap (x0.62). Only the fuel cell goes through it today (`project_templates/eh_templates.py:525`), so
nothing is wrong in practice yet. The rule hangs on the component type, but what decides it is what the row's
capacity measures. The same gap hides two more facts that a row only implies today: its currency and money year
(carried as free fields), and whether its overnight cost already includes the grid connection (the project prices
that separately, so a source that includes it double-counts; found in GS-2 review for the UK, CER and EIA rows).

## What to build

Typed fields on every pack cost row (values.csv columns; the loader refuses a row without them):

- `currency` (already a column; make it required for money rows) and `money_year` (today `currency_year`; keep the
  column or rename with a loader alias, one name).
- `capacity_basis`, one of the shared set: `output_electric`, `output_electric_ac`, `output_electric_dc`,
  `input_electric`, `output_product` (per MW of product output, e.g. Haber-Bosch NH3), `storage_energy` (per MWh
  stored: battery energy part, NH3 and H2 tanks), `not_applicable` (campus equipment priced per unit, per km or per
  bay; the existing `basis` column stays). Separate from the existing `basis` column (`per_MW`, `per_MWh`, ...).
- `includes_grid_connection`: `true`, `false` or the string `"unknown"` (never null).

`price_from_pack` dispatches on `capacity_basis`, not the component type:

- `output_electric*` on a Link scales the overnight cost by the Link's efficiency (a price per kW out, `p_nom` is the
  input); `input_electric` never does; `output_product` / `storage_energy` follow their own documented rule.
- A row whose `includes_grid_connection` is `true` or `"unknown"` is reported in the returned stamp and the
  provenance; the commercial connection fee is not charged again where the row says it is included (decide the exact
  rule in the plan with the gatekeeper; it is a pricing-semantics choice).

## Tests first (red before green)

1. An electrolysis Link priced per input: overnight cost equals the row, not row x efficiency (fails today, x0.62).
2. The fuel cell Link still scales by efficiency.
3. The currency key: the stamp and the annualised-cost key carry the row's currency (also closes the battery branch
   of `price_from_pack`, which still returns `annualised_cost_eur_per_mw_yr`; follow-up from the GS-2 gatekeeper OK).
4. The double-counted connection: a row with `includes_grid_connection = true` plus a separately priced connection is
   flagged or not charged twice, per the agreed rule.
5. A schema test refuses any pack row missing a typed field, in every shipped version and regional pack.

## Backfill

Every existing row of the EU versions 2026-10-05 .. 2026-10-20 and the three regional packs (2026-10-21+ca_federal,
+uk, +us_federal). Never edit a shipped version: backfill means new versions of each (a new dated EU version and
new regional versions), per the pack README. Pin the new hashes. Values already agreed with GS-EU:

- Regional solar-utility (CER, DESNZ, EIA) `output_electric_ac`; onwind and gas-genset `output_electric`.
- `includes_grid_connection`: CER `false`, DESNZ `false` (Infrastructure left out), EIA `"unknown"` (generic one-mile
  interconnections included, not separable).
- EU rows: onwind `output_electric`, solar-utility `output_electric_ac`, solar-rooftop `output_electric_ac`,
  electrolysis `input_electric`, fuel-cell `output_electric`. GS-EU's #501 DEA flags (onwind and PV `true`, AEC/PEM
  `"unknown"`) come from the DEA August 2026 workbooks, not technology-data v0.14: check the v0.14 text before copying
  a flag onto the pack's own rows.
- The enum may live in `defaults_pack/` after #499 merges; #501 imports it from there.

## Not in scope

Region selection from a project (separate wiring plan); new technologies; the regional finance defaults.

## Comments
