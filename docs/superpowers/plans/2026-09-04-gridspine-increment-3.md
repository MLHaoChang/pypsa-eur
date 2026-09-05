# gridspine Increment 3 Implementation Plan — Contingency Screening and the Handoff Bundle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a ranked year into a defensible contingency study and a complete handoff. N-1 severity becomes a ranking criterion over the whole year; the selected snapshots get full AC N-1 and LODF-pruned N-2, IEC 60909 fault levels and an SCR pre-check; and the handoff stops being one `.raw` and becomes a bundle a dynamics engineer can open — `.raw`, `.dyr`, `contingencies.csv` and a ledger README that says which number was measured and which was invented.

**Architecture:** Extends `gridspine/` in place. New: `static/contingency.py`, `static/shortcircuit.py`, `static/strength.py`; `schema/contingency.py`; `handoff/dyr_writer.py`, `handoff/contingencies.py`, `handoff/bundle.py`; `templates/` grows past H. Stage boundaries stay validated artifacts. Engine cage extended: **`lightsim2grid` is an engine and lives only under `static/`.**

**Tech Stack:** as increment 2 (pypsa 1.1.2, pandapower 3.1.2 via pypi, highspy, pandas, pyyaml) **+ lightsim2grid** (task 1) and `pandapower.shortcircuit` (already present, unused).

**Spec:** `docs/superpowers/specs/2026-08-27-gridspine-design.md` — phase 3, and the `static/` + `handoff/` rows of the package layout.
**Prior art:** increments 1 and 2; `GRIDSPINE_HANDOFF.md` §6 rulings are binding. Carried forward without re-litigation: canonical-ID allowlist `[A-Za-z0-9_-]` **fullmatch**; never `pp.from_json`/`pp.from_pickle` on a client file; `mip_rel_gap=0.01` is an optimality tolerance, not tuning; frozen statuses rounded in `_rounded_status`.

### Three findings from the 8760 h run that this increment must not repeat

The full-year study (2026-09-04, 61 windows, 1 h 52 m, all optimal) produced three facts the earlier 336 h runs could not:

1. **`inertia_excl_equiv_mws` saturates.** 96 of 8760 hours tie at exactly 7780 MW·s. `select_snapshots` breaks ties chronologically, so the five "thinnest" hours were simply the five earliest of 96 — all in early January. Task 9 addresses this; any new criterion must be checked for the same failure.
2. **Handoff ruling 4's premise is wrong.** It calls G_BUS_39's ~50 000 MW·s "a near-constant floor under every hour". Over the full year the equivalent is **decommitted in 4892 of 8760 hours (56 %)**. The ruling's conclusion (rank on the equivalent-excluded column) survives and is better justified than before; its stated reason does not. Whether an aggregated interconnection equivalent should be a committable unit at all is an open modelling question — **do not resolve it inside a task; raise it.**
3. **Windows are not uniform.** One window took ~11 minutes against a ~1.3 min average over the preceding 29, grinding at a 1.10 % gap. Anything this increment adds to the per-window or per-snapshot path multiplies against that variance.

## Global Constraints

- ALL commands via `pixi run …`; test gate `pixi run gridspine-tests`; TDD Evidence (RED+GREEN actual output) per task report; mutation check where a task names one. The gate is now also CI-enforced (`.github/workflows/test.yaml`, job `Gridspine`).
- Execute in a dedicated worktree; path-limited commits with `git commit --dry-run -- <paths>` untracked check.
- **Engine cage, extended:** `pypsa` only under `producers/`; `pandapower` **and `lightsim2grid`** only under `ingest/`, `static/`, `handoff/`; `schema/`, `ranking/`, `templates/`, `readback/` import neither. The cage test in `test_ranking.py` is an ALLOWLIST — widen it deliberately or not at all.
- **Runtime discipline: no test solves more than 336 h, and no test runs AC contingency analysis on more than 4 snapshots.** A test over >120 s is a defect. The full-year DC pass is CLI-only.
- Determinism: contingency enumeration is sorted and closed-form; same inputs → byte-identical `contingencies.csv`.
- Every fault-level, reactance and time constant added to `templates/` carries `measured|datasheet|assumed`. The `.dyr` is a report artifact: an untagged number must not reach it.
- Model routing: **[Opus]** implement / **[Opus, Fable review]** master reviews line-by-line / **[FABLE]** Fable implements.

### Locked decision: DC ranks the year, AC verifies the selection

"N-1 severity joins the ranking" is circular as written — severity decides which snapshots to study, but a full AC N-1 sweep over 8760 hours x 46 branches (35 lines + 11 transformers) is 402 960 load flows and is not affordable at any point in this pipeline.

**Resolution: two passes with different fidelities.**

- **Pass 1 (whole year, DC).** LODF is a matrix identity over a fixed topology: post-outage DC branch flows for every single outage are one dense multiply per hour. That is affordable for 8760 hours and yields `n1_severity` as a ranking criterion alongside the increment-2 metrics.
- **Pass 2 (selected snapshots only, AC).** Full AC N-1 and LODF-pruned N-2 via lightsim2grid, IEC 60909 and the SCR pre-check run ONLY on the hours pass 1 selected.

The honest cost, which must be stated in the ledger and the report: **the ranking's severity term is a DC estimate.** DC misses voltage collapse and reactive limits entirely, so an hour that is dangerous only in AC can be missed by pass 1 and never reach pass 2. Task 8 asserts the size of that blind spot on the 39-bus fixture (compare DC-ranked severity against AC severity on a sample of hours) rather than assuming it away.

---

### Task 1: Environment — lightsim2grid **[Opus]**

**Files:**
- Modify: `pixi.toml` (`[pypi-dependencies]`), `pixi.lock`
- Test: `tests/gridspine/test_contracts.py` (extend the env-wiring check)

**Interfaces:**
- `lightsim2grid` currently appears in `pixi.lock` ONLY as an optional extra of pandapower (`extra == 'performance'` / `'all'`); `import lightsim2grid` fails in `default`. Add it as a pinned `[pypi-dependencies]` entry, same discipline as `pandapower = "==3.1.2"` — an exact pin, because a range resolved differently per platform is the defect that pin exists to prevent.
- **Re-locking is not a one-line change, and `pixi run sync-locks` is not the tool for it.** `sync-locks` runs `pixi install -e default` and then EXPORTS explicit specs to `envs/`; it does not re-solve. The solve happens when `pixi.toml` changes. And the workspace sets `exclude-newer = "7d"` with `0d` overrides for linopy/pypsa/atlite, so that window is relative to the moment of solving: re-locking today moves packages that have nothing to do with lightsim2grid, across all four platforms of a 45 000-line lock.
- Therefore: treat this as a lockfile change, not a dependency addition. Inspect the resulting `pixi.lock` diff and report its scope BEFORE committing; run `unit-tests` and `integration-tests`, not just `gridspine-tests`, because the environment under them moved too. The repo has `.github/workflows/update-lockfile.yaml` — check whether the lock update belongs in that managed process rather than in this task.
- **Confirm the entry point against the INSTALLED version and record it in the task report.** Do not write tasks 4–5 against a remembered signature; read the installed package.
- **CONFIRMED on 0.10.1 (2026-09-05):** neither remembered name exists at top level — there is no `lightsim2grid.ContingencyAnalysis` and no `SecurityAnalysis`. The class is **`lightsim2grid.contingencyAnalysis.ContingencyAnalysisCPP`** (a C++ binding: `add_n1(id)`, `add_all_n1()`, `add_nk(ids)`, `compute(Vinit, max_iter, tol)`, `get_flows()`, `get_voltages()`, `is_grid_connected_after_contingency(...)`, `change_solver(SolverType)`), constructed on a `GridModel` from **`lightsim2grid.gridmodel.init_from_pandapower(pp_net)`**. Branch ids in that API are lightsim2grid's own ordering, NOT the RAW triple — task 4 must map through `branch_keys` and prove it with the same 1:1 test the contingency set has.
- **Lock outcome (measured):** the re-solve added 180 lines and removed none — `LightSim2Grid 0.10.1` (four platform wheels) plus its runtime deps `pybind11 3.1.0` and `pip 26.2.1`; no existing package moved. pixi solved only the new PyPI subtree against the existing lock, so the `exclude-newer` re-lock this task warned about did not occur here. It remains the risk for any future edit that invalidates the conda half.
- **Pre-existing, not introduced here:** the lock pins **Python 3.12.13 on linux-64 but 3.13.0 on osx-64, osx-arm64 and win-64**, which is why the non-linux lightsim2grid wheels are cp313. Any "works on my machine" gridspine discrepancy between platforms should start there.

- [ ] **Step 1: failing test** — extend the env-wiring check to import lightsim2grid and assert a version; RED before the pin.
- [ ] **Step 2: pin + re-solve. Step 3: GREEN on all four platforms in the lock. Step 4: report the resolved version AND the confirmed contingency entry point. Step 5: commit.**

---

### Task 2: Schema — contingency, fault and severity boundaries **[Opus, Fable review]**

**Files:**
- Create: `gridspine/schema/contingency.py`
- Test: `tests/gridspine/test_contingency_contract.py`

**Interfaces:**
- `validate_contingency_set(df)` — columns `contingency_id`, `kind ∈ {branch, unit}`, `element_ids` (list), `order ∈ {1,2}`. `contingency_id` obeys the canonical charset by **fullmatch** and is unique; `element_ids` non-empty, length == `order`, every id present in the registry (checked by the caller, not here — `schema/` stays engine-free).
- `validate_contingency_results(df)` — columns `contingency_id`, `hour`, `converged`, `max_branch_loading_pct`, `min_vm_pu`, `max_vm_pu`, `n_violations`, `severity`. Pre-coercion guards in the increment-2 style: `converged` exactly bool, `hour` integral, loadings finite and ≥ 0, `severity` finite and ≥ 0.
- `validate_fault_levels(df)` — columns `bus`, `ikss_ka`, `sk_mva`, `case ∈ {max, min}`; positive and finite.
- A **non-convergent contingency is a row with `converged=False`, not a missing row.** Increment 1's lesson generalises: an absent row and a survived contingency are indistinguishable downstream, and the ranking must treat non-convergence as maximal severity (spec: "Non-convergent LF and infeasible UC are *results*").

- [ ] **Step 1: failing tests** — one deliberately-broken artifact per validator that MUST be rejected: duplicate `contingency_id`; `order=2` with one element; a `severity` of NaN; a fault level of 0 kA; `converged` as the string "False". Assert the fractional-hour and dtype-coercion ordering the increment-2 validators established.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: drop the `order`/`len(element_ids)` cross-check → the N-2 test fails. Step 5: commit.**

---

### Task 3: Contingency set from the registry **[Opus]**

**Files:**
- Create: `gridspine/static/contingency_set.py`
- Test: `tests/gridspine/test_contingency_set.py`

**Interfaces:**
- `branch_contingencies(net) -> DataFrame` — one N-1 entry per in-service line and transformer, keyed by the SAME `(from_bus, to_bus, ckt)` triple the RAW writer stamps and `LFResult.branch_flow` carries. Reuse the increment-2 keying; do NOT invent a second branch identity. A cross-module test asserting the key sets match 1:1 is REQUIRED — this is the exact gap task 8 of increment 2 closed for branch flows, and it reopens here.
- `unit_contingencies(registry) -> DataFrame` — one N-1 entry per `kind ∈ {gen, res}` unit. `ext_grid` is excluded and the exclusion is ledgered: outaging the slack leaves no reference bus, so it is a different study (islanding), not a contingency.
- `n2_candidates(branch_contingencies) -> DataFrame` — all unordered pairs, `order=2`. On case39 that is C(46,2) = 1035 exactly, which is why task 5 prunes rather than solves.
- Deterministic ordering: sorted by `contingency_id`, and `contingency_id` derived from the element ids so it is stable across runs and platforms.

- [ ] **Step 1: failing tests** — counts on `case39_res` (branches, units, pairs); every id passes `validate_contingency_set`; out-of-service branches excluded but the id scheme unchanged by their absence; the writer↔set key-consistency test.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN. Step 5: commit.**

---

### Task 4: N-1 full AC screening **[Opus, Fable review]**

**Files:**
- Create: `gridspine/static/contingency.py`
- Test: `tests/gridspine/test_contingency.py`

**Interfaces:**
- `screen_n1(net, contingencies, hour) -> DataFrame` (validated by `validate_contingency_results`) — applies each outage to a COPY of the snapshot-applied net, runs AC LF, records loading/voltage extremes and violation counts. Non-convergence → `converged=False` and maximal severity, never an exception and never a dropped row.
- Backend: lightsim2grid, via the entry point task 1 confirmed. **The net handed in must already carry the snapshot** (`apply_snapshot` first) — a screen run against case39's native peak is the increment-1 defect wearing a new hat.
- `severity(row)` is defined ONCE, in a module docstring the report quotes: a scalar combining overload depth and voltage excursion, monotone in both, and **infinite-substitute (a large finite sentinel, not `inf`) for non-convergence** so it sorts correctly and survives CSV round-trip.
- The net must be restored between contingencies. Prove it: assert the base-case LF after a screen equals the base-case LF before it, element by element.

- **LANDED 2026-09-05 (`3a48aa11`), with measured facts a follow-on must not re-derive:** (1) lightsim2grid matches pandapower to 1e-10 in |V| and 5e-10 in from-side kA on connected cases but returns the FROM side only — loading is defined once, from/HV-side, for both solver paths, and sits up to 6.08 percentage points below pandapower's two-sided `loading_percent` on case39. (2) 11 of the 46 branch outages ISLAND the grid (nine radial generator connections; the line BUS_16-BUS_19 and the transformer BUS_19-BUS_20 cut off the whole BUS_19/BUS_20 pocket); lightsim2grid does not solve the surviving island. The results contract gained an `islanded` flag (islanded ⇒ not converged) so this topology fact, identical in every hour, is not read as a divergence and does not sit as a constant floor under task 8. (3) Losing `G_BUS_39`, the interconnection equivalent, does not converge under any solver setting tried; recorded as a collapse result. (4) case39's hour-0 base case already carries THREE lines over 100 % (L11 127.6, L21 127.1, L19 111.7), so severity is never zero on this fixture — that is the ratings data, not the module.
- [ ] **Step 1: failing tests** — a 4-snapshot fixture; a hand-picked outage with a known post-contingency overload; a deliberately islanding outage recorded as `converged=False`; the net-restoration test; the "screen without `apply_snapshot`" case caught (see mutation).
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + MUTATION REQUIRED: skip `apply_snapshot` before the screen and confirm a test fails. Increment 2 showed the load total alone is blind at the annual peak — assert on a per-contingency result, not a base-case aggregate. Step 5: commit.**

---

### Task 5: N-2 via DC-LODF prune, AC verify the survivors **[FABLE]**

**Files:**
- Create: `gridspine/static/lodf.py`; modify `gridspine/static/contingency.py`
- Test: `tests/gridspine/test_lodf.py`

**Interfaces:**
- `ptdf(net) -> ndarray`, `lodf(net) -> ndarray` — DC sensitivities over the in-service topology, bus-name-keyed on the way out. Pure numpy given the susceptance matrix; the radial/islanding case (LODF denominator → 0) must be detected and reported, not divided through.
- `screen_n2(net, candidates, hour, prune_threshold) -> DataFrame` — DC-estimate every pair, keep those whose estimated post-outage loading exceeds the threshold, AC-verify ONLY those, and record the prune in the result (`pruned=True` rows carry the DC estimate and `converged=NA` semantics made explicit in the schema).
- **The prune threshold is a ledgered assumption, and the plan does not fix its value.** Measure it: on the 39-bus fixture, run a full AC N-2 once, offline, and report the threshold that loses no genuine violation. A threshold chosen for speed without that measurement is exactly the "safety substitution" trap in handoff ruling 11.
- Validate the LODF against a brute-force AC reference on a small case — the identity must be proven, not asserted.

- **LANDED 2026-09-05, and the measurement came out the other way.** All 1035 pairs solve in lightsim2grid in ~0.07 s, so on case39 the prune buys no time; it is built and measured because the spec requires it at scale. Two deviations: pruned pairs go to a separate prune log (a results row means "solved"; a half-estimated row would be read as an outcome downstream), and pairs are solved ONE PER ANALYSIS — lightsim2grid deduplicates and reorders the contingencies it is given (six requested pairs with a repeat came back as five rows, two not at their position), so a batched result cannot be aligned by insertion order; task 4's `add_all_n1` is safe only because 0..n-1 is already its order, and both paths now assert the outaged branches carry no current. **Measured threshold: 92.2 %, which prunes NOTHING.** 520 of 561 connected pairs create a violation the base case did not have, none by voltage alone, and the lowest DC estimate among them is the lowest over all pairs; those pairs sit at 92 % in DC with zero predicted new overloads while AC finds one. The DC blind spot on this fixture is UNDERESTIMATED loading on the critical branch, not the missing voltage term — and because the base case already violates, the prune metric had to become the DC max loading over branches not already over their rating. That number is the `n2_prune_threshold` the ledger README declares; task 13 passes it. One connected pair (BUS_05-BUS_08-1 -- BUS_06-BUS_07-1) diverges in AC and is recorded as such.
- [ ] **Step 1: failing tests** — LODF row against brute-force single-outage DC flows; an islanding pair raises rather than returning `inf`; prune keeps every pair the full AC run flagged on the fixture.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: drop a term from the LODF denominator → the brute-force comparison fails. Step 5: report the measured threshold. Step 6: commit.**

---

### Task 6: IEC 60909 short circuit — and its OWN status mapping **[Opus, Fable review]**

**Files:**
- Create: `gridspine/static/shortcircuit.py`
- Test: `tests/gridspine/test_shortcircuit.py`

**Interfaces:**
- `fault_levels(net, dispatch, hour, registry, case="max") -> DataFrame` (validated by `validate_fault_levels`) — `pandapower.shortcircuit.calc_sc`, three-phase, IEC 60909, per bus.
- **THE TRAP, recorded in `_apply_res`'s docstring and binding here: `apply_snapshot` maps a curtailed RES unit to `in_service=False`. That is correct for load flow — a zero-injection PQ element and an absent one are the same node equation — and WRONG for short circuit.** A curtailed inverter is still energised, still synchronised, and still contributes fault current. `in_service=False` deletes it from the fault calculation and understates the contribution at exactly the buses the study is about.
- Therefore this module needs **its own status → element mapping**, `apply_fault_state(net, dispatch, hour, registry)`, which keeps curtailed RES in service with its fault-current parameters and only de-energises units genuinely disconnected. Do not reuse `_apply_res`. Do not "fix" `_apply_res` either — load flow needs it as it is.
- Short-circuit parameters (`k`, `rx`, `sn_mva`, generator `vn_kv`/`xdss_pu`) come from `templates/`, tagged. pandapower will silently skip an element with missing SC data: **assert element coverage explicitly** — a fault level computed from half the fleet is a plausible-looking wrong answer.

- [ ] **Step 1: failing tests** — a hand-checkable 3-bus fault level; coverage assertion fails when a unit lacks SC data; **the mapping test: an hour with a curtailed RES unit yields a HIGHER fault level than the same hour run through `_apply_res`'s mapping, and the difference is non-trivial.**
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + MUTATION REQUIRED: point `fault_levels` at `_apply_res` and confirm the mapping test fails. A green test here that survives that substitution is worthless. Step 5: commit.**

---

### Task 7: SCR pre-check **[Opus]**

**Files:**
- Create: `gridspine/static/strength.py`
- Test: `tests/gridspine/test_strength.py`

**Interfaces:**
- `scr(fault_levels, registry, net) -> DataFrame` — per RES bus, `sk_mva / p_rated_mva` of the inverter-based capacity at that bus, with the conventional weak/strong bands as REPORTED thresholds, not pass/fail gates.
- Scope discipline: **plain SCR only.** WSCR, ESCR, the impedance screen and the RoCoF→EMT flag are spec phase 4. A pre-check that quietly becomes a grid-strength study is scope creep with a compliance-shaped tail.
- Uses `p_mw` INSTALLED capacity from `RES_LEDGER`, not the dispatched hour — SCR is a network-strength property, and dividing by a curtailed output would report a weak bus as strong. Ledger the choice.

- [ ] **Step 1: failing tests** — hand-computed SCR on the fixture; installed-vs-dispatched asserted explicitly (a curtailed hour and a full-output hour give the SAME SCR); a bus with no RES absent from the result rather than `inf`.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN. Step 5: commit.**

---

### Task 8: N-1 severity joins the ranking — DC over the year **[Opus, Fable review]**

**Files:**
- Create: `gridspine/ranking/severity.py`; modify `gridspine/ranking/metrics.py`, `gridspine/ranking/select.py`
- Test: extend `tests/gridspine/test_ranking.py`

**Interfaces:**
- `n1_severity_dc(dispatch, loads, lodf_matrix, base_flows) -> Series` indexed by hour — the pass-1 DC estimate from the locked decision above. `ranking/` imports NO engine, so the LODF matrix arrives as a plain array computed under `static/` and handed across the boundary as an artifact, exactly like every other stage.
- `snapshot_metrics` gains `n1_severity_dc`; `_RANKING` gains `("max_n1_severity", "n1_severity_dc", "max")`. Selection therefore spans FIVE criteria and returns between k and **5k** rows — every caller, docstring and test asserting the 4k bound must be updated. `test_year_study.py` asserts `K <= len(sel) <= 4 * K`; it will go red, and that is the test doing its job.
- **Quantify the DC blind spot.** On a sample of hours, compare the DC severity ranking against AC severity from task 4 and report the rank correlation and the worst disagreement. Publish the number in the ledger; do not assert a threshold the fixture happens to meet.

- [ ] **Step 1: failing tests** — hand-computed DC severity on a 4-hour fixture; the 5k bound; a hour extreme in severity alone is selected with exactly that reason; the DC-vs-AC comparison test reports (does not gate).
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: zero the LODF contribution → severity becomes constant and the selection test fails. Step 5: commit.**

---

### Task 9: Selection — break the floor tie **[Opus]**

**Files:**
- Modify: `gridspine/ranking/select.py`
- Test: extend `tests/gridspine/test_ranking.py`

**Interfaces:**
- Finding 1 above: 96 hours tie at the inertia floor and the chronological tie-break hands back the five earliest, all in one January week. The current behaviour is deliberate ("Determinism matters more than the choice itself") and that ruling stands — determinism is NOT negotiable — but "deterministic" and "chronologically clustered" are not the same requirement.
- Add a deterministic spreading tie-break: among hours tied on the ranked column, prefer maximal separation in hour-of-year (a closed-form farthest-point pass over the tied set, seeded at the earliest hour). Same input → same output; no RNG; no seed to carry.
- **This changes which snapshots a study selects.** It invalidates the 2026-09-04 selection and any PowerFactory validation performed against it. Sequence it BEFORE the next full-year run and say so in the report.

- [ ] **Step 1: failing tests** — a fixture with a wide tie asserts the selected hours are spread, not the first k; determinism across two calls; a fixture with NO ties selects exactly as before (regression).
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: revert to `[:k]` and confirm the spread test fails. Step 5: commit.**

---

### Task 10: Templates v2 — dynamic parameters beyond H **[Opus, Fable review]**

**Files:**
- Modify: `gridspine/templates/unit_params.py`, `gridspine/templates/data/case39_units.yaml`
- Test: extend `tests/gridspine/test_unit_params.py`

**Interfaces:**
- The template carries FOUR fields per unit (`h_s`, `mbase_mva`, `source`, `include_in_inertia`). A GENROU `.dyr` record needs roughly fifteen — `xd`, `xq`, `xd_p`, `xq_p`, `xd_pp`, `xl`, `t_do_p`, `t_qo_p`, `t_do_pp`, `t_qo_pp`, `h`, `d`, `s1`, `s12`. Task 11 cannot be written until they exist.
- Per-field provenance, not per-unit: the classic case39 set is `datasheet` for the machine reactances but the saturation coefficients are commonly `assumed`. A single unit-level tag would launder one into the other. Restructure so `source` is per field, keeping `load_unit_params()`'s current return shape working for `ranking/` (which only reads `h_s`/`mbase_mva`/`include_in_inertia`).
- Model class per unit (`GENROU`/`GENSAL`/inverter) selects the record layout in task 11; RES units get an inverter class with fault-current parameters (task 6 consumes these).

- [ ] **Step 1: failing tests** — the increment-2 tests must still pass unchanged (that IS the compatibility assertion); a per-field `assumed` tag surfaces in the provenance count; a unit missing a field its model class requires raises rather than defaulting.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: default a missing reactance to 0 → the required-field test fails. Step 5: commit.**

---

### Task 11: `.dyr` writer **[FABLE]**

**Files:**
- Create: `gridspine/handoff/dyr_writer.py`
- Test: `tests/gridspine/test_dyr_writer.py`

**Interfaces:**
- `write_dyr(net, unit_params, path) -> dict[unit_id, bus_number]` — PSS/E dynamic data records, one per machine, model class from the template.
- **Bus numbers MUST come from the same assignment `write_raw` uses.** A `.dyr` whose bus numbering disagrees with its `.raw` is worse than no `.dyr` — it imports cleanly and attaches machines to the wrong buses. A cross-module test asserting the two numberings are identical is REQUIRED, in the spirit of increment 2's `_bus_numbers` local reproduction.
- Increment 1's RAW writer deviates from the naive form in 12 places, each verified against the v33 field layout. Expect the same for `.dyr`: write the expected record text for a toy machine BEFORE the code, field by field, and cite the layout in comments.
- Names are emitted unescaped, same as the RAW writer — the `[A-Za-z0-9_-]` fullmatch guard is what stands between a unit id and a forged record.

- [ ] **Step 1: failing tests** — expected `.dyr` text for a 2-machine toy written by hand first; the raw↔dyr bus-numbering consistency test; an unknown model class raises.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: perturb one bus number in the dyr path → the consistency test fails. Step 5: commit.**

---

### Task 12: Bundle — contingencies.csv, ledger README, assembly **[Opus, Fable review]**

**Files:**
- Create: `gridspine/handoff/contingencies.py`, `gridspine/handoff/bundle.py`
- Test: `tests/gridspine/test_bundle.py`

**Interfaces:**
- `write_contingencies(contingency_set, path)` — the set in the form the dynamics tool reads, keyed on the same branch triple as everything else.
- `write_ledger_readme(ledger, path)` — the assumptions ledger as prose a client reads, not a JSON dump. Every `assumed` entry appears; the provenance counts appear; the DC-severity blind spot from task 8 appears with its measured number; the N-2 prune threshold from task 5 appears with the measurement that justified it.
- `export_bundle(outdir, hour, …) -> Path` — one directory per selected hour containing `.raw`, `.dyr`, `contingencies.csv`, `ledger.md`, plus the LF and screening results. This is the artifact the GUI will later surface as a download (spec: "Handoff bundle surfaces as a download"), so it must be complete standing alone — a bundle that needs the study directory to be intelligible is not a handoff.
- **A bundle whose ledger omits an `assumed` value must fail to build.** The ledger is the product; making it optional makes it decorative.

- [ ] **Step 1: failing tests** — a bundle for one hour contains every expected file; an `assumed` entry deleted from the ledger input → build raises; the README names every RES site and every per-field `assumed` tag.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + mutation: drop one ledger section → the completeness test fails. Step 5: commit.**

---

### Task 13: Driver v3 — screening and bundles in the year study **[Opus, Fable review]**

**Files:**
- Modify: `gridspine/drivers/year_study.py`
- Test: extend `tests/gridspine/test_year_study.py`

**Interfaces:**
- The chain gains, after selection: per selected hour → `apply_snapshot` → `run_lf` (unchanged) → `screen_n1` → `screen_n2` → `fault_levels` (via `apply_fault_state`) → `scr` → `export_bundle`. Pass-1 DC severity is computed once for the year, before selection.
- `run_year_study(outdir, hours, k, window, overlap, screen=True)` — the flag exists so the increment-2 behaviour stays reachable and testable, NOT so screening can be quietly skipped in production. Default `True`.
- `StudyResult` gains `screening: dict[int, DataFrame]` and `bundles: dict[int, Path]`.
- Stage names gain `screening` and the manifest records per-hour violation counts, so the manifest alone answers "which selected hour was worst".
- **Runtime:** the existing module-scoped 336 h fixture stays ONE solve; screening in tests is capped at ≤4 snapshots and a reduced contingency set. If the file passes 120 s, cut the contingency set, not the assertions.

- [ ] **Step 1: failing tests** — the 4k→5k bound update; bundles exist per selected hour; a non-convergent contingency reaches the manifest as a recorded result; `screen=False` reproduces increment 2's artifacts exactly.
- [ ] **Step 2: RED. Step 3: implement. Step 4: GREEN + full gate. Step 5: run the CLI once at 336 h; do NOT run 8760 in a test — report the command. Step 6: commit.**

---

## Parallelism map

- Task 1 is a hard barrier: tasks 4, 5 and 13 import lightsim2grid.
- Tasks 2 and 3 are independent of task 1 (schema and set enumeration need no engine) → they can start in the first wave alongside it.
- Task 6 (short circuit) needs only task 2 + `templates/`; task 10 must land first for the inverter fault parameters. 6 and 7 are then sequential (7 consumes 6's output).
- Tasks 4 → 5 are sequential (5 verifies through 4's AC path). Task 8 needs 5's LODF but not its AC verify, so 8 can follow 5's `lodf.py` half.
- Task 9 is independent of everything (pure `select.py`) → any wave. Sequence it before the next full-year run.
- Tasks 10 → 11 → 12 are a chain; 12 also needs 5 and 8 for the ledger numbers.
- Task 13 is the barrier: needs all.

## After increment 3

1. **PowerFactory validation, still outstanding.** Increment 1's `.raw` gate has never been closed against an independent oracle, and increment 3 adds a `.dyr` beside it. Validate a *selected* min-inertia hour — but note task 9 changes which hours those are, so validate after the re-run, not before.
2. Full-year re-run with the five-criterion selection and DC severity; expect a different and better-spread snapshot set than the 2026-09-04 run.
3. **Increment 4 per spec phase 3's remainder:** the action layer (`create_study`, `run_pipeline`, `list_ranked_snapshots`, `export_handoff_bundle`, …), then GUI wiring, then chat tool registration. The spec is explicit that GUI wiring is "a thin, late, path-limited backend change — deliberately the last increment" because pypsa-gui is under active concurrent development.
4. Open modelling question to settle with Hao, not in a task: should the aggregated interconnection equivalent (`G_BUS_39`, h = 500 s) be a committable unit the UC can decommit? It is off 56 % of the year, which is what makes every min-inertia hour an equivalent-off hour.
5. Smaller carried item: all three wind farms share one `wind_cf` series, so wind is perfectly correlated across sites. That understates fleet variability and biases `max_ibr_share` and `min_inertia_excl_equiv_mws` — the two criteria the study ranks on. Fix it between full-year runs, never between a run and its validation.
