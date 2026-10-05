# Assessment: one way to parameterise every asset (expert and guided)

**Date:** 2026-10-05. **Status:** assessment, for the owner. **Refs read:** `origin/master` (855bbac2d),
`origin/claude/determined-tesla-np09ww` (unification plan v1.2), `origin/claude/energy-tool-features-research-fdixs0`
(IC finance engine), `origin/claude/edge-tool-ux-research-n0n2l6` (guided study). PyPSA 1.1.2 (pinned,
`pypsa-gui/gui-requirements.txt:37`). Line numbers are on master unless a branch is named.

**The ask.** "Whether you invest in a battery, a line, hydrogen or anything else, it should always be the same
approach: you fill out the parameters needed. Apart from the variables that change per asset, the approach should be
the same, in both UIs."

## Executive summary

- Today an asset's economics can be typed in **two incompatible bases**: an upfront `overnight_cost` (+ `lifetime`
  + `discount_rate`) or an already-annualised `capital_cost`. Which one is used differs by asset class, by entry
  path (properties panel, quick-add, chat, template, guided pack, EH redundancy) and by who wrote the code.
- PyPSA 1.1.2 makes the two bases **mutually exclusive per asset**: when `overnight_cost` is set, `capital_cost` is
  ignored (`pypsa.costs.periodized_cost`: "If overnight_cost is NaN: use capital_cost directly"). Nothing in the GUI
  states this rule to the user; the expert editor shows both fields side by side.
- The **labels disagree with the semantics**: the editors label `capital_cost` "€/MW" (PropertiesPanel.tsx:419,
  GenerationStack.tsx:45), the tooltips say "€/MW/yr" (propertyDocs.ts:86), the glossary says "always labelled per
  year" (CONTEXT.md:185-187), quick-add says "$/MW" (CreationForm.tsx:62). PyPSA's own unit is "currency/MW"
  because it is per modelled horizon, which is a third meaning.
- **Batteries are the worst case**: four ways to price one (EH template: a single annualised €/MW bundling 4 h of
  energy; EH redundancy: literal `capital_cost=80×trains`; guided pack: two-annuity `capital_cost` from €/kW +
  €/kWh with two lifetimes; expert: whatever the user types). Only the guided pack carries the physics (power part
  and energy part); only the expert path can carry an `overnight_cost`, and it is the wrong one (per MW only).
- The **finance engine reads a different field than the LP**: `finance_case._assets` reads the typed
  `overnight_cost` only (fdixs0 `services/results/finance_case.py:978-992`), the LP charges `periodized_cost`.
  Compile rule **C1 of the unification plan does not fix this and has a latent defect**: it sets `overnight_cost`
  on the battery and says "the LP keeps the annuitised `capital_cost`"; PyPSA will instead annuitise the blended
  overnight over the single storage lifetime and ignore `capital_cost`, under-costing the inverter part (about 12 %
  on the library's numbers). See §2.3.
- **Annuity math lives in four implementations** (PyPSA, `periodized_costs._annuity`, GS `proforma._annuity_pv_factor`,
  PyPSA-Eur `calculate_annuity`) and **lifetime has three fallback rules** (PyPSA `inf`, config `default_lifetime`
  for overnight-priced assets only, vintage clones `periods[-1]-period+1`).
- **Recommendation:** one declarative **asset parameter schema** (per class, fixed group order: Identity, Size,
  Investment, Fixed O&M, Variable cost, Performance, Replacement/degradation, Provenance), with the investment typed
  **only as overnight parts** (each part: basis, overnight, lifetime, FOM share) and `capital_cost` **derived in one
  function** and shown read-only. Expert editors, quick-add, chat tools, templates, EH packs, the guided ledger and the
  finance engine all read and write through that schema.
- **Sequencing:** the schema core (backend module + derive + one accessor) is a **prerequisite for U2's
  `compile.py`**, otherwise U2 ships C1 as written. Everything UI-side can follow U2/U3 in parallel. Rough size: core
  ≈ 1.5–2 k LoC with tests; expert UI ≈ 1.5 k; guided view ≈ 0.5 k; templates/packs ≈ 0.4 k.
- Main risks: PyPSA semantics (overnight ⊕ capital_cost, unequal `nyears` refusal, `lifetime` drives retirement),
  existing projects (new columns default NaN, legacy "capital_cost only" assets need a labelled migration), and
  the ~40 cost-related golden/parity tests, which must stay byte-identical for single-part assets.

---

## 1. Inventory: how each asset is parameterised today

### 1.1 The storage model (what the network can hold)

PyPSA 1.1.2 attributes and declared units (printed from `n.components.<c>.defaults`):

| Class | capacity | `capital_cost` | `overnight_cost` | `fom_cost` | `lifetime` | `build_year` | `discount_rate` | other cost-relevant |
|---|---|---|---|---|---|---|---|---|
| Generator | `p_nom` MW | currency/MW, 0 | currency/MW, NaN | currency/MW, 0 | years, inf | year, 0 | per unit, NaN | `efficiency`, `marginal_cost` currency/MWh |
| StorageUnit | `p_nom` MW (+ `max_hours` h) | currency/MW | currency/MW | currency/MW | inf | 0 | NaN | `efficiency_store/dispatch`, `standing_loss` |
| Store | `e_nom` MWh | currency/MWh | currency/MWh | currency/MWh | inf | 0 | NaN | `standing_loss` |
| Link | `p_nom` MW (bus0 input) | currency/MW | currency/MW | currency/MW | inf | 0 | NaN | `length` km (default 0), `efficiency`, `efficiency2..` |
| Line | `s_nom` MVA | currency/MVA | currency/MVA | currency/MVA | inf | 0 | NaN | `length` km |
| Transformer | `s_nom` MVA | currency/MVA | currency/MVA | currency/MVA | inf | 0 | NaN | — |

Semantics that matter (PyPSA `pypsa.costs.periodized_cost`, and `Components.overnight_cost`):

- LP coefficient = `periodized_cost = (overnight × annuity(r, L) × nyears if overnight set else capital_cost) + fom_cost`.
  `capital_cost` and `fom_cost` are **per modelled horizon**, not per year; the GUI treats both as per year and
  scales them transiently by `nyears` (`periodized_costs.py:193-232`).
- `overnight_cost` set ⇒ `capital_cost` **ignored**. Unequal `nyears` across periods ⇒ `overnight_cost` **refused**
  (`myopic.py:768` works around it).
- `overnight_cost` unset ⇒ PyPSA **back-calculates** it from `capital_cost / (annuity × nyears)` and raises for the
  whole class if any asset lacks `discount_rate` or `lifetime` (`periodized_costs.py:308-327`, `upfront_cost_series`).
- `lifetime` is not only a cost input: with `build_year` it drives **activity** in multi-period runs
  (`periodized_costs.py:165-173`, `period_utils.active_period_years`).

The GUI's Pydantic models mirror these columns one-to-one and add `discount_rate` per asset plus custom columns
(`curtailment_cost`, outage data): `models/schemas.py:105-151` (Line), `:165-217` (Link), `:220-292` (Generator),
`:303-347` (StorageUnit), `:350-387` (Store), `:404-445` (Transformer). The global fallbacks live on the solver
config: `discount_rate=0.07`, `inflation_rate`, `default_lifetime=25` (`models/schemas.py:529-535`).

### 1.2 Entry paths × asset classes

Legend: **ON** = overnight + lifetime (+ rate); **CC** = annualised `capital_cost` typed directly; **both** = both
fields offered; **none** = no cost field; **lit.** = hard-coded literal.

| Asset | Expert properties panel | Quick-add (CreationForm) | Chat `create_component` | EH templates (`eh_templates.py`) | EH redundancy (`adequacy/redundancy.py`) | Guided pack (n0n2l6 `study/packs.py`) | Cost-data seed |
|---|---|---|---|---|---|---|---|
| Generator (gas/diesel genset) | both; "Capital cost €/MW", "FOM €/MW/yr", "Overnight €/MW" (`PropertiesPanel.tsx:418-436`) | CC only, "$/MW" (`CreationForm.tsx:61-62`) | opaque `attrs` object, no field docs (`chat_tools_schema.py:404-409`) | CC lit. `62_000` "annualised €/MW/yr" (`:148-151`), `45_000` diesel (`:298-300`); existing gensets `marginal_cost` only (`:136-139`) | CC lit. `60.0` (`:321`) | n/a | none in GUI |
| Generator (PV/wind) | both (same card) | CC only | opaque | CC lit. `120_000` wind, `45_000` PV (`:207-210`) | — | **ON**: `overnight=€/kW×1000`, `lifetime`, `discount_rate`, `fom=%×overnight` (`packs.py:626-636`) | GS CSV: `solar-utility` 482 EUR/kW_e, FOM 2.48 %/yr, 40 y (technology-data v0.14.0) |
| Battery (StorageUnit) | both, all per MW; `max_hours` separate (`:681-690`) | **none** (`storFields` has no cost field, `:65-72`) | opaque | CC lit. `48_000` per MW of a 4 h / 6 h battery, energy bundled (`:153-156`, `:229-232`, `:302-305`); no lifetime | CC lit. `80 × trains` (`:432-438`) | **CC = two annuities** (inverter €/kW, storage €/kWh×max_hours, two lifetimes; `:163-177`, `:647`); `overnight` left unset on purpose (`:649-652`); FOM inverter only | GS CSV: inverter 214 EUR/kW 10 y, storage 190 EUR/kWh 25 y, RTE derived |
| Battery (Store + Link) | Store card: both per MWh (`:878-888`); Link card: both per MW | thermal Store only, CC €/MWh (`:223`) | opaque | not used | — | deferred to MVP-2 (GS spec amendment 2026-09-28) | PyPSA-Eur sector convention |
| Electrolyser (Link) | both per MW of **input** (`:1369-1380`) | CC €/MW (`:154`) | opaque | **none** (`:216-217`) | CC lit. `40.0` "eh_spare_conversion" (`:392`) | n/a | — |
| H2 store (Store) | both per MWh | CC €/MWh (thermal form) | opaque | **none** (`:218-219`) | — | n/a | — |
| Fuel cell (Link) | both per MW input | CC €/MW (`:165`) | opaque | CC lit. `90_000` (`:222-224`) | — | n/a | — |
| Heat pump (Link, bus2 optional) | both per MW of **electrical input** | CC €/MW, "η / COP" (`:189-192`) | opaque | — | — | n/a | catalogues are per kW_th (PyPSA-Eur converts `capital_cost × efficiency`, `prepare_sector_network.py:4248`) |
| CHP (multi-port Link) | both per MW of fuel input; `efficiency2` (`schemas.py:195-196`) | CC €/MW (`:207-211`) | opaque | — | — | n/a | — |
| Line | both per MVA; r/x/b per km × length (`:1854-1861`, `:2076-2092`) | **none** (`:135-144`) | opaque | not in templates | — | n/a | PyPSA-Eur: `length × HVAC overhead capital_cost` (`add_electricity.py:437-441`) |
| Transformer | both per MVA (`:404-445` schema) | CC €/MVA (`:128`) | opaque | — | — | n/a | — |
| Grid import/export Link | both per MW | — | opaque | **none**, `p_nom` only (`:119-120`) | — | `capital_cost=0` (`:602-607`) | IC: capacity fee is an explicit objective term, not `capital_cost` (IC spec row 254) |

### 1.3 Who reads what (solve, results, finance)

| Reader | Investment basis read | Lifetime | FOM | Notes |
|---|---|---|---|---|
| LP objective (PyPSA) | `periodized_cost` (overnight⊕capital_cost + fom) | annuity + activity | yes | per horizon; GUI scales typed annual values by `nyears` transiently (`periodized_costs.py:193-232`) |
| `periodized_capital_costs` (`periodized_costs.py:397-593`) | both: `capital_cost` (annualised) and `overnight_cost` (typed or **back-calculated**) | fills `default_lifetime` only for overnight-priced assets (`:259-271`) | `fixed_cost = capital + fom` | THE seam for Economics, Compare, Asset Detail, cost_breakdown lifetime CAPEX (`asset_economics.py:102-121`, `cost_breakdown.py:144-250`, `economics.py:79-204`) |
| Capex budget constraint (`solver/objective.py:20-30, 119-126`) | overnight, **falls back to `capital_cost`** when unset | — | — | budget (€) compared against an annualised figure when the fallback fires: a unit error of the annuity factor |
| Vintage clones (`vintage_service.py:405-426`) | copies every cost column | `lifetime` = original or `periods[-1]-period+1` | copied | a third lifetime rule |
| Finance engine (fdixs0 `finance_case.py:978-992`, `finance/case.py:95-103`) | **typed `overnight_cost` × capacity only**; `None` otherwise ("never back-calculated") | `lifetime` | not read here (FOM arrives as a ledger cash line) | replacement via `FinanceInputs.replacement_capex`, degradation via `degradation_by_asset`, COD via `cod_by_asset` (`models/finance.py:229, 239, 263`) — all keyed by asset name, separate from `build_year` |
| Guided pro forma (n0n2l6 `proforma.py:12-38, 264-276`) | PV: `upfront_cost_series`; battery: ledger parts, never the column ("back-calculates one lifetime from a two-annuity `capital_cost` and overstates it") | ledger | ledger | replacements and `annuity_pv` salvage computed here; to be retired in U2 |
| EH DtC cost-at-target (`adequacy/dtc.py:601-639`) | whatever the LP charged | — | — | inherits the template literals |

### 1.4 Cross-cutting inconsistencies (summary table)

| Concern | Expert editor | Quick-add | Templates / EH | Guided ledger/pack | Finance (IC) | PyPSA-Eur reference |
|---|---|---|---|---|---|---|
| Cost concept | both, free choice | annualised only | annualised literals | overnight (PV) / two-annuity (battery) | overnight only | overnight + FOM % + lifetime → `capital_cost` once (`process_cost_data.py:171-173`) |
| Units | €/MW, €/MWh, €/MVA (label omits /yr) | $/MW, €/MW, €/MVA, €/MWh | €/MW(/yr in comment) | EUR/kW, EUR/kWh, %/year | currency, totals | EUR/kW, EUR/kWh, EUR/MW·km |
| Lifetime | per asset; blank → global 25 (overnight assets only) | not offered | none | per part (two for battery) | `lifetime_years` per asset | per technology |
| Build year | per asset; vintage clone button | not offered | none (0) | none (one year) | `cod_by_asset` (date) | `build_year` |
| FOM | €/unit/yr | not offered | none | % of investment per part | ledger line | % of investment folded into `capital_cost` |
| Efficiency | η, η2, η3; store/dispatch | η, COP | η | RTE → sqrt each side | — | `sqrt(inverter η)` each side (`add_electricity.py:708`) |
| Replacement | none | none | none | inverter every 10 y, `finance_defaults.yaml` rules | `replacement_capex` list | none |
| Degradation | none | none | none | placeholder rows (not used) | `degradation_by_asset` (rate or steps) | none |
| Per-km | r/x/b only | r/x/b only | — | — | — | `length × €/MVA·km` |
| Two-part battery | no (per MW only) | no | no (bundled) | yes (ledger) → collapsed to one `capital_cost` | no | yes (`costs_for_storage`, `process_cost_data.py:194-204`) |
| Provenance | none | none | one string per template (`PROVENANCE`) | per row: source, year, currency year, range, status | none | source columns in CSV |

---

## 2. Diagnosis: where it hurts

### 2.1 Users

**Experts** see two investment fields and no rule for which one wins. The tooltip says "Leave empty to use the
capital_cost you typed directly" (`propertyDocs.ts:90`), the label beside it says "€/MW" for a number the tooltip
calls "€/MW/yr", and the quick-add that created the asset showed "$/MW". The 2026-07-31 finding ("why are the
economics for electrolyzers all 0") is this confusion in production: the user typed an overnight cost, saw
`capital_cost = 0`, and read it as a bug (`findings/2026-07-31-link-economics-missing.md`). The GS spec already asked
for the badge fix ("the `€/MW` badge on the annuity field is corrected to `€/MW/yr` in the expert view too", spec §9
line 245) and for a preflight check that catches an upfront figure typed into the annuity field (line 248); neither
is on master (`validation_service.py` has no such magnitude check).

**Novices** in Guided mode get a clean ledger (EUR/kW, source, year, range) for the one question that exists, but
the moment they "Open in Expert" (U3) the same battery appears as `capital_cost = 95 712 €/MW`, `overnight_cost`
blank, `lifetime = 25`, with no trace of the €/kW and €/kWh they typed and no way to edit those. The ledger and the
expert card are not two faces of one state; they are two states.

**Templates** teach the wrong lesson: every EH candidate carries only an annualised literal and no lifetime, so
"Total over lifetime" in Capacity Expansion back-calculates an upfront cost with the global `default_lifetime` — the
upfront figure a user sees for `bess_new` changes when they change a solver setting. The EH redundancy placeholders
(`capital_cost=60.0` €/MW/yr for a gas genset) are three orders of magnitude below the template's `62_000` for the
same asset, so an N-1 spare is nearly free in the LP.

### 2.2 Correctness

1. **Two cost bases, one asset.** PyPSA silently ignores `capital_cost` when `overnight_cost` is set. The GUI lets a
   user set both, shows both, and the reporting layer then has to guess which one the LP used (that is what
   `periodized_capital_costs` exists for; it is 200 lines of defensive code around one missing rule).
2. **Annuity in four places.** PyPSA's `annuity`; `periodized_costs._annuity` (`:29-41`), used by the GS pack and
   pro forma; `proforma._annuity_pv_factor`; PyPSA-Eur's `calculate_annuity`. They agree today by convention, not
   by test. Discounting conventions differ too: the GUI's `discount_rate` is nominal-or-real by user choice
   (`schemas.py:529-534`), the GS basis is real 2020 EUR, `FinanceInputs` is nominal (plan C4).
3. **Lifetime has three fallbacks** (PyPSA `inf`; `default_lifetime` only for overnight-priced assets; vintage
   `periods[-1]-period+1`). Because `lifetime` also retires assets, "blank" means different things on the same
   network depending on which path wrote the row.
4. **Battery energy vs power.** Every path but the guided pack prices a StorageUnit per MW with the energy cost
   bundled through `max_hours`; change `max_hours` in the expert card and the bundled `capital_cost` is now wrong
   and nothing recomputes it. The guided pack prices it right but has to write the sum of two annuities into
   `capital_cost` because `overnight_cost` "cannot carry two lifetimes" (`packs.py:39-49`).
5. **FOM basis.** Expert: €/unit/yr; guided: % of investment per part; PyPSA-Eur: % folded into `capital_cost`. A
   network imported from PyPSA-Eur already contains FOM in `capital_cost`; typing `fom_cost` on top double-counts, and
   nothing says so (FOM reconciliation finding, 2026-09-27).
6. **Lines per km.** The panel curates r/x/b per km and multiplies on save (`PropertiesPanel.tsx:1854-1861`), but
   cost is per MVA with no length link. PyPSA-Eur prices lines as `length × €/MVA·km` (`add_electricity.py:437-441`);
   `recalculate_line_lengths` rescales impedances with consent but leaves cost untouched (`network_lines.py:27`).
7. **Capex budget mixes bases** (`objective.py:119-126`): falls back to the annualised `capital_cost` as if it were
   an upfront cost.
8. **Multi-port links price the input port**, while every public catalogue prices heat pumps and fuel cells per
   unit of output (PyPSA-Eur multiplies by efficiency; `prepare_sector_network.py:4248`). The GUI quick-add asks
   "Capital cost €/MW" next to "η / COP = 3.5" and the user has to know which MW.
9. **"When built" exists twice**: `build_year` on the asset and `FinanceInputs.cod_by_asset` (fdixs0
   `models/finance.py:229`), keyed by name, with a refusal `cod_missing` when they are not both filled.
10. **Degradation has three homes** (`BessSpec.calendar_fade_per_year` in `models/flex_archetypes.py:60-72`,
    `FinanceInputs.degradation_by_asset`, GS placeholder rows), none reaching the LP.

### 2.3 Compile rule C1 as written changes the LP coefficient

Plan §4 C1: "Compile sets, on the StorageUnit, `overnight_cost = (storage €/kWh × max_hours + inverter €/kW) × 1000`
per MW, one `lifetime` (the storage lifetime) … The LP keeps the annuitised `capital_cost`."

With PyPSA 1.1.2 that last sentence does not hold: `periodized_cost` uses `overnight × annuity(r, 25)` as soon as
`overnight_cost` is non-NaN and ignores `capital_cost`. On the library's numbers (r = 7 %, inverter 213.9 €/kW 10 y,
storage 189.9 €/kWh 25 y, 4 h): two-annuity = 0.1424×213.9 + 4×0.0858×189.9 ≈ 95.7 €/kW/yr; C1's single annuity =
0.0858×(213.9 + 4×189.9) ≈ 83.5 €/kW/yr, about **12.7 % cheaper** in the LP, and the golden QA driver
(`qa_decision_study.py`) would record it as a "numeric delta with cause". This is not a numeric difference to
accept; it is the two-part structure being lost. The schema below (two-part investment, `capital_cost` derived, PyPSA
`overnight_cost` left NaN for composite assets, one accessor for the upfront parts) removes the contradiction. **C1
should be rewritten against the schema before U2 lands** (§5).

---

## 3. Proposal: one asset parameter schema

### 3.1 Principle

One declarative description per asset class, served by the backend, rendered by every face, written through one
write path, with **one derived `capital_cost`** that no user ever types.

```
   expert card · quick-add · chat tool · template · EH pack · guided ledger · CSV import
                     │  (all produce the same AssetParams, each field with provenance)
                     ▼
        services/asset_schema/   schema.py   (groups, fields, units, reveal rules, per class)
                                 derive.py   (parts → PyPSA columns; THE annuity call site in the GUI)
                                 access.py   (upfront parts, lifetime, build year, FOM back from a network)
                     │
                     ▼
   PyPSA columns (overnight_cost | capital_cost, fom_cost, lifetime, build_year, discount_rate, custom part columns)
   + n.meta["asset_params"] (provenance per field)
                     │
          ┌──────────┴───────────┬──────────────────────┬────────────────────┐
          ▼                      ▼                      ▼                    ▼
   LP (PyPSA periodized_cost)  periodized_capital_costs  finance_case._assets  guided ledger / report
```

Rules:

1. **Investment is typed as overnight parts, never as an annuity.** A part = `{basis, overnight, lifetime, fom_share}`
   with `basis ∈ {per_MW, per_MWh, per_MVA, per_MVA_km, per_MW_km, lump}`. Most assets have one part. A battery has
   two (power, energy). An HVDC link has two (per-km cable, lump converter pair). The UI shows €/kW and €/kWh (what
   catalogues publish); storage is PyPSA's per-MW/MWh/MVA with one conversion table.
2. **`capital_cost` is derived.** Single-part assets: `derive` writes PyPSA `overnight_cost`, `lifetime`, `fom_cost`,
   leaves `capital_cost = 0` and lets PyPSA annuitise (so `nyears` scaling, the unequal-`nyears` refusal and
   `n.statistics` keep working unchanged, and every existing golden test keeps its numbers). Composite assets:
   `derive` writes `capital_cost = Σ part_i × annuity(r, L_i)` per sizing unit (with `max_hours` for the energy part),
   `fom_cost = Σ fom_share_i × part_i`, leaves PyPSA `overnight_cost` NaN, and keeps the parts in custom columns
   (`inv_power_overnight`, `inv_power_lifetime`, `inv_energy_overnight`, `inv_energy_lifetime`, `inv_per_km_overnight`,
   …; same mechanism as `outage_rate_value`, netCDF round-trip for free). `derive` uses `periodized_costs._annuity`;
   a golden test asserts it equals PyPSA's `annuity` to 1e-12 on the fixture, so the GUI has one annuity.
3. **One accessor for the upfront cost.** `access.upfront_parts(n, class, name)` returns the parts (typed, or a
   single back-calculated part flagged `derived_from_capital_cost` for legacy rows, or `None` per ADR-0001).
   `periodized_costs.upfront_cost_series` and fdixs0 `finance_case._assets` both call it; the "back-calculate one
   lifetime from a two-annuity capital_cost" error class disappears.
4. **Discount rate** is global unless the asset overrides it (today's behaviour), shown in the Investment group as
   "Discount rate: 7 % (global)" with an override. The transient fill stays.
5. **Lifetime is never blank for a priced part.** Blank is refused at the schema (the library or the user supplies
   it); the three fallbacks collapse to one: "no part ⇒ `inf` (never retires), a part ⇒ its lifetime".
6. **Existing (non-extendable) assets** fill the same Investment group (the LP does not charge it, reporting and
   finance do: installed CAPEX, retirement, sunk vs incremental). The group is labelled "Installed cost (sunk)" and
   the derived annuity row says "not charged by the optimiser (not extendable)". `build_year` is the one "when
   built" fact; the finance `cod_by_asset` date is derived from it (`build_year`-01-01, overridable) instead of
   being a second mandatory input.
7. **Provenance per field**: `{source, source_year, currency_year, range, status: default|customised|measured,
   illustrative}` stored in `n.meta["asset_params"][class][name][field]` and written by every path (templates stamp
   `illustrative`, the generic defaults pack stamps its hash, the user's edits stamp `customised`). The guided ledger
   row becomes a **view** of these entries (same keys, same statuses), so rule 5 of the unification plan ("expert
   edits mark the ledger row customised") is a property of the store, not a sync job.

### 3.2 The groups (always in this order, on every face)

| # | Group | Fields (per class as declared) | Who shows it |
|---|---|---|---|
| 1 | Identity | name, class, carrier, bus/ports, role tag (`eh_role`), owner | all |
| 2 | Size | capacity basis and existing value; extendable; min/max; modular size; for batteries: power + duration (or energy); for lines: rating + length | all (guided: the question's sizing choice only) |
| 3 | Investment | parts (basis, overnight, lifetime, per-km flag), build year, discount rate (global/override); **derived:** annualised cost €/unit/yr, upfront per unit, upfront total | all; guided shows key parts, hides the derived row behind "how it is computed" |
| 4 | Fixed O&M | per part: % of overnight per year, or €/unit/yr (one stored) | all |
| 5 | Variable cost & fuel | marginal cost €/MWh (optionally composed: fuel price × heat rate + VOM), start-up/shut-down, curtailment cost | generators, links, storage |
| 6 | Technical performance | efficiencies per port (input/output basis stated), round-trip, standing loss, availability, ramps, unit commitment, outage rate + MTTR | class-specific |
| 7 | Replacement & degradation | replacement interval per part (default = part lifetime when shorter than the horizon), augmentation, calendar/cycle fade | finance and guided; expert as a collapsed group |
| 8 | Provenance | source, year, currency year, range, status, illustrative | all (guided: always visible; expert: chip + hover) |

### 3.3 Four assets under the schema

**PV (Generator, carrier solar)**
```
Identity     pv · Generator · solar · bus=site
Size         p_nom 0 MW · extendable · max = 2× connection (80 MW)
Investment   part[power]: 482.5 €/kW, 40 y, FOM 2.48 %/yr        (technology-data v0.14.0, 2020 EUR, range ±30 %)
             build year 2027 · discount rate 7 % (global)
             derived: 36.2 €/kW/yr annualised · 482 500 €/MW upfront
Fixed O&M    derived 11.95 €/kW/yr                                   (from the % above)
Variable     0 €/MWh · curtailment cost 0
Performance  availability = profile (synthetic, flagged) · outage —
Replacement  none within 40 y
Provenance   library default · illustrative=no
```
PyPSA columns written: `overnight_cost=482_500`, `lifetime=40`, `fom_cost=11_950`, `build_year=2027`,
`capital_cost=0`. This is exactly what the guided pack writes today (`packs.py:626-636`); the expert card now shows
the same thing.

**Battery (StorageUnit, composite)**
```
Identity     battery · StorageUnit · battery · bus=site
Size         power 0 MW extendable (max 80) · duration 4 h  (energy = power × duration)
Investment   part[power]:  213.9 €/kW,  10 y, FOM 0.34 %/yr   (inverter)
             part[energy]: 189.9 €/kWh, 25 y, FOM 0 %/yr      (storage block)
             build year 2027 · discount rate 7 % (global)
             derived: 95.7 €/kW/yr annualised (30.5 power + 65.2 energy) · 973 €/kW upfront · 77.9 M€ at 80 MW
Fixed O&M    derived 0.72 €/kW/yr
Variable     0 €/MWh
Performance  round-trip 92.2 % (→ η store = η dispatch = 0.96) · standing loss 0 · cyclic SoC
Replacement  inverter every 10 y (years 10, 20 of a 25 y horizon) · fade: not modelled (placeholder)
Provenance   library default · illustrative=no
```
PyPSA columns written: `overnight_cost=NaN`, `capital_cost=95_712` (derived, labelled), `fom_cost=722`,
`lifetime=25` (energy part, the retiring part), `max_hours=4`, custom `inv_power_overnight=213_900`,
`inv_power_lifetime=10`, `inv_energy_overnight=189_900`, `inv_energy_lifetime=25`. Finance reads the parts through
the accessor: upfront 973 €/kW × 80 MW, replacement of the power part at 10 and 20 — the same numbers the GS pro
forma books today, now from one store. Changing duration or either part re-derives `capital_cost`. A later
Store + Link mapping of the same schema (PyPSA-Eur's sector convention) writes each part onto its own component; the
user-facing form does not change.

**Electrolyser (Link, one input port, one output port)**
```
Identity     electrolyser · Link · electrolysis · bus0=hub (AC in) · bus1=h2 (H2 out)
Size         p_nom 60 MW_el (input basis) · extendable? no
Investment   part[power]: 1 500 €/kW_el, 20 y, FOM 2 %/yr  · cost basis: input port
             build year 2024 (existing → "installed cost, sunk") · discount rate 7 %
             derived: 141.6 €/kW/yr · 90 M€ installed
Fixed O&M    30 €/kW/yr
Variable     0 €/MWh
Performance  η 0.68 (MWh H2 per MWh el) · outage FOR 4 %, MTTR 48 h
Replacement  stack every 10 y at 30 % of the part (optional)
Provenance   user · source "vendor quote 2025"
```
The cost-basis flag matters for the fuel cell and heat pump: `cost_basis=output` converts with the port efficiency
at derive time (PyPSA-Eur's `capital_cost × efficiency`), and the card says "€/kW of heat output (COP 3.5 → 1 429 €/kW
of electrical input)".

**Line (per km)**
```
Identity     L_ab · Line · AC · bus0=a · bus1=b
Size         s_nom 100 MVA · extendable · length 42.3 km (from coordinates)
Investment   part[per_km]: 1 200 €/MVA·km, 40 y, FOM 1 %/yr    (basis per_MVA_km)
             derived per MVA: 50 760 €/MVA upfront · 3 807 €/MVA/yr
             build year 0 (existing) · discount rate 7 %
Provenance   user
```
PyPSA columns: `overnight_cost = per_km × length` written by `derive`; a length change (bus move, recalculate from
coordinates) re-derives it alongside the impedance rescale (`network_lines.apply_recalculate_line_lengths`), with the
per-km value kept in `inv_per_km_overnight`. A transformer is the same schema without the per-km part.

### 3.4 Units

| Schema field | UI unit | Stored (PyPSA) | Factor |
|---|---|---|---|
| overnight per power | €/kW (MW shown on hover) | currency/MW | ×1000 |
| overnight per energy | €/kWh | currency/MWh | ×1000 |
| overnight per rating | €/kVA | currency/MVA | ×1000 |
| overnight per km | €/MVA·km or €/MW·km | currency/MVA (after × length) | × length |
| derived annualised | €/kW/yr | currency/MW per horizon (scaled by `nyears` at solve) | ×1000, ×nyears |
| FOM | % of overnight per year (default) or €/kW/yr | currency/MW per year | — |
| marginal | €/MWh | currency/MWh | 1 |

The labels are fixed in the schema (`unit_display`, `unit_storage`), so "CC (€/MW)" in `GenerationStack.tsx:45` and
"$/MW" in `CreationForm.tsx:62` cannot drift again; `attributeCatalog.ts` already appends PyPSA's unit to raw-grid
headers (`:190-202`) and gets the display unit from the same table.

---

## 4. UI consequence: two faces, one schema

Both faces render the same groups in the same order from `GET /api/asset_schema/{class}?carrier=…`. The expert card
renders every field; the guided key-parameter list renders the fields the question marks `key` and folds the rest
into "Defaults in use (n)" with provenance chips (the existing `LedgerReview` chip and source line, n0n2l6
`pages/decision/LedgerReview.tsx:57-95`). Both write through `PUT /api/network/{class}/{name}` → `derive`. The chat
tool `create_component` gets the schema as its `attrs` documentation instead of an opaque object.

**Battery, expert card (properties panel)**
```
┌ Battery · battery_1 ───────────────────────────────── StorageUnit · battery ┐
│ Identity   name battery_1   carrier battery   bus site   role —   owner site owner│
│ Size       power [  0 ] MW  ☑ extendable  min [0] max [80]  duration [4] h       │
│            energy = 0 MWh (power × duration)                                      │
│ Investment ┌ Power part   [213.9] €/kW   life [10] y   FOM [0.34] %/yr  ● library │
│            └ Energy part  [189.9] €/kWh  life [25] y   FOM [0   ] %/yr  ● library │
│            build year [2027 ▾]   discount rate 7 % (global) [override…]          │
│            Annualised (derived)  95.7 €/kW/yr  = 30.5 + 65.2   ⓘ how computed     │
│            Upfront (derived)     973 €/kW · 77.9 M€ at 80 MW                      │
│ Fixed O&M  0.72 €/kW/yr (derived from the shares above)                           │
│ Variable   marginal [0] €/MWh                                                     │
│ Performance round-trip [92.2] %  standing loss [0] %/h  ☑ cyclic SoC              │
│            outage rate [ — ] basis [FOR ▾]  MTTR [ — ] h                          │
│ Replacement power part every 10 y (years 10, 20) · fade not modelled   ▸ edit     │
│ Provenance technology-data v0.14.0 · 2020 EUR · ranges ±30 % assumed   ▸ details  │
└───────────────────────────────────────────────────────────────────────────────────┘
```

**Battery, guided key-parameter list (question "Is a battery worth it?")**
```
Your key parameters                                   ● = default from the library, ✎ = yours
  Battery storage cost   [189.9] €/kWh   ● technology-data v0.14.0 (2020 EUR) · range 133–247   matters most
  Battery inverter cost  [213.9] €/kW    ● same source · range 150–278                           matters most
  Hours of storage       (2 h) (4 h) (6 h)   compared as options
  Discount rate          [7.0] %        ● PyPSA-Eur default · range 4.9–9.1                      matters most
Defaults in use (6)  ▸ inverter life 10 y · storage life 25 y · FOM 0.34 %/yr · round-trip 92.2 %
                       · build year 2027 · inverter replaced in years 10 and 20
Open in Expert → the same card above, every default visible and editable; a change here marks the row ✎.
```

**Line, expert card**
```
┌ Line · L_ab ───────────────────────────────────────────────────── Line · AC ┐
│ Identity   name L_ab  carrier AC  from a  to b                               │
│ Size       rating [100] MVA  ☑ extendable  min [0] max [300]  length 42.3 km │
│ Impedance  r [0.03] Ω/km  x [0.3] Ω/km  b [0] S/km  (× length on save)       │
│ Investment ┌ Per-km part [1 200] €/MVA·km  life [40] y  FOM [1] %/yr  ✎ user │
│            build year 0 (existing)  discount rate 7 % (global)               │
│            Upfront (derived)  50 760 €/MVA   Annualised  3 807 €/MVA/yr       │
│ Provenance user · "TSO unit rates 2025"                                      │
└──────────────────────────────────────────────────────────────────────────────┘
```
Guided view of a line (when a grid-connection question exists): "Connection cost per km [1 200] €/MVA·km ● · length
42.3 km from the map · everything else default".

The D22 reveal rules (`attributeCatalog.ts:227-263`) move into the schema as `reveal_when` (extendable → bounds,
committable → UC fields, composite → second part), so the create and edit forms stay in agreement by construction,
which is the thing D22 was written to protect.

---

## 5. Migration and sequencing

### 5.1 Fit with the unification plan (U1–U4)

| Step | What | Owner / files | Relative to the plan |
|---|---|---|---|
| **S0 Schema core** (prerequisite for U2) | `services/asset_schema/{schema,derive,access}.py` + custom columns on the six Pydantic models + `derive` hook in `network_crud._create/_update_component` + `upfront_parts` branch in `periodized_costs.upfront_cost_series` + unit table + golden test (two-part battery added to `tests/golden/fixture.py`, single-part numbers unchanged) | New package: no owner today. `models/schemas.py`, `network_crud.py` are IC/GS **shared hot files** (plan §5); `periodized_costs.py` is in neither list → lands as its own PR through the owner, **before U2's PR** | Replaces plan C1's battery recipe: compile writes the two parts; the LP coefficient stays the two-annuity sum it has today; finance reads the parts. C1 text to be amended in the plan |
| **S0b finance read** | fdixs0 `finance_case._assets` → `access.upfront_parts`; `cod_by_asset` derived from `build_year` with override; replacement default from part lifetimes | IC session (its files) | U1 follow-up PR, same PR as the generic defaults pack (U1 a), which should emit rows in the schema's part vocabulary |
| **S1 Expert face** | `PropertiesPanel` six cards + `CreationForm` field specs + `GenerationStack` columns + `propertyDocs` rendered from the schema; labels fixed; derived rows read-only; per-km line part; vintage clone button clones the parts | Frontend files in neither ownership list (`layout/*` is listed as shared) → own PR, any time after S0 | Parallel to U2/U3 |
| **S2 Guided face** | Ledger rows = view of `asset_params` provenance; `LedgerReview` keeps its chips; "Open in Expert" lands on the S1 card; rule 5 of the plan becomes a store property | GS session (`services/study/*`, `pages/decision/*`) | Inside U3 |
| **S3 Seeds** | EH templates, EH redundancy placeholders, chat `create_component` docs, CSV import: all through `derive` from the generic defaults pack (lifetime, FOM, provenance, `illustrative`) | templates: no owner (`project_templates/`); redundancy: `services/adequacy/*` is shared | After U1 a; before U4 (the data-centre template is the first consumer) |
| **S4 Later** | Store + Link mapping of the composite schema (GS MVP-2 item), HVDC per-km + converter pair, degradation into the LP, fuel-price composition of `marginal_cost` | — | After U4 |

### 5.2 Prerequisite vs later

- **Prerequisite for U2:** S0 (schema core, derive, accessor) and the C1 amendment. Without it U2 either ships the
  LP-coefficient change in §2.3 or keeps writing a two-annuity `capital_cost` that the finance engine cannot read.
- **Prerequisite for U3's "Open in Expert":** S1 at least for the StorageUnit and Generator cards (the guided
  question's two assets); the other four cards can follow.
- **Later:** S4; also the preflight "upfront typed into the annuity field" check becomes unnecessary once
  `capital_cost` is not typed, so it should not be built.

### 5.3 Size (rough)

| Step | Backend | Frontend | Tests | Notes |
|---|---|---|---|---|
| S0 | ~900 LoC | — | ~500 | schema declarations are data (~300 LoC of them) |
| S0b | ~150 | — | ~150 | one accessor swap, one derived date |
| S1 | ~100 (route) | ~1 500 (six cards collapse into one renderer; `PropertiesPanel.tsx` is 2 647 lines today) | ~400 | the largest, and the most mechanical |
| S2 | ~300 | ~200 | ~200 | ledger seeding reads the same pack rows |
| S3 | ~400 | — | ~150 | templates gain lifetimes, FOM, provenance |

### 5.4 Risks

| Risk | Why | Mitigation |
|---|---|---|
| PyPSA semantics | overnight ⊕ capital_cost; unequal `nyears` refuses overnight; `lifetime` retires assets; `n.statistics` charges installed capacity | `derive` is the only writer; composite assets leave `overnight_cost` NaN so `capital_cost` is honoured; the `nyears` refusal is caught at derive with the existing myopic workaround; `lifetime` is never filled by `derive` on a legacy row (the fill's own rule, `periodized_costs.py:155-173`) |
| Existing projects / netCDF | new custom columns absent (NaN) on old files; legacy rows have `capital_cost` only, some with `fom` folded in (PyPSA-Eur imports) | `access.upfront_parts` returns a single part flagged `derived_from_capital_cost` (ADR-0001: flagged, never fabricated); a one-time "Review cost basis" migration card lists those assets; `illustrative`/`legacy` chips in both faces; the EH templates are rebuilt by `_build.py`, so they migrate for free |
| Golden and parity tests (~40 files: `test_golden_economics`, `test_fom_reconciliation`, `test_capex_*_parity`, `test_overnight_cost_derivation`, `test_cost_breakdown_lifetime_capex`, GS `test_proforma_golden`) | they pin today's numbers | S0 changes no number for single-part assets (PyPSA still annuitises); the two-part battery is added to the fixture with its expected two-annuity coefficient and expected upfront parts; GS's 28 pro forma goldens port onto `engine_adapter` with the same parts (U2 already plans the port) |
| Frontend contract tests | label strings (`vocabularySource.test.ts`, `tests/fixtures/tool_schema_audit_phase1.csv` for tool docs) | labels come from the schema; the audit CSV is regenerated in the same PR |
| Shared-file conflicts | `models/schemas.py`, `network_crud.py`, `validation_service.py`, `layout/*` are on the plan's shared list | S0 is additive (new columns, one hook call); merged before U2 by the owner; S1 after U2 |
| Scope creep into the finance engine | replacement/degradation already exist in `FinanceInputs` | the schema only *defaults* those from the parts; `FinanceInputs` stays the engine's input and keeps its field names (facade §6) |

---

## 6. Recommendations

1. **Adopt the schema** (§3): overnight parts + lifetime + FOM share per part, `capital_cost` derived in one function,
   one accessor for the upfront parts, provenance per field in `n.meta`.
2. **Amend plan C1 now**: the battery compiles to two parts, not a blended `overnight_cost`; record §2.3 in the plan's
   review table and re-run the "numeric deltas" expectation of U2 against the two-annuity coefficient.
3. **Land S0 before U2's PR** as its own owner-merged PR; S0b inside the IC U1 follow-up; S1 any time after; S2 in U3;
   S3 before U4.
4. **Fix the labels in S1, not before**: a label-only patch now would be redone when the cards render from the schema;
   the glossary (CONTEXT.md:181-187) already states the rule the UI will follow.
5. **Fix the capex-budget fallback** (`objective.py:119-126`) in S0: with the schema there is always an upfront part
   or a flagged legacy value; the budget refuses a flagged asset instead of mixing bases.
6. **Seed every template and EH placeholder from the generic defaults pack** (S3), with lifetimes and provenance;
   retire the literal `60.0 / 40.0 / 80.0` redundancy costs.
7. **Do not build** the GS spec's "upfront typed into the annuity field" preflight check; remove the typed annuity
   field instead.
8. **Defer** Store + Link batteries, HVDC two-part links and LP degradation to S4; the schema already describes them,
   so adding the mapping later does not change the user-facing form.
