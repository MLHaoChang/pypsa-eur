# Whole-branch end-to-end review — `claude/solution-fmea-integration-0mx5lc` @ 81bdf53 vs master 07b32c2

**Status:** review complete; 7 SERIOUS, 14 MINOR, 8 notes, all reproduced. **The seven SERIOUS findings are FIXED** (§6, seven commits `b6800b7..177c9a4`), each fix reviewed as shipped code in two passes and the gaps those passes found closed. M1, M8, M12, U2 and one note are closed with them; the remaining minors and notes stand as recorded. Findings below are
every one reproduced by a script or probe test named in the table, then read
against the code. Nothing here was accepted on a reviewer's word.

**Why this exists.** Every phase (0–12h) was adversarially reviewed as its own
unit. Nothing had read the branch as ONE artifact: 177 files, ~22k lines of
backend/frontend code and tests, five studies sharing one foreground network,
four adequacy surfaces that must agree, one new per-asset flag crossing every
storage boundary. This review did that, in four slices (adequacy math core;
routes/state/concurrency; frontend↔backend contract; persistence round trips)
plus a live end-to-end pass over all runnable smoke suites.

## 1. Live end-to-end pass

Server: isolated app-data + projects root, LOCAL_MODE=1, branch 81bdf53, pip stack (pypsa 1.3.0 / pandas 3.0.5).

### Branch-added suites S15–S31 (17 suites): PASS 74  FAIL 0  SKIP 1
- SKIP S17.6: the coupling loop did not reach `met` within max_solves=3 on the fixture, so no certified cap to compare against the panel copy. Fixture coverage gap, not a defect.

### Master-era suites (regression), same server
- First pass: S2 (6), S5 (3) pass; S3/S4/S7/S9–S14 skipped — projects root empty; `project_templates/*/network.nc` are gitignored build outputs, absent in the container.
- Built templates with `project_templates/_build.py` (3bus, ieee14; belgium kept absent — needs workflow output). Tree stays clean.
- Second pass: S7 (5), S10 (4), S11 (26), S12 (20), S13 (9), S14 (9): PASS 73  FAIL 0  SKIP 0.
- Seeded a solved 3bus project (`qa_e2e_seed_solved`, objective 61733.46) via the API; third pass: S3 (4 pass, 1 skip flat/no by_period), S4 (1 pass, 2 skip: no multi-period nc, flat), S9 (5 pass, 1 skip: no co2 carrier). All skips are fixture-shape skips.
- Not run: S1/S6 (need vite dev server on :5173), S8 (runs `pytest test` with a live 10 MB download).

### Totals across all runs: PASS 161  FAIL 0  SKIP 13 (all fixture-shape)

## 2. Findings

### 2.1 SERIOUS

Paths relative to `pypsa-gui/backend/`. Every row was reproduced by me from the
named script (all under the review scratch directory, not committed) and then
read against the code at the cited lines.

| # | file:line | what is wrong | reproduction | expected |
|---|---|---|---|---|
| S1 | `models/schemas.py:74,129,179,257,299` (`outage_rate_value: float \| None`), `services/adequacy/occurrence.py:275` (`validate_outage_params` — a WARNING), `services/adequacy/copt.py:217` (`_unit_states`), `routers/results.py:5333` (`/copt` gates on nothing) | **One generator with an outage rate that is NaN, negative or ≥ 1 silently corrupts the COPT for the whole fleet.** The schema has no range validator; the UI input says "0–1" but does not clamp; the validator only warns (`outage_params_implausible`, rendered as a generic row); `/copt` runs on demand with no gate. The four surfaces then DISAGREE: COPT computes with the bad number, the MC raises `AssertionError` (study "failed"), the margin credits the unit at 0. `mc.py:317` says "occurrence.py rejects q ≥ 1" — it does not. | `agentA/s4_nan_q_poison.py`: 1 good unit → LOLE 0.80 h / EUE 76 MWh; add one q=NaN unit → **LOLE 0.0, EUE 0.0, every FMECA ΔEUE NaN, table mass NaN**. q=−0.1 → **LOLE −0.80 h**, table mass 1.1. `agentA/s5_range.py`: schema accepts −0.1 / 1.5 / nan; q=1.5 → COPT LOLE **−1.4 h** silently, MC `AssertionError`, margin derate 0. | Refuse at the boundary (finite, `0 ≤ q < 1`) as Phase 12g did for every other finite-default attribute; and refuse in `fleet_and_residual` like `transition_probs` already does, so a value that arrives by any other path (netCDF import, bundle) cannot reach `_unit_states`. |
| S2 | `routers/results.py:3081-3096` (fmea_sweep), `:3231-3246` (frontier), `:3423-3433` (mc), `:3893-3903` (coupling), `:4548-4558` (margin) vs publish `:3162/:3300/:3654/:4376/:5321` | **The study-start mesh is check-then-act outside the lock.** The five `_study_running(...)` / `status == "running"` gates run with no lock; the publish + `t.start()` is a separate `get_solver_state_lock()` hold. Two study POSTs close together both pass the gates; the second overwrites the first's record, so the first worker is **orphaned** — unabortable, invisible to every guard — and both mutate the same foreground network lock-free between their solves. | `agentB/test_probe1.py::test_T1`: two concurrent `POST /results/fmea_sweep` barriered after the gates → `[200, 200]`, two live `fmea-sweep` threads, `_state["fmea_sweep"]` holds only the second. | `[200, 409]` — the branch's own standard for `/run` (`simulation.py:611-623`: "Gate + claim + start under a SINGLE _state_lock hold"). |
| S3 | `routers/simulation.py:560-562` (`blocking_study_detail()` outside the claim) vs `:623-676`; the study POSTs' `status == "running"` check outside their publish hold | **`/run` and a study start are two unsynchronised check-then-act sections.** Same class as S2 across the solve/study boundary. | `test_probe1.py::test_T2`: concurrent `/simulation/run` + `/results/fmea_sweep` → both 200; `_state.status == "running"` AND `fmea_sweep.status == "running"` at once. | One 409. Do the study gate inside `/run`'s claim block and the solve gate inside the study publish block. |
| S4 | `routers/results.py:3095, 3245, 3433, 3903, 4558` — the study mesh tests the status STRING | **A study can start while an ABORTED solver worker is still alive** (in `restore_modelling`, or stuck in native HiGHS). `/simulation/abort` flips status to `"aborted"` but the thread keeps running; `_solver_in_flight()` (`simulation.py:176-204`) exists for exactly this and is what preflight, save and activate use — the study routes do not. The sweep's `freeze_capacities` + base solve then start on a network whose LP transforms are still being reverted; for a stuck native solve the study blocks on the mutation lock forever with `status: running`, which blocks every swap route and `/run`. **User-reachable with two clicks: Abort, then Start study.** | `agentB/test_probe2.py::test_T3`: `status="aborted"`, live thread → preflight `deferred: true, deferred_stuck: true`; `/lock_status` `worker_alive: true`; `POST /results/fmea_sweep` → **200 running**. | 409. Replace the string check with `_solver_in_flight()` in the five study POSTs. |
| S5 | `routers/projects.py:1396` (`_save_context` gates only on `_solver_in_flight_ctx`), `:1977` (`activate_project`); `pypsa_service._evict_if_over_cap` (eviction save) | **Save and activate are outside the study mesh.** A study worker is never in `_state["thread"]`, so a save landing between a sweep's lock-free `mutate` / `unfreeze` (`sweep.py:243, :268`) exports the CONTINGENCY network and a `results_state.pkl` carrying the contingency's lost load as the user's project. Load, import, template and reset are guarded (`refuse_if_study_running` at `:865, :1147, :2109`); save is not. | `test_probe1.py::test_T4`: live `fmea_sweep` record → `POST /api/projects/demo?force=true` **200**, `POST /api/projects/{other}/activate` **200**. | 409 naming the study, like the load path. Gate `_save_context` (and so autosave/eviction) and `activate_project` on `running_study_key(ctx.solver_state)`. |
| S6 | `routers/results.py:3170` (`targets_permyriad: list[float]`), `:3321` (`cov_target: float`), `:3790-3793` (`target_lole_h`, `eps0`), `:4440` — plain `float` | **Non-finite floats in study request bodies are accepted, the study is published and RUNS, then the POST response 500s and every later `GET /results/{study}` 500s** until a swap clears the record. Phase 12g introduced `Finite` (`schemas.py:21`, `allow_inf_nan=False`) for asset attributes and did not apply it to the five study request models; coupling's own `target > 0` passes `inf`. Negative/zero frontier targets are not refused either. | `test_probe1.py::test_T7`: `POST /results/frontier {"targets_permyriad": [Infinity, 1.0]}` → worker ran (`status: done`, `[inf, 1.0]`), response `ValueError: Out of range float values are not JSON compliant`; `test_probe2.py::test_T9`: mc `cov_target: NaN` → 500; coupling `target_lole_h: Infinity` → 500; frontier `[-1, 0, NaN]` → 500. | 422 before publish: `Finite` on every study float field; refuse ≤ 0 targets. |
| S7 | `routers/projects.py:77` (`_BUNDLE_FILES`), `:936` (`import_bundle`), `:2284`, `:2864` (bundle export); `services/adequacy/worksheet.py:31`, `stress.py:46` (`SIDECAR_NAME`) | **The user-authored FMEA worksheet (`adequacy_worksheet.json`: expert rows + overlays) and the stress-scenario registry (`adequacy_stress_scenarios.json`) are not in `_BUNDLE_FILES`.** They survive a plain save and a rename, but a project BUNDLE export/import, a project SNAPSHOT, and a SCENARIO fork all silently drop them — the shared bundle arrives with an empty worksheet and no stress scenarios, and a snapshot restore cannot bring them back. | `agentD/test_ws.py`: bundle zip = [network.nc, solver_config.json, metadata.json]; snapshot dir has no sidecar; scenario fork `GET …/worksheet` = empty, `GET …/stress_scenarios` = []. | Add both sidecars to `_BUNDLE_FILES` (the tuple already carries the presentation-only `layout.json` for exactly this reason). |

### 2.2 MINOR

| # | file:line | what is wrong | reproduction | expected |
|---|---|---|---|---|
| M1 | `routers/results.py:3162-3163` (+ `:3300, :3654, :4376, :5321`) | Record is published BEFORE `t.start()`. If `start()` raises (thread limit), `record_is_running` (`project_context.py:270` — `ident is None` counts as running, by design) is True for the rest of the process: `/run`, project load, `/network/reset` all 409; the study's `/abort` is a 200 no-op. No route can clear it. | `test_probe1.py::test_T5`. | Start, then publish, in the same lock hold; or roll the record back on exception. |
| M2 | `services/adequacy/copt.py:566`; `solver_service.py:3504`; `validation_service.py:2296` | Negative static `p_max_pu`: COPT/MC credit nameplate × (1 − q), margin credits 0; the preflight sentence (`availability_may_include_outages`) is true of neither. Bounded: full preflight errors `generator_min_gt_max` so `/run` refuses — but `/copt` and `/mc` run on demand regardless. | `agentA/s1`: `A_neg_static` COPT cap 100, MC mean 88.2, margin derate 0.000. | One number (0 MW): fold `max(cf, 0)`; exclude negatives from the may-include sentence. |
| M3 | `copt.py:499-522, :544, :721`; `solver_service.py:3626` | All-NaN `p_max_pu` COLUMN: COPT/MC credit nameplate × (1 − q), margin 0, LP unbounded. Bounded: full preflight errors `nonfinite_bound`; `/copt`/`/mc` still run. | `agentA/s1`: `B_nan_col` COPT cap 100, MC 90.0, margin 0.000. | Return the zero series when a column exists with no finite value, or gate the on-demand routes on `nonfinite_bound`. |
| M4 | `copt.py:600` (`rate_is_zero`), `:814` (`DETERMINISTIC_ROW_NOTE`); `results.py:3628, :5434` | A typed `outage_rate_value = 0` (flag NOT set) on a profiled unit lands in `deterministic_units` and its FMECA row asserts "(p_max_pu_includes_outages)". `outages_in_availability` never reaches `CoptUnit`, so the note cannot tell the two apart. | `agentA/s1`: `C_q0_varying` row note = `DETERMINISTIC_ROW_NOTE`, flag False. | The note and the list claim the flag only when it is set. |
| M5 | `copt.py:616-642`; `results.py:5429-5434, :3628-3637` | A flagged unit with a FOLDED static (no column) is a table unit at q = 0: it appears in `folded_units`, NOT in `deterministic_units` (both routes require a profile), and gets no row note — while the same flag on a column unit gets both. The 12h disclosure is asymmetric. | `agentA/s1`: `D_flag_static` in `split.table`, absent from `split.deterministic`, no note; `E_flag_col` has the note. | `deterministic_units` = every flag-zeroed unit, built from `rate_is_zero(u)` rather than `split.deterministic`. |
| M6 | `solver_service.py:3516-3560` (margin derates storage by carrier-default q, battery 0.02); `mc.py:195-231` (`StorageSpec` has no outage rate); `elcc.py` | The reserve margin derates storage for outages; the MC and the portfolio ELCC simulate storage with NONE. Preflight's "derates N assets using class averages" names a rate the engines never use. The spec is silent. | `agentA/s6`: `bat` 50 MW → margin derate 0.980 (credit 49.0); storage ELCC row 50.0. | One storage-outage rule across surfaces, or drop it from the margin and say so. |
| M7 | `routers/results.py:4758` (`m_ceiling = min(m_max, MAX_MARGIN)`); `frontend/src/api/simulation.ts:509` (`margin_ceiling: number \| null`, doc "null = unbounded"); `MarginLoopPanel.tsx:345-347` | The backend never sends `null` for an unbounded fleet — it sends the schema cap 5.0 — and the panel renders "ceiling 500 %" with a tooltip saying no plan exists above it. | `agentC/payloads/edges.json` `nofirm_margin_loop_post`: `"margin_ceiling": 5.0` for a fleet whose only extendable has `p_nom_max = inf`. | Emit `null` when `m_max` is not finite; keep the schema cap internal. |
| M8 | `frontend/src/utils/projectActions.ts:385` (every 409 from activate → `'busy-solve'`); `Sidebar.tsx:1037`, `ProjectTabs.tsx:211` | The branch's study-running 409 on load/import/undo carries a `detail` naming the study; the frontend maps every 409 to fixed copy "Finish or abort the running solve", and the Abort button it points at hits `/simulation/abort`, which does not stop a study. | `agentC/payloads/swap_guard.json` project_get 409 detail vs the rendered copy. | Surface `detail` for 409, or branch on it. |
| M9 | `api/simulation.ts McResult`, `pages/results/adequacy.tsx CoptPayload.fleet` | `folded_units` / `deterministic_units` are on the wire (`mc.json`, `solve_results.json`) and referenced by NO frontend file. The 12h disclosure is emitted and never rendered. | grep + captured payloads. | Render (a chip like the activity note) or drop from the payload. |
| M10 | `frontend/src/layout/AppHeader.tsx:310` (`onError: () => toast.error('Nothing to undo')`); `api/client.ts:186` (interceptor toasts `detail`) | The branch adds a 409 study refusal on undo (`refuse_if_study_running("undo")`) whose `detail` explains the study; the header ALSO toasts "Nothing to undo" for the same click — two contradictory toasts. | `agentC/payloads/swap_guard.json` undo → 409 with the study sentence. | One toast, with the detail. |
| M11 | `pages/results/FmeaTab.tsx:73, :97` (`toast.error(\`…${e.message}\`)`) | On a 409-mesh / 422 refusal the sweep button shows axios' generic "Request failed with status code 409" beside the interceptor's real detail; McPanel, LoopPanel and MarginLoopPanel use `blockerMessage(e)`. No FmeaTab test covers the refused path. | `agentC/test_capture3.py::test_409_mesh`. | Same pattern as the other panels. |
| M12 | `routers/projects.py:1975-1993` (`activate`) | The study swap-guard is on `GET /projects/{name}` (load), undo, import and template, but NOT on `POST /projects/{id}/activate` — the only route the frontend's project switch calls (`utils/projectActions.ts:380`). The user is switched away from a running study they can then neither see nor abort. (Same route as S5's save half.) | `agentC/payloads/activate_guard.json`: activate-other-during-study 200; load-other-during-study 409. | Guard activate like load, or document the exception. |
| M13 | `services/adequacy/occurrence.py:186-215`; `models/schemas.py:75,130,180` (`outage_rate_basis: Literal["FOR","EFORd"] | None`) | An UNSET `outage_rate_basis` in a mixed column (some units set, some not) reloads from netCDF as `""` (NaN → "" on string coercion). The resolver reads `""` as unset, so the engines agree — but the wire differs: `GET /generators` returns `""` where it returned `null` before the save, and echoing that row into `PUT /generators/{name}` is a 422 `Input should be 'FOR' or 'EFORd'`. The GUI is unaffected (`cardKit.tsx:325-328` maps `""` → null); scripted and chat round-trips hit it. | my direct check: mixed column `["EFORd", NaN]` → helper export/import → `''`; `GeneratorCreate(outage_rate_basis='')` → 422. | Same wire value before and after load: serialise `""` → `null`, or accept `""` in a before-validator like `_flag_none_is_false`. |
| M14 | `services/adequacy/report.py:339` (`"peak_snapshots": [str(x) …]`), `net_window.snapshots`; rendered verbatim at `ReserveMarginPanel.tsx:275, :456` | On MultiIndex snapshots the peak and net-window hour labels serialise as tuple reprs — `"(2030, Timestamp('2030-01-01 21:00:00'))"` — and the panel prints them as-is. | `agentD/test_http_rt.py` multi-period margin payload (re-run by me): `"peak_snapshots": ["(2030, Timestamp('2030-01-01 21:00:00'))"]`. | An ISO timestep string (`x[-1]` when `x` is a tuple). |

### 2.3 NOTES (recorded, no action required)

- N1 `copt.py screening_analysis`: LOLE −1.8e-15 when capacity exactly covers load (`agentA/s1`). Clamp at 0.
- N2 Spec §12h: "engines and margin credit at nameplate × cf × (1 − q)" is false for cf < 0 (M2); "the net-load window moves together" is vacuous — the window (`report.py:233-239`) nets `profile × cap` with no (1 − q) term. `mc.py:317` comment "occurrence.py rejects q ≥ 1" is false (S1).
- N3 NaN hour: engines count 0, margin mean skips (documented at `copt.py:499`; `agentA/s6`: `w_nan` engines 42.75 vs margin 57.0). Documented divergence, but spec §12h's "surfaces move together" overstates it.
- N4 `tests/test_adequacy_study_swap_guard.py SWAP_ROUTES` parametrises 2 routes over HTTP; spec §4 A1 promised seven. `import_bundle`, snapshot restore and `io/import/netcdf` are guarded by construction via `reset_network` (fresh grep: `io.py:191`, `projects.py:967/1179/2132`, `network.py:1933/2347`, `snapshots.py:503`, `clustering.py:309`) but never exercised over HTTP.
- N5 Every study's closing restore is a full LP behind a fresh `Event()` (`results.py:4213, :5135`); `/simulation/abort` answers 400 "No simulation running" during it. Documented as intended; the user has no control for that span.
- N6 `api/types.ts LostLoadComparison.shed_hours?` is emitted and has no consumer; `/results/reserve_margin` carries `fingerprint` that the TS type lacks. Harmless.
- N7 `agentD`: `M5` has a second face — a STATIC-availability flagged unit's FMECA row carries `occurrence_per_year = 0`, ΔEUE 0 and NO note, and `folded_units` does not say the rate was zeroed, so the flag is invisible in `/copt` for the static case (`agentD/fmeca.py`: `g_flag_static: [0.0, 0.0, ""]`). Same fix as M5.
- N8 `S17.6` (coupling-loop verdict copy) skipped on the fixture because no run reached `met` in three solves — the check has never run against a certified cap in this container.

### 2.4 Unreproduced suspicions (listed so the next reader does not re-derive them)

- U1 `get_fmea_sweep` / `get_frontier` / `get_mc` (`results.py:3020, :3178, :3336`) iterate `st.items()` with no lock while the worker `record.update(...)`s NEW keys at completion → a poll in that instant could raise "dictionary changed size during iteration". The two loops' GETs hold `get_solver_state_lock()`; these three do not. Sub-microsecond window; not reproduced.
- U2 `_evict_if_over_cap` protects active / session-active / queued-solve contexts only; a context carrying a live study is evictable and eviction SAVES it (S5's consequence by another path). Did not fire in-process; the reachable trigger is a second browser session activating past the resident cap.
- U3 Margin non-extendable capacity reads `gens.p_nom` (`solver_service.py:3499`); engines read `solved_capacity` (`p_nom_opt` first). Diverges only if a non-extendable row carries `p_nom_opt ≠ p_nom`.
- U4 `resolve_outage_params` `df.loc[name]` on a duplicated generator index returns a frame — untested.

## 3. Method and coverage

Four adversarial reviewers, each given the full branch diff and a slice, each
required to reproduce every finding by running code before reporting it, and
each writing to disk incrementally (a first attempt was lost to a rate limit).
I then re-ran every reproduction myself and read every cited line. The
slices, and what each found CLEAN, so the reader knows what was looked at and
not just what was found:

**Adequacy math core** (`services/adequacy/*`, the design spec). The four
surfaces were run on the same networks: flagged + folded, flag on a varying
column, static 0, static > 1, extendable `p_nom_opt`, MultiIndex + build-year /
lifetime activity (four cases), `load_scalers` per period — COPT residual ==
MC residual == margin demand per period; availability series match the
margin's active set and derate; `activity_summary` agrees. The MC is the COPT
by construction (`snapshot_inputs` reuses `fleet_and_residual`'s `CoptUnit`
objects); `coupling.snapshot_hash` and `portfolio.network_fingerprint` both
hash the resolved, post-flag rate. The normalisers were driven over 28 flag
value kinds and 8 column dtypes with identical rates and derates before and
after; the only direct flag read outside the resolver is
`validation_service.py:2187-2195`, via `flag_is_set`. `static_fold_factor`'s
gates (column present → None; NaN → None; [0,1) folded) hold. Stress and
sweep read the resolver, not the flag.

**Routes, state, concurrency** (all routers and services the branch touched).
A route × gate matrix (in the reviewer's report) over every added or modified
route against the six gates. Clean: the netCDF lock is never re-entered (all
11 callers hold it; ordering is always mutation lock → netCDF lock); abort
reaches every engine loop boundary and every stopped study restores in a
`finally`; the loops' `solve_at` uses a private sink so `/results/adequacy`
never shows a mid-iterate value; `/run` validates under the lock before
mutating for LP and myopic; the 12c/12h preflight codes are mutually exclusive
per unit by construction and match the engine's fold; 12f/12g cannot
double-fire on one Load; on-demand GETs return 200 with NaN in series and
204/200 on an unsolved network; sync 422s fire before publish.

**Frontend ↔ backend contract.** Every TS type the branch added was diffed
field-by-field against a captured payload (edges: zero load, no firm capacity,
flat network, unbounded fleet, failed iterate). tsc clean; 414/414 frontend
tests pass; no reference to the retired code or to any 12h field; unknown
preflight codes fall through to the generic row; the deferred triple, abort
shapes and 409 bodies match; every nullable is guarded in every panel; the
branch-added tests are not tautological (they mock only the API layer and
render the real components, and the `PropertiesPanel` flag test drives the real
Edit → toggle → Save flow and asserts the PUT). The
`p_max_pu_includes_outages` round trip accepts `true/"true"/"True"/1/"yes"` →
true and `false/"false"/0/null` → false, and a unit without the column reads
`false`.

**Persistence round trips.** The flag column in nine dtypes × two snapshot
shapes through every helper-routed boundary (project save/load, io
export/import, snapshot restore, template, bundle import): dtype always `bool`
after, `flag_is_set` always agrees. Outage columns: NaN vs 0 vs absent stay
distinguishable; no unit flips between having and lacking outage data. Solve
leaves the flag `bool`; `/results/copt` is byte-identical after a project
load; vintage expansion inherits flag, rate and profile and the fold still
applies. FMECA rows == engine unit set on a 17-unit mixed fixture in both
snapshot shapes, with the netted and deterministic notes consistent with
`split` and `fidelity_note`.

## 4. What this means for the PR

No BLOCKER. Nothing changes a number the earlier phases certified: the four
surfaces agree on every well-formed input the review could construct, the
persistence layer preserves the new columns everywhere, and the contract with
the frontend holds. The seven SERIOUS findings are all the same shape —
**a boundary the branch built for one path and did not extend to its
neighbours**:

- S1 is Phase 12g's "refuse non-finite at the boundary" rule, not applied to
  the one attribute whose consumer is the branch's own engine.
- S2, S3 and S4 are Phase 11's "gate + claim under one lock" rule, applied to
  `/run` and not to the five study starts — and S4 is reachable with two
  clicks (Abort, then start a study).
- S5 and M12 are the swap guard, applied to load/import/template/reset and
  not to save/activate.
- S6 is `Finite`, applied to asset attributes and not to study requests.
- S7 is `_BUNDLE_FILES`, extended for `layout.json` and not for the two
  sidecars the branch added.

Every one has a small, local fix on the pattern the branch already uses a few
lines away. Recommended order: S4 and S1 first (user-reachable, silent),
then S2/S3 together (one lock hold), S5 + M12 together, S6, S7.

## 5. Reviewer reports

The four reports, their scripts, captured payloads and probe tests are in the
session scratch directory (`e2e_review/agent{A,B,C,D}/`) and are not
committed; every claim in §2 carries the script that produced it and was
re-run by me.

## 6. The fixes — and what reviewing them found

Built in the order §4 recommended, one commit per finding, every fix with a
test demonstrated red against the named removal and the source restored by
saved copy and verified by hash; then reviewed as shipped code, twice.

| finding | commit | what ships | tests / bites |
|---|---|---|---|
| S4, S2, S3, M1 | `b6800b7` | One predicate for the study mesh (`_study_mesh_blocker`: the other studies, then `_solver_in_flight()` — the status string is gone), called early and again INSIDE the publish hold (`_publish_study`); `/run` and `/run_ac_pf` re-check the mesh inside their claim; a `start()` that raises rolls the record back. Three existing mesh tests had pinned the status-string shape with no worker thread and now park a live thread. | 9 tests; 4 bites |
| S1 | `be449eb` | `OutageRate = Annotated[float, Field(ge=0, lt=1, allow_inf_nan=False)]` on all five schemas; the same 422 in `PATCH /_bulk`; `fleet_and_residual` raises `OutageRateError` naming the unit before any unit is built (so `/copt`, `/mc`, both loops answer 422); `build_copt` refuses a bad `q`; the margin marks a rate outside [0, 1) unpriceable. Correction to §2.1 S1: through the network a NaN rate is "unset" (resolver `_is_set`), so the NaN case was reachable only by direct `CoptUnit` construction — closed too. | 61 tests; 4 bites |
| S5, M12, U2, M8 | `9d44a7c` | `_save_context` refuses with a structured 409 (`error_kind: "study_in_flight"`, the study's sentence) for every caller; the save wrapper refuses BEFORE `create_root`; `activate` refuses the same way; `_evict_if_over_cap` protects a context with a live study; the frontend maps that kind to `'busy-study'` and toasts the sentence in all four switch entry points. | 7 + 4 tests; 5 bites |
| S6 | `9959394` | `Finite` on every study request float; the frontier refuses a non-positive or non-finite target before publishing. | 12 tests; 2 bites |
| S7 | `99733a9` | Both sidecars in `_BUNDLE_FILES`; every loop tolerates absence, so an older bundle or snapshot still imports. | 5 tests; 1 bite |

**Verification on the fixed head.** Full backend suite 3285 passed / 43
failed — the failure set identical to master's 43 in both directions; frontend
910/910 and tsc clean; the 17 branch-added live suites 74 pass / 0 fail / 1
fixture-shape skip, as before the fixes.

**The fix review, first pass** (an adversarial reviewer over `20835e4..99733a9`,
every finding reproduced, all 32 of the original concurrency probes re-run
green) confirmed S1, S2, S4, S6 and S7 closed and found three things:

- F1 — the S1 refusal was caught by `/copt`, `/mc` and both loops and NOT by
  `GET /results/mc/elcc_candidates`, which let `OutageRateError` out as a 500.
  A new defect from the fix.
- F2 — S5 only narrowed: the save gate ran before `_save_context` took
  `ctx.mutation_lock` and shared no lock with the study publish, so a save
  that had passed its gate could export a network a sweep had meanwhile begun
  mutating lock-free — measured as the study's `p_nom` on disk as the user's
  project.
- F3 — S3 only narrowed: `POST /simulation/queue`, the Run button's real path,
  had no study gate; the dispatcher claimed the resident context and re-solved
  the network a live study was measuring, failing only at its post-solve save.

`5f6f4c9`: the candidates route answers 422; `_save_context` re-checks the
study INSIDE the mutation lock and `_publish_study` takes that same lock
outside the state lock, so a study publishes either before a save has begun
exporting or after it has finished; the enqueue route and the dispatcher
refuse a live study. Six tests. **My first F2 test did not bite** — its
harness held the save before the pre-lock gate, which caught the study either
way — and was rewritten into the two windows the review had measured, each
now red against its own removal.

**Second pass** (over `5f6f4c9`, plus the reviewer's own re-baselined bites
of every earlier fix, all red): a lock audit found no order inversion or
deadlock (mutation-outer, state-inner everywhere; both RLocks); and two more
things. The dispatcher's study check and its claim were two acquisitions of
the state RLock — the comment said "the same hold" and it was not — and a
study POST landing between them was admitted and then solved over (probe P7).
And the quiet-toast change was a no-op: the interceptor keyed on a top-level
`code` that only the middleware 409 carries, while the save's structured 409
carries `detail.error_kind`, so the autosave and the pre-switch save still
toasted the study sentence — and, it turned out, the pre-existing
solver-in-flight one too. `177c9a4`: check and claim under one hold (a test
parks the dispatcher before its second acquisition); the interceptor reads
either field; `_publish_study`'s mutation-lock acquire is bounded at 5 s and
answers 409 rather than waiting out a solve; the snapshot-restore docstring
states that a snapshot predating the sidecars leaves the live worksheet in
place.

**Final verdicts:** S1–S7 closed; F1–F3 and the second pass's two findings
closed; M1, M8, M12, U2 closed. Still open, as recorded: M2–M7, M9–M11, M13,
M14, N1–N8 (none user-reachable silent defects; each has its fix in §2.2).
