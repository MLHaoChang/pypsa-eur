# EH zonal MC — the four open items closed: grid storage, several grid areas, two-area COPT screening, common-mode import outages

**Date:** 2026-09-28  
**Branch:** `claude/eh-zonal-mc-import-outages` (continues from `d4a30d1`)  
**Plan:** [`2026-09-28-eh-zonal-mc-open-items.md`](../plans/2026-09-28-eh-zonal-mc-open-items.md)  
**Closes:** "Still open" in [`2026-09-27-eh-zonal-mc-import-outages.md`](2026-09-27-eh-zonal-mc-import-outages.md)

Each work package was built TDD (red → green), then reviewed by an independent reviewer agent against its diff. The review findings and what was done about them are recorded under each package. After WP5 an end-to-end QA and integration run closes the note.

## WP1 — grid-side storage dispatch

**What changed.**
- `mc_zonal.ZonalInputs` now holds `areas: (GridArea, …)`. `simulate_zonal_blocks` dispatches grid stores under a pinned, non-anticipative policy, per hour and per draw:
  1. Grid stores discharge against the grid's own deficit first.
  2. The remaining surplus is offered through the Link.
  3. The hub's stores dispatch against what is left.
  4. Grid stores then give remote support, bounded by the Link headroom.
  5. Grid stores charge only from surplus that was not offered to the hub.
- `_discharge_only` / `_charge_only` are `mc._dispatch`'s two passes with a per-draw remaining-rating bound `p_rem` (S, draws), so the two discharges in one hour share one rating. `mc._dispatch` indexes the rating per store, not per draw, so it could not be reused.
- `grid_area.storage` / `storage_dispatched` disclose the stores.

**TDD.** Red: `AttributeError: single_area`, `TypeError: … 'grid_storage_enabled'`. Green: `test_energy_hub_zonal_storage.py`. `tests/zonal_oracle.py` is a frozen copy of the 2026-09-27 kernel and serves as the bit-identity oracle: a grid without storage, or with `grid_storage_enabled=False`, returns exactly the oracle's arrays.

**Review findings (independent reviewer, commit `546f9c8`) and resolution.**

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R1.1 | Bit-identity, the five-step policy, units / delivery ratio, period re-initialisation, runtime guards (ratio 0, efficiency floor, `grid=None`) | no bug | — |
| R1.2 | `test_grid_storage_never_charges_…` passed vacuously. The Link had q > 0, so the battery charged in Link-outage hours (3 100 MWh logged); a mutant charging from all surplus still passed | test gap | Rewritten: a firm Link with headroom and a hub that is short. It asserts `charge_mwh == 0`, and that the full battery does support in the same scenario. A diagnostics `trace` was added to `simulate_zonal_blocks` (per-area own / support / charge MWh; it never changes the result). The mutant is now killed. |
| R1.3 | Step 1 (grid-first) was not pinned; the "bridges the grid's own shortfall" acceptance item was not met | test gap | `test_grid_storage_serves_the_grid_own_deficit_first`: `own_mwh > 0`, and hub EUE ≤ the no-storage EUE draw by draw. The skip-step-1 mutant is now killed. |
| R1.4 | The shared per-hour rating (`p_rem`) was not pinned | test gap | A unit test of `_discharge_only` twice in one hour, plus `test_a_store_at_its_rating_for_the_grid_gives_no_remote_support` (the battery spends its 60 MW on the grid, so support must be 0). The reset-rating mutant is now killed. |
| R1.5 | A grid area on a different horizon than the hub would be silently wrong | risk | `_AreaState` raises `ValueError` when the residual length or periods differ. Test added. |
| R1.6 | Offered-but-unused power is never stored (a conservative consequence of step 5) and should be stated | design note | Stated in the `grid_area.note`. |
| R1.7 | `storage_dispatched` was true for stores rated 0 MW in every period | nit | Now filtered on `p_nom_mw > 0` and a non-zero series. Test added. |

All three reviewer mutants were re-run against the new tests and each fails at least one test.

## WP2 — several grid areas

**What changed.**
- `hub_fleet_scope` records each live Link's grid component and its hub-side and grid-side series (`link_grid`, `grid_components`).
- `eh_stages._grid_areas` builds one `GridArea` per grid component that a live import Link reaches, in the order the Links are listed:
  - Each area has its own pruned snapshot, `import_idx`, firm-block series and delivery ratio.
  - Area `k` samples from substream `GRID_STREAM_KEY − k`.
  - An area with no sampled unit, or whose snapshot is refused, keeps `grid=None`. Its Links see an unbounded surplus (v1), and the reason is recorded.
  - Zonal applies when at least one area is sampled.
- `fleet_scope.grid_area` became `grid_areas: [...]` (`area`, `links`, `buses`, `sampled`, `reason`, `units`, `capacity_mw`, `demand_peak_mw`, `storage`, `storage_dispatched`, `note`). Values that cannot be resolved are `null`.
- The 2026-09-27 "grid side is N separate components — v1 applies" refusal is gone.

**TDD.** Red: `KeyError: 'grid_areas'`, `'sampled_unit' == 'zonal'`. Green: `test_energy_hub_zonal_areas.py`. Four earlier assertions were updated for the rename (`grid_area` → `grid_areas[0]`, and the no-grid note).

**Review findings (independent reviewer, commit `bae42b1`) and resolution.**

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R2.1 | Area construction, per-area `import_idx` / firm / ratio, the kernel's union exclude and firm add-back, CRN separation, and the `zonal` ⇔ `zonal_inputs` coupling | no bug | — |
| R2.2 | Several unbound areas were not bit-identical to v1. Imports were summed in float64 and cast once, so per-draw EUE was off by up to 0.008 MWh, which could flip LOLE near `SHORTFALL_TOL` | risk | Each area's import is now added to the hub's float32 sum in Link-position order. `test_two_unbound_areas_replay_v1_bit_for_bit` covers it (non-integer caps, 2000 draws), and the old float64-sum mutant fails it. The docstring now scopes the guarantee to at most one sampled Link per area; several Links into one area can differ from v1 by rounding only. |
| R2.3 | Frontend type still declared `grid_area` | risk | `EhGridArea` + `grid_areas?: EhGridArea[]` in `api/simulation.ts`; `tsc` is clean. |
| R2.4 | Stale module docstring (said grid storage is not dispatched and one area only) | nit | Rewritten. |
| R2.5 | `demand_peak_mw` was `0.0` for an empty residual | nit | Now `null` (ADR-0001). |
| R2.6 | Surviving mutants: stream index ignored; a `grid=None` area gives no import; firm series in the wrong area; delivery ratio pooled across areas; multi-Link areas and interleaved order untested; refused-snapshot branch never run | test gaps | Six tests added: identical fleets on the two areas draw different paths; an unsampled area replays v1 bit for bit; a firm Link lands in its own area; per-area ratios with efficiencies 0.9 / 0.5; interleaved `a, b, a2` groups as `[a, a2], [b]`; a refused snapshot is marked unsampled with its reason and `null` values. All five in-place mutants were re-run and each now fails at least one test. The stream test needed truly identical fleets (same MTTR) to bite. |

## WP3 — two-area COPT screening

**What changed.**
- `mc_zonal.expected_surplus_fraction` computes `f_h = E[min(cap_h, S_h)]/cap_h` from the grid area's own COPT. It uses `E[min(c', (C−r)⁺)] = c' + ES(r) − ES(r+c')`, which is exact on the COPT grid.
- `eh_stages._screening_fleet` gives each area's sampled Link unit the profile `f_h` for the class-A screening only (UP = `f_h × cap`). Firm-block Links are derated by adding `(1 − f_h) × firm` back to the residual.
- The MC keeps unprofiled units; profiling them would count the grid twice.
- Disclosed as `fleet_scope.copt_import_model` (`expected_surplus_profile` / `two_state` / `firm_block`), `copt_import_note`, and `grid_areas[k].copt_surplus_fraction_min`.

**TDD.** Red: 8 of 9 tests failed (`AttributeError: expected_surplus_fraction`, missing `copt_import_model`). Green: `test_energy_hub_zonal_copt.py`, including hand-computed PMFs for one unit at q = 0.2 (0.64 / 0.8 / 0) and for delivery ratio 0.5.

**Review findings (independent reviewer, `e75d379..2d88570`) and resolution.**

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R3.1 | The identity (negative r, beyond-table, ratio 0, cap 0; brute-forced on 200 random fleets to 1e-9), the grid surface matching `_screen_block`, mixed firm and sampled Links in one area, and `_rank_import_links_once` with a profiled Link | no bug | — |
| R3.2 | **Holding the grid at its expected value can misstate LOLE in either direction, and the payload did not say so.** Measured: 13.1 h against an exact 21.6 h on a case where the hub is barely short; 802 h against 576 h on the fixture | risk | Two parts. (a) `copt_import_note` now says the screening "may over- or under-state LOLE against the MC", that it ranks and does not certify, and points to the exact figure. (b) **A new exact analytic metric, `copt_metrics.import_exact`** (`mc_zonal.exact_import_metrics`). It mixes the import's per-hour distribution in exactly (Link states × the area's grid COPT × common mode, areas convolved), with the import rounded down to at most 128 levels (1 MW on the fixtures). Without storage it equals the MC expectation. It is pinned inside a 4000-draw MC's CI on three fixtures (grid short, grid unreliable, firm Link) and on a two-Link area. With an unbound grid it equals the v1 COPT to 1e-9, reached by an independent route. |
| R3.3 | An ample grid with q > 0 gave `f = 1 − 5e-15`, which profiled the Link for nothing and cost it a `K_EXACT` slot | risk | `f` is snapped to 1 when it is within `SURPLUS_SNAP_TOL = 1e-9`. Test: ten 100 MW grid units at q = 0.02 leave the screening Link unprofiled. |
| R3.4 | The `K_EXACT` change was under-disclosed: `screening_analysis`'s `fidelity_note` was dropped | risk | Kept as `snap.copt_fidelity_note` and `fmea_top.copt_fidelity_note`. |
| R3.5 | Note wording: `f` is the area's share, not a single Link's | nit | Reworded ("its AREA's total Link cap"). |
| R3.6 | `copt_import_model` said `expected_surplus_profile` even when every fraction failed | nit | Now based on `copt_fractions` being non-empty, otherwise `two_state`. Tests cover both the failure path (`copt_note` present) and the normal path (`copt_note` absent). |
| R3.7 | Pre-existing crash, now more visible: a Link with an hourly `p_max_pu` gets an hourly `capacity_series`, and the per-period COPT failed with "not constant over the block" | bug (pre-existing) | `_screening_fleet` folds a non-block-constant Link series into the profile (UP = `cap_max × shape`, exact for a mixed unit). Tests pass in `auto` and `sampled_unit` modes. |
| R3.8 | Surviving mutants: `f` applied from the wrong area; ratio wired as ones; snap removed; `r` clamped at 0; `periods` ignored | test gaps | Tests added: two areas with different fractions; end-to-end efficiency 0.5 (`f ≤ 0.4`); the snap test; negative grid residual (0.92); ratio 0 → 0; a per-period grid surface (`[10, 10, 50, 50]`). All five mutants were re-run and each now fails at least one test. |

## WP4 — common-mode import outages (opt-in Link data)

**What changed.**
- Link attributes `common_mode_rate`, `common_mode_mttr_hours` and `common_mode_basis` (default `FOR`) on an identified import Link model one event that takes the Link and the grid area behind it down together. Nothing is defaulted.
- The MC samples one two-state chain per event from its own substream (`CM_STREAM_KEY − j`, 2²⁰ below the grid keys). While a chain is down, its area's offered import and grid surplus are both 0: no import, no remote support, no charging.
- Any event runs the two-area engine, even on a hub with no sampled grid area (that area is then unbounded).
- `exact_import_metrics` adds each area's event as a point mass at 0.
- Disclosed as `fleet_scope.import_common_mode` (link, rate, MTTR, basis, area, applied, reason), `import_common_mode_sampled` and `copt_common_mode`.

**TDD.** Red: all 10 of `test_energy_hub_common_mode.py` failed (`KeyError: 'import_common_mode'`, …). Green after the implementation. A follow-up, found before review, added a test that a firm Link without its own event data, in an area that has an event, also loses its import.

**Review findings (independent reviewer, commit `17ee034`) and resolution.**

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R4.1 | Bit-identity and stream separation, the exact float cast of the 1/0 chain, several chains per area (product), the point mass in `exact_import_metrics`, the q_eff formula, the firm add-back (no double derating), and parsing (blank / NaN / `[0, 1)` / missing MTTR / islanded) | no bug | — |
| R4.2 | **B1:** with `import_model="sampled_unit"` or `"firm_block"` the payload said `applied: True` although neither engine modelled the event | bug | After the freeze, an applied entry with no area is flipped to `applied: False` with the reason "import_model=…: modelled only on the default 'auto' path". Tests cover both modes. |
| R4.3 | **B2:** an implied MTTF under 1 h (rate 0.9, MTTR 1 h) passed the freeze and only failed at certify time | bug | `_common_mode_entry` calls `transition_probs` and refuses the pair at snapshot time, naming the Link. Test added. |
| R4.4 | **R1:** the screening folded `q_cm` into each Link unit independently, so P(all of an area's Links down) came out as q_cm² instead of q_cm (985 h vs 1190 h exact) | risk | Replaced with an exact mixture (`_screen`). The screening runs once per combination of event states (at most 2⁶; beyond that, events are held up and the note says so). LOLE / EUE / by-period and every row's ΔEUE and € are probability-weighted. `lolp_max` is the maximum over states, flagged `lolp_max_is_upper_bound`. Each event gets its own class-A row (`common_mode:<links>`, `component_class: CommonMode`, ΔEUE = EUE − E[EUE \| event up]). The firm-to-unit conversion is gone, and Link units keep their own q. Pinned: with an unbound grid the screening LOLE equals `import_exact` to 1e-9 (two firm Links, one event); the old per-unit fold fails three tests. |
| R4.5 | **R2:** the event-only path labelled a firm block `planning_limit_only` / `firm_block`, and `copt_import_note` was empty | risk | New `import_firmness` value `common_mode_sampled` for that case; `copt_common_mode: "event_mixture"`; the note describes the mixture. |
| R4.6 | **R3:** common-mode data on a non-import Link, or under the whole-network scope, was dropped without a word | risk | Reported as not-applied entries with the reason. The per-unit relabel concern is moot now that the mixture adds its own event row. |
| R4.7 | **R4:** grid storage keeps serving the area's own deficit during an event | risk (conservative) | Disclosed in the area note: the event cuts the export only. |
| R4.8 | Nits: a q = 0 event still recorded a chain (switching a v1 hub to the two-area engine); the screening units carried the first chain's MTTR | nit | A q = 0 event records no chain (test added). The screening units were removed along with R4.4. |
| R4.9 | Surviving mutants: surplus not zeroed during the event; all chains on one stream; the exact metric using only the first chain; no B1 / B2 / R1 fixes | test gaps | New tests: the grid charges strictly less during an event; two chains in one area give P(up) ≈ 0.49 over 20 000 draws; the exact metric matches the MC for two chains in one area and for events in two areas; plus the B1 / B2 / R1 pins above. All seven mutants were re-run and each now fails at least one test. |

## WP5 — report, panel, chat copy

**What changed.**
- `EhReferenceDesignPanel.tsx`:
  - The certification block gains a grid-areas line (count, sampled, dispatched grid storage, and the reason for each unsampled area) and one line per common-mode event (applied, or why not).
  - The FMEA block gains the COPT import line (the screening model, whether events are mixed, and the exact import LOLE with its rounding) and the COPT caveat and fidelity notes.
  - `importModelLabel` distinguishes a firm block under a sampled event, and a zonal hub whose Links are firm.
- `api/simulation.ts`: `EhGridArea`, `EhCommonModeEvent`, and the new `EhFleetScope` fields.
- The chat tool description and the `CHATBOT.md` row describe grid areas, grid storage, common mode and `import_exact` (zonal / event path only, no storage).

**TDD.** Red: 4 new panel tests failed on missing exports. Green. `EhReferenceDesignPanel.test.tsx` went from 35 to 39 tests, then to 45 after the review.

**Review findings (independent reviewer, `17ee034..6554d4d`) and resolution.**

| # | Finding | Severity | Resolution |
|---|---|---|---|
| R5.1 | `EhImportFirmness` lacked `common_mode_sampled`; `import_common_mode_sampled`, `copt_common_mode`, `import_units` and `EhGridArea.copt_note` were emitted but untyped | bug (type) / risk | Added. `tsc` is clean. |
| R5.2 | `importModelLabel` said "firm … (no Link outage data)" for a firm block under a sampled event, and "Link outages … sampled" for a zonal hub whose Links are firm | wording | It now branches on `import_firmness`: "firm block; common-mode event sampled" and "grid-side surplus sampled; Links firm". Tests added. |
| R5.3 | `coptImportSummary` ignored the event mixture and never rendered the ±40 % caveat or the fidelity note; "exact" hid the Δ-MW rounding | wording | It appends "common-mode events mixed exactly" and "import rounded down to Δ MW levels" when Δ > 1. A new `fmeaCoptNotes` renders `copt_import_note` and `copt_fidelity_note`. Tests added. |
| R5.4 | A q = 0 event was shown as an event | nit | Now "q=0 — no effect". |
| R5.5 | `copt_import_model` was still set when the COPT was skipped (no sampled hub unit) | nit | The backend nulls `copt_import_model` / `copt_import_note` on that path. |
| R5.6 | `CHATBOT.md` stated `import_exact` without its scope | nit | Now "zonal / common-mode path only, no storage". |
| R5.7 | QA: `_fmea` never ran the JSON-clean check; the event-only path, `import_model == "zonal"` under an event, and a non-null `copt_surplus_fraction_min` were all unchecked | test gaps | All added to QA section 4, with a q = 0 control for the event-only path. |
| R5.8 | Frontend test gaps: the new labels, a `firm_block` COPT line, events mixed, a pre-change report, a rendered `CommonMode` row, ignored entries, idle storage | test gaps | Six tests added. |

Clean per the reviewer: no 0 / NaN / "undefined" render path; nothing in the QA driver passes vacuously (every ordering step requires numeric values); with-vs-without pairs share the same draws, so the orderings are meaningful.

## End-to-end QA — `tests/qa_eh_reference_design.py` (108 / 108 steps over HTTP)

Section 4 is new. Every run is weak_flexible (`apply_pack → ens_solve → mc_certify → assemble` over HTTP), pack MC 200 draws, seed 0.

| Scenario | MC LOLE (h) | Control | Reading |
|---|---|---|---|
| Grid short of its own load when its unit is out, **with** a 200 MW / 8 h grid battery | **516.84** | **1307.61** without the battery | The battery bridges the grid's own 150 MW deficit and still has 50 MW for the hub. 516.84 h is exactly the 2026-09-27 v1 figure, i.e. a grid that can always back the Link: an independent consistency check. |
| Hub on two separate grids | **1004.59** | — | `import_model = zonal`, two areas (`poc_a`, `poc_b`), both sampled, each with a COPT surplus fraction |
| Common-mode event on the PoC (q = 0.05, MTTR 24 h), sampled grid | **785.03** | **585.28** without the event | Applied, area 0, engine `mc_zonal`. The event is ranked as its own class-A mode. |
| The same, class-A screening vs exact | screening **965.98** | exact **751.36** | The expected-surplus screening overstates here, as disclosed. `import_exact` is the no-storage analytic figure and sits within the MC's range. |
| Event only: firm Link, unsampled grid, event q = 0.05 | **626.34** | **415.005** with q = 0 | `import_firmness = common_mode_sampled`. The q = 0 control reproduces the 2026-09-27 firm-block LOLE exactly. |

Sections 1–3 (the 2026-09-27 journeys) are unchanged: weak_flexible 585.2775 h (zonal), pair 499.32 h vs 415.005 h, off_grid 1400.48 h = 1400.48 h.

## Integration — before / after

Environment: the SessionStart venv (`~/.venv-pypsa-gui`, Python 3.12, pandas 2.3.3, pixi-lock pins).

| Suite | Before (`d4a30d1`, end of 2026-09-27) | After (`24efd72`) |
|---|---|---|
| `test_energy_hub_*.py` + `test_adequacy_sweep.py` | 243 | **317** (+74: `zonal_storage` 13, `zonal_areas` 13, `zonal_copt` 25, `common_mode` 23), 0 failed |
| full backend `pytest -m "not slow"` | 5792 passed, 31 skipped | **5866 passed, 31 skipped, 0 failed** |
| `run_qa_drivers.py` | 22 drivers | **22 drivers passed** |
| `qa_eh_reference_design.py` | 64 / 64 steps | **108 / 108** steps |
| frontend `npx vitest run` | 1952 | **1962** (177 files; `EhReferenceDesignPanel.test.tsx` 35 → 45); `tsc --noEmit` clean |
| `ruff check` on changed backend files | — | clean. `mc.py` is untouched in this round and still carries its 19 pre-existing docstring-style hits, the same as master. |

The single-area engine (`mc.py`) was not modified in this round. The MC, ELCC and coupling-loop suites run unchanged inside the full backend count.

## What is still deliberately not done

- **The class-A screening's grid is held at its expected value.** It ranks modes but does not certify; the ±40 % caveat is on the payload. `import_exact` (exact, no storage) and the MC (certifying) sit beside it. Exact per-mode attribution under the grid distribution would need a multi-state unit in `copt.py`'s attribution engine; that is a larger change, left for a later round.
- **The grid-storage policy is pinned, not optimal:** grid-first, remote support within the Link headroom, and charging only from surplus not offered to the hub. That is non-anticipative and conservative, and disclosed.
- **Common-mode events are opt-in data only.** Nothing is defaulted, and there is no library of event rates.
