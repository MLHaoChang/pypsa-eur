# Edge Investment Case — Phase 0 (contracts & seams): end-to-end QA

**Plan:** `docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md` (v2.1) · **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` · **Branch:**
`claude/energy-tool-features-research-fdixs0` · **Head at gate:** see commit carrying this note.

## What Phase 0 delivers

| WP | Deliverable | Tests | Review verdict → closure |
|---|---|---|---|
| 0.1 | `models/commercial.py`, `models/finance.py`, `models/flex_archetypes.py`; `InvestmentCaseReport` skeleton (house-shape `completeness` + `sections`), export keys, 8 fixtures | 61 | PASS WITH CONDITIONS → falsifying validator table, full §4 round-trip set, completeness shape aligned to EH, null defaults for unsupplied `inflation` / `contingency_share` (c5b9792) |
| 0.2 | `services/finance/packs/` loader: dated, hashed, immutable packs; `RuleLookup(not_established)`; `eu_de`, `us_federal` stubs | 14 | PASS WITH CONDITIONS → JSON-native rule values (no `default=str`), `country` join key to `Tariff.jurisdiction`, `valid_to` + versioned registry, pack-level hash tests (e35f271) |
| 0.3 | `services/results/physical_quantities.py` seam agreeing with `compute_asset_economics` | 14 | PASS WITH CONDITIONS → per-link p1 fallback, NaN-weight parity, PoC role set + net flow, inline flat network covering Store / 3-port Link / two PoC links (284d051) |
| 0.4 | `sensitivity` scenario type (backend + frontend + badge), no migration | +5 backend, +3 frontend | PASS WITH CONDITIONS → badge render test, route-level PATCH test, stale comments; a pre-existing test that used `sensitivity` as its "unknown" value was switched to `exotic` (265895d) |
| 0.5 | `investment_case_report`, `billing_frames`, `last_commercial_terms` in `RESULT_STATE_KEYS`; report helpers; QA driver extension | 12 + 3 driver steps | PASS WITH CONDITIONS → UTC-normalised billing frames and JSON commercial terms (a tz-aware frame is refused by the restricted unpickler and would drop EVERY side result on reload); `/run` and queue claims clear the three keys (ca6ca7a) |
| 0.6 | AST-based tripwires: no router / `solver_service` / `services.solver.*` imports from `services/{commercial,finance,library}`; docstring-only `__init__`s | 12 (4 skip until P1 creates the packages) | — |

## Gate evidence (2026-09-27)

| Check | Result |
|---|---|
| Phase 0 test files | **117 passed, 4 skipped** (skips = packages P1 creates; `finance` guards against vacuity) |
| Full backend suite (`-m "not slow"`, Python 3.12 venv pinned to `pixi.lock`: pypsa 1.1.2, linopy 0.8.0, highspy 1.14.0, pandas 2.3.3, numpy 2.4.6, xarray 2025.6.1) | **5,790 passed, 31 skipped, 13 failed** in the run; all 13 re-run **green**: 12 needed `pywebview` (the `test` env's desktop feature, now installed at the pinned 6.2.1), 1 was the tenancy test fixed in 265895d after the run began |
| QA drivers (`tests/run_qa_drivers.py`) | **21/21 passed**, incl. the extended `qa_save_load_roundtrip.py` (63 steps) |
| Frontend (`vitest run`) | **177 files, 1,943 tests passed** (baseline 1,940) |
| Frontend typecheck (`tsc --noEmit`) | clean |

## Environment note

The container has no pixi. A venv built with `uv` on Python 3.12 (the repo's `gridspine` distribution
requires ≥3.12) with every science pin taken from `pixi.lock` reproduces the `test` environment. A
first attempt on Python 3.11 with a newer pandas (3.0.x) failed the golden fixture with an xarray
alignment error; aligning to the lock's pandas 2.3.3 fixed it. Record: use the lock, not the ranges
in `pixi.toml`.

## Carried forward (not Phase 0 scope)

- The queued-solve claim does not clear the `eh_*` result keys the foreground `/run` claim clears
  (pre-existing; noted by the WP0.5 reviewer). Not fixed here — it belongs to the EH stack.
- `billing_frames` size at 15-min resolution (35,040 rows per item-year) re-pickles on every save
  and is unpickled by compare to read `last_lost_load`; P2 WP2.1 should store float32 and only the
  needed columns, or move the frames to a sidecar file.
- Tariff fixtures use ISO `DE`/`US`; packs carry `country` for the join (P1 binds them).
- FOM: `physical_quantities` exposes FOM separately; the delegated fix
  (`claude/fix-fom-reconciliation`) decides whether economics fold it into fixed cost.

## Gate verdict

- [ ] Assessor verdict: _pending_
