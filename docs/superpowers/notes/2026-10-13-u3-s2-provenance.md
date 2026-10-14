# U3 S2 and rule 5: the guided ledger as a view of the asset parameters

Parent plan: `plans/2026-10-05-one-investment-engine-two-faces.md`, §3 rule 5 and §10 S2. U3 progress record:
`plans/2026-10-13-guided-study-u3.md`. Owner decision (13 Oct): S1a owns `n.meta["asset_params"]` and its
single writer, `services/asset_schema/provenance`. The study calls `provenance.record` for pack defaults and
reads `provenance.get`; it builds no second store.

## What changed

| Where | What |
|---|---|
| `services/study/asset_view.py` (new) | `LEDGER_FIELDS`: the one table mapping a ledger row to the asset field the pack writes from it (key, component, asset, S1a field key, scale to stored units). `record_pack_defaults`, `asset_snapshot`, `expert_edits`, `mark_expert_edits`. |
| `services/study/packs.py` | `build_site_network` stamps every mapped field through `asset_view.record_pack_defaults`. The pack meta gains `pv_kind`, because the pack reads one PV kind's rows. |
| `routers/studies.py` | `GET .../ledger` shows the ledger through `_with_expert_edits`, reading each owned option fork on disk through a small per-file cache. Maturity stays the stored ledger's. |
| `services/study/report.py` + `decisionVocabulary.ts` | The `changed_in_expert:` prefix sentence, mirrored (the parity test pins both). |
| `LedgerReview.tsx` | A "Changed in Expert" chip on a row an Expert changed. |

## The rules

**Stamping.** When the pack writes an asset field from a ledger row, it stamps that field. A row the
guided user has not touched is stamped `default`, with source = the ledger version, `"generic-defaults
<pack version> <hash12>"` (the pack id and hash). A row the user changed is stamped `customised`, or
`measured` for a measured value, with the source the user gave. The currency year, the range (scaled
to stored units) and `illustrative` travel with the stamp. Only the PV kind the intake chose is stamped,
and the grid-only baseline stamps nothing.

**An Expert edit (rule 5)** is a mapped field where both of these hold:
- S1a's store calls the field `customised` (the Expert card and the chat tool stamp a changed field);
- its value no longer matches what the ledger compiles to (relative tolerance 1e-6).

A stamp without a change is not an edit, and neither is a change without a stamp: the study does not
guess who moved a value. The round-trip row maps onto `efficiency_store` and `efficiency_dispatch`
through its square root.

**Marking is a view.** The ledger read shows each edited row `customised`, `changed_by: expert`, with the
note `changed_in_expert:<key>:<option>`. The stored ledger is not rewritten, so the row's value stays the
study's. The study's maturity is not raised by an edit to a fork. The edited fork already makes the
findings stale (`fork_changed_since_run`, through `run_hashes`), and the route test pins that.

## Mapped fields

| Ledger row | Asset | S1a field | Scale |
|---|---|---|---|
| `battery_inverter_eur_per_kw` | StorageUnit `battery` | `inv_power_overnight` | ×1000 |
| `battery_inverter_lifetime_years` | StorageUnit `battery` | `inv_power_lifetime` | ×1 |
| `battery_inverter_fom_pct_per_year` | StorageUnit `battery` | `inv_power_fom_share` | ×0.01 |
| `battery_storage_eur_per_kwh` | StorageUnit `battery` | `inv_energy_overnight` | ×1000 |
| `battery_storage_lifetime_years` | StorageUnit `battery` | `inv_energy_lifetime` | ×1 |
| `battery_round_trip_efficiency` | StorageUnit `battery` | `efficiency_store`, `efficiency_dispatch` | √ |
| `discount_rate` | StorageUnit `battery`, Generator `pv` | `discount_rate` | ×1 |
| `pv_<kind>_eur_per_kw` | Generator `pv` | `overnight_cost` | ×1000 |
| `pv_<kind>_lifetime_years` | Generator `pv` | `lifetime` | ×1 |

Not mapped: the PV FOM, which the pack writes as `fom_cost` (a derived product of two rows), and the
tariff and finance rows, which compile to the engine's `commercial` / `finance` config, not to an asset
field. An Expert edit to those still makes the findings stale (`engine_inputs_changed_since_run`). Marking
their rows by `engine_path` is the outline's second half of rule 5 and waits for an IC hook in the
Investment editors.

## Dependencies

- **#178** (IC: the facade freeze and `PERMITTED_MODULES: services.asset_schema.provenance`). Without it,
  `test_study_one_engine` flags `asset_view`. The module imports it as
  `import services.asset_schema.provenance as provenance`, the form the permission covers.
- **S1b** (the cards from the descriptors): shows the stamps on the Expert card. "Open in Expert" badging
  then needs nothing more from the study.
