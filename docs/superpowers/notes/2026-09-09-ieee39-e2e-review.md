# IEEE 39-bus end-to-end review — `claude/solution-fmea-integration-0mx5lc` @ 60c4106

**Status:** review complete. **2 SERIOUS** (one branch-owned, one pre-existing on
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
| **F1** | **SERIOUS** | `routers/results.py:4887-4901` (clamp) → `services/adequacy/coupling.py:195-197,470` → `results.py:5062,5294,5158-5169,5205-5213`; `MarginLoopPanel.tsx:333,453` | **The margin loop certifies, displays and PERSISTS a reserve margin it never solved.** When the controller's blind step overshoots the fleet ceiling, `solve_at` clamps to `m_ceiling` and solves there — but returns nothing about which margin it evaluated, so the controller records the margin it *asked* for. Every downstream consumer is faithful to that wrong number: the iterate rows, `lever_star`, the verdict sentence, and on `restore="final"` the value written into the user's solver config. | Live, stressed IEEE 39 (ceiling **19.9 %**): `POST /results/margin_loop {"target_lole_h":0.7,…,"restore":"final"}` → status `met`, rows 15.75 % → **363 %** → **131.5 %**, verdict "verified at a reserve margin of 131.5%, and that margin has been APPLIED to your solver settings", `reserve_margin` left at 1.315, closing re-solve `base_restore_status: validation_failed`, and the next preflight refuses the config with `reserve_margin_unreachable`. Record: `scratchpad/ieee39/margin_loop_repro.json`. Also reproduced through the repo's own stub suite. |
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
