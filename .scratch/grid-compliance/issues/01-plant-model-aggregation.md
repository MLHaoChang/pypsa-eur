<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# GC-01 Plant model aggregation and a versioned model set with data freeze

Status: ready-for-agent
Type: task
Parent: `.scratch/grid-compliance/spec.md`
Blocked by: none
Gated: **yes** (GK A, gridspine)
Model tier: opus
Size: M
Owner decision: GC-SCOPE-1, GC-TEXT-1 (2026-11-17)
TRGC: 01, 02 (feeds every export pack)
Likely files: `gridspine/ingest/campus.py`, `gridspine/schema/campus.py`, new `gridspine/ingest/aggregate.py`, new `gridspine/schema/model_set.py`, `tests/gridspine/test_campus.py`, new `tests/gridspine/test_aggregate.py`

**Confidentiality (GC-TEXT-1).** Cite only the public grid code (SAGC / TRGC by SE / NGSA) and generic engineering. TRGC numbers and titles may be named. Never name a project, customer, consultant, EPC or OEM, here, in code, tests, fixtures, UI text or PR bodies.

## Why

TRGC 01/02 ask for a detailed and an aggregated plant model. GridSpine has the detailed campus
model and a Thevenin at the PCC, but no aggregated equivalent, no slot for a vendor model file and
no record of which data a model was built from.

## What to build

1. **Aggregated equivalent** (`ingest/aggregate.py`): collapse converter units behind each PoC
   into one equivalent unit + one equivalent collector branch + main transformer, using the WECC
   single-machine equivalencing method (collector impedance by the power-weighted
   "equal-loss" rule, charging summed, unit ratings summed). Output is a `Campus` the existing
   load flow and short-circuit code accept unchanged.
2. **Vendor-model slot** in `schema/campus.py`: per unit type, an optional reference (file name,
   tool, model name, version, hash) to an external black-box model. Stored, never parsed.
3. **Model set and data freeze** (`schema/model_set.py`): `freeze(campus_spec, params)` returns a
   `ModelSet` with version id, SHA-256 of the canonicalised inputs, timestamp, and the
   detailed/aggregated pair. Refreezing identical inputs gives the same hash.
4. A manifest dict every export pack (GC-04..06) embeds: `model_set_id`, `data_hash`, `variant`
   (`detailed` | `aggregated`).

## Done when

- On a fixture with N identical units on a radial collector, the aggregated collector impedance
  matches a hand calculation of the equal-loss formula to 1e-6, and the PoC P, Q and |V| from the
  aggregated LF match the detailed LF within 0.5 % at three dispatch points.
- PoC three-phase fault level from the aggregated model is within 2 % of the detailed one.
- `freeze` is deterministic (same inputs → same hash; one changed parameter → different hash;
  key order does not matter). Tests kill the mutation "hash ignores parameters".
- A unit with zero rating or a non-radial collector refuses aggregation with a named error,
  not a silent fallback.

Release note: one new file under `doc/release-notes.d/` (see its README).
