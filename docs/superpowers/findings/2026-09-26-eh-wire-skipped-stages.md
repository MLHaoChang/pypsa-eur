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

`pixi` is not available in the cloud container. `gridspine/drivers/year_study.py` uses Python-3.12 nested-quote f-strings, so the backend does **not** import under 3.11 — the venv must be 3.12 (`pixi.toml` pins `3.12.12`). With an unpinned `pip install`, pandas resolves to 3.x and ten EH/sweep tests fail with `Cannot interpret '<StringDtype…>' as a data type` inside `network.copy()` / frozen re-solves; pin to the lock (`pandas==2.3.3 numpy==2.4.6 scipy==1.17.1`) and they pass. The gridspine and desktop suites additionally need `pandapower`, `pywebview` and `lightsim2grid==0.10.1` (all pixi-provided, none in `requirements.txt`). These failures are environment artefacts, not code defects. `.claude/hooks/session-start.sh` now builds exactly this environment for Claude Code on the web sessions (venv at `~/.venv-pypsa-gui`, `PYTHONPATH` set, frontend `npm install`); `.gitignore` whitelists only that hook and `.claude/settings.json`.

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
| `test_energy_hub_*.py` + `test_adequacy_sweep.py` | **167** passed | **221** passed on the final head (+54 new: certify 14, certify scope 6, frontier 21, fmea_top 7, lcoh 6; two P1.5 tests updated deliberately; 0 failed) |
| full backend `-m "not slow"` | (not run on master in this container) | **5770 passed, 31 skipped, 0 failed** on the final head (venv matched to pixi: Python 3.12, pandas 2.3.3, plus pandapower, pywebview and `lightsim2grid==0.10.1` for the gridspine/desktop files; with lightsim2grid 1.1.0 they fail on `LSGrid.get_lineor_res`, an environment artefact) |
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
| `mc_lole_h` | 415.005 h (hub-side fleet; 82.1 h before the fleet fix below, when the 200 MW grid unit was sampled) |
| `certification.verdict` | **failed** — "MC LOLE 415 h > target 3 h — certification FAILED although the ENS target is met (spec decision 2)" |
| `frontier` | 5/5 points at 40 / 20 / 10 / 5 / 2.5 ‱, every point `excludes_shed_cost: true`, `period_basis: single_period`, `base_restored: true (optimal)` |
| `fmea_top` | 3 modes (B `import_poc`, A `base`, A `peaker` — the grid-side unit is out of the hub fleet), note carries "Link-primary" + "SCLOPF" |
| `tea` | LCOE established; `lcoh_eur_per_kg: null`, `lcoh_status: skipped` |
| `levers` / `dtc` | `ok` / `ok` (unchanged sibling stages) |

`strong_grid` (`apply_pack → ens_solve → assemble`) on the same hub plus an electrolyser Link feeding an H₂ load: `lcoh_eur_per_kg ≈ 0.50 €/kg`, `lcoh_status: ok`; `certification` / `frontier` / `fmea_top` all `skipped` (not requested), none invented.

That first row is the point of the work: a report that *looks* adequate on ENS is now told, in its own words, that it is not bankable on LOLE.

## Follow-up fix: the MC / COPT fleet is the hub's, not the copper plate

Reviewing the shipped stages turned up an optimistic verdict. The MC and the COPT are single-area, so they sampled generators **behind** the import Link. On an `off_grid` hub, whose PoC Link the pack islands, a 200 MW grid-side unit the LP cannot reach still covered the hub's deficits: the test `test_off_grid_certification_ignores_generation_behind_the_islanded_poc` was red with **"MC LOLE 0 h ≤ target 3 h — certified"** for a hub that sheds.

`eh_stages.hub_fleet_scope` now splits the network at the import Link(s) before the freeze:

- Only Links identified by `eh_role == grid_import` or an `eh_poc` endpoint are trusted as the boundary. The carrier fallback can match Links inside the hub, so it keeps the whole network in scope with a note.
- The grid side is the Link's `bus0` (import flows bus0 → bus1), flipped when only bus0's side carries an `eh_critical` bus. `eh_poc` is not used for orientation, because fixtures and the SCR gate tag it on either side.
- If another branch still connects the two sides, there is no hub boundary, and the scope falls back to the whole network with a note.
- On the hub side, the MC and COPT read a pruned copy of the solved network. The import is a firm block up to the Link's planning cap per snapshot: 0 MW when islanded, the pack's 50 MW for `weak_flexible`. It is disclosed as `fleet_scope` on the certification and fmea_top payloads, with `import_firmness: planning_limit_only`. Link outages stay in the Class-B ranking rather than being sampled.

Tests: `test_energy_hub_certify_scope.py` (6: off_grid verdict flips to `failed`, weak_flexible counts 50 MW not 200 MW and screens only hub units, carrier-only and non-separating fallbacks, bus0 orientation and its critical-side flip).

## Cleanups in the same PR

- **Legacy `gates` fallback retired.** A required-but-unrequested `mc_certify` used to set `gates` to `not_established` ("mc_certify required by pack but not requested"). The `certification` section now carries that reason, so `gates` is the dynamics field only: `weak_flexible` runs the SCR gate, the other archetypes get `skipped`. The P1.5 test that pinned the fallback now asserts `certification == not_established` and `gates == skipped`. No frontend change was needed.
- **Frontier ladder and FMEA top-N are pack fields.** `ArchetypePack.frontier_ladder` (default ×4, ×2, ×1, ×½, ×¼; distinct positive finite factors, at most the frontier engine's 12 points) and `ArchetypePack.fmea_top_n` (default 10, 1–50). Budget trimming now keeps the factors nearest ×1 on a log scale, so a 3-point budget sweeps ×2, ×1, ×½ rather than dropping the whole tight end.

## Still open / deliberately not done

- **The import is firm up to its cap.** The hub-side scope treats the PoC Link as a firm block (planning limit), not as a unit with its own outage chain; a zonal MC that samples the Link and the grid behind it is a separate engine change. The carrier-fallback case still certifies on the whole copper plate and says so.
- `fmea_top` Class B is Link-primary only (decision 14); SCLOPF Line/Transformer rows remain omitted and the note says so.
