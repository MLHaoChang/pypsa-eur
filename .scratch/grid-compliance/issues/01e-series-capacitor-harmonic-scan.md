<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-01e Series capacitors in the harmonic impedance scan

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (follows GC-01b)
Gated: **yes** (GK A2, gridspine)

## Why

GC-01b gives a campus series capacitors (`Campus.series_capacitors`, a negative-reactance pandapower
`impedance`). `harmonic_scan.impedance_scan` walks only lines and transformers, so for now it refuses
such a campus and names the capacitor (GK A2 condition S5).

## What to build

Carry each series capacitor in the harmonic Y matrix as -j·Xc/h at order h, like `subsync_scan.z_poc`,
and lift the refusal. Test the series resonance against a hand calculation.
