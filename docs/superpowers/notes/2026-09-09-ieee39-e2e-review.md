# IEEE 39-bus end-to-end review — `claude/solution-fmea-integration-0mx5lc` @ 60c4106

**Status:** review complete; **F1 is FIXED** (§7). **2 SERIOUS** (one branch-owned, one pre-existing on
master), **3 MINOR**, **4 notes** — every one reproduced live against a running
backend and read against the code. The whole solution-FMEA journey was driven on
a real test system rather than on hand-built fixtures: the IEEE 39-bus (New
England) network, twice — as built, and load-scaled ×1.25 so the reserve margin
binds and the planning loops have to build.

**Why this exists.** The whole-branch review (`2026-09-08-whole-branch-e2e-review.md`)
read the branch as one artifact and drove the smoke suites, whose fixtures are
small and purpose-built for the assertion at hand. This pass asks a different
question: does the feature hold up on a *system* — 39 buses, 46 branches, a
mixed thermal/hydro/nuclear fleet with real unit sizes, a wind farm on an hourly
profile, a battery, an extendable peaker, 168 hourly snapshots — driven through
every surface in the order a user would meet them.

## 1. The test system

`pypsa-gui/backend/smoke/ieee39/build_ieee39.py` (committed with this note).
Topology, impedances, ratings, loads and generator sizes are the standard
MATPOWER `case39` (100 MVA, 345 kV). Everything the standard data is silent on
is generic and stated in the module docstring:

| Silent in case39 | Generic value used |
|---|---|
| carriers | 30 hydro; 31, 33 nuclear; 32, 34, 38, 39 coal; 35, 36, 37 gas |
| marginal cost (€/MWh) | hydro 5, nuclear 10, coal 30, gas 55 |
| outage data | per-carrier defaults, with explicit EFORd/MTTR typed on four units (hydro 0.02/24 h, nuclear 0.03/72 h, coal 0.06/48 h, gas 0.05/24 h) so both `source` kinds appear |
| time | 168 h, double-peak daily shape, weekend dip, scaled so each bus peak = its `PD` |
| VRE / storage / expansion lever | 500 MW wind at bus 16 (hourly CF, mean 0.36), 200 MW / 4 h battery at bus 20, extendable gas peaker at bus 16 (`p_nom_max` 2 000 MW, 60 000 €/MW·a) |

Two variants: **base** (peak 6 157 MW against 7 367 MW of thermal+hydro) and
**stressed** (loads ×1.25, peak 7 696 MW — above the firm fleet, so the 15 %
margin binds and PK16 must be built).

`pypsa-gui/backend/smoke/ieee39/journey.py` drives the journey over HTTP with
the standard library only, writing every response to disk as it lands.

## 2. The journey

Server: isolated app-data + projects root, `PYPSAGUI_LOCAL_MODE=1`, branch
`60c4106`. 46 steps per run: create project → activate → import netCDF → save →
read the editor rows → solver config (VoLL 10 000 €/MWh, reserve margin 15 %,
10 peak hours) → preflight → LOPF → margin/adequacy/lost-load/cost/statistics →
COPT → ELCC candidates → MC + per-asset ELCC + portfolio ELCC → stress-scenario
registry PUT/GET → class-B/C sweep → FMEA modes → frontier (3 targets) →
coupling loop → margin loop → worksheet PUT/GET (expert class-D row + overlay) →
save → snapshot → bundle → config restore.

**Every step returned 200 on both runs.** No 500 anywhere, no non-finite float
in any payload (`finite_scan` over every response body).

| | base | stressed ×1.25 |
|---|---|---|
| LOPF | optimal, 14.26 M€, 3.1 s | optimal, 118.21 M€, 3.2 s |
| reserve margin | 7 325 / 7 080 MW firm, met, not binding | 8 850 / 8 850 MW, met, **binding**; PK16 built to 1 605 MW |
| COPT | LOLE 0.831 h / EUE 292 MWh | LOLE 1.264 h / EUE 602 MWh |
| sequential MC (n = 2 000) | LOLE 0.478 h / EUE 152 MWh | LOLE 0.884 h / EUE 437 MWh |
| top criticality | G39 1.90 M€/yr | PK16 4.03 M€/yr, G39 2.36 M€/yr |
| ELCC | G39 784 / 1 100 MW; portfolio 145 / 306 MW | PK16 1 205 / 1 605 MW; portfolio 133 / 306 MW |
| class B/C sweep | (no scenarios registered) | 3 rows: cold snap ΔEUE 2 738 MWh, heat wave 579 MWh, `climate_1987` → `profiles_not_supported_yet` |
| frontier (10‱/3‱/1‱) | 3 × ok | 3 × ok |
| coupling loop | met, ε* = 100‱, 1 solve | unreachable, 2 solves |
| margin loop | met, 1 solve, ceiling 49.8 % | unreachable, 3 solves, ceiling 19.9 % |

The engine ordering is coherent throughout: COPT (analytic, no network, no
storage) reads pessimistic against the MC (chronological, storage dispatched),
the fidelity chip says so, and the FMECA ranking matches the fleet's own sizes
and rates. The stressed run's `unreachable` verdicts are honest — the candidate
set genuinely cannot reach 0.5 h on the MC's own LOLE.

**The minors shipped in `60c4106` were exercised live and behaved as designed:**
M6 — the battery carries its carrier-default 0.02 into the MC (`S20` derate 0.98
in the margin table, and the same 1 − q on the store's power and energy);
M4/M5 — a flagged static-folded unit (`p_max_pu` 0.9 + `p_max_pu_includes_outages`)
appears in **both** `folded_units` and `deterministic_units`, its FMECA row note
names the flag, while a unit whose rate is typed 0 gets the distinct rate-zero
note and its own listing; the unflagged twin appears in neither list. The UI
renders the same numbers (screenshots in §5).

## 3. Findings

| # | Rank | Where | What | Reproduction |
|---|---|---|---|---|
| **F1** | **SERIOUS — FIXED, §7** | `routers/results.py:4887-4901` (clamp) → `services/adequacy/coupling.py:195-197,470` → `results.py:5062,5294,5158-5169,5205-5213`; `MarginLoopPanel.tsx:333,453` | **The margin loop certifies, displays and PERSISTS a reserve margin it never solved.** When the controller's blind step overshoots the fleet ceiling, `solve_at` clamps to `m_ceiling` and solves there — but returns nothing about which margin it evaluated, so the controller records the margin it *asked* for. Every downstream consumer is faithful to that wrong number: the iterate rows, `lever_star`, the verdict sentence, and on `restore="final"` the value written into the user's solver config. | Live, stressed IEEE 39 (ceiling **19.9 %**): `POST /results/margin_loop {"target_lole_h":0.7,…,"restore":"final"}` → status `met`, rows 15.75 % → **363 %** → **131.5 %**, verdict "verified at a reserve margin of 131.5%, and that margin has been APPLIED to your solver settings", `reserve_margin` left at 1.315, closing re-solve `base_restore_status: validation_failed`, and the next preflight refuses the config with `reserve_margin_unreachable`. Record: `scratchpad/ieee39/margin_loop_repro.json`. Also reproduced through the repo's own stub suite. |
| **F2** | **SERIOUS (pre-existing on master — NOT introduced by this branch)** | `routers/network.py:238-291` (`_update_component` = `n.remove` + `n.add`; same on master) | **Editing any component through the Properties panel destroys its time series, silently changing every adequacy answer.** A full-row `PUT /network/generators/W16` with *no field changed* drops `generators_t.p_max_pu["W16"]`: the 500 MW wind farm becomes a firm must-take at `p_max_pu` 1.0. The Time Series view can still show the profile (it is re-served from the saved project), so the two disagree with no error anywhere. | Live, base IEEE 39: COPT LOLE **0.8312 → 0.3185 h** (−62 %), ELCC candidate nameplate **306.3 → 500.0 MW**, and the next full solve is refused — `reserve_margin_unpriceable_assets: … (no outage data, no availability profile): W16`. Same for loads: a full-row `PUT /network/loads/D39` drops `loads_t.p_set` from 20 columns to 19. |
| **F3** | MINOR | `services/adequacy/frontier.py:221,283`; `FrontierPanel.tsx:84-88`; plan `2026-08-29-fmea-phase8-reserve-margin.md:222` | **The frontier under a standing reserve margin is a flat curve that says the opposite.** The margin is deliberately *not* stripped (unlike the contingency sweep), which the phase-8 plan calls correct "**but must be stated on the panel**" — the record carries no margin field and the panel never mentions it. With the margin already covering every swept ε the three points are identical, `knee` is null, and the panel's null-knee copy reads "every step still buys more avoided-shed value than it costs. Sweep tighter targets to find one." Nothing was bought and tighter targets cannot change that. | Live, both runs: targets 10‱/3‱/1‱ → identical `total_system_cost_eur`, `achieved_ens_mwh` 0.0, `warning: null`, `knee: null`. No frontier test covers a margin or a flat curve. |
| **F4** | MINOR | `results.py:4936-4942,5069`; `MarginLoopPanel.tsx:460` | The margin loop borrows the cap loop's `binding` vocabulary to drive the controller's plateau pre-test, and the panel renders it raw under "Bound by" — so on a study with no energy cap the user reads **`system_cap`** for every iterate where the *margin* bound, and `voll` when it did not. | Live, stressed: both iterates `"binding": "system_cap"` with `cap_mwh: null`. A label map in the panel is the cheap fix (two tests pin the wire value). |
| **F5** | MINOR | `coupling.py:426-443`; `results.py:4900` | One wasted full solve + MC per met run through the clamp: bisection re-asks for a different `x` that maps to the same ceiling margin, stopped only by the `plan_hash == met_hash` check. | Live: iterates 2 and 3 of the `restore="final"` repro have identical cost and identical MC LOLE. |
| N1 | note | `results.py:4079-4080,4195` | The coupling record's `eps0` echoes the request even when the controller floors it to `EPS_FLOOR_PERMYRIAD` — header only, rows and `eps_star` are right. | Read from code. |
| N2 | note | `results.py:4770,4792`; `validation_service.py:2104-2109` | The loop's fleet ceiling `m_max` and the validator's `required_mw` are computed by two different expressions and compared with no tolerance. Not observed (the live ceiling solve passed), but an ulp of disagreement would report `unreachable` on a reachable network. | Read from code. |
| N3 | note | `frontend/src/api/projects.ts:114-119` | `GET /api/projects/unclaimed` 404s in local mode by design (`skipErrorToast`), but it still logs two console errors on every projects-page load. Cosmetic; noted because it is the only console noise in the UI pass. | UI smoke. |
| N4 | note | — | `POST /api/simulation/abort` returned 400 once during the UI walk (tab mount). Not reproduced deterministically; recorded so a future pass can watch for it. | UI smoke. |

**F1 and F2 are different animals.** F1 is this branch's own code and its
consequence is a user left holding a config the preflight refuses, after being
told the plan was certified. F2 predates the branch (`_update_component`'s
remove+add is identical on master) and belongs to the network editor, not to
the FMEA feature — but the adequacy surfaces are what make it *visible*: before
this branch a lost `p_max_pu` column changed a dispatch, now it changes a
reliability verdict. It is recorded here, not fixed here, because fixing the
editor's update path is outside this PR's scope and wants its own change.

## 4. Method

- Both networks built with `build_ieee39.py`, exported to netCDF, imported
  through `POST /api/io/import/netcdf` — the same path a user's file takes.
- The journey (`journey.py`) writes each response to `NN_step.json` before the
  next call, so a crash or rate limit loses nothing, and scans every body for
  non-finite floats.
- Flag/fold/rate-zero behaviour was isolated with single-variable probes: flag
  `W16` alone; static 0.9 on `G33` with and without the flag; typed `q = 0` on
  `G34` — each against a fresh re-import, comparing COPT, MC, the margin table
  and the ELCC candidate list.
- F1's chain was traced through the code by an adversarial agent, reproduced
  live, and then re-reproduced through the repo's own `_Stubs` harness
  (`scratchpad/ieee39/review/margin_loop_review.md`).
- UI pass: `npm run build` then Playwright over the served `dist` — projects
  page → workbench → Results → Adequacy → FMEA, screenshots at each step,
  console errors and ≥400 responses collected.

## 5. Artifacts

| What | Where |
|---|---|
| builder + journey driver | `pypsa-gui/backend/smoke/ieee39/{build_ieee39.py,journey.py}` (committed) |
| journey responses, base / stressed | `scratchpad/ieee39/out2/`, `out3/` (46 JSON files each) |
| F1 live record | `scratchpad/ieee39/margin_loop_repro.json` |
| F1 code-trace + proposed fix + biting test | `scratchpad/ieee39/review/margin_loop_review.md` |
| UI screenshots | `scratchpad/ieee39/ui/*.png` |

## 6. Verdict

The feature holds up on a real system. Six studies, five engines and four
disclosure surfaces ran on a 39-bus network end to end, twice, with no 500, no
non-finite payload, no surface disagreeing with another about what it credited,
and the minors shipped in `60c4106` behaving as designed under load. The one
branch-owned defect (F1) is narrow, fully traced, and has a route-only fix with
a test that bites; the other serious finding (F2) is the network editor's, older
than this branch, and reported for its own change.

## 7. F1 fixed

`routers/results.py`, route-only — `services/adequacy/coupling.py` is the shared
controller and stays untouched:

* `_solved_margin: dict[float, float]` beside `_ceiling_missed` / `_last_at_ceiling`;
* one line in `solve_at`, after the clamp and before the solve, recording the
  margin that call is about to evaluate against the controller's `x`;
* `_translate` and the `m_star` line read that map, falling back to
  `to_margin(x)` for an `x` that never reached a solve.

The map is unambiguous because the controller never solves one `x` twice and
the pre-controller probe never becomes a row.

**Test:** `tests/test_adequacy_margin_loop.py::test_a_clamped_iterate_reports_the_margin_it_SOLVED`
— the existing `_Stubs` harness reaches the clamp with no special setup (the
default `lole_fn` misses at the informed start, the blind `x/4` step then asks
for 4.26 against the fixture's 1.34 ceiling, inside `MAX_MARGIN` so it is
clamped rather than schema-refused). It asserts every published `lever_value`
is a margin that was solved and is at or under the published ceiling, that
`lever_star`, `final` and the verdict name the ceiling, and that
`restore="final"` persisted that same margin.

**Bites (both verified, file restored byte-identical afterwards):** restore
`_translate` to `to_margin(row["eps_permyriad"])` → `assert 4.26 <= 1.34`;
restore the `m_star` line → `assert 1.63 == 1.34`.

The neighbouring spelling test (`test_the_verdict_names_the_margin_the_panel_tells_you_to_type`)
walked this same clamp path and certified 1.49 against a 1.34 ceiling without
noticing, because it asserts spelling only; it now also asserts
`lever_star <= margin_ceiling`.

**Live re-run**, stressed IEEE 39, same request that produced the defect
(`target_lole_h` 0.7, `restore="final"`):

| | before | after |
|---|---|---|
| iterate rows | 15.75 %, 363 %, 131.5 % | 15.75 %, 19.9 %, 19.9 % |
| `lever_star` | 1.315 (ceiling 0.1988) | 0.198772 = the ceiling |
| verdict | "verified at a reserve margin of 131.5%" | "verified at a reserve margin of 19.9%" |
| closing re-solve | `base_restored: false`, `validation_failed` | `base_restored: true`, `optimal` |
| preflight on the config left behind | refused, `reserve_margin_unreachable` | `ok: true`, 0 errors |

Suites: `test_adequacy_margin_loop.py` 60 passed, plus the coupling and
frontier suites (134 passed together).

F5 (the one wasted solve per clamped met run) is unchanged and still stands —
the two ceiling iterates above are it. F2, F3, F4 and the notes stand as
recorded.

## 8. The minors commit reviewed as shipped code

`60c4106` was also read adversarially as shipped code, against the whole-branch
note's definition of each finding, with edge probes on the head
(`scratchpad/ieee39/review/minors_review.md`). **No serious finding.** It closes
M3, M5, M6, M7, M9, M10, M11, M13, M14 and N1 as defined, and M2 and M4 for the
engine surfaces; every number-bearing change was checked against its neighbours
(the storage derate reads the same resolver row the margin does and is hashed
into both CRN keys; the fold clamp matches the margin's `[0, 1]` clamp; the
all-NaN rule matches the must-take `fillna(0)` and the margin's `_finite(mean, 0)`;
the disclosure lists are disjoint from `profile_units` by construction). All ten
new tests and the three updated ones go red against the pre-fix version of their
own fix's files.

Three MINOR follow-ups, none a silent number change, recorded rather than fixed:

| # | Where | What |
|---|---|---|
| **F6** | `copt.py:772-776` (must-take branch) | M2 clamps the *occurrence-bearing* branch of the membership walk. A **must-take** generator (no outage data) with a negative static `p_max_pu` still nets NEGATIVELY into the residual in both engines — residual 120 for a demand of 100 — while the margin lists it unpriceable. Same defect class, the other branch of the same walk; pre-existing, not opened by `60c4106`. |
| **F7** | `validation_service.py:2243-2244,2295-2299` | M2's expected fix had two halves: fold `max(cf, 0)` **and** exclude negatives from the `availability_may_include_outages` sentence. The engine half shipped; the preflight sentence still names a negative-static unit and states a formula no surface applies. |
| **F8** | `results.py:3683` (`/mc` payload) | M4's fix traded a wrong reason for no reason on one shape: a unit whose rate is **typed** 0 (flag not set) *with* a profile used to appear in `/mc`'s `deterministic_units` — true that no outages were sampled, false about why — and now appears in no `/mc` list at all. `/copt` still names it, through the rate-zero row note that M4 added. Strictly more correct and slightly less complete; closing it means a second list (`rate_zero_units`) on both payloads and a third clause in the chip, which is new API surface and is left as the maintainer's call. |

Plus nine NITs in the report (a vacuous final assertion in one new test, a stale
comment after M5, the blank-basis spelling tuple now written in three files, an
import placement, no frontend test for M10, and a folded-to-zero unit still
offered as a 0 MW ELCC candidate).

## 9. The rest of the findings closed

`c2915cf` closes F2, F3, F4, F6, F7 and F8. Every fix has a test demonstrated
red against the named removal, and the source file was restored byte-identical
(sha256) after each bite.

| # | Fix | Test |
|---|---|---|
| **F2** | The component's time-varying INPUT columns are carried across `_update_component`'s remove+add and put back under the OLD name, so the rename below re-keys them with everything else that refers to the component. Outputs are deliberately not carried — an edit invalidates the solve, and one component holding stale dispatch the rest of the network no longer has is a worse answer than none. | `test_review_ieee39.py`: a full-row PUT, a one-field edit, and the residual the engines build being identical either side. Bite: `saved_series = []` → the fixture's EUE goes 4.89 → 0.0. |
| **F6** | Both branches of the membership walk clip availability at 0 — the static cell and the column alike. | Residual 100 for a demand of 100 on each shape. Bite: unclamp → 350. |
| **F7** | `availability_may_include_outages` names a static in `[0, 1)` only. | `neg` absent, `half` present. Bite: drop the lower bound → both named. |
| **F8** | `rate_zero_units` on `/copt` and `/mc`, disjoint from `deterministic_units` and from `profile_units`; the chip reads "1 static CF folded · 1 includes outages · 2 rate 0" and its tooltip gives each reason. | Both routes driven; bite: drop either list → `KeyError`. |
| **F3** | The record carries the `reserve_margin` the sweep ran under (null when none), and a flat curve says so — naming the standard already covering every swept target instead of claiming every step still pays. | Backend: the field, set and null. Frontend: `curveIsFlat` plus the message. Bite: drop the field → `KeyError`. |
| **F4** | The panel maps the borrowed vocabulary: `system_cap` → "reserve margin", `voll` → "not binding". The wire is unchanged, so the controller's plateau pre-test and the two tests that pin it still hold. | `bindingLabel` unit test plus the row assertion, which used to pin the raw `voll`. |

**A new finding, pinned rather than fixed — F9.** Renaming any NON-Bus
component through the update route raises `KeyError` on pypsa 1.3.0:
`rename_component_names` derives the cross-reference column from the renamed
class (`generator`, `load`, `line`) and then indexes EVERY component's static
frame with it, so the first frame without that column raises. Verified on bare
PyPSA: Generator, Load and Line renames all raise; a Bus rename succeeds,
because `bus` is a column the frames that refer to it actually have. That call
is on master too, so renaming a generator from the Properties panel is a 500
today, before any of this branch's code runs. It is pinned as a known defect
(`test_renaming_a_non_bus_component_is_a_500_on_pypsa_1_3`) so that fixing it
— in PyPSA, or by re-pointing dependents here — fails the test and says so.

**Live re-run**, IEEE 39-bus base network, the same edit that produced F2:

| | before | after |
|---|---|---|
| COPT after a full-row PUT of `W16` | LOLE 0.319 h, EUE 98 MWh | LOLE 0.831 h, EUE 292 MWh — unchanged by the edit |
| `W16` ELCC candidate | 500.0 MW (nameplate) | 306.3 MW (its profile's peak) |
| preflight after the edit | 1 error, `reserve_margin_unpriceable_assets` | ok, 0 errors |
| next solve | `validation_failed` | `completed` |

Regression: 850 backend tests over every adequacy, review, validation and
editor suite the fixes touch; frontend 456 tests across the results, layout
and API trees with `tsc` clean.

What remains open from this review is F5 (a clamped met run spends one
redundant solve, bounded by the controller's own plan-hash check) and F9
above.

## 10. Full-suite parity on the merge candidate

The branch's parity claim was last proven at `177c9a4`. Three commits touched
backend source after it (`60c4106`, `210eb09`, `c2915cf`), so it was re-proven
on the head that would actually merge, `42c24c0`:

| | result |
|---|---|
| backend, whole suite | **3309 passed, 43 failed, 19 skipped** (27 min) |
| the 43 | byte-identical to pristine `master`'s baseline — `branch − master` and `master − branch` are **both empty** |
| frontend | **922 passed** across 96 files |
| `tsc --noEmit` | clean |

The 43 are the environmental set characterised in
`2026-09-08-baseline-failures-characterised.md` (39 environmental, 4 the
pandas-3 unpickler); none is introduced or masked by this branch. The passing
count rose from 3285 to 3309 with the 24 tests the minors and this review's
fixes added.

## 11. The journey re-run on the PyPSA this project PINS

Every live pass in §2 ran on the container's PyPSA 1.3.0. The project pins
**1.1.2**, and that gap is not hypothetical: it hid six broken tests and one
real schema gap (a link's secondary efficiencies accepting a non-finite value)
until CI ran the pinned version for the first time. So the journey was run
again against 1.1.2 — the C1 option in
`2026-09-10-next-steps-options.md`.

**Environment.** `pixi` is not available here, so the reproduction is a venv
carrying `pypsa==1.1.2` with `pandas<3` over the container's stack. The pandas
bound matters and is the fix for an earlier dead end: a first venv took 1.1.2
with the container's pandas 3, where linopy rejects Arrow-backed string arrays
and every solve dies. That environment was sound for metadata and schema paths
and useless for solves; this one runs both. Both IEEE 39 networks were rebuilt
under it rather than reused, so nothing crossed the version line.

**Result: no defect, and the two versions agree on every number.**

| | pinned 1.1.2 | 1.3.0 (§2) |
|---|---|---|
| steps, both runs | 46/46, **every one 200** | 46/46 |
| base: objective | 14.26 M€ | 14.26 M€ |
| base: COPT | LOLE 0.8312 h / EUE 292.5 MWh | identical |
| base: sequential MC (n = 2 000) | LOLE 0.4775 h / EUE 151.5 MWh | identical |
| base: ELCC G39 / G30 / G38 | 784.2 / 962.8 / 723.9 MW | identical |
| stressed: firm vs required | 8 850 / 8 850 MW, binding | identical |
| stressed: COPT, MC | 1.2636 h / 602.5 MWh, 0.8840 h / 437.3 MWh | identical |
| stressed: loop verdicts | coupling `unreachable` (2 solves), margin `unreachable`, ceiling 19.877 % | identical |

The three differences from §2's recorded runs are all this work's own fixes
showing up live, not version effects:

* the frontier record now carries `reserve_margin: 0.15` where §2 recorded
  `None` (F3);
* `rate_zero_units` is a list where §2 had no such key (F8);
* the stressed margin loop's second iterate reads **19.88 %** — the ceiling it
  actually solved — where §2 recorded **363 %**, the margin it merely asked
  for (F1).

That last row is the defect F1 fixed, seen from the other side. The third
iterate still reports 1752 %, and correctly: it is `solve_status: "error"`
with the condition *"a reserve margin of 1752.0% is beyond the configured
maximum of 500% — the search has run out of lever"*, so it never reached a
solve and has no solved margin to report. `lever_star` is null and the verdict
names the 19.9 % ceiling. That is the documented fallback in F1's fix, working.

**What this closes.** The feature's arithmetic, its disclosures and all six
studies behave identically on the version the project ships. What it does not
close: the self-hosted `Run validation` job, which has still never completed,
and the QA drivers, which cover no adequacy route (options C2 and C3).
