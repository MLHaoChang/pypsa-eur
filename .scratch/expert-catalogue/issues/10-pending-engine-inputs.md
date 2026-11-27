<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 10 Offer the engine inputs `not_offered.py` marks pending

Status: needs-triage
Source: expert-catalogue 01a-2 (coverage rule, spec §3.5 and Q3 (a)). Follow-up, not blocking 01a-2.

## What

`pypsa-gui/backend/services/asset_schema/not_offered.py` lists each `pending:` row: inputs an engine reads that the catalogue does not offer yet. Make each an entry, or move it to another reason kind with the reason stated:

- Line `r`, `x`, `b`, `g`, `type` and Transformer `b`, `g`, `type`, `tap_position`, `tap_side` (load flow; the PSS/E importer writes them);
- Bus `v_mag_pu_set`, `v_mag_pu_min`, `v_mag_pu_max` (load flow);
- Line/Transformer `s_max_pu`, `num_parallel`;
- StorageUnit `overnight_cost` (one total next to the per-part costs).

Adding a row to `fields.py` changes the `asset_schema/{component}` payload pinned by `tests/test_catalogue_legacy_schema_pin.py` (GK B (63) C2): re-record it on purpose in the same PR, or add the entry outside the legacy view.

## Acceptance

- No `pending:` row is left in `not_offered.py`, or each one left names why it waits.
- `tests/test_catalogue_coverage.py` passes with the pinned table updated.
