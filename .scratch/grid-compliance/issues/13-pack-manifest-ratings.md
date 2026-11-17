<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-13 Pack manifest: plant ratings bound by the data hash

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md` (follow-up of GC-07, GK A2 condition K2d)
Gated: yes (GK A, gridspine)
Owner: GC-06 (PSCAD/EMT pack); GC-04 for a PSS/E pack manifest

## Why

The GC-04 battery states P and Q setpoint steps in pu of the plant's rated active and apparent
power, and GC-07's pack read-back puts the `p_poc_mw` and `q_poc_mvar` channels in pu of those
ratings. The pack manifest records no rating today, so read-back takes rated P from the campus
run's connection capacity (`pcc.p_connection_mw`), which need not equal the plant's rated output,
and reads every MVA-based case "unknown" unless the caller states a rated MVA and its source.

## What to build

1. GC-06: write `rated_mw`, `rated_mva` and `rating_source` into `manifest.json`, under the
   `data_hash` (in a hashed file, so the hash binds them).
2. GC-07 pack read-back: take the ratings from the manifest when present, name
   `rating_source` in every P- and Q-based row and the judgment header, and refuse a pack whose
   manifest rating is not finite or is ≤ 0.

## Done when

- A pack written with ratings is judged on them without caller input, and a changed rating
  changes the `data_hash`.
