# Plan: M5, schema-driven forms for the non-asset components

**Date:** 2026-10-14. **Owner decision:** picked 14 Oct (relayed by the coordinator), after Economics in Expert.
**Source:** umbrella plan `2026-10-05-one-investment-engine-two-faces.md` §11 ("M5 catalogue-driven forms"); the S1
asset cards (`2026-10-13-s1-expert-asset-cards.md`) are the pattern.

## What exists today

| Class | Edited today | Note |
|---|---|---|
| Bus | Properties → `BusPanel`, hand-written read-out and form, `propertyDocs` tips | full-row PUT; Energy Hub tags and extras around it |
| Carrier | Bottom panel → Carriers grid only (no Properties card) | `co2_emissions`, `nice_name`, `color`, `unit` |
| GlobalConstraint | Solver Settings → create-only form and table | updated only by the per-period fuel limits |
| ShuntImpedance | no frontend at all | routes exist |

## Slices

- **M5a (this PR):** descriptors for all four classes (`services/asset_schema/fields.py`), so the schema route,
  the provenance store, the raw-table headers and the chat tool's attribute docs cover them; the Bus panel renders
  from them (`AssetFieldsEdit`, `seedForm`, `buildPatch`), with the coordinate pair and the Energy Hub tags kept
  around the descriptors; the Bus tooltips folded into the descriptors' help.
- **M5b:** a Carrier card in Properties and the GlobalConstraint form in Solver Settings, from the descriptors.
- **M5c:** ShuntImpedance gets a frontend (api, a card, a Bottom panel tab).

## Decisions (M5a)

- **No investment groups** for these classes: identity and technical fields only. A GlobalConstraint is all
  identity (its fields are the constraint itself, not an asset's size or performance).
- **A fourth blank policy, `empty`:** a text column the write model types `str = ""` (a Bus's country and
  sub-network, a Carrier's display name, colour and unit) saves "" when emptied. The old Bus form did; null would be
  refused. A GlobalConstraint's optional carrier, carrier attribute and period save null (`clear`).
- **The Bus read-out** keeps its identity rows (carrier, control, country, sub-network, coordinates) with the
  descriptors' help, and shows the rest through `assetReadoutItems` (the nominal voltage, with its provenance chip).
- **Extras on the Bus are saved** like on every asset card (`extrasPatch`); the old Bus save sent only the
  current row, so an edited extra column was dropped.
- **The raw tables:** the Buses and Carriers tabs now take the descriptors' labels and stored units, so the last
  short names (`COL_LABELS`) are gone.
- `bus.coordinates` stays in `propertyDocs`: the coordinate pair is one row over two described fields.

## M5b as built (14 Oct)

- **The Carrier card** (`layout/properties/CarrierPanel.tsx`): a Carriers-tab row opens it in Properties (before,
  the row click was skipped). It reads out the display name, the colour with a swatch, the unit and the CO₂
  intensity with the descriptors' help, and edits through `AssetFieldsEdit`, `seedForm` and `buildPatch`; a cleared
  text column saves "", a rename moves the selection. The header's "view results" action is hidden: a carrier is
  not an asset.
- **Reveal rules may name several values:** a tuple in a descriptor (a list on the wire) reveals on any of them. A
  GlobalConstraint's carrier attribute shows for primary energy only, its carrier for the two carrier-scoped
  limits.
- **The GlobalConstraint form** (`pages/solver/GlobalConstraintForm.tsx`) replaces Solver Settings' hand-written add
  form and adds edit in place (a row's Edit button). A field the chosen type does not read saves null, so an
  operational limit never keeps "co2_emissions". The carrier keeps the grouped `CarrierSelect` with its explicit
  "pick a carrier" choice, and the period is a choice among the model's periods.
- **Found on the way, fixed first (#239):** PyPSA reads a carrier-scoped limit's carrier from `carrier_attribute`,
  and the app wrote `carrier`, so those limits never bound. The service now writes both; the form relies on that.
