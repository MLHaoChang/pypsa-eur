# Edge Investment Case — Phase 0 (contracts & seams): end-to-end QA

**Plan:** `docs/superpowers/plans/2026-09-26-edge-investment-case-p0-p1.md` (v2.1) · **Spec:**
`docs/superpowers/specs/2026-09-26-edge-investment-case-design.md` · **Branch:**
`claude/energy-tool-features-research-fdixs0` · **Head at gate:** see commit carrying this note.

## What Phase 0 delivers

| WP | Deliverable | Tests | Review verdict → closure |
|---|---|---|---|
| 0.1 | `models/commercial.py`, `models/finance.py`, `models/flex_archetypes.py`; `InvestmentCaseReport` skeleton (house-shape `completeness` + `sections`), export keys, 8 fixtures | 71 | PASS WITH CONDITIONS → falsifying validator table, full §4 round-trip set, completeness shape aligned to EH, null defaults for unsupplied `inflation` / `contingency_share` (c5b9792) |
| 0.2 | `services/finance/packs/` loader: dated, hashed, immutable packs; `RuleLookup(not_established)`; `eu_de`, `us_federal` stubs | 14 | PASS WITH CONDITIONS → JSON-native rule values (no `default=str`), `country` join key to `Tariff.jurisdiction`, `valid_to` + versioned registry, pack-level hash tests (e35f271) |
| 0.3 | `services/results/physical_quantities.py` seam agreeing with `compute_asset_economics` | 14 | PASS WITH CONDITIONS → per-link p1 fallback, NaN-weight parity, PoC role set + net flow, inline flat network covering Store / 3-port Link / two PoC links (284d051) |
| 0.4 | `sensitivity` scenario type (backend + frontend + badge), no migration | +5 backend, +3 frontend | PASS WITH CONDITIONS → badge render test, route-level PATCH test, stale comments; a pre-existing test that used `sensitivity` as its "unknown" value was switched to `exotic` (265895d) |
| 0.5 | `investment_case_report`, `billing_frames`, `last_commercial_terms` in `RESULT_STATE_KEYS`; report helpers; QA driver extension | 12 + 3 driver steps | PASS WITH CONDITIONS → UTC-normalised billing frames and JSON commercial terms (a tz-aware frame is refused by the restricted unpickler and would drop EVERY side result on reload); `/run` and queue claims clear the three keys (ca6ca7a) |
| 0.6 | AST-based tripwires: no router / `solver_service` / `services.solver.*` imports from `services/{commercial,finance,library}`; docstring-only `__init__`s | 10 at gate (6 pass, 4 skip until P1 creates the packages); 11 after the relative-import condition | gate condition 1 → relative imports resolved against the file's package |

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
- Gate condition 3: when the first new `services/solver/` module lands (WP1.3 / WP1.5a), add an
  assertion that `test_solver_facade_surface.py`'s file glob includes it.
- Gate finding 5 (P4/P5): input defaults that are numbers rather than `None` — `GensetSpec` heat rate
  2.8, `DataCentreLoadSpec` UPS loss 0.03, `BessSpec` DoD 0.9, `DebtTranche` fees 0.0,
  `TaxEquityStructure.itc_recapture_years` 5, and `FinanceInputs.escalation` (a missing key must be
  defined as 0 or `not_established`) — must be recorded as sourced assumptions or become `None`.
  **Placed (IC P4 plan v1.0, WP4.0, 2026-09-30):** the finance items closed in P4 — `DebtTranche` fees, DSRA
  months and grace years default to `None` (0 must be typed), `itc_recapture_years` → `None` (P7's), and
  `escalation` per the P4 plan C4 (a class with cashflows and no rate is `not_established`). The archetype
  items (`GensetSpec` heat rate, `DataCentreLoadSpec` UPS loss, `BessSpec` DoD) are **deferred to P5**
  (the archetype builders), where they become sourced assumptions or `None`.
- Gate finding 7 (P4): `physical_quantities` imports `services.solver_service` (like
  `asset_economics`); finance consuming it inherits the solve stack transitively, which the direct
  tripwire does not catch. P4 decides whether to move `periodized_capital_costs` behind a leaf module.
  **Closed (IC P4 plan v1.0 C1, WP4.0, 2026-09-30):** the finance engine takes a plain `FinanceCase`; the one
  adapter that reads a solved network lives in `services/results/` and is injected into the runner by the
  router. The tripwire forbids `services.results` in `services/finance/**`, and a subprocess test imports
  every finance module and asserts `services.solver_service` is not loaded (transitive).
- FOM: `physical_quantities` exposes FOM separately; the delegated fix
  (`claude/fix-fom-reconciliation`) decides whether economics fold it into fixed cost.

## Gate verdict

- [x] **Assessor verdict (2026-09-27): GO WITH BINDING CONDITIONS.** Re-ran 11 test files (318 passed,
  4 skipped), `qa_save_load_roundtrip.py` (63/63), frontend vitest (70/70) and `tsc`; confirmed
  pywebview 6.2.1 in the venv. Binding conditions and closure:
  1. Tripwire ignored relative imports → **closed**: resolved against the file's package, including
     `from ... import routers`; probe test added.
  2. Seam tests could not tell `objective` from `generators` weightings (a swap survived) →
     **closed**: edge fixture now uses objective 3.0 / generators 2.0; new test; the same mutation
     now fails.
  3. Solver-facade file-list assertion → **due with the first `services/solver/` module** (P1).
  4. Findings-note counts → **corrected** (WP0.1 71 tests; WP0.6 10 at gate).
  Also taken: pack `rules` are a read-only mapping after load (finding 6).
- Phase 1 may start.
