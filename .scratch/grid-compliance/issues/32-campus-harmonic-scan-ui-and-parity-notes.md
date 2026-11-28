<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Harmonic emission screen follow-ups from gc 29 slice B

Status: needs-triage
Type: task
Review: UX 34 + GK A2
Source: gc 29 (#2142) (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- `CampusHarmonicScanSection` renders raw scan error text (UX 34).
- `basisText` double-dots a label that already ends in a full stop.
- The route-parity binary-transfer fragment is accurate only for PUT (GK A2). The parity checker needs an owner decision for a better reason string.

## Comments
