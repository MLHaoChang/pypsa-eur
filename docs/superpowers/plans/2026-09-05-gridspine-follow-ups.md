# gridspine Follow-ups Plan — After Increment 3

> **Status 2026-09-05: written the evening increment 3 closed, after inspecting the v3 year. Task F1 is landing in the same session; F2–F4 are next and in this order; F5 onwards are follow-ups the owner sequences.** Every task obeys the increment-3 global constraints (pixi only, gate `pixi run gridspine-tests`, RED/GREEN evidence, a mutation per task, path-limited commits, the engine cage). Rulings are in `docs/superpowers/handoffs/2026-09-05-gridspine-increment-3-handoff.md`; this plan cites them by number.

**Goal:** Make the study rank on the number the screen actually measures, on a screen that solves the grid pandapower solved, and let the pipeline re-study a saved dispatch without repeating the two-hour unit commitment. Then the owner's modelling decisions and increment 4.

## What the v3 year found (measured, 2026-09-05)

1. **lightsim2grid 0.10.1 drops `gen.in_service` when the slack comes from `ext_grid`.** `init_from_pandapower` applies the flags, then its slack adder (`_aux_add_slack.py`) calls `init_generators` again over every pandapower gen plus the slack and never re-applies them. Every decommitted unit comes back as a live PV bus holding its voltage setpoint. Measured at v3 hour 1803: base case 0.14 pu off pandapower (max Vm 1.085 vs 1.197), branch N-1 rows up to 12 severity units off, `BUS_13-BUS_14` outage max Vm 1.198 vs pandapower's 1.209. `sgen` is not re-initialised and keeps its flags. **8754 of the v3 year's 8760 hours have at least one synchronous unit off**, so every branch N-1 row, every N-2 row, every prune threshold and the blind-spot rho in the v3 bundles were solved on the wrong grid. The unit rows (pandapower) and everything else in the bundle (`.raw`, `.dyr`, dispatch, loads, fault levels, SCR) are unaffected. Hour 0 has every unit on, which is why the increment-3 tests (all at hour 0) passed and match pandapower to 1e-10 there.
2. **The DC severity proxy ranks the wrong hours.** Over the 23 selected hours Spearman rho is −0.57 (−0.77 over the 11 without a diverging N-1 case). Over 20 hours spread uniformly through the year it is +0.79 — the proxy is fine on typical hours and fails on exactly the extremes the study exists to find: light-load high-IBR hours where the AC term is overvoltage (ruling 30), which DC cannot see. Caveat: the AC side of both numbers carries finding 1 for branch rows; the worst rows at the min-inertia hours were unit rows (pandapower), so the sign is real and the magnitude must be re-measured after F1.
3. **AC N-1 over the year is affordable; the increment-3 premise was not.** The locked decision called 8760 × 46 AC solves "not affordable at any point in this pipeline". Measured per hour on case39 (this sandbox, 4 cores): `apply_snapshot` 28 ms, `deepcopy` 5 ms, `pp.runpp` base 24 ms, `init_from_pandapower` 3 ms, `ContingencyAnalysisCPP` all 46 branch outages 4 ms, one `GridModel.ac_pf` 0.2–0.4 ms. The whole of `screen_n1` costs 0.53 s per hour — 77 min per year — and 0.4 s of it is the pandapower unit loop (14 × deepcopy + runpp). With unit outages on the same GridModel the screen is ~70 ms per hour, ~10 min per year, against a ~2 h unit commitment. The plan's "no test runs AC contingency analysis on more than 4 snapshots" was a runtime rule for a 0.5 s screen; it becomes a seconds budget (F2).
4. **The N-2 prune never prunes.** Threshold measured per selected hour 86.0–102.6 %, prunes nothing at any of the 23 hours (as at hour 0, ruling 17). To re-measure after F1; if it still prunes nothing across a year, the prune is a documented no-op kept because the spec requires it at scale.

---

## Task F1 — Solve the grid pandapower solved (the lightsim2grid in-service fix)

**Files:** `gridspine/static/contingency.py`, `tests/gridspine/test_contingency_decommitted.py` (new).

- [x] RED: a module fixture at hour 0 with `G_BUS_33`, `G_BUS_34`, `S_BUS_34` curtailed (`_hour_tables(..., curtailed=...)`; slack picks up ~1150 MW; base converges). Tests: `gridmodel_for` status vectors equal the pandapower flags (gens in table order, then the slack); base `ac_pf` matches `res_bus.vm_pu` to 1e-8; every converged branch N-1 row matches a pandapower single outage to 1e-6 (≥25 compared); a unit row still matches; outaging an already-off unit reproduces the base case; ≥8 N-2 pairs match pandapower double outages to 1e-6. Shipped code: base 0.19 pu off, branch rows 2e-3 off, N-2 off.
- [x] GREEN: `gridmodel_for(work)` — the one place a GridModel is built — re-applies `gen.in_service` (`deactivate_gen`) and `sgen.in_service` (`deactivate_sgen`) after `init_from_pandapower`, then refuses a model whose `get_gen_status()` / `get_sgens_status()` disagree with the net. Both call sites (`_screen_branches`, `_ac_pairs`) go through it. `N1_LEDGER` records the fact.
- [x] Mutation: remove the re-deactivation → exactly the four lightsim2grid-path tests red, the two pandapower-path tests stay green.
- [x] Gate (481 passed, 2 skipped), path-limited commit `9c08b9a6`.

**Not done here, on purpose:** no upstream patch, no version bump (1.0.0 is a major; ruling on the pin stands). Re-check the behaviour on any future lightsim2grid bump — the status-vector check in `gridmodel_for` is the regression guard.

## Task F2 — `max_n1_severity` ranks on the AC screen's own number

**Files:** `gridspine/static/contingency.py` (unit loop on the GridModel; a year pass), `gridspine/ranking/select.py` (criterion column), `gridspine/ranking/severity.py` (docstring/ledger: DC becomes the measured proxy, not the criterion), `gridspine/drivers/year_study.py`, tests `test_contingency.py`, `test_contingency_decommitted.py`, `test_select_ties.py`, `test_ranking.py`, `test_severity.py`, `test_year_study.py`; plan annotation in `2026-09-04-gridspine-increment-3.md` (the locked decision's premise is measured false).

Steps (landed 2026-09-05, same session as F1):
- [x] 1. `_screen_units` on the shared GridModel (`deactivate_gen`/`deactivate_sgen`, `ac_pf` from the base voltages, `get_lineor_res`/`get_trafohv_res`, `reactivate`). pandapower is the oracle: every unit at the light decommitted hour matches to 1e-6 (`test_severity_ac.py`), `G_BUS_39` is still a recorded collapse in both engines. `screen_n1` builds the GridModel once (`gridmodel_for`) for both halves.
- [x] 2. `n1_severity_ac(net, cset, dispatch, loads, registry, hours=None) -> Series` in `static/contingency.py`; `BaseCaseNotConverged(ContractError)` raised by `screen_n1`/`_n2_prepare`, caught by the year pass as NaN. Pinned equal to a direct `screen_n1` at three hours (all-on peak; 80 % load two units off; 60 % load three units off — the light hour is the severe one, the v3 pattern).
- [x] 3. Driver: `metrics["n1_severity_ac"]` over all hours on the same contingency set the selected hours are screened with; `_RANKING` reads it; manifest `n1_severity_ac_pass` = {seconds, hours, hours_not_converged, hours_compared, spearman_rho_dc_vs_ac, worst_rank_gap_dc_vs_ac}; `dc_severity_blind_spot` keeps the selected-hour numbers and adds the year-wide ones; ledger entry names the criterion and the measured agreement.
- [x] 4. Runtime measured: **136 ms/hour** on the v3 dispatch (100 spread hours, 13.6 s; ~20 min per 8760 h — the validation and `apply_snapshot` overhead on top of the ~15 ms of solves). Year-study module: fixture 327 s before → 334 s after; 23 tests in 349 s. The faster unit loop pays for most of the 336-hour pass. Increment-3 plan annotated (locked decision superseded).
- [x] 5. Mutation: criterion reads `n1_severity_dc` again → `test_max_n1_severity_ranks_on_the_ac_screens_own_number` red (result recorded in the commit).
- Measured on the way: DC-vs-AC Spearman rho over 100 spread hours of the v3 dispatch **−0.01** (AC from the fixed engine path). The DC proxy carries no ranking information on this fixture.

**Decision recorded, not re-opened:** DC stays in the artifacts because it is the only affordable estimate on a grid where AC N-1 over the year is NOT ~10 minutes; the ledger says which one ranked.

## Task F3 — Driver resumes from a saved dispatch

**Files:** `gridspine/drivers/year_study.py`, `tests/gridspine/test_year_study.py`.

- Split `run_year_study` into `dispatch_year(outdir, hours, window, overlap) -> (dispatch, loads, net, registry)` and `study_dispatch(outdir, dispatch, loads, k, screen, ...) -> StudyResult`; `run_year_study` composes them and is byte-identical in output (test: the existing module fixture's artifacts).
- CLI `--from-dispatch <dir>` reads `dispatch.csv`/`loads.csv`, validates them against the net (`validate_dispatch`/`validate_loads`, hour count), and runs ranking → handoff only. Manifest records `dispatch_source` (the path and the sha256 of both files) so a v4 bundle names the v3 dispatch it came from.
- Mutation: skip `apply_snapshot` in the resumed path → the RAW stage-order test goes red (same property as increment 2's).

## Task F4 — v4 artifacts: re-study the v3 dispatch

- After F1–F3: `--from-dispatch results/gridspine_year_v3 --k 5` into `results/gridspine_year_v4`. ~15 min (AC year pass + 20–25 selected hours of N-2 + fault levels). Hand over `selected.csv`, `metrics.csv`, `manifest.json`, bundles tarball.
- Report against v3: which hours changed and why (criterion or fix), corrected prune thresholds, the year-wide DC-vs-AC rho, and whether the min-inertia hours' overvoltage (ruling 30) survives the fix. Update the handoff doc: ruling 31 (the lightsim2grid bug), ruling 30's numbers, §2 status, §7 list. Mark the v3 tarball's screening files superseded.

## Task F5 — Inverter reactive control (owner decision first)

The five RES units are PQ `sgen` rows with `q_mvar = 0`. Ruling 30: with the synchronous fleet off, case39 sits at 1.2 pu before any outage and 1.36 pu after losing `G_BUS_30`. Options: (a) keep PQ and treat the overvoltage as a finding; (b) constant power factor from the templates' `cos_phi`; (c) Q(V) droop within the unit's Q range; (d) voltage-controlled (`gen` rows with `vm_pu`) — PowerFactory-like. The choice changes the min-inertia hours' AC severity, the fault-level `apply_fault_state` (RES always energised, ruling on `sgen` in `shortcircuit.py`), the SCR voltages and the `.raw` generator records. **A task only after the owner picks; implement as a template field with provenance, never a code default.**

## Task F6 — Decorrelate `wind_cf` across the three farms

`synthetic_profiles.res_cf_for` hands one series to `W_BUS_33/35/37`; fleet variability is understated and biases exactly `max_ibr_share` and `min_inertia_excl_equiv_mws`. Three seeded series with a stated correlation (ledgered) — between runs, never between a run and its validation; the v4 dispatch is NOT re-solved for this.

## Task F7 — One "net carries the hour" guard

`static/contingency._check_net_carries_hour` and the copy in `handoff/bundle.py` → one function in `static/loadflow.py` next to `apply_snapshot` (it is the inverse check of that function). Tests already cover both call sites; the mutation is to weaken the tolerance in one place and watch both go red.

## Task F8 — Merge housekeeping

`master` is two commits ahead (the old `GRIDSPINE_HANDOFF.md`; the pypsa-gui fix from PR #4). Merge `master` into `gridspine-inc2`, replace `GRIDSPINE_HANDOFF.md` with the increment-3 handoff (or a pointer to it), open the PR when the owner wants review. CI: `workflow_dispatch` on the branch runs the `Gridspine` job in ~12 min (run 33988832829 on `e7d12e19`: green).

## Task F9 — Increment-4 plan

Spec phase 3's remainder: the action layer (`create_study`, `run_pipeline`, `list_ranked_snapshots`, `export_handoff_bundle`, …), then GUI wiring, then chat tool registration — "a thin, late, path-limited backend change — deliberately the last increment". Plan it in the increment-3 plan's format from `docs/superpowers/specs/2026-08-27-gridspine-design.md` §phase 3 and the handoff's §4 API table. F3's `study_dispatch` is the seam the action layer calls.

## Owner questions (unchanged from the handoff §6, plus one)

- Should the 500 s interconnection equivalent (`G_BUS_39`) be a committable unit at all? (decommitted 56 % of the year; outaging it collapses the system)
- Severity relative to a base case that already violates three ratings: incremental, ledgered ratings, or a fixture property?
- SCR at the minimum or the maximum fault case?
- Radial generator losses: islanding, or generation contingencies?
- **New:** which of F5's four representations of the inverters is the client's?

## Order and dependencies

F1 → F2 → F3 → F4 (this order; F4 is the first artifact a PowerFactory validation should use). F5 waits on the owner. F6 after F4's validation. F7, F8 any time. F9 after F3.
