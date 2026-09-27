# EH reference design — the three skipped stages wired (`frontier`, `mc_certify`, `fmea_top`) + LCOH

**Date:** 2026-09-26  
**Branch:** `claude/eh-wire-skipped-stages` (from `master` `ec23302`)  
**Plan:** [`2026-09-26-eh-wire-skipped-stages.md`](../plans/2026-09-26-eh-wire-skipped-stages.md)  
**Spec:** [`2026-09-14-eh-reference-design.md`](../specs/2026-09-14-eh-reference-design.md) decisions 1–3, 9, 14, 16–18

## The gap, as verified on `master`

`services/adequacy/eh_study.py` carried `IMPLEMENTED = {apply_pack, ens_solve, redundancy, levers, dtc_stress, dtc_planning, assemble}`; `frontier`, `mc_certify` and `fmea_top` were always recorded `skipped` with "not implemented in P1.5 sync driver". Consequences on every `weak_flexible` / `off_grid` report (`mc_certify_required=True`): `mc_lole_h` never filled, certification never established, `frontier` and `fmea_top` sections empty, and `TeaBlock.lcoh_eur_per_kg` never computed although `services/results/lcoh.py` exists. The engines all existed and were already driven by other runners.

## What changed

| Area | Change |
|---|---|
| `models/energy_hub.py` | **additive**: `REPORT_SECTIONS` gains `certification` (after `target`); `ArchetypePack.mc_draws / mc_seed / mc_cov_target`; `TeaBlock.lcoh_status / lcoh_note`; `CertificationVerdict` literal; `DEFAULT_EH_MC_DRAWS=200`, `MAX_EH_MC_DRAWS=2000` (asserted equal to `mc.MAX_DRAWS`) |
| `services/adequacy/eh_stages.py` (new) | `freeze_fixed_plan` (MC inputs + COPT screening under the lock, ONCE, at the end of `ens_solve`), `run_mc_certify_stage`, `certification_verdict`, `frontier_targets_for` / `run_frontier_stage`, `run_fmea_top_stage`; owns `FMEA_TOP_LINK_PRIMARY_NOTE` (re-exported by the driver) |
| `services/adequacy/eh_study.py` | all stages executable; the three stage blocks inserted in decision-18 order; `_remaining()` / `_budget_gate()` so no stage runs over `budget_solves` silently; `mc_lole_h` passed to the assembler; `compute_tea(network=, cfg=)` |
| `services/adequacy/eh_report.py` | `compute_tea` wraps `compute_lcoh` when the network has electrolyser Links; `has_electrolyser_links`; `_live_result_df` (the live frames — inside the study the network IS the solved plan) |
| Frontend | `EhReferenceDesignPanel.tsx`: MC LOLE + verdict chips, LCOH chip / flag, certification block, frontier table (knee marker, ex-shed label, CSV), FMEA top-N table (Link-primary note, CSV); `COMPLETENESS_ORDER` gains `certification`; API types additive |
| Chat / docs | `run_eh_study` + `get_adequacy_results` descriptions name the wired stages; `CHATBOT.md` tool rows likewise; payload shape (`EXPORT_KEYS`) unchanged |
| QA | `tests/qa_eh_reference_design.py` (auto-discovered by `run_qa_drivers.py`) |

### Design points worth keeping

- **Certify the plan the report describes.** `mc_certify` and the class-A half of `fmea_top` read a snapshot frozen at the end of `ens_solve`, *before* the frontier re-solves the network at other targets. The frontier's closing restore normally lands on the same optimum, but a certification must not depend on "normally": if that restore ever fails, the frontier payload says so (`base_restored=False`) and the certified LOLE is still the ENS plan's.
- **Decision 2 is a rule in one place.** `certification_verdict(mc_lole_h, target_lole_h)` never sees ENS; `ens_met` rides in the payload for disclosure only. Verdicts: `certified` / `failed` / `no_target` (LOLE reported, pack states no target) / `not_established` (with the engine's reason).
- **Budget is charged the way `campaign` charges it.** Frontier = points + 1 restore; Class-B sweep = base + Links + restore; MC and COPT = 0. A stage that does not fit is `skipped` with the shortfall in its note; the pre-existing solve stages now gate on `remaining > 0` too.
- **LCOH is never 0.** `lcoh_status="skipped"` (no electrolyser Links), `not_established` (Links that produced no H₂, or engine failure), `ok` (finite). ADR-0001.
- **One report builder** (`assemble_reference_design_report`) — the stages return `(status, payload, note, solves_charged)` fragments only.

### A P1.5 test premise corrected

`tests/test_energy_hub_study.py::test_default_stages_never_leave_unimplemented_pending` asserted `frontier == "skipped"` on the *default* pipeline — that was the gap itself, pinned. It now asserts the frontier **ran**. Separately, the "no occurrence data" fixture cannot be the P1.5 `gas` network: `gas` has a carrier-default outage rate, so its fleet is not empty; the new fixture uses a carrier with no library default.

## Environment note (for the next person)

`pixi` is not available in the cloud container. `gridspine/drivers/year_study.py` uses Python-3.12 nested-quote f-strings, so the backend does **not** import under 3.11 — the venv must be 3.12 (`pixi.toml` pins `3.12.12`). With an unpinned `pip install`, pandas resolves to 3.x and ten EH/sweep tests fail with `Cannot interpret '<StringDtype…>' as a data type` inside `network.copy()` / frozen re-solves; pin to the lock (`pandas==2.3.3 numpy==2.4.6 scipy==1.17.1`) and they pass. The gridspine and desktop suites additionally need `pandapower`, `pywebview` and `lightsim2grid==0.10.1` (all pixi-provided, none in `requirements.txt`). These failures are environment artefacts, not code defects.

## Local verification

### Backend (venv: Python 3.12, `pypsa 1.1.2`, `linopy 0.8.0`, `highspy 1.14.0`, pandas 2.3.3)
```
cd pypsa-gui/backend
PYTHONPATH=<repo-root>:<backend> python -m pytest tests/test_energy_hub_*.py tests/test_adequacy_sweep.py -q
PYTHONPATH=<repo-root>:<backend> python -m pytest -m "not slow" -q
PYTHONPATH=<repo-root>:<backend> python tests/run_qa_drivers.py
```

| Suite | Before (master `ec23302`) | After |
|---|---|---|
| `test_energy_hub_*.py` + `test_adequacy_sweep.py` | **167** passed | **204** passed (+37 new: certify 14, frontier 11, fmea_top 6, lcoh 6; 0 failed) |
| full backend `-m "not slow"` | (not run on master in this container) | 5775 collected: **5607 passed**, 32 skipped, 136 failed/errored — every one of the 136 is `No module named pandapower` (gridspine pipeline → 503) or `No module named webview` (desktop), packages pixi ships that the venv lacked; none in EH / adequacy / results / chat files. After `pip install pandapower pywebview "lightsim2grid==0.10.1"` (the pixi pin — 1.1.0 lacks `LSGrid.get_lineor_res`) the twelve affected files were re-run: **241/241 passed**, so the suite is **5743 passed, 32 skipped, 0 failed** end to end. |
| `run_qa_drivers.py` | 21 drivers | **22 drivers passed** (`qa_eh_reference_design` auto-discovered) |

### Frontend
```
cd pypsa-gui/frontend
npx vitest run src/pages/results/EhReferenceDesignPanel.test.tsx   # 23 → 32 passed
npx vitest run                                                       # 177 files, 1949 passed
```
`tsc --noEmit` clean. (The frontend has no eslint config in this checkout.)

## E2E evidence — `tests/qa_eh_reference_design.py` (39/39 steps, over HTTP)

`weak_flexible` default pipeline on the shared fixture (`tests/eh_stage_fixtures.py::certifiable_weak_network`), VOLL 150, pack ENS cap 10‱, target LOLE 3 h:

| Field | Value |
|---|---|
| `pipeline.solves_consumed / budget_solves` | 14 / 30 (ens 1 + frontier 6 + Class-B 3 + levers 3 + DtC 1) |
| `achieved_ens_permyriad` | 10.0 (cap binds; ENS target **met**) |
| `mc_lole_h` | 82.125 h |
| `certification.verdict` | **failed** — "MC LOLE 82.1 h > target 3 h — certification FAILED although the ENS target is met (spec decision 2)" |
| `frontier` | 5/5 points at 40 / 20 / 10 / 5 / 2.5 ‱, every point `excludes_shed_cost: true`, `period_basis: single_period`, `base_restored: true (optimal)` |
| `fmea_top` | 4 modes, classes A+B, rank 1 = Link `import_poc` (Class B), note carries "Link-primary" + "SCLOPF" |
| `tea` | LCOE established; `lcoh_eur_per_kg: null`, `lcoh_status: skipped` |
| `levers` / `dtc` | `ok` / `ok` (unchanged sibling stages) |

`strong_grid` (`apply_pack → ens_solve → assemble`) on the same hub plus an electrolyser Link feeding an H₂ load: `lcoh_eur_per_kg ≈ 0.50 €/kg`, `lcoh_status: ok`; `certification` / `frontier` / `fmea_top` all `skipped` (not requested), none invented.

That first row is the point of the work: a report that *looks* adequate on ENS is now told, in its own words, that it is not bankable on LOLE.

## Still open / deliberately not done

- **MC is single-area copper plate** (its standing `MC_WARNING_V1`): the import Link's cap and the `off_grid` islanding are not seen by the sampler, so a grid-side generator in the same electrical fleet counts at full capacity. The fixture keeps nothing behind the PoC for that reason; a zonal MC is a separate engine change.
- The legacy `gates` fallback ("mc_certify required by pack but not requested") is kept for the pinned P1.5 test; with the `certification` section in place it is redundant and can be retired with a frontend change.
- `fmea_top` Class B is Link-primary only (decision 14); SCLOPF Line/Transformer rows remain omitted and the note says so.
- Pack-level frontier ladder / top-N are module constants (`EH_FRONTIER_LADDER`, `FMEA_TOP_N`), not pack fields — promote when a pack needs a different curve.
