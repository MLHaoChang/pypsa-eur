<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-20: transformer magnetising branch (MAG1/MAG2) in the PSS/E RAW

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/issues/04-psse-pack.md`
Gated: yes (GK A2, gridspine)

## Why

`gridspine/handoff/raw_writer.py` writes MAG1 = MAG2 = 0 for every two-winding transformer.
The comment calls this a case39-scope simplification, and it is exact there because case39 has
`pfe_kw == i0_percent == 0`. Campus transformers carry `pfe_kw` and `i0_percent`, so the RAW
(and every PSS/E pack and bundle built from it) leaves out their magnetising branch and its
losses. Found during GC-04 PR6 review (GK A2).

## What to build

Write MAG1/MAG2 from `pfe_kw`/`i0_percent` with the RAW's CM convention stated, on the same
base as pandapower's own branch (the GC-04 PR6 ppc oracle in `tests/gridspine/test_raw_v35.py`),
for both tap sides.

## Done when

- The RAW read as PSS/E reads it matches `net._ppc['branch']` b and g (shunt) for a transformer
  with nonzero pfe_kw and i0_percent, for an HV and an LV tap.
- case39 output stays byte-identical.
