# MVP-1: a guided decision study answers "Do I need a battery at my site, and what is it worth?"

**Status:** IMPLEMENTED on `claude/edge-tool-ux-research-n0n2l6`. S0–S8 are committed and each gate reads GO, or GO after its binding conditions were closed. S9 (this note, the QA driver, packaging and the integration carries) is committed as `b02b117`. Its gate, the plan-level definition of done, read **GO WITH BINDING CONDITIONS** (BC-S9-1 to BC-S9-3, `scratchpad/gate-s9.md`). The gate fixes follow in the next commit: BC-S9-1 and BC-S9-2 closed, BC-S9-3 pending the coordinator's full-suite run.
**Owner-closed gates, re-checked:** S4, S6 and S8 ended "GO WITH BINDING CONDITIONS, closed by the owner" with no assessor re-check at the time. The S9 assessor re-verified every one of those closures by mutation (BC-S4-v2-1/2, BC-S6-v2-1, BC-S6-v2-2, BC-S8-v2-1, BC-S8-v2-2): each fix's test goes red without the fix.
**Date:** 2026-09-30
**Plan:** `docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md`
**Spec:** `docs/superpowers/specs/2026-09-28-guided-investment-study-design.md`
**Gate notes:** `docs/superpowers/notes/2026-09-2{8,9}-mvp1-*-gate.md`, `2026-09-30-mvp1-{s5,s7,s8}-gate.md`

## The gap, as it was on master

A user with a meter file and a tariff could not get an investment answer from the product. The expert view could size a battery. But the answer was spread across several places:

- an annuity field labelled as an upfront cost;
- a net profit of about zero at the optimum, with no word on why;
- no tariff, no bill, no demand charge;
- no cash flow, NPV or IRR on a stated basis;
- no verdict, and no sensitivity with the size held fixed;
- no report.

Every answer needed the canvas, and every study mutated the user's own project.

## What changed, per phase

The merge-base with master is `67c4c77`.

| Phase | Commits | What it added |
|---|---|---|
| Plan | `8c73edf`, `a713530`, `9f1de15`, `95b6cd2`, `0fbc0e7` | Gap analysis; plan v1 (NO-GO), then v2 with BC-1 to BC-7; the S2 seed source was pinned to technology-data v0.14.0 |
| S0 pre-fixes | `5203809`, `b9288f8`, `2844dcf`, `74c90f9` | Annuity badges carry `/yr`; EUR everywhere; the zero-profit caveat reaches the user (`services/results/sizing.py`, `economics_caveats.py`) |
| S1 contracts | `6b51509`, `fefb045`, `217ccfb` | `models/study.py`; the sidecar store `studies/<id>.json` in `_BUNDLE_DIRS`; project-scoped routes under `/api/projects/{name}/studies` (404, never 403); auth-mode refusal until OPEN-ITEMS 1 is closed |
| S2 library and ledger | `d87f09f`, `650f213`, `48631fd` | `study_library/` (technology-data v0.14.0, two illustrative tariffs, finance defaults, sector load profiles); `seed_ledger`, `apply_user_row`, maturity |
| S3 tariff | `e8b2300`, `71eaf67`, `ca28316` | Tariff prices as Link `marginal_cost`; `_wrap_with_demand_charge`; `BillCalculator`; the `demand_charge_eur` bridge term |
| S4 pack and runner | `47d68e2`, `f038146`, `fc12f40`, `8a64f13` | `BESS_AT_SITE`; `build_site_network` (a StorageUnit costed with two annuities); M0 base project; study-owned option forks; the runner on the base context; study-owned contexts are outside the resident cap |
| S5 pro forma | `1dfa969`, `61961e1`, `489e46f`, `a5be52f` | `build_investment_case` (CAPEX from the ledger, inverter replacements, annuity-basis salvage, NPV/IRR/paybacks/LCOS); the XLSX pro forma with a live `=CF0+NPV(...)`; the site golden fixture |
| S6 findings | `791c536`, `afb3767`, `a37e233` | Value streams; battery attribution against a PV-only reference; the fixed-size tornado on throw-away forks; the verdict; `explain` lifted from `chat_tools` |
| S7 report | `b73b573`, `55e1917`, `3a18f4a`, `5791dce` | `assemble_decision_report`; the prose guard; the stale rule shared with the case's 409; HTML (autoescape, CSP sandbox), DOCX and XLSX; `python-docx` pinned |
| S8 frontend | `b6566fa`, `e65679e`, `59b444d` | The `decision` panel and wizard tab: intake, options, tariff, finance, ledger, run, verdict, why/how, robust, report |
| Harness | `3094055` | The per-thread sandbox check holds connections, not their ids (a flaky suite test; not an MVP-1 change) |
| **S9** | `b02b117`, plus the gate-fix commit that follows it | See below |

### S9 (`b02b117`) and its gate fixes

| Where | Change |
|---|---|
| `backend/tests/qa_decision_study.py` (new) | The QA driver, over HTTP with a real LP. Found by `run_qa_drivers.py` |
| `pypsa-gui.spec` | `datas` gains `templates/decision_report.html.j2` and `study_library/`. `hiddenimports` gains `matplotlib.backends.backend_agg` |
| `backend/smoke/check_bundle.py` | `EXPECTED` gains `decision_report.html.j2`, `study_library`, `technology_costs.csv`, `tariffs.csv`, `finance_defaults.yaml` and `load_profiles` |
| `backend/main.py` | `lifespan` calls `forks.sweep_leftover_forks` before `solve_job_store.reconcile_on_boot`. A failed sweep never fails boot |
| `backend/services/study/forks.py` | `sweep_leftover_forks(db)`. It removes throw-away `-var-` forks, and option forks whose study record is absent. It deletes only what `is_study_owned` proves, through `delete_fork` |
| `backend/routers/projects.py` | `_saves_its_own_directory`. The owner keys round-trip only when the network being saved was bound to that directory. A forced Save-As or Save-a-Copy over a fork drops them |
| `backend/services/study/runner.py` | `_wait` has a deadline: `SOLVE_WAIT_DEADLINE_S` (4 h), then an abort and `_ABORT_GRACE_S`. It raises `SolveDeadlineExceeded` (`solve_deadline_exceeded`). The run fails typed at that option and stops |
| `backend/services/study/tornado_runner.py` | `_wait_variant` maps the deadline to `VariantFailed("solve_deadline_exceeded")`, so the row is not established |
| Tests | `test_study_s9_integration.py` (11, new); `test_study_report_live_lp.py` (4, new); `test_packaging_requirements.py` (+3); `test_study_library.py` (+4, gate S5 G10); `test_study_case_routes.py` (+1, gate S5 G11) |
| Plan | The S9 driver line no longer names `upfront_cost_series` (S4 and S5 carries). "Five options" became "every option, four with PV off" |

**Frozen layout.** No code change was needed. `render_html.TEMPLATES_DIR` and `library.LIBRARY_DIR` are both `parents[2]`-relative, which follows the `presets.json` precedent. Under `pathex=[BACKEND]`, that is the `_MEIPASS` root, where the new `datas` land.

`test_the_decision_studys_data_resolves_in_a_frozen_layout` rebuilds the layout from the spec's own `datas`. It loads both modules from that tree, then renders the template's loader and loads the library there. It was red on `TemplateNotFound` before the spec change.

## Evidence

**Red first, verbatim.**

- *Packaging:*
  - `assert None == 'templates'` (the spec had no entry for the template);
  - `assert '"matplotlib.backends.backend_agg"' in ...`;
  - `jinja2.exceptions.TemplateNotFound: 'decision_report.html.j2' not found in search path: '.../_MEIPASS/templates'`.
- *Save-As:* `assert not ({'owner_base_project', 'owner_option_id', 'owner_study_id', ...} & {...})`, for both the forced copy and the rebind.
- *Deadline:*
  - the route, with the attribute stubbed: `assert 'done' == 'failed'`, because the run waited 20 s for the hung solve and then reported done;
  - the unit test: no `SolveDeadlineExceeded`;
  - the tornado: no `_wait_variant`.
- *Sweep:* `AttributeError: module 'services.study.forks' has no attribute 'sweep_leftover_forks'`, 5 times.
- *Tests written after the fact.* The G10 and G11 tests and the live-LP report test pass on existing code, because they test S5 and S7 behaviour. Their red is the mutation below.

**Mutations, on a copy of the tree (`scratchpad/s9/mut`; the main tree was never edited for a mutation).**

| Mutation | What it does | Red |
|---|---|---|
| G10 | the loader's snake_case guard removed | exactly the 3 new G10 cases |
| G11 | `fork_matches` accepts a missing recorded hash | exactly `test_a_run_record_without_the_forks_hash_is_refused_not_trusted` |
| M3 | owner keys always round-trip | exactly the two forced-save cases |
| M4 | owner keys never round-trip | the own-save control, the forced-save cases, the sweep test and three runner tests (the queue's save loses ownership), as expected |
| M5 | the deadline never fires | the deadline route test and the wait unit test |
| M6 | the tornado re-raises the untyped deadline | exactly the tornado deadline test |
| M7 | the sweep ignores whether the study record exists | both sweep tests (the live study's forks and the unreadable record's forks are deleted) |
| M8 | the sweep and `delete_fork` skip the ownership check | the sweep test (a forged copy under another parent is deleted) and the rule test |
| M9 | the sweep runs after the queue is reconciled | exactly the lifespan-order test |
| M10 / M11 | the template / the library `datas` line removed | the spec-and-EXPECTED test and the frozen-layout test |
| M12 | the report drops the `battery_only_zero` tag | two live-LP report tests and two existing S7 tests |
| M13 | the runner drops the `size_at_upper_bound` caveat | the four live-LP tests and the existing runner size-bound test |

**The QA driver (`qa_decision_study.py`), as committed in `b02b117`: 57 passed, 0 failed, in 94.8 s.** Wall time was 1 min 42 s with imports. (The gate fixes move the storage quote to 500 EUR/kWh and add the margin checks; see "Gate fixes" below for that run's numbers.) It makes 7 real HiGHS solves:

- 4 in the run (54.5 s);
- 2 in the tornado (18.0 s);
- 1 in the aborted run (`none`; the `bess_1h` solve was aborted).

Its setup:
- The load is the site fixture's evening spike, as a kW meter CSV with timestamps. It is written by the guided flow's draft path (`load.csv_text`, BC-S8-5) into the study's own base project.
- The tariff is the DE seed. PV is off.
- The storage quote is edited to 450 EUR/kWh and the inverter quote to 230 EUR/kW.

What it checks:

- **Sizing.** `bess_1h` sizes 0.7958 MW. `bess_2h` and `bess_4h` size to zero and are judged by size (`skipped`).
- **Verdict.** It is **`marginal`**, driver `demand_charge_price`. At 6,300 EUR/MW/month the fixed-size battery NPV is −21,634 EUR; at 11,700 it is 555,272 EUR. So `marginal` is reached through the API. The constructed LP case in `test_study_tornado_lp.py::test_the_sign_flips_at_the_high_storage_cost_bound_so_the_verdict_is_marginal` is the second proof.
- **CAPEX.** It is 541,137.46 EUR, which is `packs.battery_upfront_eur_per_mw` (680,000 EUR/MW) × 0.7958 MW, exact. `upfront_cost_series` would say 831,618 EUR/MW, 22.3 % high at these quotes. The S4 gate measured 24–35 % at library costs.
- **FOM.** It is 617.73 EUR/yr × 25 = 15,443.31 EUR. That equals `asset_economics.fom_cost_eur` and the StorageUnit row of `cost_breakdown` (617.73229) on the fork's own network.
- **Campaign.** 4 solves are charged to a `decision_study` campaign on the base context. The session's context has no campaign.
- **Tornado.** The tornado re-dispatches twice on throw-away forks. It has four bars (demand charge, discount rate, storage cost, inverter cost). Afterwards no `-var-` row or directory is left, nothing is marked study-owned, and the option forks' directory hashes are unchanged.
- **Report.** It renders to HTML (CSP sandbox, three inline PNGs), DOCX (with the Assumptions table) and XLSX (Verdict, Tornado, Assumptions, Provenance, and cash flows per option). After a ledger edit, `stale` flips with `ledger_changed_since_findings`, and the case answers 409.
- **Abort.** An abort mid-run gives `aborted`, with `bess_1h`, `bess_2h` and `bess_4h` pending. Their forks are removed and `none` is kept. `options_status` is `not_established`. No pre-existing project changed.
- **No user project touched.** Creating either study, and uploading its load, changed no pre-existing project. The user project that was resident at the cap with an unsaved bus is still resident and unsaved. No user context was evicted, and no user project directory changed during the whole journey.
- **Delete.** Deleting each study cascades to its forks (4 and 1).

**The live-LP report (`test_study_report_live_lp.py`, 4 solves, about 45 s).** It runs on the site fixture with a storage quote of 400 EUR/kWh and a sizing limit of 1.0 × the connection:

- `bess_4h` sizes to zero;
- `bess_pv_2h` builds a 0.633 MW battery (interior) and 2.0 MW of PV **at its bound** (`size_at_upper_bound:pv`);
- the PV-only reference is solved.

The rendered report carries, from a real solve:
- `battery_value_against_pv_only_reference`;
- `battery_only_options_sized_to_zero`;
- `size_at_upper_bound`;
- `market_revenue_at_duals_exceeds_cost_at_size_limit`, and not the zero-profit sentence;
- `value_streams_increment_over_pv_only`.

The report goes to HTML, DOCX and XLSX.

### Gate fixes (after `b02b117`; red first, verbatim reds in `scratchpad/s9-implementation.md`)

| Item | Change | Red before |
|---|---|---|
| BC-S9-1 | The executive summary lists the verdict's drivers by their ledger label (`payload.driver_labels`, `drivers_heading`), in the HTML, the DOCX and the XLSX Verdict sheet (`verdict_driver` rows) | `'Demand charge on peak import' in <executive_summary section>` false |
| [S2] | The driver's storage quote is 500 EUR/kWh; it asserts the centre battery NPV > 100,000 EUR, the demand-charge low bound < −25,000 EUR, and every other bound > +25,000 EUR; and that the summary lists the driver | — (driver) |
| [S3] | `pypsa-gui.spec`: `collect_data_files("docx")` and `"docx": "pyz+py"` (`docx/parts/*.py` read `parts/../templates`); `EXPECTED` gains `default.docx`; a test renders a report DOCX in a subprocess with `docx` imported from the rebuilt layout | `assert 'collect_data_files("docx")' in <spec>` |
| [N4] | `render_html.num` clamps −0.0 and noise below the displayed precision to 0 | `assert '-0' == '0'` |
| [S4] | The Assumptions table shows the range the tornado tests (`findings.bounds_for`); a re-centred one is marked, with the library range beside it (report HTML and DOCX; `tested_low`, `tested_high`, `tested_range_note` in both workbooks) | `KeyError: 'tested_low'` |
| [N5] | The maturity advice names only what holds the badge (`report._tighten_advice` from `StudyMaturity.reasons`) | the advice asked for a metered load already uploaded |
| [N2] | The tornado stops at the first solve past the deadline: status `aborted`, typed `solve_deadline_exceeded` error, note `tornado_stopped_at_solve_deadline`, the timed-out row flagged, the rest pending | `assert 'done' == 'aborted'` |

The driver after the fixes: **60 passed, 0 failed, 110.2 s** (1 min 58 s wall).
- `bess_1h` sizes 0.7958 MW.
- The centre battery NPV is 227,029 EUR.
- The demand-charge bounds give −61,423 EUR (low) and 515,482 EUR (high). The lowest other bound is the discount-rate high bound, at +99,405 EUR.
- CAPEX is 580,926.98 EUR, which is 730,000 EUR/MW × 0.79579 MW. `upfront_cost_series` reads 20.8 % high.

## The golden reconciliation numbers

These come from the S5 gate's independent recomputation on the site golden fixture (DE seed, library ledger). Every figure matches the payload to a relative error of 3e-15 or better (IRR 9e-13).

| | `bess_2h` | `bess_pv_2h` |
|---|---|---|
| Sizes | battery 0.77196 MW | battery 0.81040 MW, PV 2.27426 MW |
| CAPEX, year 0 | 458,272.60 | 2,491,114.70 |
| Inverter replacement, years 10 and 20 | 165,143.29 | 173,366.57 |
| FOM per year | 557.36 | 29,195.77 |
| Savings per year | 81,117.82 | 365,516.28 |
| Salvage, year 25 | 96,406.67 | 1,474,407.93 |
| NPV | 371,681.60 | 1,566,950.76 |
| IRR | 15.5637 % | 12.8690 % |
| Payback, simple / discounted (years) | 5.6886 / 7.5142 | 7.4070 / 11.3836 |

- The baseline bill is 866,424.00 EUR.
- The identity NPV = LP objective saving × AF(7 %, 25) holds to 1e-13, PV included, which is the basis of `npv_nonnegative_at_optimum_by_construction`.
- S6 recomputed the tornado independently to about 1e-10.
- S7 cross-checked every HTML and DOCX table and fact against the findings and case payloads, with 0 mismatches.

## Counts, before and after

| | master merge-base `67c4c77` | `3094055` (before S9) | `b02b117` (S9) |
|---|---|---|---|
| Backend tests, `pytest --collect-only` | 6,050 in 311 files | 6,527 | 6,550 in 340 files |
| Frontend tests, `vitest list` | 1,964 in 177 files | 2,114 in 192 files | 2,114 (S9 changes no frontend file) |
| QA drivers run by `run_qa_drivers.py` | 22 | 22 | 23 |

- The merge-base count was collected in a throw-away `git worktree` under the session scratchpad.
- **Final S9 runs.**
  - Every `test_study_*`, `test_studies_routes`, the packaging, sidecar, error-kind manifest, save-guard, swap-guard, QA-driver-coverage, proforma-golden, golden-coverage and FOM-reconciliation tests (37 files): 591 passed, exit 0.
  - `run_qa_drivers.py`: all 23 drivers passed in 8 min 14 s, while the mutation batch ran alongside. That includes `qa_asset_economics`, `qa_cost_decomp_overnight` and `qa_eh_reference_design`, which are unchanged, and `qa_decision_study` (109 s under that load).
  - The full backend suite was not re-run by the S9 implementer; the coordinator runs it (BC-S9-3).
- Full backend suite on <commit>: <result>
- The gate fixes add 6 backend tests (the BC-S9-1 test, three report tests for [N4], [S4] and [N5], the tornado-deadline stop test and the python-docx frozen-layout test), so the gate-fix commit collects 6,556.

## A real-profile finding the report now states

**The shipped sector profiles size every battery-only option to zero** (gate S5). The S5 gate solved `bess_2h` for both profiles, `commercial_office` and `industrial_two_shift`, against both seed tariffs, and got p_nom_opt = 0 every time.

This is plausible, not a bug:
- a 2 h battery costs about 64k EUR/MW/yr in annuities;
- the monthly demand charge is worth 108k EUR/MW/yr;
- so a battery pays only if it can shave about 0.6 MW per MW, which needs a peak plateau shorter than about 3.4 h;
- office peaks are broad plateaus.

With PV, the option's value is mostly PV's (68 % energy, 13 % demand on the office profile). That is why S6 values the battery against a PV-only reference.

Synthetic profiles also lack the short spikes real meters show, so they **understate** peak-shaving value. The report states:
- `synthetic_load_understates_peak_shaving`;
- `battery_only_options_sized_to_zero`;
- `sector_profiles_have_broad_peaks`, when both apply.

This is also why the QA driver and the golden fixture use an uploaded evening-spike load.

## S8 deviations from the plan (recorded in the plan's "Amended at S8")

- **A Sidebar row, "Decision study".** Without it a closed study could not be reopened.
- **No typical-week chart in WhyHow.** The Dispatch component reads the active project, and an option fork is not the active project. The page and the report say so (`typical_week_not_in_mvp1`) and point to Expert view → Results → Dispatch on the fork.
- **"Open a blank model" replaces the "custom question" card.** A custom-question record runs no pack, so the card would lead a novice to a study that cannot run.
- **A draft's load travels as `load.csv_text`** and is written as an upload into the study's own new base project at creation (BC-S8-5). A later PATCH cannot store `csv_text`.
- **A header in a unit other than kW or MW is refused** (`load_upload_unit_unsupported`, BC-S8-6). CR-only CSVs (Excel for Mac) are read.

## Still open / deliberately not done

- **OPEN-ITEMS 1 and auth mode.** The study routes refuse unconditionally in multi-user mode (BC-6), because an Expert-view save of a fork still goes through the process-global `_user_ts`. `PYPSAGUI_DECISION_STUDIES=1` enables them in local mode only.
  - The QA driver and the suite enable them by overriding the dependency, because their sandbox is multi-user.
  - Closing this needs the container-tenancy fix for `services/user_timeseries.py`, which is its own plan.
- **MVP-2 items (the plan's "Deferred"):**
  - native PDF, PPTX, share link, AI paragraphs;
  - post-tax and nominal bases, degradation;
  - Store + Link battery;
  - scenario sets, ancillary and capacity revenue, resilience value, multi-party;
  - other templates, break-even bisection and the option map;
  - a representative-week quick screen;
  - ledger CSV import and OpenEI import.
- **Reopening a study after a reload** (S8 [S5]). It is reachable only through the Sidebar row; the hub is not restored on reload.
- **Marking study-owned forks in project lists** (review [S11], S8 [S9-c]). Option forks appear as ordinary `<base>-opt-…` children. A user who deletes one gets `option_not_solved` on the next read.
  - Not done in S9. It is a list-payload flag plus a frontend change in the project lists, and S9's scope was backend integration.
  - The S9 Save-As fix limits the damage in the other direction: a user's network saved OVER a fork (Save-As, Save-a-Copy) loses the owner keys and is never swept or cascaded as the study's. A user's Expert-view edit saved onto the fork's OWN directory keeps the keys by design, so it is deleted with the study, or swept once the study record is gone (gate S9 [N3]).
- **A size limit on the JSON body before parsing** (S8 [S-v2-1]). The 25 MB `csv_text` cap is checked after FastAPI has parsed the body; an 80 MB body costs about 200 MB of RSS before the refusal.
- **Debouncing the draft preview.** Each load or site change posts `/preview`.
- **Spelled-out numbers in the prose guard.** `validate_prose` rejects digits outside `{{fact_id}}`, but "four million" passes (documented at S7).
- **The remaining report nits** (S7, carried by S8 to S9 or later): rounding in the formatted facts, and the evidence-gap labels. (`Engine` gained `method_constant`, and the XLSX labels IRR and payback as static values, in S7 and S8.) The case workbook's Assumptions sheet now carries the tested range too; the verdict-drivers list, the negative zero, the re-centred range and the maturity advice are fixed (gate fixes below).
- **S0 follow-up, the `/yr` labels.** `pages/GenerationStack.tsx` still heads the annual capital cost `CC (€/MW)` (Generator and StorageUnit) and `CC (€/MWh)` (Store), without `/yr`. The S0 gate named `CapacityExpansion` and `propertyDocs` in the same follow-up, and the quick-add label and edit-mode badges are correct but untested. This is a live instance of the mislabel S0 exists to remove.
- **S3 [N4].** `objective_decomposition._bridge` recomputes `demand_charge_eur` against the CURRENT solver config, not the one the solve used. The study's runner passes the fork's own config, but the Expert view's bridge on a re-configured project does not.
- **S3, cross-hour export cycling.** The export-price preflight pairs the Links hour by hour; it does not see import-to-export cycling ACROSS hours through the battery (charge on a cheap import hour, export at a dearer export hour). The seed tariffs are safe (their export price is below every import band, S4), but a custom tariff is not checked.
- **S2, untested ledger branches.** Reset clearing the attention note; a user tariff whose source says illustrative; the re-seed filter re-deriving a supplied tariff's prices.
- **S6 [N2], the energy-price skip code.** On a single-band tariff the tornado records `energy_price_level` as skipped with `not_applicable` (the ledger's `energy_price_level` row has no value on one band, `unavailable: not_applicable`, and the skip rule returns that before its single-band check), not the documented `energy_price_level_no_effect_single_band`. The bar is correctly absent; the code the report explains is the generic one.
- **This gate's residuals (gate S9).**
  - [S2] The first S9 driver's `marginal` was thin: at 450 EUR/kWh the demand-charge low bound was −21,634 EUR, and at 420 the verdict turned `recommended`, failing the driver. Fixed below (500 EUR/kWh, asserted margins).
  - [N2] The tornado carried on after a solve outlived the deadline. Fixed below (it stops, as the run does).
  - [N1] The driver leaves the harness's temporary projects root behind, as every QA driver does (pre-existing).
  - [N6] A queued job that boot reconciliation restores on a swept fork fails as an untyped `FileNotFoundError` (`queue_error`); `reconcile_on_boot` could drop queued rows whose `storage_dir` is gone. Not fixed.
  - The case workbook (`case.xlsx`) and the report both show the tested range; the report's HTML and DOCX mark a re-centred one. The UI's ledger step does not show it (frontend, not in scope).
- **Residuals of the S9 carries themselves:**
  - The startup sweep assumes no run or tornado is live, which is true at boot of a single instance. Two overlapping local-mode launches (the single-instance guard D11/H1 has not landed) could sweep a live tornado's variant fork. The tornado would then record that row as failed. No user project is at risk.
  - A solve that is stuck past the deadline and the grace period leaves its fork, because `delete_fork` refuses while the queue holds the job. The run records it in `fork_removal_refused`, and the next startup sweeps it only if it is a variant or its study is gone.
- **A macOS `.app` build has not been run with the new `datas`.** The frozen layout is verified by a test that rebuilds it from the spec, and `check_bundle.EXPECTED` will report a miss on the next real build.
