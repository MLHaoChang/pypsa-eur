<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-01b Series capacitors in the campus description

Status: needs-triage
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none (follows GC-01)
Gated: **yes** (GK A2, gridspine)
Model tier: opus
Size: S
Owner decision: GC-SCOPE-1, GC-TEXT-1
TRGC: sub-synchronous screening (GC-08)
Likely files: `gridspine/ingest/campus.py`, `gridspine/schema/campus.py`, `tests/gridspine/test_campus_series_capacitors.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering.

## Why

GC-08's sub-synchronous scan takes `series_caps=[(from_bus, to_bus, Xc ohm at f0)]`, but a campus
description cannot hold a series capacitor: the top-level keys (`_TOP`) are closed and a tagged
value must be positive, so a negative reactance is refused. Until a key exists, GC-08's campus
step reports "not evaluated".

## What to build

1. A `series_capacitors` campus key: `{name: {from_bus, to_bus, xc_ohm}}`, or the compensation
   degree `k` (0 < k < 1) with the `line` (a cable name) it compensates, giving Xc = k · X_line.
   Exactly one of `xc_ohm` and `k` + `line`. Values are tagged `{value, source}`; `xc_ohm` is the
   positive capacitive reactance magnitude at f0.
2. Stored on `Campus` (a table: name, from_bus, to_bus, xc_ohm, source) and exposed in the
   `series_caps` form GC-08 reads. Decide with GK A2 whether it also enters the pandapower net
   (e.g. as an impedance element) or stays a study-only record.
3. Refusals naming the field: unknown bus, buses at different nominal voltages, both or neither
   of `xc_ohm` / `k`, `k` outside (0, 1), a `line` that is not a cable between the same buses,
   untagged value, duplicate name.

## Done when

- A campus with a series capacitor builds; GC-08's scan reads it and evaluates the campus step.
- Each refusal has its own test asserting its reason.
- A campus without the key builds unchanged.
- How GC-01's aggregation treats a series capacitor inside the collector tree is decided
  (refuse, like other non-converter elements, unless GK A2 rules otherwise).

Release note: one new file under `doc/release-notes.d/` (see its README).

## Comments

**2026-11-17.** Filed from GC-08's request via the coordinator; GC-08's plan (GO by GK A2) consumes
it. Not folded into GC-01: GC-01 is already two PRs, and this is a separate schema change.
