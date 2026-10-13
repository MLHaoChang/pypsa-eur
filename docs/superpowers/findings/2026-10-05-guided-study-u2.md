# U2: the guided investment study runs on the Investment Case engine only

**Status:** WP0–WP10 done on `claude/edge-tool-ux-research-n0n2l6`. WP10 is committed as `0c31b1b6`; master `fceb886e` (#101 the 2026-10-07 defaults pack, #135, #100 and others) is merged into the branch as `61e09fae`. Every work package passed its assessor gate (GO with binding conditions, each closed in the next package).
**Final gate:** `docs/superpowers/notes/2026-10-12-u2-final-gate.md`, GO with binding conditions U1–U3. U1 (the pre-U2 fixture's two netCDFs were gitignored and never committed, so 8 of the 10 W4 tests failed on a clean checkout) is fixed in `80730434` (`git add -f`). U2 (the W4 refusal must be true on every network it refuses) and U3 (these records) are fixed in the change after it (see W4 below). Post-merge evidence at the gate, on a clean `git archive` of `61e09fae`: the study and shared-file set 1155 passed, 9 failed (8 = U1; 1 = master's own `test_packaging_requirements` failure, `tools/pilot_evaluation_plugin.py` importing `pytest` unguarded, which fails identically on `fceb886e`), 1 skipped; `qa_decision_study.py` 67/67 (`marginal` / `bess_1h` / `["demand_charge_price"]`), `qa_investment_case.py` 259/259, `qa_value_flows.py` 235/235; vitest (decision pages, utils) 50 files, 569 passed; `tsc -b` clean. Left before the PR: the full backend suite on a clean checkout, `pixi run gui-qa-drivers`, an owner for master's red packaging test, and the owner items below.
**Date:** 2026-10-12
**Plan:** `docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md` (the per-package status blocks are the detailed record; this note is the summary)
**Parent plan:** `docs/superpowers/plans/2026-10-05-one-investment-engine-two-faces.md` (§4 C1–C6, §5 U2, §6 the facade)
**Gate notes:** `docs/superpowers/notes/2026-10-0{6,7,8}-u2-*-gate.md`, `2026-10-12-u2-wp9-gate.md`, `2026-10-12-u2-final-gate.md` (WP10, the merge and U2 as a whole)
**Before U2:** `docs/superpowers/findings/2026-09-28-guided-investment-study-mvp1.md`

## What U2 set out to do

MVP-1 gave the guided study its own engine: a bill calculator, the tariff written into the grid Links as permanent prices, its own demand-charge LP wrapper (`SolverConfig.demand_charge`), and a pro forma that built the cash flow and KPIs. The Investment Case (IC) effort built the product's engine for the same job. U2 rewires the study onto IC through ONE seam, so that no figure the study shows is computed by a second engine:

- every LP price and the demand charge are IC's commercial bindings (`lp_bindings`), compiled from the guided form and the ledger by `services/study/compile.py`;
- every bill is IC's tariff engine (`billing.bill_site` / `rate_meter`), the export credit from the value-flow line;
- every cash flow and KPI is IC's finance engine (`finance_case.build_finance_case`, `engine.run_case`);
- `services/study/engine_adapter.py` maps those onto the guided view shapes (`Bill`, `InvestmentCase`), which the findings, the report and the frontend keep reading.

## What changed, per package

| Package | Commits | What it did |
|---|---|---|
| WP0 | `361682db`, `cff661bf` | Froze 245 pre-U2 figures (`tests/fixtures/u2_pre_numbers.json`: the QA driver, the S5 golden table, both seeds' bills, a 3-month demand-charge toy); merged master with IC |
| WP1 | `56a38e01` | The facade spike as tests: every open engine question answered by a test (20) |
| WP2 | `28544ac5` | The five GS engine test files re-expressed on the seam (82 source tests, `u2_port_map.json`), red until their package |
| WP3–WP5 | `4414fde8`, `a9cf3f39`, `ac8ec427` | The ledger from IC's defaults pack (rows 21–34); `compile.commercial_from_ledger` (the export series minted per study, C3); `engine_adapter.bill` / `bill_meter` |
| Stage-1 gate | `dee96dec` … `6e4a3021` | C1–C8 |
| WP6 | `325fa2ab`, `ae7fb77f`, `c1e032f7`, `243cb6e7` | The LP switch: the runner solves every option on IC's commercial chain; forks bound to the study's export series |
| WP7 | `7b3452ed`, `48d023c9` | The case on the finance engine: C1 (the battery's two upfront parts), C2 (remaining-life terminal value), C4 (real basis), C5 (bounds from the one `FinanceCase`) |
| WP8 | `b4c2da53`, `90f4fabf` | Findings, tornado, runner and routes on the engine; `compiled_hash` and the stale path; pack seeding; §5.4 vocabulary rename with read-compat |
| WP9 | `ce717d09`, `14d0281f` | The workbooks: IC's own workbook plus the guided sheets (the live-formula view over the engine's cash series) |
| **WP10** | `0c31b1b6` | Deleted GS's engine; W4; the QA driver's judging rule; golden, packaging; the WP9 gate's V1–V3; this note |
| Merge | `61e09fae` | master `fceb886e` (#101, #135, #100 and others); conflicts in `routers/projects.py` and `AppHeader.tsx`, both sides kept |
| Final gate | `80730434`, then the U2/U3 change | U1: the pre-U2 fixture committed; U2: the engine mark and a true W4 message; U3: records |

## WP10: what was deleted, and what replaced it

| Deleted | Replaced by |
|---|---|
| `services/study/tariff.py` (`BillCalculator`, `write_tariff_prices`, `demand_charge_config`, `band_prices`, the F1-B4 record, `tariff_from_ledger`, `validate_tariff_intake`) | `engine_adapter.bill`; IC's `materialise_poc_prices`; `compile.apply_ledger`, `compile.validate_form` (same refusal codes, `export_cap_unsupported` kept at intake) |
| `services/study/proforma.py`, `proforma_xlsx.py` | `engine_adapter.option_case` / `bound_case` / `case_workbook`; a stored record without the engine's series gets `workbook_view.stored_cash_flows` (values as stored, no formula) |
| `objective._wrap_with_demand_charge`, `DemandChargeRefused`; `SolverConfig.demand_charge` and its schema field; the F1-B4 record in `run_simulation`; the `demand_charge_eur` bridge term | IC's `add_demand_terms`; the "Commercial" cost-breakdown component (`commercial_cost_terms`) |
| `validation_service._check_export_cycling`, `_site_storage`, `_cross_hour_cycling`, `EXPORT_CYCLING_CODES`, `export_cycling_flags` (F1-B6) | IC's `preflight.network_findings` (same codes, U1 f) |
| `study_library/technology_costs.csv`, `tariffs.csv`, `finance_defaults.yaml`, and `library.load_library` | the pinned defaults pack (`library.load_defaults`); rows 1–20 frozen in `tests/fixtures/legacy_study_library.json` (gate C8 a). `study_library/load_profiles/` stays (Q7) |
| The findings' and the bounds' pro forma fallbacks; the report's `lcos_both` rule; HELP `lcos_excludes_charging_energy_cost`, `lcos_two_definitions` (and their frontend mirror) | the engine only: a network the engine did not solve is refused (`variant_not_engine_solved`); a stored report keeps its own facts and sentences |
| The five old test files | their 82 tests are ported (`u2_port_map.json`, 80 targets, 2 dropped with reasons) |

Kept for stored studies (read-compat): the `Engine` literals `bill_calculator` and `cash_flow_expander` (read-only labels), `library.LIBRARY_VERSION` (the legacy ledger's version), `CaseKpis` legacy names, the F1-B4 meta key on old networks (ignored on read).

## W4: a pre-U2 network is refused, never re-solved cheaper

A study's base and forks written before U2 carry the tariff in two pieces: the energy and export prices as permanent `links_t.marginal_cost` on `grid_import` / `grid_export`, and the demand charge in `SolverConfig.demand_charge`. Once the field is gone, the one loader of a saved config (`routers.projects._solver_config_from_dict`) drops it, and any save rewrites the file without it. Re-solving would keep the energy prices and lose the demand charge.

Measured on the stored pre-U2 fixture (`tests/fixtures/pre_u2_fork/`, written by the old code before it was deleted), re-solving the base:

| Config | Objective (EUR, 336 h) | |
|---|---|---|
| as stored (the wrapper reads the charge) | 39,848 | the study's tariff |
| re-saved (the field dropped) | 27,248 | **12,600 EUR cheaper, silently** |
| a commercial block added in Expert | 79,696 | the tariff charged twice |

**Design.** Refuse, typed, in preflight. `services/study/pre_u2.py` recognises a decision-study pack network (`n.meta["decision_study_pack"]`) **without the engine mark** whose meter Links carry a non-zero price, static or per snapshot. `validation_service.validate_for_run` reports it as the error `study_network_tariff_in_links`, so both the pre-run check and every solve (`run_simulation` runs the same preflight) refuse it. The network is the witness, not the config, because the config forgets. A network without the pack marker is the user's own tariff.

**The engine mark (final gate, U2).** At WP10 the detector keyed on "pack marker + priced meter Link", and a post-U2 study network has the same pack marker: a Save-As of a post-U2 fork, priced by hand on `grid_import`, was refused as "built by an earlier version… Re-run the study", false on both counts. And re-running never clears a pre-U2 study's **base**: since U2 the runner rebuilds only the forks, and nothing rewrites the base's `network.nc`. Fixed:
- `packs.build_site_network` (the one writer of the study's base and forks) writes `n.meta["decision_study_pack"]["tariff_on"] = "engine"`. It lives inside the pack record, so it travels wherever the pack marker does; tested through netCDF and, through the routes, on a study's real base and fork, a Save-As of the fork, a scenario branched from it and a bundle round trip.
- Only pack networks **without** the mark are refused. A price a user types on a marked network's meter Link is an ordinary Expert edit (tested static and hourly, with and without the engine tariff, and through the Properties route). With an engine tariff, IC's commercial layer adds its prices **on top of** the Link's own cost (`lp_bindings`: "base cost + adders"; a typed 100 EUR/MWh makes the materialised import price 230 instead of 130). That is IC's stated contract, the typed price being a separate charge; no IC preflight or GS check flags it.
- The message says what is wrong in plain words and offers two ways out that work for any copy: start a new study for the same site from a question card; or clear the old prices (for the hourly prices a pre-U2 study wrote: Time series, Links, Marginal cost, upload a profile of zeros for each Link, since the static price in Properties does not replace an hourly one, and a netCDF-borne series is not an "uploaded profile" the Time series page can delete; for a static price: Properties). With them cleared, no tariff applies until one is added in the commercial settings, and the message says so. "Re-run the study" is no longer offered.
- The refusal lifts once both prices are cleared (unpriced Links are not the old tariff): tested in process (zeros, dropped, NaN; one Link cleared still refuses and names the other) and walked through the real routes on both fixtures (upload zeros; or upload then delete): preflight clean, and still clean after save and reload. The cleared fork then solves optimal; the cleared base has no cost at all and the solver says so ("Objective function could not be created").

**Limits.** Renaming **both** meter Links bypasses the detector (a deliberate Expert edit of both names; accepted). The fixture `tests/fixtures/pre_u2_fork/` cannot be rebuilt (its recorder needs the deleted code); its netCDFs are committed with `git add -f` (`*.nc` is gitignored) and their sha256 are in `make_pre_u2_fork.py`; the W4 tests fail with that explanation when they are missing.

Migration was rejected: the Links hold an 8,760-h price series, not the bands and charges it came from, so an engine tariff rebuilt from it would not be exact.

## Deltas against WP0

Every figure the engine moves by design is a row of `tests/fixtures/u2_deltas.json` with a cause. 79 rows; the causes:

| Cause | Rows | Size | What |
|---|---|---|---|
| `lp_dispatch_on_ic_chain`, `lp_on_ic_commercial_chain_same_formulation`, `year_billed_demand_basis_verified_same_formulation` | 35 | ≤ 2.9e-16 rel | the same LP on IC's chain (WP5/WP6) |
| `lcos_is_the_engines_storage_metric` | 5 | +58 % | owner decision 6: the engine's LCOS includes charging (244.39 → 385.45 EUR/MWh on golden `bess_2h`) |
| `c1_parts_close_the_s4_gap` | 3 | −17 % | the two upfront parts close the S4 back-calculation gap |
| `fom_rounded_by_pypsa_statistics` | 5 | ≤ 6.9e-9 rel | IC's FOM line is `n.statistics.fom`, rounded to 5 decimals (engine ask) |
| `lp_degenerate_dispatch_ties` (WP10) | 6 | ≤ 1.8e-5 rel | the TOU seed billed on the engine's golden dispatch: under the flat DE energy price the solve ties differently from GS's (import: 34 hours on `bess_2h`, 22 on `bess_pv_2h`; on `bess_pv_2h` the export Link also shifts in 38 hours and the battery in 60; same import/export MWh and monthly peaks) |
| `c4_ledger_seeded_from_the_pinned_pack` | 3 | hash | the ledger seeds from the pack (rows 21–34 are rows) |
| `vocabulary_rename_5_4` | 3 | 0 | §5.4 key names |
| IC semantics (`fixed_prorated_by_covered_hours`, `annual_peak_prorated_by_represented_hours`, `capacity_rated_on_poc_size`, …) | 9 | — | WP5's recorded semantic differences on partial periods |
| other bookkeeping (`dispatch_not_solved_by_engine`, `one_engine_rename`, `owner_decision_7_taxes_levies`, …) | 10 | — | |

**WP10's re-derivation.** With GS's engine deleted, `tests/u2_record_pre_numbers.py` re-derives the WP0 golden table, seed bills and toy on the engine (the freeze itself is final; the recorder refuses to re-record). Everything is within the fixture's tolerances except the recorded rows: the golden table's FOM rounding, LCOS (under `levelised_cost`), ledger hash and `terminal_value_eur` name, and the TOU seed's tie deltas.

## Port map

82 source tests (`test_tariff_bill.py` 33, `test_tariff_demand_charge.py` 12, `test_demand_charge_absent_is_noop.py` 1, `test_proforma_golden.py` 28, `test_proforma_xlsx.py` 8) → 80 targets in `test_engine_adapter_{bill,demand,case_golden,workbook}.py`, `test_study_compile_commercial.py`, `test_study_preflight_ported.py`, `test_no_commercial_is_noop.py`, `test_xlsx_formula_eval.py`; 2 dropped with reasons (GS's `write_tariff_prices` Link-pricing tests, replaced by "no prices on the Links"). Since WP10: the sources are deleted, the map is the record, and no test may carry `pending()` (C8 c).

Tests whose oracle WAS GS's live code now read a frozen record of it, taken before the deletion: the S5 golden figures (WP0), the C5 bound NPVs (`tests/fixtures/u2_gs_bound_npvs.json`, from a clean copy of 14d0281f), the year-billed and §4.7 toys (constants in the tests, re-run on that copy), the legacy ledger (`legacy_study_library.json`). Where a closed form exists the test uses it instead (BC-7: a grid-only bill at a moved demand price moves by exactly (Δprice) × Σ monthly peaks).

## Mutations, WP6–WP10

Each package's table is in the plan's status block; all ran on scratch copies, each file restored and compared byte for byte.

| Package | Mutations | Red as intended |
|---|---|---|
| WP6 | `settlement="15min"`; demand item dropped | both |
| WP7 | M1–M8 (salvage rule, a typed `overnight_cost`, escalation, the bounds' basis, FOM, replacements, the gate, way a) | all 8 |
| WP8a | M1–M11 (compiled hash, finance, bills, labels, rows 21–34, stale rules, the preview) | all 11 |
| WP8b | M1–M7, Y1a/b, Y2a–d, G4, G11 | all |
| WP9 | M1–M10, O4a–d, O9, Z2r, Z3r, O5b, N10r | all |
| WP10 | MW1–MW14 (19 runs: the BillCalculator re-import, W4 removed or reading static prices only, N7, C8 c, two seam breaches, the gate's G4a–c and G7, V2 a/b, V3, grants, a non-engine report case, "library range") | all 19 |

## Engine asks still open (to IC, through the owner)

- **Q15** (frozen 2026-10-13, ask 3): `binding.bind_commercial` (binding a fork's in-memory network) and `series_store.resolve` in the facade.
- **delete_series / pinned_by** (landed 2026-10-13, ask 1): delete a study's minted export series (all versions) and ask whether a config pins it; GS reads `bundle_pins` and `series_store.list_series` meanwhile.
- **WP7 freeze** (frozen 2026-10-13, ask 3): `finance.engine.wacc_gate`, `lp_bindings.META_LINKS`, the `FinanceResult` fields GS reads (`op`, `op_incremental`, `terminal`, `gate`, `flags`, `reasons`, `sections`, `incentives`).
- **N15** (frozen 2026-10-13, ask 3): the asset schema's `derive.apply_parts` / `has_parts` (S0's).
- `scale_capex(case, f, *, asset=, part=)`, so the per-part CAPEX bound needs no WORKAROUND (X6).
- Unrounded `n.statistics` FOM, CAPEX and OPEX.
- A public test helper that records a written dispatch as an engine solve (landed 2026-10-13, ask 5: `lp_bindings.record_dispatch_as_solve`; GS's fakes no longer read `lp_bindings` internals).
- `put_flat_export_series(label=…)`; an inactive `poc_link` refused typed at bind.
- One public structure for the engine's cash identity terms (WP9's wish).

`tests/test_study_one_engine.py::ALLOWED_EXCEPTIONS` lists every non-facade engine name the seams use, each with its ask; a retired WORKAROUND must leave the list.

## Open items

**Owner decisions (2026-10-13, relayed to #154 by the coordinating session):**
- **C3e: retired (2026-10-13, IC engine ask 1):** the study deletes through `series_store.delete_series` and no longer reads the Library rows or pin sidecars. Was: **accepted for now, and tracked.** The study keeps reading the engine internals the export-series delete needs (`bundle_pins`, `series_store.list_series`, `LibraryItem`) until the engine adds `delete_series` / `pinned_by`, then switches to them.
- **Engine asks approved and commissioned from the engine:** per-part `scale_capex`, unrounded FOM statistics, a frozen engine interface, and a public "record a solve" helper. Each is adopted in a follow-up PR once it lands; #154 does not wait on them.
- **A hand-typed price on a grid Link:** the engine warns and shows both prices, with no change to the numbers. This is commissioned from the engine side; the study changes nothing.
- **The report workbook (WP9 decision W2):** accepted. The engine's sheets stay in each option's own case workbook.
- **X6: closed** by #155, where the parent plan's C5 records the per-part CAPEX workaround.

**Still open:**
- **Master's CI:** master's packaging test is fixed by #148, which is merged into #154. Master's 33 reliability tests from #145 are cleared by #156, which merges first.
- **The engine asks above,** until they land.
- **U3:** the guided face inside Guided mode (the plan's U3 outline): the start card, the study chat tools, "Open in Expert", and rule 5. It builds on M2, the task-ordered Expert sidebar.
- **Smaller items, recorded in the plan:**
  - a "defaults updated" offer for legacy ledgers;
  - row 30 non-zero, and row 31 `eu_de`;
  - the pre-WP9 ledger CSV header for the MVP-2 importer.
