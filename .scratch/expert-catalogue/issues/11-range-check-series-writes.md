<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 11 Run the catalogue range check on time-series writes

Status: needs-triage
Source: expert-catalogue 01a-3a (GK B2 R6). Follow-up, not blocking 01a-3.

## What

01a-3a puts the hard ranges of `services/asset_schema/ranges.py` on every static write path: REST create, update and bulk, chat writes, previews, project changes, sensitivity cases, experiments and the Library applies. Time-series writes do not run the check yet:

- `*_t` uploads (user time series, `services/user_timeseries.py`);
- series edits from the asset drawer;
- the bulk series paths.

A series value of `standing_loss` above 1, or `efficiency_dispatch` at 0, reaches the solve as it does today.

## Acceptance

- Each series write path refuses the values `ranges.problem` refuses, with the same `value_out_of_range` kind and text, and has a test naming the path.
- A value equal to the stored one is never refused (C8, R4), and restores never check (R5).
