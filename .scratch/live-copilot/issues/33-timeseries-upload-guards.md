<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# LC-33 — Time-series upload: untyped duplicate refusal, silent fill, off-horizon horizon replacement

Status: ready-for-agent
Type: bug

Found by LC-32 (D03, `LC-D03.upload_guards`) driving `upload_timeseries` on a four-snapshot
network (`loads`, `L1`, `p_set`):

1. A CSV with a repeated timestamp fails inside pandas with `InvalidIndexError: Reindexing
   only valid with uniquely valued Index objects`, not a typed refusal; the stored series is
   unchanged. The model gets an untyped error, not an `error_kind` it can act on.
2. A CSV missing one snapshot (00:00, 01:00, 03:00) is accepted (`rows: 3`) and the gap is
   forward-filled (1, 2, 2, 4) with no warning.
3. A CSV whose timestamps all lie outside the horizon (2026-06-01 for a 2026-01-01 network) is
   accepted and replaces the network's snapshots with its own two rows.

Wanted: a typed refusal for 1, a stated fill or a refusal for 2, and an explicit refusal or
confirmation for 3. Then add the covering tests to
`pypsa-gui/backend/tests/test_lc06_d03_chronology_edges.py` and flip `LC-D03.upload_guards`.
