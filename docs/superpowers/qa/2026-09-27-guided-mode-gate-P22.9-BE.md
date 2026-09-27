# QA gate — P22.9-BE (independent review)

- **Scope:** `git diff 4f1852d..HEAD -- pypsa-gui docs` (commits `7af2c98`, `d34e7b1`, "WIP P22.9-BE").
- **Contract:** spec `2026-09-27-guided-mode.md` §2.1–§2.3, §8; plan P22.9-BE section and phase note.
- **Reviewer:** independent Opus-class QA agent. It did not write this code and edited no backend source or test file.
- **Date:** 2026-09-27

## Verdict: **NO-GO**

The phase fixes what the spec lists. The Bus columns, the passive-branch `sub_network` and the `SubNetwork` rows all come back on every wrapped path. The tests are not vacuous. Row 2 is green. But the live-server reproduction turned up one more table that the same topology pass rewrites, and the fix leaves it changed. Spec §8.3 says: "a bug in another surface found during the gate blocks the gate; it is fixed or explicitly deferred by the product owner in the plan".

## Blockers

### B1. The FMEA sweep still rewrites `Generator.control` on the live network (same mechanism as bug 3)

- **Where:** `pypsa-gui/backend/services/adequacy/sweep.py:63` (`_TOPOLOGY_BUS_COLS`) and `:81–141` (`preserve_bus_topology`). The context manager restores `buses.control/sub_network/generator`, the passive-branch `sub_network` and the `SubNetwork` rows. It does **not** restore `generators.control`.
- **Mechanism (PyPSA 1.1.2, read in the venv):** `determine_network_topology()` → `SubNetwork.find_bus_controls()` → `find_slack_bus()` (`pypsa/network/power_flow.py:1233–1260`). When a sub-network has no Slack generator, it writes `n.generators.control = "Slack"` on that sub-network's first generator. When it has several, it demotes the extra ones to `"PV"`. That is the same pass the fix undoes for the Bus table, and it runs in the same optimize post-processing (`pypsa/optimization/optimize.py:1104–1105`).
- **Evidence (row 5, live server, HTTP):** after `POST /api/results/fmea_sweep` on `eh_datacenter`, `GET /api/network/generators` shows:
  - `grid_supply: control PQ → Slack`
  - `genset_1: control PQ → Slack`

  Both stay changed after the EH study. `control` is a user input column in the Expert Generators table. This is the "every bus went PQ → Slack" symptom of bug 3, moved to the next table. Neither the tests nor the planned P22.9-FE smoke (which asserts only `/buses` and `/links`) would catch it.
- **Failing ids:** none today. No test covers `/generators`. That gap is part of the finding.
- **To close:**
  1. Save and restore `n.generators["control"]` inside `preserve_bus_topology`, aligned on the saved index like the Bus columns.
  2. Extend `test_fmea_sweep_leaves_the_live_tables_equal` and the abort test so `_tables` also compares `/generators`, excluding `p_nom_opt` on the sweep paths per the accepted deviation.
  3. Or have the product owner defer the fix explicitly in the plan.

## Non-blocking notes

1. **Accepted deviation, with a documentation correction.** The deviation is that `p_nom_opt` comes from the closing base re-solve. I accept it: it is an optimisation output, written by the solve that also leaves `dispatch: fresh`, and restoring it would contradict that dispatch. However, the plan note (`plans/2026-09-27-guided-mode.md:99`) scopes it to `/links`. Row 5 shows it covers every `*_nom_opt`: on `/generators`, `p_nom_opt` changes 0 → nameplate for `grid_supply`, `genset_1..4` and `rooftop_pv`. Stores and storage units are presumably affected too. Reword the note to say "all `*_nom_opt` outputs", and pin it on generators as it is pinned on links.
2. **The restore runs outside the network lock, and edits during a study are not refused.** `network_crud` create and update take `PyPSAService.get_lock()` but have no study-running guard. So a user who edits a bus's `control` or `generator`, or a line's `sub_network`, while a long sweep, frontier or loop runs has that edit silently reverted when the context exits. A bus added mid-study keeps the solver-written values, because it is not in the saved index, and trips the mismatch warning. The restore (`sweep.py:113–136`) also writes `n.buses` without the lock, so it can race a concurrent remove+add. This is the same pre-existing hazard class as `freeze_capacities`' undo (`sweep.py:180–185`, also lock-free), so it is not a regression. Consider taking the lock in the `finally`, or refusing network edits while a live-network study runs.
3. **The margin loop has no test.** `margin_loop_runner.py` gets the same `ExitStack` wrapper as the coupling loop, but only the coupling loop has an HTTP test (`test_coupling_loop_leaves_the_live_buses_equal`). The code is symmetric, so the risk is low.
4. **The `ExitStack` double close is correct.** The explicit `topology.close()` in `_worker` empties the stack, so the outer `with` exit is a no-op. `preserve_bus_topology`'s `finally` swallows `Exception`, so the explicit close cannot raise into the record update. The record flips only after the restore (coupling `:579`, margin `:911`; FMEA and frontier return from inside the `with`). The abort path and the "base solve failed" `RuntimeError` path both run the context exit.
5. **Other solve paths.** I grepped every `_solve_once(`, `run_simulation(`, `.optimize(` and `.pf(` under `services/` and `routers/`. The phase note's twelve-site table matches the code. The only unwrapped live paths are the user's own foreground solve (`routers/simulation.py:620`, `solve_queue.py:1250`) and AC PF (`ac_pf_service.py:383`), all out of scope per §2.1 item 4. No study path is left unwrapped.
6. **`p_set_peak`** (`network_crud.py:66–90`):
   - An all-NaN or empty series gives `max()` = NaN, which falls back to the static value.
   - A missing ts column falls back to the static value.
   - A non-finite or absent static value gives `null`.

   All three are correct. One edge case: for a negative (generation-like) load, `max` gives the least-negative value, not the largest magnitude. The FE sums `|p_set_peak|`, so such a load would be under-reported. The spec literally says `max`, so this is not a finding against the contract; it is worth a note for P22.9-FE.
7. **Expert behaviour** changed only by the restores and the additive `p_set_peak` field. One visible side effect: after a sweep, `sub_networks` is empty again, so the next foreground solve re-runs the topology pass, which is the fresh-network behaviour. `test_a_foreground_solve_still_works_after_a_sweep` covers this and passes.

## Vacuity check (item 3)

I disabled the fix at runtime without touching any file. A pytest plugin in the scratchpad (`qa229/mutplug/qa229_mut.py`) monkeypatches `services.adequacy.sweep.preserve_bus_topology` to a `nullcontext`.

| Test (`tests/test_live_network_untouched.py`) | With the fix removed |
|---|---|
| `test_fmea_sweep_leaves_the_live_tables_equal` | **FAILED** (`buses/dc_mv changed: control PQ→Slack, generator ''→genset_1, sub_network ''→1`) |
| `test_fmea_sweep_restores_topology_columns_after_abort` | **FAILED** |
| `test_preserve_bus_topology_restores_on_an_exception` | **FAILED** |
| `test_frontier_sweep_restores_bus_topology` | **FAILED** |
| `test_coupling_loop_leaves_the_live_buses_equal` | **FAILED** |
| `test_contingency_sweep_restores_branch_sub_network` | **FAILED** |
| `test_eh_study_leaves_the_live_tables_equal` | passes (a guard by design) |
| `test_a_foreground_solve_still_works_after_a_sweep` | passes (a regression guard for the next solve) |
| `test_after_a_sweep_status_reports_dispatch_fresh_without_a_foreground_condition` | passes (a bug-2 state pin) |

**The guard is meaningful.** I ran a second mutation that makes `Network.copy` return `self` when called from `eh_study.py`, so the study solves the live object. `test_eh_study_leaves_the_live_tables_equal` then **FAILED** (`buses/dc_mv changed: sub_network ''→1, control PQ→Slack, generator ''→genset_1`). It compares whole Bus and Link rows with no exclusions. Those rows start blank or zero on the template (`control PQ`, `sub_network ''`, `generator ''`, `p_nom_opt 0`), so any live solve flips them.

**The `p_set_peak` tests** cannot pass without `_add_load_peak`: the key would be missing. They cover the static, time-series and null cases on both routes.

## Evidence

### Row 2 (re-run by the reviewer, cwd `pypsa-gui/backend`)

```
PYTHONPATH=/home/user/pypsa-eur:/home/user/pypsa-eur/pypsa-gui/backend /tmp/claude-0/venv/bin/python -m pytest tests/test_energy_hub_templates_e2e.py tests/test_energy_hub_review.py tests/test_guides.py tests/test_energy_hub_study_isolation.py tests/test_live_network_untouched.py tests/test_chat_tools_endpoint_map.py tests/test_chat_tools_dispatch.py tests/test_network_loads_peak.py tests/test_adequacy_abort.py -p no:cacheprovider -W ignore -q -o addopts=""
432 passed in 366.49s (0:06:06)   EXIT=0
```

### Row 5 (live uvicorn, port 8765, local mode, scratch app-data and projects dirs; transcript `scratchpad/qa229/row5.txt`; server stopped afterwards)

The steps:

1. `POST /api/projects/from_template/eh_datacenter` returned 200. Before any solve, the three buses read `control PQ`, `sub_network ''`, `generator ''`, and every generator read `control PQ`.
2. `POST /api/results/fmea_sweep` with the template's stress registry (2 scenarios) finished `done` in 13.6 s: `base_restored=True`, `base_restore_status=optimal`, 4 rows.
   - **Buses:** 0 rows differ.
   - **Links:** only `p_nom_opt` differs (`grid_import 0→40`, `site_transformer 0→80`).
   - **Generators:** `control` differs on `grid_supply` and `genset_1` (PQ→Slack). `p_nom_opt` differs on six generators. This is blocker B1.
   - **Status:** `dispatch: fresh`, `condition: null`, `solve_time: null`. That matches the bug-2 backend state.
3. `POST /api/results/eh_study` (all ten stages, recommended archetype `weak_flexible`, budget 60) finished `done` in 48.7 s. Buses, links and generators: 0 rows differ from the post-sweep snapshot.
4. **Overall S0 → S2:** buses equal (**pass**); links differ only in `p_nom_opt` (**pass**, per the accepted deviation); generators `control` changed (**B1**).
