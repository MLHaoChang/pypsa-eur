<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-28: derive or check plant ratings against the campus once it names its plant

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/13-pack-manifest-ratings.md` (GK A R5, ruling A)
Gated: yes (GK A, gridspine)

## Why

Since GC-13 a pack's `rated_mw` and `rated_mva` are stated by the caller with a required
`rating_source`. The pack's `campus_data_hash` proves which campus run the pack was built from,
not that the ratings are that campus's values. The campus description does not name its plant:
it has per-unit `p_mw` and `s_mva` and the connection capacity `pcc.p_connection_mw`, and a
plant's rated output can legitimately exceed that capacity (export limiting). So no rating can be
derived from the campus or checked against it without defining "the plant" first.

## What to build

1. The campus description names the plant whose ratings the packs use: its units, or a stated
   rated MW and MVA with a source.
2. The pack writers take the ratings from there. Otherwise `judge_pack_results` checks a pack's
   ratings against them, naming both.

## Done when

- A pack whose ratings are not its campus's stated plant ratings is refused or flagged, and a test
  covers it.
