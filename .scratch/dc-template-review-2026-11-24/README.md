<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Critical review of the data-centre template and its assessment chain (2026-11-24)

**Scope.** This review covers `eh_datacenter` and `eh_datacenter_staged` on master `06859826`, end to end. The chain runs from compute load to energy, then reliability, the trade-off front, visuals, electrical dimensioning, grid code, export and control. Each stage is judged on code, tests and the running app, not on issue files. Grid-code references are to the public SAGC (May 2026) and generic engineering only (SAGC-1).

**How it was checked.**
- **Code reads.** Four read-only maps, then spot checks of every line cited here.
- **Tests run locally, all green:**
  - `test_eh_datacenter_staged_case`, `test_dc_time_to_power`, `test_energization` and `test_dc_curtailment`;
  - `test_adequacy_frontier`, `test_pcs_statcom`, `test_grid_compliance` and `test_control_settings`;
  - `tests/gridspine/test_psse_pack`, `test_emt_pack`, `test_dgs_writer`, `test_raw_writer` and `test_gfm_tuning`. Together the last two groups gave 161 passed, 1 skipped.
- **App.** The API and Vite ran locally, and I signed in as the dev admin:
  - I created projects from both templates over the API.
  - I ran the cost-vs-availability frontier on the staged template.
  - I probed the Gridspine, grid-readiness and grid-compliance routes.
  - I walked Guided §1 and §10 and the Expert results in Playwright. The downscaled shots are in `shots/`.
- **Not walked.** The 3D scene could not be shown because the template ships with no site location (te/01), so it was checked by code only. The chat dock needs a key, and no external tool (PSS/E, PowerFactory, PSCAD) is installed.

## Verdict

| # | Stage | State | Top gap | Severity |
|---|---|---|---|---|
| 1 | Compute load → required energy | Partly built: IT MW × utilisation × PUE(T), phased | Ambient temperature is a fixed Central-European cosine whatever the site (**04**); only `ai_training` is accepted, and there is no compute input (**05**) | major |
| 2 | Reliability integrated with the energy model | Built: the ENS cap sits inside the LP, plus MC LOLE/EUE, COPT, transformer N-1 and the UIF/SSTI screen | Deterministic ENS is 0 on the template, so reliability varies only in MC, which the trade-off never reads (in **01**) | major |
| 3 | Multi-objective: reliability vs capex vs opex vs CO2 | **Single points only.** The one curve has one axis and collapsed to one point on the template | No front, no CO2 axis, no capex/opex split (**01**); CO2 cannot be a DC goal (**02**); genset CO2 is understated about 3× (**03**) | **blocker** |
| 4 | Visuals: site, live demand vs energization, 3D | 3D scene with results projection, Blender export; energization as rows | No demand-vs-energization chart and nothing live before a run (**06**); no DC plant in 3D (**07**); no location, so no scene (te/01) | major |
| 5 | Electrical dimensioning | Transformers (MILP + N-1) and STATCOM (LP) are sized | No harmonic filter design (**08**); switchgear and cable ratings are typed, not chosen; no equipment schedule (**09**) | major |
| 6 | Grid-code compliance | Broadly built: the scorecard has about 20 rule rows, profiles plus a SAGC-type draft from the PDF, and the TRGC tracker (GC-09 UI merged) | gc/03 (planning-criteria rows), gc/24 (PASS without rating), gc/22, DC-29 (500 after a solve) | major (open issues) |
| 7 | Export: PSS/E, PowerFactory, PSCAD; Gridspine | Writers, read-back and judge are built and tested against own parsers and stubs | Nothing downloadable from a DC project (gc/23, gc/09 item 6); never validated in the real tools (**11**); the Gridspine panel refuses a DC project (409) | major |
| 8 | Control strategy: GFM / GFL | Island GFM tuning (droop, H) and steady-state Q modes | No grid-connected GFM/GFL design from SCR (**12**); the choice is lost in PSCAD and DGS, and the AS-07 sheet is unreachable (**10**); cs/02, cs/03, cs/06 open | major |
| 9 | Missing blocks | — | Cooling plant, thermal storage and water (**13**); the front (**01**); the equipment schedule (**09**); filter design (**08**) | — |

**Bold** numbers are new issues in `issues/` (all `needs-triage`). Others cite open issues: `te/` is `template-e2e`, `gc/` is `grid-compliance`, `cs/` is `walkthroughs/control-strategy-2026-11-17`, and `DC` is `datacenter-grid-readiness`.

**Top 5, in build order:**
1. **01** — the multi-objective front. It is the owner's core ask, and today it collapses to one point.
2. **03 + 02** — correct the genset CO2, then make CO2 a cap. Small, and 01 needs both.
3. **gc/23 + 11** — let the PSS/E, EMT and DGS packs leave the app, then validate them once in the real tools.
4. **04** — ambient temperature from the site location. A cheap fix with a large effect at a hot site.
5. **06** — the demand-vs-energization timeline. It is the one visual the owner named that does not exist.

## 1. Compute load → required energy

**What exists.**
- **Load model.** `services/dc_load_profile.py:125-129` gives the facility load as `IT MW × utilisation(t) × PUE(T(t))`, where `PUE(T) = pue_at_design + slope × max(0, T − 20 °C)` (`:102-104`). `T(t)` is `ambient_c` (`:92-99`).
- **Load split.** `_apply_dc_profile` (`project_templates/eh_templates.py:664-671`) puts the critical share on `it_load` and the rest on `cooling`.
- **Phasing.**
  - The staged template uses `DC_PHASES` (`eh_templates.py:440`) with a connection energization schedule (`DC_ENERGIZATION_MW`, `:450`).
  - The AI-training workload model (`AiWorkloadSpec`) covers checkpoint dips, idle hours and oscillation for the dynamic envelope.
  - Phase energy and rack counts come from `services/dc_it_energy.py`.

**Wiring.**
- **Guided.** The DC card is `DcLoadForm` (`pages/hubDesign/cards/StartCard.tsx:128`). It sets IT load, PUE at design, backup and architecture, and the create body always sends `dc_load`.
- **Expert.** The same fields are in `NewProjectWizard.tsx:371`; architecture and backup sit in `DcArchitectureSection` and `DcBackupSection`.

**Tests.** `test_dc_load_profile.py` covers PUE only above the design temperature, the critical share holding every hour, and phases per period. `test_dc_load_form_defaults.py` checks the form defaults against the presets.

**Gaps.**
- **Ambient temperature ignores the site.** It is always a 10 °C-mean temperate cosine, with no location or weather input and no field shown (**04**, major).
- **Workload kinds.** `inference`, `mixed` and `flat` are in the schema but refused at create. There is no compute → IT MW layer (accelerators × kW). `waste_heat_export` is a field that no code reads (**05**, major).
- **Default build without the spec** (API create with no body): the single-year network keeps a fixed 8–14 MW cooling day shape (`eh_templates.py:179-184`) that does not depend on IT or PUE. The UI always sends the spec, so this matters only for API and chat creates (minor, noted in 13).
- **Critical share.** It is a share of the *facility* load, so PUE excursions put cooling on the critical IT bus. At PUE 1.5 the critical load is 1.125 × IT. This is acceptable only if documented; 13 replaces it with a cooling plant (minor).

## 2. Reliability assessment integrated with the energy model

**What exists.**
- **ENS cap inside the LP.** `_wrap_with_ens_cap` (`services/solver/adequacy.py:225`) adds the reliability goal (`services/reliability_goal.py`), so expansion sizes to it.
- **Sampled-outage studies.** Sequential Monte Carlo LOLE/EUE (`services/adequacy/mc.py`), COPT screening (`copt.py`), ELCC (`elcc.py`) and the LOLE verdict.
- **N-1.**
  - Classes B/C contingency sweep: `adequacy/sweep.py`.
  - Transformer N-1 in campus sizing: `campus_electrical_service.py:157`.
  - Post-solve SCLOPF N-1: `solver/diagnostics.py:502`.
- **UIF.** The SSTI unit-interaction screen is `grid_readiness.py` `_uif_row`.
- **DC backup sizing and proof.** `adequacy/dc_backup.py` (`size_bess` `:240`, `assess` `:1296`).
- **Loops.** The sizing, margin and coupling loops re-solve on reliability levers.
- **EH study.** It runs the ENS stage on the solved network and MC-certifies the frozen fleet (`eh_study.py:237`, `:504`).

**Wiring.**
- **Guided.** The Goal card sets the target. The §10 Reliability tile ("Expected shortfall") reads the EH study (shot `03-guided-s10-results.png`).
- **Expert.** The Reliability results tab and the FrontierPanel.

**Tests.** `test_adequacy_ens_cap.py` (cap binds, per period), `test_adequacy_frontier.py`, `test_dc_backup.py` and `test_intent_comparison_dc_template.py`.

**Gaps.**
- **Reliability does not vary in the dispatch the trade-off reads.**
  - On the staged template the LP has no outages and VOLL is 5000 €/MWh, so ENS is 0 at every target (reproduced, see 3).
  - Reliability varies only in the MC certification, which the frontier and comparisons do not use.
  - Folded into **01** (major).
- **The frontier is not part of the DC EH study.** `frontier_default` is unset for the DC packs (`eh_study.py:179`), so it runs only by hand in Expert (in **01**).
- **Investigated, not a bug.** A second login session first reports VOLL 0 and has the frontier refused until it opens the project (`/projects/{id}/activate`). Solver state is per login session, and the UI opens the project. A first create after a fresh server start returned 5000.

## 3. Multi-objective result: reliability vs capex vs opex vs CO2

**Plainly: no front exists. There are only single points.**
- **The one curve is single-axis.** `services/adequacy/frontier.py` is an epsilon-constraint sweep on the ENS cap only: 8 default targets, at most 12, and one total cost per point.
  - On `eh_datacenter_staged` (API, master `06859826`), all 8 points returned `binding: "voll"`, `achieved_ens_mwh` 0 and the same total system cost.
  - `knee` and `warning` were null.
  - So the curve is a single point, and the panel would draw a flat line without saying why.
- **Comparisons are presets, not a front.** The intent comparison (`services/compare/intent_table.py`) and the architecture comparison (`/campus-electrical/{name}/dc-architecture/compare`) give one point per preset.
- **CO2 is reported, not constrained.**
  - It is reported in Expert (`pages/results/Emissions.tsx`, `services/compare/emissions.py`).
  - It can be *priced* (`co2_price`, null in both DC configs; `services/solver/assumptions.py:684-801`).
  - It can be *capped* only through the generic Expert "CO2 cap preset" GlobalConstraint (`pages/SolverSettings.tsx:1070-1121`).
  - Neither template sets a cap.
  - Guided shows no CO2 at all: there is no `co2` in `pages/hubDesign/*.tsx` or `headline_kpis.py`, and §10 has no CO2 tile (shot).
- **The CO2 numbers are understated.** DC gensets have no `efficiency`, so the gas carrier's 0.20 t/MWh (fuel) is counted per MWh of electricity. That is about a third of what the spec's own 2.8 heat rate implies. The grid intensity is one hard-coded 0.35 t/MWh (**03**).

**Gaps:** **01** (blocker), **02** (major), **03** (major).

## 4. Visual completeness

**What exists.**
- **3D scene.**
  - `pages/SiteCanvas.tsx` and `site3d/*`: parametric templates plus five CC0 hero GLBs.
  - Results projection in `site3d/resultsLayer.tsx`, fed by `GET /sites/{id}/scene/results`.
  - A Blender-free GLB export (`services/site_scene/export.py`).
  - A Blender refinement job, which returns 503 `worker_unavailable` without a configured worker.
- **Layout and photo proposals.** These are site-scene 07 (ready-for-human).
- **2D map.** `MapCanvas` with the map-layout service.
- **Energization.**
  - Rows per period: `components/gridReadiness/EnergizationRows.tsx`, `PUT /{name}/flow/energization`.
  - The run monitor's text list: `runMonitor/EnvelopeSection.tsx:84`.
  - The time-to-power headline: `services/dc_time_to_power.py`, `headline_kpis.py:66`.

**Wiring.**
- **3D.** Reachable from Expert's canvas switcher ("Site 3D") and from the Guided Start card's site setup, but only after the project is saved and has a location.
- **Guided.** §1 says "The site has no location: no bus is placed" (shot `01-guided-hub.png`); te/01 is open.

**Tests.** `test_site_scene_*.py` (routes, layout, export, Blender job, worker, return, GLB read), `site3d/*.test.ts(x)`, `test_energization*.py`, `test_dc_time_to_power.py` and `EnergizationRows.test.tsx`.

**Gaps.**
- **Demand vs energization.** There is no chart of demand against energized import plus on-site firm capacity per period. Time to power counts grid power only, and nothing updates before a run (**06**, major).
- **3D has no data-centre plant.** No cooling plant, substation or switchgear building, and no per-phase build-out; the data hall is the Load fallback (**07**, minor).
- **No location, no 3D for a fresh DC project** (te/01, ready-for-agent; its Status is right).
- **Open site-scene issues:** 06 (Blender worker, ready-for-agent), 08 (AI-guided flow), 10 (artifact streaming) and `10-put-edit-resets-solve-to-idle` (needs-triage). Two files share number 10.

## 5. Electrical asset dimensioning

**What exists.**
- **Transformers.**
  - Chosen by the campus MILP from library ratings (`defaults_pack/.../values.csv`, `tr_*` rows), with `n_minus_1` true and a 0.2 margin (`campus_electrical_service.py:157`, `:1983`, `:2396`).
  - Taps are solved per hour, and the reason is recorded when the range runs out (`:987-993`).
  - The template's own site transformer is a fixed 80 MW Link (`eh_templates.py:173`).
- **STATCOM and reactive compensation.** LP variables `q_ss` and `q_dyn`, priced per Mvar-year (`services/solver/pcs_statcom.py:273-286`), with AS-05 coupling to the PCS. Results are in `CampusUnitQCurveSection.tsx`; tests in `test_pcs_statcom.py`.
- **Banks and reactors.** Capacitor banks and shunt reactors are equipment kinds with capex (`campus_electrical_service.py:1047`).
- **Harmonics.**
  - The TDD row (IEEE 519) and the harmonic row (IEC 61000-3-6).
  - The impedance scan with a resonance clearance (`CampusHarmonicScanSection.tsx`).
  - Spectrum input (asset-sizing 09).
- **Switchgear and cables.**
  - The IEC 60909 short circuit (`gridspine/static/campus_sc.py`) is checked against `ik_rated_ka` and `max_i_ka` *typed* in the campus file (`gridspine/ingest/campus.py`, `_BUS_OPTIONAL`, `_CABLE_FIELDS`).

**Wiring.** Expert reaches all of this through Grid connection › Grid study › Run study; Guided through "Run grid checks" (§10 tile) and the Site card scorecard.

**Gaps.**
- **Harmonic filters are never sized.** There is no filter kind, rating, tuning or capex, so a failing TDD has no remedy (**08**, major).
- **Switchgear and cable ratings are declared, not chosen,** and there is no single equipment schedule (**09**, major). gc/24 ("PASS while the limit is blank", ready-for-agent) is the display half and is in flight with the GC-23+24 builder.
- **The site transformer in the energy model is a fixed 80 MW Link,** not linked to the campus MILP's choice (minor, noted in 09).
- **Open:** asset-sizing-loop 20 (the loop applies the commercial layer, needs-triage) and DC 28 (solved-size rows "edited since", ready-for-agent).

## 6. Grid-code compliance (SAGC / KSA TRGC chain)

**What exists.**
- **Readiness scorecard.** `services/adequacy/grid_readiness.py` has rules at `:112-135` and rows for:
  - SCR/ESCR and grid strength;
  - ramp, oscillation, RVC and reactive capability;
  - harmonics, TDD, unbalance, flicker and motor start;
  - P–V/Q–V;
  - SSTI, UIF, subsynchronous and island support;
  - LOLE;
  - frequency services.
- **Profiles.** `gridspine/templates/data/grid_codes.yaml` ships EU RfG/DCC, generic and IEEE 2800/NERC. A SAGC-type profile is drafted from an uploaded PDF with clause and page (DC 10), and 60 Hz holds throughout (DC 09).
- **TRGC tracker (GC-09).** `services/grid_compliance/` (catalogue, tracker, pack, revision log), `routers/grid_compliance.py` and `components/GridComplianceSection.tsx`, mounted in `CampusElectricalPanel.tsx:289`.
  - The "pack" is the deliverable's report ZIP, not the model files.
- **Dynamic studies (ride-through, RMS, EMT) are out of scope** by GC-SCOPE-1, and no row pretends otherwise.

**Wiring.**
- **Guided.** The Site card scorecard (`SiteCard.tsx:314`) and "Run grid checks".
- **Expert.** `IssuesPanel.tsx:420` and Grid study. The tracker lives only in the Expert Grid study block.
- **Probe.** `GET /api/grid-compliance/{name}` and `/campus-electrical/{name}/grid-readiness` both answer 200 on a fresh DC project.
- **Default profile.** A fresh DC project defaults to the EU RfG/DCC Continental Europe profile (50 Hz). A 60 Hz SAGC project must draft its own profile first. This is by design, but nothing in Guided says so (minor; fits te/05 words).

**Tests.** `test_grid_readiness_*.py` (tdd, unbalance, voltage stability, motor start, rules, limits, live, poc_xr), `test_grid_compliance*.py`, `GridComplianceSection.test.tsx` and `CampusGridCodeSection.test.tsx`.

**Gaps (all already filed).**
- **Major:**
  - gc/03, planning-criteria strength rows in the run (ready-for-agent);
  - gc/24, PASS without a rating (ready-for-agent, in flight);
  - gc/22, campus judgment storage (ready-for-agent);
  - DC 29, a 500 after a foreground solve (needs-triage).
- **Minor:** gc/13 genset in strength, gc/08c–f subsync follow-ups, and gc/10 assistant tools (needs-triage).

**Status corrections.**
- **gc/09 reads "ready-for-agent; … UI and item 6 wiring not started".** Its backend (#1577) and UI (#1854) have both merged. Only item 6, the DGS download, is left. Suggested Status: `ready-for-agent; backend #1577 and UI #1854 merged; left: item 6 DGS download`.
- **Two files are numbered 13** in `grid-compliance/issues/`: `13-genset-in-strength-fault-level` and `13-pack-manifest-ratings`.

## 7. Export to PSS/E, PowerFactory and PSCAD; preliminary run in Gridspine

**What exists, all in the `gridspine/` package.**
- **PSS/E.**
  - RAW (`handoff/raw_writer.py`).
  - DYR with REGCA1/REECA1/REPCA1 (`dyr_writer.py`); REGFM is not written (owner decision I8).
  - SEQ, CON/MON/SUB and psspy scripts.
  - The PSS/E pack (`psse_pack.py`), which refuses REPCA1 without an aggregated plant.
- **PowerFactory.** DGS (`dgs_writer.py`, `PENDING_VERIFICATION` at `:95`).
- **PSCAD.** The EMT pack (`emt_pack.py`), with an MHI API stub "untested against a PSCAD install".
- **Campus bundle.** `handoff/campus_bundle.py` combines the PSS/E pack, the EMT pack and `gfm_models.csv`.
- **Read-back.** `gridspine/readback/` (pack judge with a SHA-256 manifest; PF CSV compare).
- **Tests run green:** `test_psse_pack`, `test_emt_pack`, `test_dgs_writer`, `test_raw_writer`.

**Wiring from a DC project.**
- **The campus study writes the bundle** (`gridspine/drivers/campus_study.py:701`), but no route or button serves it.
  - gc/23 is open (ready-for-agent, GC-23+24 builder live).
  - The DGS download is gc/09 item 6.
- **Results › Hand-off › Exports** offers bundle, NetCDF, CSV, Excel, MATPOWER, workbook and template only.
- **The Gridspine panel refuses a DC project.** `GET /api/gridspine/{name}/status` returned **409**: "a capacity_expansion project; this operation needs a planning → dynamics project".

**Preliminary run in Gridspine: met, by another door.** The DC Grid study (`POST /campus-electrical/{name}/run`) is a Gridspine run: `gridspine.drivers.campus_study` covers load flow, IEC 60909, the harmonic scan, P–V/Q–V and the island. The Gridspine *panel* pipeline (ranking, screening, per-hour hand-off) is for Study projects only (pio/08).

**Gaps.**
- **No export leaves the app for a DC project** (gc/23, gc/09 item 6).
- **No validation in the real tools** (**11**, major).
- **The control choice is lost in PSCAD and DGS** (**10**).
- **gc/04b** (detailed REPCA1 plant), **gc/12** (contract-connected channel; PR-B left), **gc/13-pack-manifest-ratings**, **gc/04e**, **gc/01e/01f** (series capacitors in scan and writers) and **gc/17/25** (needs-info) stay open as filed.

## 8. Control strategy: grid forming and grid following

**What exists.**
- **Presets.** Per-unit control presets (`gridspine/schema/island.py:236-272`). The template's `island_config.json` sets BESS `gfm_droop` and PV `gfl_qu`.
- **Island GFM tuning.** Droop and equivalent H by bisection on a one-bus frequency model (`gridspine/static/gfm_tuning.py:190`, `frequency.py:325`). Served by `services/campus_island_tuning.py` and `/island/gfm-settings` GET/POST/apply, with UI in `CampusIslandGfmSection.tsx`. The Guided §10 line reads "Grid-forming settings: no recommendation yet".
- **Steady-state Q control.** Modes and the PoC P–Q chart (`gridspine/static/voltage_control.py`; gc/02 done).
- **AS-07 control-settings sheet** (`services/control_settings.py`), written only by the sizing loop.
- **Tests run green:** `test_gfm_tuning`, `test_control_settings`.

**Gaps.**
- **No grid-connected strategy.** Nothing turns the SCR/ESCR result and the profile's required services into a GFM/GFL choice and settings (**12**, major).
- **The choice is lost in PSCAD and DGS.** The control-settings sheet has no route (**10**, major).
- **Open from the control-strategy walk:**
  - cs/02, no Controls block in the asset form;
  - cs/03, no Guided route for the operating reserve;
  - cs/06, Guided results hide the Expert run and the Control card;
  - all ready-for-agent, Status right.
  - cs/13 (island study written after a concurrent edit) is needs-triage.
- **Dynamic confirmation (EMT/RMS) stays external** (GC-SCOPE-1).

## 9. Modules or blocks missing for the data-centre template

| Block | Why the DC chain needs it | Issue |
|---|---|---|
| Multi-objective front (ENS × CO2 × capex/opex) | The owner's decision surface | **01** |
| CO2 goal and correct genset intensity | CO2 as a constraint, on right numbers | **02**, **03** |
| Site climate → PUE | Cooling energy and peak load at hot sites | **04** |
| Compute layer and inference/mixed workloads | "Assume the compute load" | **05** |
| Demand-vs-energization timeline | Time to power per hall, visually | **06** |
| DC plant in the 3D library | A complete site picture | **07** |
| Harmonic filter design | Remedy for a failing PQ row; capex | **08** |
| Equipment schedule (switchgear, cables, all ratings) | Electrical BoM for procurement and the TSO | **09** |
| Control choice in every export | PSCAD/PF models carry the designed control | **10** |
| Real-tool validation record | "Validated in those models" | **11** |
| Grid-connected control design | GFM/GFL from grid strength | **12** |
| Cooling plant, thermal storage, water (WUE) | Flexibility under a capped connection; water as a siting constraint | **13** |

Not proposed: protection coordination (relays are in the island plan, I5), GPU step-load envelope (asset-sizing 04, done), business case (BC-ALL-1, `business-case/`, mostly done), and dynamic studies (GC-SCOPE-1).

## New issues

| File | Severity | Model | Review |
|---|---|---|---|
| `issues/01-multi-objective-front.md` | blocker | opus | GK A + UX |
| `issues/02-co2-cap-goal.md` | major | sonnet | GK A + UX |
| `issues/03-genset-co2-per-mwh-el.md` | major | sonnet | GK A |
| `issues/04-ambient-from-site-location.md` | major | sonnet | GK A + UX |
| `issues/05-compute-layer-and-workload-kinds.md` | major | sonnet | GK A + UX |
| `issues/06-demand-vs-energization-timeline.md` | major | sonnet | UX + GK B |
| `issues/07-site-3d-dc-asset-vocabulary.md` | minor | sonnet | UX |
| `issues/08-harmonic-filter-dimensioning.md` | major | opus | GK A |
| `issues/09-equipment-schedule-switchgear-cables.md` | major | opus | GK A + UX |
| `issues/10-control-choice-in-pscad-and-dgs.md` | major | opus | GK A |
| `issues/11-external-tool-validation-record.md` | major | person with licences, then sonnet | GK A |
| `issues/12-grid-connected-control-design.md` | major | opus | GK A + UX |
| `issues/13-cooling-plant-and-water.md` | major | opus | GK A + UX |

## What this review skipped

- **The 3D scene in the browser.** The template has no location, so it was checked by code read only.
- **The chat dock.** No API key.
- **Any external tool.** No PSS/E, PowerFactory or PSCAD install.
- **The single-year template's frontier.** Only the staged one was run.
- **The MC certification run.** Not run on either template.
- **Risk.** Issue 01's "collapses to one point" is shown on the staged example's 3-week sample. A different VOLL, or outages added to the LP, would change it; issue 01 asks for words in that case either way.
