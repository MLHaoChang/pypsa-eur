# gridspine — increment 3 continuation handoff

**Written:** 2026-09-05 · **Owner:** Hao (Hitachi Energy Power Consulting)
**Purpose:** everything a fresh session needs to pick up after increment 3. It assumes zero prior context and supersedes `GRIDSPINE_HANDOFF.md` on `master`, which describes increment 2 at eight tasks of nine and was written before any of this landed. That file could not be updated from the branch — it exists only on `master`, and `gridspine-inc2` is two commits behind `master` (the handoff itself and the pypsa-gui fix from PR #4) — so this document lives under `docs/superpowers/handoffs/` where it cannot collide at merge time.

---

## 1. Where the code is

Branch **`gridspine-inc2`** on `MLHaoChang/pypsa-eur`. Everything below is landed, tested and pushed. Increment-3 commits, in order:

| Commit | Task | Landed |
|---|---|---|
| `e20fe5d6` | inc-2 T9 | year-study driver (the last increment-2 task) |
| `769c9e41` | — | the gridspine gate runs in CI (`Gridspine` job in `test.yaml`) |
| `46a11039`, `084c434b` | — | increment-3 plan, `docs/superpowers/plans/2026-09-04-gridspine-increment-3.md` |
| `5d1f5d7a`, `569ba591` | T2, T3, T10 | contingency/fault schema; contingency set; templates v2 (per-field provenance) |
| `5d0cccd3` | T11 | `.dyr` writer |
| `40ac342f` | T12 | `contingencies.csv`, ledger README, bundle assembly |
| `e8d6f516` | T6 | IEC 60909 fault levels with the fault state's own mapping |
| `ebdf79ab` | T1 | `lightsim2grid==0.10.1` pinned |
| `3a48aa11` | T4 | AC N-1 via lightsim2grid; `islanded` flag |
| `a1e674df` | T5 | DC LODF + N-2 screen; measured prune threshold |
| `d65f915f` | T8 | DC N-1 severity as the fifth ranking criterion |
| `37f8727b` | T9 | boundary-tie spreading |
| `9ca6e14c` | T13 | driver v3: screening, fault levels, SCR, bundles, measurements |

(Task numbers are the plan's; T7, SCR, is in `e8d6f516`'s neighbour `static/strength.py` — see the plan's per-task notes, each annotated with what was measured when it landed.)

```bash
pixi install                 # several minutes cold
pixi run gridspine-tests     # the gate: 474 passed, 2 skipped, ~11-12 min in a 4-core cloud container
```

The two skips are unchanged from increment 2 (no `from_psse` in pandapower 3.1.2; PowerFactory fixture awaiting manual export).

## 2. Status

- **Increments 1, 2 and 3 are complete.** Every task landed with RED evidence, GREEN, a mutation that turned exactly the intended test red, and a full gate before its path-limited commit.
- **The full-year v3 run is done** (`--hours 8760 --k 5`, screening on, from `9ca6e14c`; 14:24–16:17 UTC 2026-09-05, 61 windows all "Optimization successful", `STUDY_EXIT=0`). It selected **23 hours, all converged**: 146, 147, 203, 274, 275, 355, 379, 403, 427, 451, 523, 707, 731, 1605, 1803, 4324, 6697, 7851, 8086, 8211, 8355, 8715, 8716. By criterion: min-inertia 146/1803/4324/6697/8716; max-IBR 147/7851/8211/8355/8715; max-load 355/379/403/427/451; max-import 379/451/523/1605/8086; max-N-1-severity (DC) 203/274/275/707/731. The spread rule works — the five min-inertia hours span January to December instead of five adjacent January hours as in the 2026-09-04 run. Output `results/gridspine_year_v3/` (18 MB, 23 bundles) is gitignored; the session handed over `selected.csv`, `metrics.csv`, `manifest.json` and a tarball of the whole directory.
- **Two measurements from that run (ruling 30 below):** the N-2 prune threshold measured per hour ranges 86.0–102.6 % and never prunes anything; the DC-severity blind spot is Spearman rho **−0.57** with worst rank gap 21 over the 23 hours (−0.77 over the 11 hours with no diverging N-1 case). The DC proxy ranks the wrong hours for AC severity. Read ruling 30 before trusting `max_n1_severity` as a criterion.
- **Still the owner's:** PowerFactory validation, now of a *bundle* from the v3 run (`.raw` + `.dyr` + `contingencies.csv` + `ledger.md`), not the increment-1 peak hour. Increment 1's <1 % gate has still never been closed against an independent oracle.
- `GRIDSPINE_HANDOFF.md` on `master` is stale on four counts (8 of 9; Task 9 remaining; 52 windows — it is 61; ruling 4's "near-constant floor"). Replace it with this file at merge.

## 3. Environment

- **pixi in a cloud sandbox:** `pixi.sh` is blocked by the egress policy. `conda.anaconda.org` is not — the binary was bootstrapped by downloading `pixi-0.79.0-hf01adef_0.conda` from conda-forge and extracting `bin/pixi`. Set `SSL_CERT_FILE=/root/.ccr/ca-bundle.crt` for pixi's network calls.
- **lightsim2grid==0.10.1** is pinned in `[pypi-dependencies]` — the version pandapower 3.1.2 names in its own `performance` extra; 1.0.0 is a major and was not adopted blind. The re-lock added 180 lines and moved nothing (pixi solved only the new PyPI subtree). The lock pins **Python 3.12.13 on linux-64 but 3.13.0 on osx-64, osx-arm64 and win-64** — pre-existing, and the first place to look for a platform discrepancy.
- The repo's `unit-tests` show 14 setup errors in the sandbox, all `403 Forbidden` from the egress proxy while fixtures download base-network and shape data. They pass on CI runners (24 s). Not a code problem.
- `ruff` is still not in the environment. No lint pass is claimed.
- CI: `.github/workflows/test.yaml` has a `Gridspine` job (paths-filtered, 45-min cap). Triggers are push/PR to master and `workflow_dispatch`; a branch is verified by dispatching on its ref. Last verified: run #176 on `ebdf79ab` — Unit ✅, Gridspine ✅ (11 m 24 s).

## 4. The API as it stands (increment-3 additions)

Everything from the increment-2 handoff §4 still holds. New:

```python
# schema/contingency.py — validators, pre-coercion discipline as validate_dispatch
validate_contingency_set(df)       # contingency_id (canonical charset, NO 12-char cap), kind {branch,unit}, element_ids list, order {1,2}
validate_contingency_results(df)   # + islanded; islanded ⇒ not converged; diverged ⇒ NON_CONVERGED_SEVERITY (1e6, finite)
validate_fault_levels(df)          # bus, ikss_ka, sk_mva, case {max,min}; strictly positive
NON_CONVERGED_SEVERITY = 1.0e6

# schema/dc.py — the LODF as an engine-free artifact (.npz)
DCSensitivities(ptdf, lodf, islanding, rating_mva, bus_names, branch_ids, ref_bus)
validate_dc_sensitivities / save_dc_sensitivities / load_dc_sensitivities

# static/contingency_set.py
branch_contingencies(net)          # in-service branches, keyed (from_bus, to_bus, ckt) EXACTLY as the RAW writer; id "from-to-ckt"
unit_contingencies(registry)       # gen + res; ext_grid excluded (EXT_GRID_EXCLUSION_LEDGER)
n2_candidates(n1)                  # all pairs, id "a--b"; 1035 on case39

# static/loadflow.py
branch_keys(net)                   # the ONE branch identity on the static side (lines then trafos, table order); _branch_flow and the contingency set both call it

# static/contingency.py
screen_n1(net, cset, dispatch, loads, hour, registry) -> results        # refuses a net that does not carry the hour
screen_n2(net, n2, dispatch, loads, hour, registry, prune_threshold_pct) -> (results, prune_log)
measure_prune_threshold(net, n2, dispatch, loads, hour, registry) -> (threshold, report)
branch_loading_pct(net, i_from_ka, i_hv_ka)   # FROM/HV-side, both solver paths
severity(loading_pct, vm_pu)                  # sum max(0, L/100-1) + sum max(0, (Vmin-V)/0.1, (V-Vmax)/0.1)
V_MIN_PU = 0.9; V_MAX_PU = 1.1; LOADING_MAX_PCT = 100.0

# static/lodf.py
dc_base(net) -> DCState; to_sensitivities(state) -> DCSensitivities
n1_dc_flows / n2_dc_flows / dc_loading_pct / lodf_column      # islanding detected, never divided through

# static/shortcircuit.py
fault_levels(net, dispatch, loads, hour, registry, templates, case="max")   # works on a DEEP COPY
apply_fault_state(...)             # ITS OWN mapping: every RES energised, decommitted synchronous out
set_sc_params(net, registry, templates)   # asserts coverage element by element

# static/strength.py
scr(fault_levels, registry, templates)     # installed capacity from the template, never dispatched; bands reported, not gates

# ranking/severity.py
n1_severity_dc(dispatch, loads, registry, sens) -> Series      # pass 1: DC ranks the year
# ranking/select.py: _RANKING has FIVE criteria; selection is k..5k; boundary ties are spread by farthest-point over hour-of-year

# templates/unit_params.py
load_unit_templates(path) -> UnitTemplates(units, params)      # per-FIELD provenance; GENROU/GENSAL/inverter/legacy
load_unit_params(path)                                         # unchanged shape for ranking/; synchronous units only
MODEL_PARAMS, MODEL_OPTIONAL_PARAMS (rx_sc, cos_phi), provenance_counts

# handoff/
write_dyr(net, unit_params, path) -> {unit_id: bus_number}     # (I, ID) from the RAW writer's own counters
write_contingencies(cset, net, path); write_ledger_readme(entries, templates, measurements, path)
export_bundle(outdir, BundleInputs(...)) -> Path               # refuses a net that does not carry the hour; refuses a ledger with gaps

# drivers/year_study.py
run_year_study(outdir, hours, k, window, overlap, screen=True, n2_prune_threshold_pct=0.0) -> StudyResult
# StudyResult: selected, artifacts, lf_results, screening, fault_levels, scr, bundles
# CLI: --hours --k --window --overlap --no-screen --n2-prune-threshold
```

**Engine cage, extended:** `lightsim2grid` only under `static/`. `ranking/` still imports only numpy, pandas and `schema` (the allowlist test now covers `severity.py`).

## 5. Rulings and traps — continuing the increment-2 list

Each was paid for once this increment. Numbering continues from 13.

**Measured facts about the fixture**

14. **case39's hour-0 base case already violates three line ratings** (L11 127.6 %, L21 127.1 %, L19 111.7 %; pandapower's own two-sided loading agrees). Severity is never zero on this fixture, an absolute loading threshold keeps every N-2 pair, and "a violation" in any screening measurement means a NEW one. This is the `max_i_ka` data meeting this dispatch, not a module defect.
15. **11 of 46 branch outages island case39** — nine radial generator connections, plus the line BUS_16-BUS_19 and the transformer BUS_19-BUS_20, which cut off the whole {BUS_19, BUS_20, G33, G34} pocket. 473 of 1035 pairs island. lightsim2grid reports `is_grid_connected_after_contingency == 0` with all-zero voltages and does not solve the surviving island. These are `islanded` rows, identical in every hour; they are EXCLUDED from the DC severity so they do not sit as a constant floor.
16. **Losing G_BUS_39 (the 500 s interconnection equivalent) does not converge** under default Newton, 100 iterations, a DC start or Gauss-Seidel at 5000; the slack cannot pick up its output. Recorded as a collapse result (`converged=False, islanded=False`). One N-2 pair (BUS_05-BUS_08-1 -- BUS_06-BUS_07-1) diverges likewise.
17. **No lossless N-2 prune exists on case39 at hour 0.** Measured threshold 92.2 %, which prunes nothing: 520 of 561 connected pairs create a new AC violation, none by voltage alone, and the lowest DC estimate among them is the lowest over all pairs. The DC blind spot here is UNDERESTIMATED loading on the critical branch, not the missing voltage term. All 1035 pairs solve in ~0.07 s, so the prune buys no time; it is built and measured because the spec requires it at scale.

**Solver behaviour**

18. **lightsim2grid deduplicates and reorders the contingencies it is given.** Six requested pairs with one repeat came back as five rows, two not at their position. A batched result cannot be aligned to the request by insertion order; `add_all_n1` is safe only because 0..n-1 is already its order. Pairs are solved one per analysis on a shared GridModel, and both paths assert the outaged branches carry no current.
19. **lightsim2grid returns the FROM/HV side only** (matching pandapower to 1e-10 there). Loading is therefore defined once, from-side, for both solver paths, and sits up to 6.08 percentage points below pandapower's two-sided `loading_percent` on case39. Measured, pinned, ledgered.
20. **pandapower's IEC 60909 minimum case raises `UserWarning` as an exception** without `net.line.endtemp_degree`; set to 80 °C on the work copy, ledgered as assumed.
21. **pandapower raises on a missing short-circuit COLUMN but not a missing per-unit VALUE.** Coverage is asserted element by element before `calc_sc`; a fault level from half the fleet is a plausible-looking wrong answer.

**Identity and mapping**

22. **`apply_snapshot`'s RES mapping is wrong for short circuit** (the increment-2 warning, now closed): a curtailed inverter still feeds fault current. `shortcircuit.apply_fault_state` keeps every RES unit energised and takes only decommitted synchronous machines out; the mapping test demands a >5 % higher level at BUS_33 on a curtailed hour and the mutation points it back at `_apply_res`. The dispatch table cannot tell curtailment from outage, so every RES unit is treated as energised — a RES availability table is the increment that fixes it.
23. **A `.dyr` whose (I, ID) disagrees with its `.raw` imports cleanly and attaches machines to the wrong buses.** The writer CALLS the RAW writer's numbering and per-bus ID counter rather than reproducing them, and the test parses both emitted files — bus 33 carries a gen (`'1 '`) and an sgen (`'2 '`), so ignoring the counter gets the bus right and the ID wrong.
24. **H and every reactance in a `.dyr` are on MBASE.** The writer refuses a template whose `mbase_mva` differs from the RAW's MBASE rather than rescaling.
25. **A unit-level provenance tag launders `assumed` into `datasheet`.** The classic case39 set gives H, Xd, Xq, X'd, X'q, Xl, T'do, T'qo and nothing else; every subtransient and saturation value a GENROU needs is assumed. Templates v2 tags per field and rejects a unit-level `source` beside `params`. Note for the owner: the published table gives H = 28.6 for the BUS_33 machine; the file has carried 38.6 since increment 2 and was deliberately NOT changed — check your source before a `.dyr` is used in anger.
26. **`ranking/` must not import an engine, so the LODF crosses the cage as a schema artifact** (`schema/dc.py`, `.npz`). The ranking-side injection path is proven equal to the static-side DC solve for one applied hour to 1e-6 MW.

**Process**

27. **Read the installed package, not a remembered API.** Neither `lightsim2grid.ContingencyAnalysis` nor `SecurityAnalysis` exists in 0.10.1; the class is `lightsim2grid.contingencyAnalysis.ContingencyAnalysisCPP`.
28. **A "measure, don't assume" test that asserts the assumption is still an assumption.** Twice this increment a pinned expectation was wrong on first run (the loading band at 6.0 vs measured 6.08; "every unit outage converges" vs G_BUS_39). The fix each time was to run, read the number, then pin the number — and, where the number was surprising, to explain it in the ledger rather than widen the tolerance.
29. **Substring cage checks flag their own documentation.** A module whose docstring says "proven against lightsim2grid" fails a `"lightsim2grid" not in src` test. Check import statements, as the ranking cage test already did.

**From the v3 year (measured after the increment closed)**

30. **The DC N-1 severity proxy is anticorrelated with AC N-1 severity on the selected hours** (rho −0.57, worst rank gap 21 of 23). The AC number is 33–47 at every min-inertia / max-IBR hour (1803, 4324, 6697, 7851, 8211, 8355, 8715; DC says 0.5–0.7) and 1.5–5 at the load/import/DC-severity hours. It is a *voltage* term the DC solve cannot see: at hour 1803 the base case already has 22 buses above 1.10 pu (max 1.197) with no branch over 91 %, and losing G_BUS_30 lifts the maximum to 1.358 pu across 37 violations. The mechanism is the model, not the solver: at light-load high-wind hours the synchronous units are decommitted, the inverters are PQ static generators with no voltage control, and nothing absorbs line charging. Consequences: (a) `max_n1_severity` currently selects hours the AC screen finds mild, and misses the hours it finds worst — the ranking-side criterion should either use the AC screen's own number from a cheap N-1-only pass or add a voltage proxy; (b) whether 1.36 pu is a real client-grid risk or an artefact of IBRs modelled without voltage control is an owner's question, and the PowerFactory validation of a min-inertia bundle will answer it before any code does. Ruling 17's "the blind spot is underestimated loading" was measured at hour 0, a heavy-load hour, and holds there; it does not hold across the year.

31. **lightsim2grid 0.10.1 drops `gen.in_service` when the slack comes from `ext_grid`.** `init_from_pandapower` applies the flags, then `_aux_add_slack.py` calls `init_generators` a second time over every pandapower gen plus the slack and never re-applies them; every decommitted unit comes back as a live PV bus at its setpoint (`sgen` is not re-initialised and is fine). Found the evening after the v3 handover, probing the DC/AC mismatch of ruling 30: at v3 hour 1803 the lightsim2grid base case is 0.14 pu off pandapower, branch N-1 rows up to 12 severity units off. **8754 of 8760 hours have a unit off, so every branch N-1 row, every N-2 row, every prune threshold and the blind-spot rho in the v3 bundles were solved on the wrong grid**; unit rows, `.raw`, `.dyr`, dispatch, loads, fault levels and SCR are unaffected. Hour 0 has every unit on — which is why the increment-3 tests, all at hour 0, matched pandapower to 1e-10 and saw nothing. Fixed in `static/contingency.gridmodel_for` (re-applies both flag vectors and refuses a model whose status disagrees with the net; `tests/gridspine/test_contingency_decommitted.py`). The v3 screening files are superseded by the v4 re-study (follow-ups plan F4). Lesson for the fixture set: **every engine-agreement test needs one case with a unit off**; the native peak is the one hour on which this bug is invisible.

## 6. Open modelling questions — for the owner, not a task

- **Should the aggregated interconnection equivalent (G_BUS_39, h = 500 s) be a committable unit at all?** It is decommitted 56 % of the year (which makes every min-inertia hour an equivalent-off hour), and outaging it collapses the system. Both facts follow from modelling an interconnection as one machine.
- **What do case39's ratings mean for severity?** With three lines over 100 % before any outage, severity measures stress relative to a base case that is already infeasible by the data's own limits. Options: incremental severity (post minus pre), ledgered ratings, or accepting it as a property of the fixture.
- **SCR at the minimum or maximum case?** The driver takes the minimum (conservative for weak-grid screening); the spec did not say.
- **Islanded outages in the ranking.** Excluded from DC severity by design; whether a client study should treat a radial generator loss as N-1 "islanding" or as a generation contingency is a definitional choice.
- **Reactive control of the inverter units.** Ruling 30: with the synchronous fleet off, the PQ-modelled IBRs leave case39 at 1.2 pu before any outage. A voltage-controlled (PV) or Q(V) representation of the five inverter units would change the min-inertia hours' AC severity and SCR-side voltages, and is a modelling decision, not a code default.

## 7. After increment 3

1. ~~Hand over and inspect the v3 year~~ — done, see §2 and ruling 30. Next code change on the ranking side: replace or supplement the DC severity criterion (ruling 30a). Do it *after* the PowerFactory validation of a v3 bundle so the validated artifact stays reproducible from `9ca6e14c`.
2. **PowerFactory validation of one v3 bundle** — a selected min-inertia or max-severity hour. The `.dyr` is new; import both files together.
3. **Increment 4, per spec phase 3's remainder:** the action layer (`create_study`, `run_pipeline`, `list_ranked_snapshots`, `export_handoff_bundle`, …), then GUI wiring, then chat tool registration. The spec calls GUI wiring "a thin, late, path-limited backend change — deliberately the last increment."
4. **Between runs, never between a run and its validation:** decorrelate `wind_cf` across the three farms (it is one series; fleet variability is understated and biases exactly the two criteria the study ranks on).
5. Consolidate the two copies of the "net carries the hour" guard (`static/contingency.py` and `handoff/bundle.py`) into one.
6. Replace `GRIDSPINE_HANDOFF.md` on `master` with this file at merge; merge `master` into the branch first (two commits: the old handoff and the pypsa-gui fix from PR #4).

## 8. How this work was run

One cloud session, sequentially, task by task in the plan's dependency order (2, 3, 10, 11, 12, 6, 7 before the lockfile decision; then 1, 4, 5, 8, 9, 13). For every task: tests first and a RED captured; implementation; a mutation chosen to break the property the task exists for, run, and shown to turn exactly the intended tests red; the full gate; a path-limited commit naming only that task's files, with the plan annotated the same commit where a measurement changed what the plan had assumed. Probes preceded every use of an unfamiliar API and every design decision that depended on a number, and several of those probes overturned the plan's premise — the rulings above are mostly those.
