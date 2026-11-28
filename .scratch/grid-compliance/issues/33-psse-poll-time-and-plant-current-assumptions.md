<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-12 poll time (GC-27) and GC-04b unverified PSS/E assumptions

Status: needs-triage
Type: task
Review: GK A
Source: brief v25 follow-ups (GK A 39) (filed 2026-11-28 from the coordinator's follow-up lists)

## What is left

- GC-12 PR-B risk, to GC-27: each poll is logged at the requested stop time, not the time PSS/E reached.
- GC-04b is unverified on PSS/E: per-machine `increment_gref`/`vref` scaling; the MBASE-weighted plant current sum assumes in-phase currents; equivalence (1e-6 pu) was checked only on a test-only reduced REPC_A model. Values are tagged assumed.

## Comments
