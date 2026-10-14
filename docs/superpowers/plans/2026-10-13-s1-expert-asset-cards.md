# Plan: S1, Expert asset cards rendered from the schema

**Date:** 2026-10-13. **Owner:** coordinating session (owner decision S1a, 13 Oct). **Parent:**
`2026-10-05-one-investment-engine-two-faces.md` §10 (S1 row) and §11 (M4). **Spec:**
`docs/superpowers/assessments/2026-10-05-uniform-asset-parameterisation.md` §3–§4 and its S1 row (:433).
**Lands:** after M2 (#171), in slices; S1b before U3's "Open in Expert".

## Why

Each Expert card hand-writes up to four lists that must agree: the read-out rows, the `startEdit` keys,
the save payload and the edit inputs. In all there are 226 literal labels in `PropertiesPanel.tsx`, with
units baked into strings. `CreationForm`, `BottomPanel`'s `COL_LABELS` and `GenerationStack` each carry
their own copies. They drift. `COL_LABELS.capital_cost = 'CC (€/MW/yr)'` is applied to Stores, which are
€/MWh/yr. The battery card still offers `capital_cost` as a typed field although S0 derives it from the
parts, so a typed value on a battery with parts is silently dropped on save (`network_crud._update_component`
→ `apply_parts`). The frontend cannot show or edit the parts at all.

## Shape

One descriptor per field, served by the backend, rendered by one card renderer. The vocabulary is the
study's `LedgerRow` (`label`, `technical_name`, `help`, `unit`, `range`, `status`, `provenance`), so S2's
guided ledger is a projection of these descriptors, not a translation.

```text
GET /api/network/asset_schema/{component}?carrier=…
  → { component, carrier, groups: [
        { id: identity|size|investment|fom|variable|performance|replacement|provenance,
          fields: [ { key, technical_name, label, help, unit_display, unit_storage, scale,
                      kind: number|bool|enum|bus|text, options?, required, key_param,
                      derived: bool, reveal_when?, part? } ] } ] }
```

- **Group order** is fixed on every screen (plan §10).
- **`derived: true`** rows (`capital_cost`, the annualised and upfront totals) render read-only, with "ⓘ how computed".
- **`scale`** converts between display and storage units. The canonical case is ×1000 for kW↔MW (§3.4).
- **`reveal_when`** replaces the D22 rules in `utils/attributeCatalog.ts`.
- **Provenance** per field is `{source, source_year, currency_year, range, status: default|customised|measured}`,
  stored in `n.meta["asset_params"][class][name][field]` (rule 7). An Expert edit sets `customised`.

## Slices (one PR each)

| Slice | Content | Size | Gate |
|---|---|---|---|
| S1a | Backend: the descriptor module (`asset_schema/fields.py`) with Generator and StorageUnit; `GET /api/network/asset_schema/{component}`; the provenance store (`asset_schema/provenance.py`): a changed field is stamped `customised` on update, re-keyed on rename, dropped by every removal; `GET …/{component}/provenance/{name}` | ~450 backend, ~280 tests | Descriptor, route and provenance tests; no card field lost; capability matrix and inventory checks pass |
| S1b | `AssetCard` renderer and edit form, used by the **StorageUnit** and **Generator** cards; parts editing; `capital_cost` read-only; vintage clone copies the parts; fixes the silent drop | ~600 FE, ~200 tests | Today's units tests pass or are regenerated with a reason; the battery edit round-trips its parts; the typed-annuity field is gone |
| S1c | Descriptors and cards for Store, Link, Load, Line (per-km part), Transformer; the old per-card lists deleted | ~500 FE net negative | Expert snapshot reviewed field by field |
| S1d | `CreationForm` field specs, `BottomPanel` columns and `GenerationStack` headers from the descriptors; `propertyDocs` tips folded in; the chat tool `create_component` documents its attrs from the descriptors (read the harness README first); the Store €/MWh/yr label fixed | ~400 FE | Units tests; no `COL_LABELS` unit strings left |

U3 needs only S1a + S1b, for the guided question's two assets (spec :442).

## Rules

- No typed annuity field anywhere (spec :484-485), and no "upfront typed into the annuity field" preflight.
- An asset typed the old way (a bare `capital_cost`, no parts) keeps working. `access.upfront_parts`
  back-calculates its part and flags it `derived_from_capital_cost`; the card shows that flag.
- Labels follow `pypsa-gui/CONTEXT.md`: "Overnight cost", never "CAPEX"; "Capital cost" always carries its period.
- Out of scope: M1's inspector (deferred), the Economics section (S2 in the UX doc's numbering, a different series), M5's
  catalogue-driven forms for non-asset components, and Store+Link batteries (S4).

## Open points to settle in S1a (no owner decision needed unless noted)

1. Provenance storage lives in S1a, because Expert edits are what set `customised`. S2 reads it and does not write
   its own (sent to the coordinator 13 Oct; GS is archived, so the next U3 session inherits this). A pack or study
   that writes defaults stamps them through `provenance.record`.
2. The route sits in `routers/network.py` beside `/catalog/{component}` and is cached the same way (project-free).
   `carrier` only changes defaults and reveal rules, never the field set.

## S1b decisions (13 Oct)

- **Blank policy on the descriptor** (`FieldSpec.blank`):
  - `clear` sends null: an Optional column, or a bound the write model turns into ±inf;
  - `zero` for costs, a deliberate "free";
  - `keep` for everything else, where a blank capacity is a slip.
  
  `test_blank_policy_agrees_with_the_write_model` keeps the descriptors honest against `models/schemas.py`.
- **Generator descriptors gain** `control`, `e_sum_min` and `e_sum_max`, which the old card had and S1a missed.
- **A composite asset's parts render as one table** (Part × Overnight / Life / FOM), as in the spec's battery card. In the two-column card, "Power part: overnight cost" would truncate. Each input keeps the full label as its accessible name. Derived rows take a full line.
- **Units:** the descriptors say EUR and the cards say € (`unitLabel`).
- **Frontend tests** render from `src/test/fixtures/assetSchema.json`. It is generated from the backend descriptors, and `test_the_frontend_fixture_matches_the_descriptors` fails when they drift. `vitest.setup.ts` mocks `useAssetSchema` with it.
- **A battery priced the old way** (one `overnight_cost`, no parts) keeps that cost and shows a note: enter the two parts to replace it. The clone button offers the overnight cost only for such a unit; a battery's parts ride along in the `...su` spread.

## S1c decisions (14 Oct)

- **S1c ships in two PRs.**
  - **S1c-1:** Store, Load and Link.
  - **S1c-2:** Line and Transformer. A line's investment is a per-km part: `inv_per_km_overnight`, with `overnight_cost = per_km × length` written by derive and re-derived when the line lengths are recalculated (`network_lines.apply_recalculate_line_lengths`). A Transformer has the same schema without that part.
- **Store and Link price like the Generator.**
  - The overnight cost is typed (€/kWh for a Store, which sizes energy; €/kW for a Link).
  - `capital_cost` becomes a read-only derived row: overnight × annuity, as PyPSA charges it.
  - A Store or Link priced by a bare `capital_cost` keeps it, and the row shows that column.
- **Percentages:**
  - Store: `e_min_pu`, `e_max_pu` and `standing_loss` are typed as %.
  - Link: `efficiency`, `p_min_pu` and `p_max_pu` are typed as %.
- **Reveal rules:** `e_initial` shows only while the Store is not cyclic. The capacity bounds show only while the asset is extendable.
- **Kept outside the descriptors:** the Link card's Energy Hub inputs, the per-period bounds buttons, the Load profile UI and the extras section stay as they were. The descriptors render around them.

### S1c-2 (Line, Transformer)

- **A line's investment is one part priced per MVA and km** (`schema.COMPOSITE["Line"]`, columns `inv_per_km_overnight` and `inv_per_km_fom_share`).
  - The part is scaled by `length`, as a battery's energy part is scaled by `max_hours`. `derive_composite` now takes each part's scale generally (`part_scales`).
  - The part has no lifetime of its own (`PartSpec.own_lifetime=False`). It takes the line's `lifetime`, so `lifetime` stays typed, and an existing unpriced line keeps its retirement.
  - The capital cost and fixed O&M are derived. The single `overnight_cost` per MVA is replaced, and a line priced that way keeps it and shows a note.
- **Every writer of `length` re-derives the cost** (`network_crud.rederive_scaled_parts`):
  - an edit (the update path re-applies the parts);
  - "Recalculate from coordinates";
  - a bus moved on the map (`_recompute_lengths_for_bus`);
  - `POST /lengths/from_geometry`.
- **Units:** branch money stays per MVA (€/MVA/km for a line, €/MVA for a transformer), the units catalogues publish. It is not converted to per kVA.
- **Outside the descriptors:**
  - the line's r/x/b keep their per-km block, after Length, because they are stored as totals;
  - the topology (ends, carrier) and a transformer's windings stay read-only in their own sections.
- **Transformer:** no carrier field (PyPSA's Transformer has none), and no outage fields (it had none before). A cleared declared voltage saves null.

## S1d decisions (14 Oct)

- **S1d ships in three PRs.**
  - **S1d-1:** the raw-table headers (BottomPanel, GenerationStack).
  - **S1d-2:** the CreationForm field specs.
  - **S1d-3:** the propertyDocs tips and the chat tool's attribute docs.
- **A raw table shows each column as stored.** It reads the class's descriptors for the label and appends the descriptor's **stored** unit (`storedHeader`): €/MW, a fraction. It never uses the card's display unit (€/kW, %).
  - A Store's `capital_cost` now reads €/MWh/yr.
  - COL_LABELS keeps only short names for columns no schema describes (Bus, Carrier), with no units. Those take the catalog's unit, as before.
- **GenerationStack is not mounted anywhere in the app**; only its own test renders it. Its headers move to the descriptors, but its editable `capital_cost` cell stays. The grid has no per-column edit flag. Make the derived columns read-only if the page is ever routed again.

### S1d-2 (CreationForm)

- **What quick-add asks for, investment:** option 1 of the question put to the coordinator at 08:30 CEST, which was my default because no other pick came back.
  - Generator, Link, Store and Transformer get the overnight cost and the lifetime. A Line gets its per-km cost and the lifetime.
  - Storage units get a note to price their two parts on the card.
  - No form types `capital_cost`.
- **Units from the descriptors:** a described field takes its label, display unit and scale from them (`withDescriptor`). A variant keeps its own label where it says more ("Capacity (electricity in)"). `like` borrows a descriptor for a column the schema does not list: a CHP's `efficiency2` uses `efficiency`.
- **Defaults and payload:**
  - FIELD_MAP defaults, and the defaults a choice snaps in (a heat pump's COP, a transformer preset), stay in stored units and are shown converted.
  - The payload divides by the scale.
  - The form waits for the schema before it exists, so a default can never be shown in the wrong unit.
