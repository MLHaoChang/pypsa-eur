# A bulk edit of a composite cost part skips re-derivation, so the fingerprint misses the LP change

Status: ready-for-agent
Type: bug
Found in: GK B (58) review of SH-14 S-B2, 2026-11-14 (defect on master, not in the PR).
Owner area: app backend (GK B reviews).

A single edit (PUT, `pypsa-gui/backend/.../network_crud.py:691`) runs `derive.apply_parts` and rewrites
`capital_cost` / `fom_cost` / `lifetime`, which are hashed columns, so the solver-input fingerprint moves.
`network_bulk.apply_bulk_update` (~:588) writes the raw part columns and never re-derives. That covers
`inv_*_overnight`, `inv_*_lifetime` and `inv_*_fom_share` on StorageUnit and `inv_per_km_*` on Line
(`asset_schema/schema.py` `COMPOSITE`). Bulk is the path behind the assistant apply (`assistant_tools.py:712`)
and the sensitivity sweep (`workflow_tools.py:672`).

The solve still re-derives `capital_cost` (`periodized_costs.py:213-235`), so the LP sees the new cost while the
fingerprint is unchanged. GK B reproduced it: a PUT moved the fingerprint; a bulk 300k→500k on
`inv_power_overnight` left it unchanged, and likewise for lifetime, fom share and Line `inv_per_km`.

Effect: every check that compares the fingerprint alone treats old results as current: `calculations.py:161`
(`results_current`), energy_flows_read / load_shape_read stale flags, `run_lifecycle.py:57` reuse, run_evidence
retained results, sweep case reuse (`workflow_tools.py:676`), the assistant preview/apply guard and
`provenance_graph/runs.py:151`.

## Acceptance

- Bulk applies `derive.apply_parts` per row, as the single edit does (GK B rulings: "a bulk write applies its side
  effects per target", #1466 D1; "a bulk path runs the same validators as the single edit", #738).
- Test: a bulk part edit moves `capital_cost` and the fingerprint, and the bulk result equals the PUT result, for
  StorageUnit overnight, lifetime, fom share and Line `inv_per_km`.
- Check the possible second bug (unverified): staging rewrites only `capital_cost`, so a bulk fom-share edit may
  never reach `fom_cost`. Fix it in the same PR if confirmed.
- Optional backstop: also hash `part_columns()` in the fingerprint.

## Comments
