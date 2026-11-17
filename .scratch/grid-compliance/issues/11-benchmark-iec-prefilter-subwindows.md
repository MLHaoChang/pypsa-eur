<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-11 Benchmark: IEC 61400-27-2 pre-filter and transient / quasi-steady sub-windows

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md` (follow-up of GC-07, GK A2 condition B1)
Gated: yes (GK A, gridspine)

`gridspine/readback/benchmark.py` computes whole-window ME, MAE and MXE after linear resampling.
IEC 61400-27-2 also low-pass filters both signals before comparison and splits each window into
a transient part and a quasi-steady-state part, judging them separately. Until this lands, every
benchmark row carries `method = "whole-window metrics; no IEC pre-filter or sub-window split"`.

## What to build

1. An optional low-pass pre-filter (the standard's filter order and cut-off), applied to both
   frames on the common grid before the errors are computed.
2. A split of each window into transient and quasi-steady-state sub-windows at stated durations
   after each event, with tolerances keyed per sub-window.
3. `METHOD` per row reflects what was applied.

## Done when

- A step filtered by the pre-filter matches a hand-computed response within one sample.
- Sub-window boundaries match the stated durations; a row says which sub-window it is.
