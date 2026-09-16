# Phase 2 — Class-B Link residuals inventory

**Date:** 2026-09-16  
**Plan:** `docs/superpowers/plans/2026-09-14-eh-reference-design-gaps.md` Phase 2  
**Rule:** Do **not** re-implement Class B. Inventory first; fix confirmed gaps only.

## Shipped (do not rebuild)

| Piece | Evidence |
|---|---|
| Link contingency driver (`p_max_pu`/`p_min_pu`→0, static) | `services/adequacy/sweep.py::class_b_contingencies` |
| First-order severity `q × ΔEUE × VoLL` | `test_class_b_rows_price_outages_first_order` |
| Time-varying `links_t.p_max_pu` zero + restore | `test_class_b_zeroes_time_varying_availability_too` |
| Abort / closing restore | `test_adequacy_abort.py` F1b* |
| Lines/Transformers stay on SCLOPF | `solver/assumptions.py::resolve_branch_outages`; Class-B docstring |

## Candidates

### 1. Multi-carrier Link filters — **GAP → fix**

Spec §4.3: electricity-only metric. P2X / conversion Link outages that reduce
electrical demand must be `in_metric_scope=False` with non-negative zeroed
severity — never ranked as beneficial.

**Shipped:** every eligible Link gets `in_metric_scope: True`.  
**Action:** detect conversion Links (bus carriers / role / carrier); still
emit the row, flag out of scope, zero severity/criticality.

### 2. Time-varying `links_t.p_min_pu` — **GAP → fix**

**Shipped:** static `p_min_pu`→0; TS only `p_max_pu`.  
**Parity:** DtC `apply_islanding_contingency` and archetype `off_grid` already
zero both TS attrs.  
**Action:** mirror DtC in Class-B mutate + extend the TS test.

### 3. Line/Transformer SCLOPF ↔ FMEA top-N — **PARTIAL → document / N/A merge**

Merging SCLOPF branch N-1 into FMEA rankings is a different product (preventive
N-1 ≠ held-horizon ΔEUE×q). Spec decision 14: default **Link-primary**; document
omission.

**Action:** record Link-primary residual risk on EH `fmea_top` skip note;
**do not** merge SCLOPF rows into FMEA in P2.

## Other edges reviewed

| Item | Status |
|---|---|
| Closing restore / abort | SHIPPED |
| User-TS reapply survival | SHIPPED |
| Multi-port `bus2+` Links | N/A / untested |
| Stale docs saying `p_nom→0` | Doc drift only |

## P2 ship set

1. Inventory (this file)  
2. `links_t.p_min_pu` mutate/restore + test  
3. Conversion Link `in_metric_scope=False` + test  
4. `fmea_top` skip note = Link-primary residual risk (decision 14)  
5. Close SCLOPF↔FMEA merge as **N/A** for P2
