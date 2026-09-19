# Phase 7 — RAM v1 residuals inventory

**Date:** 2026-09-18  
**Plan:** `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md` Phase 7  
**Rule:** Do **not** rebuild occurrence / asset_health / COPT / MC engines. Inventory first; enrich provenance disclosure only.

## Shipped (do not rebuild)

| Piece | Evidence |
|---|---|
| `CARRIER_DEFAULTS` thermal/hydro/biomass/battery library + citations | `occurrence.py`; VRE deliberately absent |
| Resolve chain `asset` → `carrier_default` → `missing` | `resolve_outage_params` + occurrence tests |
| `CoptUnit.source` / `StorageSpec.source` in engines | `copt.py` membership walk; `mc.py` storage extract |
| Reserve-margin payload + UI `source` column | `report.py` / `ReserveMarginPanel` |
| `asset_health` sidecar + `provenance_report` | `asset_health.py`; chat reconcile |
| Worksheet **mitigability** (+ notes) | `worksheet.py` / FMEA Phase 3 |
| Honest “not full RAM/CMMS” language | EH plan/spec decision 11 |

## Candidates

### 1. COPT / MC omit rate-source on the wire — **GAP → fix**

Acceptance: “MC/COPT show library vs override provenance.”  
Engines already carry `source`; `attribute_criticality` / MC result drop it.  
`folded_units[].source == "static"` is CF-fold provenance, **not** rate provenance.

**Action:** expose `rate_source` ∈ `{asset, carrier_default, missing}` on COPT `per_mode` + `fleet.units_provenance`, and on MC result `units_provenance` (+ storage). Optional `library_citation` when `carrier_default`.

### 2. Library citation not on resolve frame — **GAP → thin fix**

Citation lives in `CARRIER_DEFAULTS[c].source` (OutageParams field name); resolve overwrites the frame column with the tier tag.

**Action:** carry `library_citation` beside `rate_source` on the wire (no separate CMMS endpoint).

### 3. Detectability schema+UI — **DROP**

Worksheet is mitigability-only by FMEA Phase 3 design. IEC detectability is schema+UI+CSV work with no EH acceptance dependency.

**Action:** mark dropped in plan; do not add fields.

### 4. Spare-lead-time severity modifier — **DEFER (optional)**

Zero code today. Acceptance does not require it; optional plan line stays deferred with planned-outage MC.

**Action:** document defer; do not invent a silent severity scale in P7.

### 5. Extend library for H₂ / heat / electrolyser — **N/A this phase**

Generation-shaped library is intentional. Multi-energy rates belong with P6 (blocked on slack redesign).

## Other edges reviewed

| Item | Status |
|---|---|
| Planned-outage calendars in MC | DEFER (decision 11) |
| Full RAM/CMMS / inspection history | N/A — `asset_health` is one current record |
| HTTP asset_health without reconcile | By design (sidecar-only); chat fuses |
| VRE FORs in `CARRIER_DEFAULTS` | N/A — profile-borne; tests pin absence |
| Reserve-margin `source` as substitute for MC/COPT | Insufficient for stated acceptance |

## P7 ship set

1. Inventory (this file)
2. TDD: COPT + MC expose `rate_source` (+ `library_citation` when library)
3. Wire provenance in `attribute_criticality` / COPT fleet / MC result
4. Drop detectability in plan
5. Defer spare-lead-time + planned-outage MC
6. Honesty `ram_note` on COPT/MC payloads
