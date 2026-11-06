<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# RP-14 — a network-native series is read as measured

Status: ready-for-agent (Q-RP-14 answered 2026-11-06)
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

## Comments

### 2026-11-05: triage sweep (AA-FILL-2), role needs-info

Was: `needs-triage`. Owner question Q-RP-14. How should a network-native series be told from an uploaded measured one? (1, Recommended) The import seeds a record with `provenance: network_native`, so `is_measured` stays strict and a measured upload is never replaced; it is a data-model change on the import path, but it is explicit. (2) An empty meta stops counting as measured: a one-line change, but an upload with lost meta could then be overwritten by 'Generate defaults'. (3) Leave it: the user clears the series by hand; no change. Once answered: ready-for-agent, Sonnet for (2) or Opus for (1), files `defaults.is_measured` and the import seeding, reviewer GK B.

2026-11-06, owner answer Q-RP-14 (coordinator card): "Import seeds provenance (Recommended)": import stamps provenance network_native. Role ready-for-agent.
