# IC G1, G2 gate: findings

**Date:** 2026-10-07. **Branch head:** d94402e (`claude/energy-tool-features-research-fdixs0`; master 44117db merged
in). **Plan:** `docs/superpowers/plans/2026-10-07-ic-g1-g2-campus-equipment.md` (plan PASS round 2, code PASS round 1,
§6). **Agreed shapes:** U1 landing plan §8 (owner-approved 2026-10-07, sent to the campus session).

## What landed

- **G1.** Defaults-pack version `2026-10-07`: 2026-10-05 unchanged (byte-identical, pin `962a0184…` kept) plus 243
  campus rows: 81 entries from `gridspine/templates/data/campus_assets.yaml` (29 transformers, 12 cables, 9 capacitor
  banks, 9 shunt reactors, 9 STATCOMs, 13 switchgear) × `overnight`, `lifetime`, `fom_share`, on one part
  `investment`. Each row is `illustrative`, sourced "assumed (gridspine campus_assets.yaml placeholder)", in 2026 EUR
  on the money rows. The technology key is `<kind>.<id lowercased>`. The manifest pins the YAML by its sha256; the
  parity test checks against that pin. The loader gains the bases `lump` (EUR/unit) and `per_bay` (EUR/bay). New pin
  `c87cb777…`; the latest version is 2026-10-07.
- **G2.** `ExtraOwnerAsset` (`services/finance/case.py`, frozen facade) and
  `build_finance_case(..., extra_assets=())`:
  - each becomes owner capex (`component = "campus:<kind>"`, no carrier) with its parts;
  - a fixed-O&M `TemplateLine` per template, added after annualising and never in the counterfactual;
  - COD by build year (`cod_from_build_year`, `cod_mismatch`, `extra_asset_staged_build`);
  - excluded from incentives (`incentive_excludes_extra_asset`);
  - the tax class from `depreciation_class_by_asset`;
  - refusals for duplicates, network-name clashes and capital-cost-derived upfronts, and a
    `extra_asset_may_double_count` flag;
  - report `extra_assets` block and xlsx rows;
  - `FinanceCase.extra_assets`, hashed only when non-empty.
- `UpfrontPart` is named only for typing and read by attribute, so the finance package never loads the solve stack
  (the IC tripwire) and `asset_schema` is untouched.

## Evidence

- **Transcription.** The reviewer's own script: all 81 entries exact (value, basis, unit, provenance, domains).
- **End to end through `run_case`** (owner battery + transformer, 2.5 km cable, capacitor bank):
  - capex = 1.15 × 2,115,000;
  - FOM per year = −Σ fom_share × overnight × 1.02^(y − base);
  - the capacitor bank's replacement in its last service year;
  - every extra's terminal term by hand;
  - the counterfactual identical with and without extras;
  - the battery LCOS unchanged;
  - tax missing vs set;
  - a 2027-07-01 COD accepted by year.
- **Hash stability.** A case without extras keeps its master hash (`64e7a34307ea1af3`, recomputed on a 546f2cb
  archive).
- **Gate on d94402e.**
  - Backend: **10,091 passed, 39 skipped, 0 failed** (82 min).
  - All **31** QA drivers pass (`qa_investment_case.py` 259/259: the 251 baseline plus scenario N).
  - vitest: **3,411** tests in 292 files pass.
  - tsc: clean.

## Known limits (disclosed, not defects)

1. Campus costs are illustrative placeholders (`source: assumed` in the campus library); a price change there needs a
   new pack version (the parity test reports it as such).
2. Extra assets' costs must be in the case's currency and money year; the caller converts. The route builder must
   refuse or convert when the campus library's `currency` / `price_year` differ.
3. `scale_capex` scales an extra's capex but not its fixed O&M line (as for network FOM, which comes from the LP's
   cost rows); a CAPEX tornado bound moves capex, replacements and the terminal value only.
4. An extra's terminal-value term is valued at the case's LP basis but was never charged by the LP; GS's
   by-construction identity does not hold with extras (the report's terminal basis says so).
5. `extra_asset_may_double_count` is broad by design: any transformer or cable extra is flagged when the owner owns any
   investable network Transformer or Line.
6. "Latest" pack now carries 81 more technologies on `lump` / `per_km` / `per_bay`; nothing in production loads the
   latest pack today. U2 filters by basis and looks a stamped tariff's pack up by its stamp version.
7. No route passes `extra_assets` yet (who builds it is the campus session's answer, §8). That route must put the
   extra assets and their `source_hash` into the assumptions digest, or a changed campus study will not mark a report
   stale.
8. No frontend change: the extra assets appear in the report payload and the xlsx, not yet in the Investment tab.
