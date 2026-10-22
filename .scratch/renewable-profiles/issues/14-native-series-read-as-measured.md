<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-14 — a network-native series is read as measured

Status: needs-triage
Type: bug
Baseline: master after #605 (RP-09)
Raised by: the engine gatekeeper, reviewing #605, 2026-10-22

`defaults.is_measured(meta)` returns True for an empty meta (`not meta`). A series the network brought in
(an imported `generators_t.p_max_pu` column) is seeded into `user_ts` with no record, so it reads as an
upload. `POST .../profiles/defaults/apply` then refuses it with `measured_series_kept`, though no one
uploaded a measured file.

Found while writing the batch-apply test: a fixture generator with a network-native `p_max_pu` was
refused by an experimental measured check, which RP-09 therefore does not carry.

To decide: should an empty meta count as measured (an upload has no generator record, so the two look
alike today), or should the import seed a record (`provenance: network_native`) so the two can be told apart?
The second is a data-model change on the import path.

Acceptance (once decided): a fixture with a network-native `p_max_pu` column is replaced by "Generate defaults
for all" and restored by one undo; a series applied as "measured" is still never replaced.
