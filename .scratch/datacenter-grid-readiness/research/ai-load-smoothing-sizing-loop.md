<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Research note: AI-load fluctuations, smoothing assets and a preference-driven sizing loop

Status: research note for the needs-triage datacenter-grid-readiness spec ([spec](../spec.md)). It changes no
spec, issue or code file. Section 5 proposes issues; none is filed.

- **Question (owner, 2026-10-30):** when sizing assets for an AI factory, how do we account for the fast power
  swings of AI loads? BESS (high C-rate or energy), supercapacitors, STATCOMs, UPS and diesel gensets can each
  help. How does a customer's design preference (for example "replace diesel", "keep diesel plus a short
  smoothing BESS" or "no UPS") enter capacity expansion, and how does the sized result flow on to the
  electrical design, the grid-code checks and the control settings, and back again?
- **Inputs:** a vendor deck shared by the owner (Hitachi Energy, "Power Conversion Solutions", pp. 22-29, 33),
  treated as a vendor claim. Each claim below is checked against a primary source where one could be read.
- **Code state read:** `master` at `bd15cd98d`. Paths are relative to the repo root. `BE` means
  `pypsa-gui/backend`, `ADQ` means `BE/services/adequacy`, and `GS` means `gridspine`.
- **Source tags:** [P] = primary (standard body page, regulator, TSO, NERC, statute, peer-reviewed or arXiv
  full text); [S] = secondary summary; [V] = vendor claim; **not verified** = no primary text could be read.

## 1. Short answer

**Partly, and most of the missing parts reuse code that already exists.** The app can size for AI-load
fluctuations at hourly fidelity and screen the sub-hourly effects in closed form. It cannot simulate them
grid-connected below one second. The step that is missing is a loop that turns the sub-hourly results into
constraints on the hourly sizing.

| Timescale | What happens | Fidelity in the app today | Fidelity after section 4 |
|---|---|---|---|
| **ms (< 0.1 s)** | GPU transients (~50 ms, [P] arXiv 2508.14318), fault ride-through, UPS transfer, converter current limits, harmonics | Static screens only: IEEE 519 TDD, harmonic scan, fault levels, SCR/ESCR. The 1 ms island frequency simulation (`GS/static/frequency.py`) covers island events only. | Unchanged. Ride-through and control interaction need EMT/HiL outside the app, which the spec puts out of scope. The app exports the sized plant and the settings for that study. |
| **s (0.1-60 s)** | Compute/communication oscillation (0.2-3 Hz), training start ramps (1.9 pu/s for 250 ms), checkpoint dips, rapid voltage change, flicker (Pst) | Closed-form envelope (`dc_load_profile.dynamic_envelope`) and smoothing screen (`ADQ/smoothing.py`). These are checks after sizing, and they only see committed batteries. | **Derived linear constraints** on the LP: smoothing power and energy floors, state-of-charge (SOC) headroom, and a cap on the residual flicker step. Each is followed by an exact screen. |
| **min (1-60 min)** | Ramp limits in MW/min, the variability limits behind area control error (ACE), checkpoint cycles, SOC recovery | Ramp and oscillation rows in `ADQ/grid_readiness.py`. Nothing in the LP. | Ramp-limited energy floors (the Marcos method) plus an hourly headroom constraint in the LP. |
| **h (1 h-days)** | Energy arbitrage, backup autonomy (diesel replacement), adequacy | Hourly PyPSA LOPF (`BE/services/solver_service.py`). Battery energy is tied to power by a fixed `max_hours`. The island ride-through rows are opt-in. | Separate power and energy variables, duration options (1/2/4 h), backup SOC reserve, and diesel run-hour caps. |

An hourly LP cannot see anything below one hour. Sub-second effects can only enter it as **derived bounds and
cuts on capacities and on hourly headroom**: the LP chooses MW, MWh and MVA, and the fast behaviour is checked
on those choices afterwards.

## 2. What the app already has

### 2.1 Specs and their state

| Spec | State | What exists | What is missing for this question |
|---|---|---|---|
| DC 01 GPU load profile | done (#421) | `BE/models/flex_archetypes.py` `AiWorkloadSpec` (ramp_s, checkpoint interval/duration, oscillation amplitude/period, idle share), `DataCentreLoadSpec`, `BessSpec`, `GensetSpec`. `BE/services/dc_load_profile.py`: `hourly_profile`, `dynamic_envelope` (max dP/dt in MW/s and MW/min, swing, checkpoint dip, oscillation band), `read_envelope`. | There is no sub-hourly time series and no spectrum: the "spectrum" is a single (amplitude, period) pair. The envelope never reaches the LP. |
| DC 02 grid-readiness scorecard | done (#442) | `ADQ/grid_readiness.py` `grid_readiness` with rows `_ramp_row`, `_oscillation_row`, `_rvc_row`, `_flicker_row`, `_harmonic_row`, `_tdd_row`, `_scr_rows`, `_after_battery`. | Rows only report a verdict and never feed back. `_flicker_row` reads `flicker_units` (Links flagged `eh_process` or `flicker_step_mva`), **not** the GPU envelope. |
| DC 03 battery in place of diesel | header says needs-triage; partial in code | `ADQ/smoothing.py` (`smoothing_screen`, `step_mwh`, `checkpoint_mwh`, `oscillation_mwh`, `coverage`, `response_latency`, `LATENCY_S` GFM 0.02 s and GFL 0.2 s, assumed). `ADQ/site_peak.py` `outage_reserve_mwh`, `ride_through_batteries`. Backup energy is reserved first and the rest goes to smoothing. | No `backup_mode` (genset, bess or hybrid). The screen counts **only non-extendable** StorageUnits, so it cannot see a battery the LP sized. There is no avoided-genset cost. |
| DC 04 control presets | done (#422) | `GS/schema/island.py` `PRESETS` (grid_following, grid_forming_support, fast_frequency_response, voltage_support), `select_preset`, `ffr_sizing`, `has_grid_former`. GUI: `campus_electrical_service.select_island_preset`. `GS/static/gfm_tuning.py` `recommend_gfm_settings`. | No power-plant-controller (PPC) settings output: no ramp limiter, smoothing filter cut-off or SOC band. Controller latency appears only as the assumed `LATENCY_S`. |
| DC 05 architecture comparison | needs-triage; not built | `ups_distribution_loss_share` and PUE in the archetype. | No architecture parameter (AC, MV UPS, 800 Vdc, SST) and no conversion-loss chain. This is the natural home for "preferences". |
| DC 10 grid-code profile | done (#691) | `GS/templates/grid_codes.py` (`validate_profile`, `band_for`, `_check_dynamic` for rows that need an RMS study), `GS/templates/data/grid_codes.yaml`, `GS/static/campus_compliance.py`. | No ramp-rate or power-variation key for loads (for example ERCOT's draft 10 MW per 5 s). Limits are generic and "assumed". |
| DC 11 SCR/ESCR | done (#693) | `GS/static/strength.py` `scr`, `escr` ((S_sc - Q_c)/P_n), `wscr`, `strength_table`. `GS/static/strength_outages.py`. `ADQ/scr_gate.py` `classify_scr` (warns below 3.0). | A low SCR is reported but never turned into a grid-forming (GFM) MVA requirement for the sizing. |
| DC 12 PQ screening | done (#726, #728, #754) | `GS/static/unbalance.py`, `GS/static/harmonic_scan.py` `impedance_scan`, `GS/static/current_tdd.py` `tdd_table` (IEEE 519 by Isc/IL). | Fine for checking. Not linked to sizing. |
| G2P 17 harmonic/flicker | done (#707, #727) | `GS/static/flicker.py` `pst`, `flicker_table`, `limits_from_profile`. `GS/static/harmonics.py` `harmonic_table`. Drivers `GS/drivers/campus_study.py` `flicker_campus`, `harmonics_campus`. | The Pst inputs are the step and repetition rate of process units. The GPU envelope's oscillation and checkpoint steps are not passed in. |

### 2.2 Engines

- **Capacity expansion.** PyPSA LOPF via linopy (`BE/services/solver_service.py` `run_simulation`),
  hourly with multi-period investment. tsam aggregation is used only for myopic runs
  (`BE/services/time_aggregation_service.py`). Add-ons use `extra_functionality` wrappers:
  `BE/services/solver/adequacy.py` (`_wrap_with_reserve_margin`, `_wrap_with_ens_cap`,
  `_wrap_with_import_energy_cap`) and `BE/services/solver/objective.py`.
- **Storage.** Storage is modelled as PyPSA `StorageUnit` with a fixed `max_hours`, so energy = p_nom x
  max_hours: **power and energy are not separate decision variables**. Other durations need another
  StorageUnit or the `storage_duration` lever in `ADQ/levers.py` (4/24/72 h, which re-solves). The defaults
  pack prices power and energy separately (`battery.power.*`, then `BE/services/asset_schema/derive.py`).
  `ADQ/redundancy.py` adds a spare 4 h unit. **There is no supercapacitor and no C-rate field anywhere.**
- **Reserve, headroom and ramp.** The LP has no generic operating reserve and no ramp constraint. The only
  headroom rows are the opt-in island rows (`SolverConfig.island_constraints`) in
  `BE/services/solver/island.py`: `_reserve_row` (sustained ride-through energy, UPS bridge),
  `_instant_rows` and `_seamless_rows` (ΔP+/ΔP- headroom, GFM MVA, RoCoF), and `_event_rows`. They are
  refused in rolling-horizon, SCOPF and myopic runs. **These rows are the template for the new constraints.**
- **Adequacy and IC loops.** `ADQ/coupling.py` (solve under an ENS cap, Monte Carlo, retune the cap,
  re-solve), `ADQ/frontier.py` (ε-constraint), `ADQ/margin_lever.py`, and their `*_loop_runner.py`
  modules. **This is the pattern to reuse for an outer sizing loop**: plain control flow around repeated
  solves.
- **Electrical design (gridspine).** `BE/services/campus_electrical_service.py` `run` with stages
  prepare, rank, island_checks, cuts, size, harmonic_scan, voltage_stability, invest. It calls
  `GS/drivers/campus_study.py`:
  - `size_campus` runs power flow over the critical hours, sizes transformers (`campus_flow.size_transformers`
    with `SizingCriteria`: 20 % margin, N-1), sizes reactive compensation (`campus_reactive.reactive_need`,
    `size_compensation`, `ibr_q_capability`), and computes fault levels and compliance.
  - `invest_campus` buys library assets (`campus_invest.select_assets`, `campus_milp.select_assets_milp`).
  - The asset library is `GS/templates/data/campus_assets.yaml`: transformers, cables, capacitor banks,
    shunt reactors, STATCOMs (±2/5/10/20 Mvar at 20/33 kV) and switchgear.
  - Unit kinds (`GS/schema/island.py`): bess, pv, wind, fuel_cell, genset, ups, load. UPS has `walk_in_s`
    and `transfers_on_islanding`. Genset has `ramp_pu_per_s` and `load_step_max_pct`.
- **The loop today is one-way.** The LP sizes the plant. The campus study sizes transformers and STATCOMs
  and checks fault levels and compliance. `grid_readiness` reports verdicts. **Nothing goes back.** No
  function turns a failed check into an LP bound, and the PCS MVA of a sized BESS does not reduce the
  STATCOM Mvar the LP sees.

## 3. Primary-source findings

The tags are the ones defined at the top. Full URLs are in the list at the end of this section.

### 3.1 What the loads do

- AI training alternates compute and communication phases. This swings "tens of megawatts within a single
  datacenter", with spectral energy around 0.2-3 Hz and sub-second (~50 ms) transients. One utility spec it
  cites as an example limits content in a critical band of ~0.1-20 Hz to 20 % of in-band energy. Ramp limits
  are stated in MW/s, without numbers. A GB200 power floor of 90 % TDP costs about 10.5 % extra energy. The
  paper recommends GPU-side smoothing plus rack storage, and BESS at larger scale. [P] Choukse et al.,
  arXiv 2508.14318 (2025).
- A 50 MW block of a 200 MW AI training site ramped at **1.9 pu/s for ~250 ms**. One site changed
  **35-70 MW within a minute** at start and stop. AI/HPC sites "commonly exclude UPS protection for the IT
  equipment" and rely on checkpoints instead. [P] NERC LLTF white paper "Characteristics and Risks of
  Emerging Large Loads", Jul 2025.
- NVIDIA GB300 power shelves store **65 J per GPU** in capacitors and use a power floor and "GPU burn" to
  shape ramps; NVIDIA claims 30 % lower peak demand. The storage does not export. [V] NVIDIA technical blog,
  28 Jul 2025.
- **What this means for sizing:** part of the smoothing is already inside the IT equipment (power floor,
  rack capacitors). The site-level asset only has to cover the residual. A power floor costs energy, so it
  belongs in the LP as a load uplift.

### 3.2 Large-load loss events and NERC

- **10 Jul 2024** (Eastern Interconnection; press reports place it in Dominion's area): a lightning arrester
  failed on a 230 kV line, and auto-reclosing gave **6 faults in 82 s**. About **1,500 MW** of data-centre
  load moved to backup through the sites' own controls; utility equipment tripped none of it.
  - About **1,260 MW** latched off at the third reclose because of "disturbance counting" schemes (typically
    **3 dips in 1 min**). It did not return for hours.
  - Frequency peaked at 60.047 Hz and voltage at 1.07 pu.
  - Static UPS rode through. DRUPS needed a manual transfer back.
  - [P] NERC Incident Review "Considering Simultaneous Voltage-Sensitive Load Reductions", 8 Jan 2025.
  - This confirms the vendor deck's ~1,500 MW figure. The deck's "UPS fast trip" framing is a simplification:
    the main cause was the counting and latching scheme.
- Later events: **17 Feb 2025, ~1,800 MW** and **22 Jul 2026, ~3,900 MW** (EI frequency 60.092 Hz).
  [P] PJM Planning Committee, "LCL Ride-Through Requirements - Background", 6 Oct 2026. No NERC incident
  review of these two was found (not verified).
- **NERC Level 2 alert, 9 Sep 2025:** transmission owners should set **"operational load ramp limits ... for
  normal and Emergency System states"**, post-disturbance recovery requirements, a cap on the load lost for a
  single contingency, and oscillation damping. Planners should use EMT studies in weak areas. [P] NERC Level 2
  alert.
- **NERC Level 3 alert, 4 May 2026:**
  - Model data must include UPS settings, the **"expected maximum ramp rate (MW/min) down and up"**, on-site
    generation and BESS, and the facility use.
  - Commissioning must test, among others, a switch "from an 'eco' mode on the UPS to supplying through
    on-site BESS".
  - It drafts a Computational Load Entity registration: ≥ 20 MW, connected at ≥ 60 kV.
  - [P] NERC Level 3 alert (NYSRC copy).

### 3.3 Interconnection requirements (ramp and ride-through)

- **ERCOT NOGRR 282 / NPRR 1308** (Large Computational Loads, ≥ 50 % power-electronic demand):
  - Approved by the PUCT on 9 Jul 2026, effective 1 Aug 2026.
  - At least 70 % of the load must ride through. The bands are 0.9-1.1 pu continuous, 0.8-0.9 pu for 2 s and
    0.5-0.8 pu for 0.5 s. The lower bands are ambiguous in the redline (not verified).
  - Consumption must return to ≥ 90 % within about 1-2 s.
  - **Disturbance-counting transfer schemes are prohibited.** Where unavoidable, the site must withstand
    ≥ 6 sags in 90 s.
  - [P] ERCOT "New Ride-through Requirements for Large Computational Facilities", 22 Jul 2026, and the
    NOGRR 282 PUCT report.
- **ERCOT draft "Large Load MW Variation Requirements": 10 MW per 5 s** (peak-to-peak, rolling window), to
  limit shaft fatigue of synchronous generators. This is the only explicit numeric variation limit found.
  [P] ERCOT Board, Interconnection and Grid Analysis Update, Sep 2026; window definition [S].
- **PJM proposal** (Large Computational Loads ≥ 50 MW): similar voltage and frequency bands, from 2027.
  **"Temporary substitution of energy consumption through other resources shall not, by itself, constitute
  compliance."** In other words, moving the IT load onto a BESS or genset is a load loss to the grid, not
  ride-through. [P] PJM PC "LCL Ride-Through Requirements - Solution", 6 Oct 2026.
- **Texas SB 6 (2025):**
  - Large loads start at 75 MW.
  - On-site backup that can serve ≥ 50 % of demand must be disclosed, and ERCOT can order it deployed or the
    load curtailed.
  - New loads must be curtailable during firm load shed.
  - [P] SB 6 enrolled text, PURA §37.0561, §39.170.
- **Ireland, CRU/2025236 (12 Dec 2025):** data centres need dispatchable on-site generation and/or storage
  ≥ **Maximum Import Capacity** after derating, together with run-hour limits and an EPA licence. EirGrid
  warns that recharging storage adds to area demand. [P] EirGrid Data Centre Technical Assessment, May 2026.
- **IEEE P4200** (data-centre interconnection) covers ride-through, "oscillation and ramp rate management"
  and PQ. IEEE P4134 covers large loads in substations. [P] IEEE SA pages. **IEEE 2800-2022** covers
  inverter-based resources, including standalone BESS, but excludes FACTS (STATCOM) and loads. [P] IEEE SA;
  scope detail [S] EPRI overview.
- **For the vendor deck:** the deck's list (ride-through, ramping and smoothing, V/f regulation, UFLS/UVLS,
  IEEE 519, IEEE 1453) matches these sources. Its PPC timing (**~100-150 ms standard vs < 10 ms fast, over
  EtherCAT**) has **no public primary source** (not verified) and stays a vendor claim. arXiv 2609.22542
  independently shows that controller and communication delay limits how well a BESS follows high-frequency
  load content.

### 3.4 Flicker and harmonics standards

- **IEEE 1453-2022** became a Standard (it was a Recommended Practice in 2015). It defines Pst and Plt,
  planning levels, emission limits at the PCC and rapid voltage changes, using the meter "described in"
  IEC 61000-4-15. IEEE 1453.1-2012 adopts IEC/TR 61000-3-7:2008. [P] IEEE SA pages.
- **IEC 61000-4-15:2010** specifies the flickermeter and gives no limits. Pst is evaluated over 10 min and
  Plt over 2 h, with **Plt = ∛(Σ₁¹² Pst³ / 12)**. [P] IEC webstore; formula [S] EPRI 1020280.
- **IEC TR 61000-3-7:2008** gives a three-stage assessment for fluctuating installations at MV, HV and EHV.
  [P] IEC preview (table of contents). The values below come via [S] EPRI 1020280 (2009), which reproduces
  the TR; the IEC text itself was not read:
  - Indicative planning levels: MV Pst 0.9 / Plt 0.7; HV-EHV Pst 0.8 / Plt 0.6.
  - **Stage 1 automatic acceptance on ΔS/S_sc at the PCC:** 0.1 % for more than 200 changes/min, 0.2 % for
    10-200 changes/min, 0.4 % for fewer than 10 changes/min.
  - Approximation: ΔU/U ≈ ΔS/S_sc.
  - Stage 2 apportions the emission by the share S_i/S_t. The Pst = 1 curves are in Annex A (not reproduced).
  - **Use:** GPU oscillations at 0.2-3 Hz are 24-360 changes/min, which is the strictest Stage 1 band. A
    10 MW residual swing therefore needs S_sc ≥ 5-10 GVA to pass Stage 1. In practice large AI sites will go
    to Stage 2, or smooth the swing before the PCC.
- **IEEE 519-2022** sets steady-state voltage and current distortion limits at the PCC and points
  inverter-based resources to IEEE 1547/2800. [P] IEEE SA. That the current limits are indexed by Isc/IL is
  standard engineering, but no primary page read here states it (not verified). The app's
  `current_tdd.table_for` already applies it.

### 3.5 Sizing storage for power and energy

- **Ramp-rate-limited smoothing** (Marcos et al., *Solar Energy* 99, 2014, PV case) [P]:
  - The battery power is P_BAT(t) = P_grid(t) - P_source(t). Storage energy is the integral of P_BAT over the
    worst event.
  - **Power is set by the worst single event. Energy is set by the event and by the SOC-recovery strategy.**
  - Capacity is **doubled** because the sign of the first event is unknown.
  - Example: a 1.1 MW plant at 10 %/min needs a 0.81 P* battery of 6.7 min (124 kWh), so it is clearly a
    power product.
  - The closed-form energy expression was OCR-garbled and must be checked against the PDF before use.
  - `ADQ/smoothing.step_mwh` (S²/(2R)) is the same triangle for a load step.
- **Frequency split for hybrid storage.**
  - Split the fluctuation with a low-pass filter. The high-pass part sets the fast device's power and its
    small energy. The low-pass part sets the battery's power and energy. The cut-off trades supercapacitor
    energy against battery cycling. [P] Liu et al., IEEE TPWRS 2016 (abstract), and Karrari et al., IEEE TSG
    2022 (battery-flywheel; abstract, DOI not verified).
  - For AI loads: [P] arXiv 2606.04869 (frequency-split hybrid ESS for datacenter smoothing), arXiv
    2603.00415 (review of four storage layers: chip, rack, facility UPS, grid BESS; names degradation and
    multi-layer sizing as gaps), and arXiv 2609.22542 (GFM BESS with feed-forward; delay limits).
- **C-rate and degradation.**
  - A semi-empirical model combines cycle-depth stress, rainflow counting and calendar ageing. Shallow,
    frequent cycles cost little per cycle but add up. [P] Xu et al., IEEE TSG 9(2), 2018 (bibliographic data
    only).
  - A sinusoid of amplitude A and period T moves A·T/π per half cycle (`smoothing.oscillation_mwh`). At 1 Hz
    that is ~3,600 half cycles an hour, so **the throughput and the cycle count, not the energy, decide the
    chemistry**.
  - Supercapacitors and high-C-rate cells (LTO, high-power LFP) suit this duty. A 4 h energy BESS would be
    oversized on energy and still wear on cycles.
  - The duration and chemistry split is engineering synthesis; no single source states it.

### 3.6 STATCOM versus BESS

- On an arc furnace at Pst 3.8, an SVC reduced flicker to 2.2-2.4 and a STATCOM to 0.65, a factor above 5.
  The best result also compensated real current and its derivative: **active-power fluctuation drives flicker
  too**. [P] Larsson & Poumarède, IEEE PES WM 1999. CIGRE TB 237 (2003) [P, abstract] makes the same
  comparison. ABB reports a factor above 4.5 [V].
- Engineering basis: ΔU/U ≈ (R·ΔP + X·ΔQ)/U².
  - A STATCOM can cancel the **voltage** effect of ΔP through ΔQ, best at high X/R.
  - It **cannot change the active-power ramp** that generators and ACE see. That needs stored energy (BESS,
    supercapacitor, or the GPU floor).
  - ERCOT's 10 MW per 5 s limit and NERC's MW/min ramp limits are active-power limits. A STATCOM addresses
    flicker and RVC; it does not address ramp compliance.
  - A BESS PCS also has Q capability: √(S² - P²) at any operating point. **An oversized PCS can replace
    STATCOM Mvar.**
- Weak grids: NERC asks for grid-forming (GFM) BESS and EMT studies where system strength is weak.
  [P] NERC GFM functional specification for BESS (Sep 2023; not read in full) and Level 2 alert. A GFM BESS
  that compensates only 40-60 % of load fluctuations comes from a secondary citation (not verified).

### 3.7 UPS, BESS-EPS, MV UPS and diesel

- IEC 62040-3:2021 covers UPS ≤ 1,000 V only, so an MV UPS falls outside it. [P] IEC webstore. Its VFI, VI
  and VFD classes: [V] Schneider White Paper 1. "Eco" mode behaves as VFD (standby), which matters for
  ride-through. NERC Level 3 asks for exactly this transition to be tested.
- **The topology decides grid compliance:**
  - A **series** MV UPS (back-to-back) decouples the load. The grid sees the rectifier's behaviour, so ramp
    and ride-through become rectifier and control settings.
  - A **parallel** BESS-EPS (GFL or GFM) leaves the load on the grid and must actively cancel each swing, so
    its latency matters (`LATENCY_S`).
  - Under PJM's text, transferring the load to backup is not compliance either way. The UPS or BESS must
    keep drawing grid power within the ride-through envelope.
  - MV UPS products: [V] GE Vernova MV UPS (notional 200 MVA / 34.5 kV / 65 MWh), ABB, Fuji.
- **How long diesel replacement must last:**
  - Uptime Tier Topology requires **12 h on-site fuel at N load** for every tier. Longer (72/96 h) is
    industry practice, not a Tier requirement. [S] Uptime Journal snippets; the page returned 403.
  - NFPA 110 Class sets minimum run time (for example 2, 6 or 48 h). [S] CSE Magazine; the NFPA text is
    paywalled.
  - Ireland requires 100 % of MIC as dispatchable capacity after derating. [P] EirGrid.
  - So a "replace diesel" BESS is sized on **energy (N-load x hours of autonomy)**, typically 12 h or more.
    It is not a 1 h or 4 h smoothing product. A 2-4 h data-centre BESS is market practice with no primary
    source (not verified).
- **Diesel constraints** if the gensets stay:
  - US RICE NESHAP caps testing and maintenance at **100 h/yr**, with up to 50 h/yr of non-emergency use.
    [P] 40 CFR 63.6640(f).
  - The EU MCP Directive lets Member States exempt plants running **≤ 500 h/yr**. [P] Directive (EU)
    2015/2193 Art. 6.
  - Under Texas SB 6, ERCOT can order backup deployed, which is a grid-service duty. [P]

### 3.8 Source list

| Short name | URL |
|---|---|
| Choukse et al., arXiv 2508.14318 | <https://arxiv.org/abs/2508.14318v2> |
| NERC LLTF white paper, Jul 2025 | <https://www.nerc.com/comm/RSTC_Reliability_Guidelines/Whitepaper%20Characteristics%20and%20Risks%20of%20Emerging%20Large%20Loads.pdf> |
| NVIDIA GB300 blog, Jul 2025 [V] | <https://developer.nvidia.com/blog/how-new-gb300-nvl72-features-provide-steady-power-for-ai/> |
| NERC Incident Review, Jan 2025 | <https://nerc.com/pa/rrm/ea/Documents/Incident_Review_Large_Load_Loss.pdf> |
| NERC Level 2 alert, Sep 2025 | <https://www.nerc.com/pa/rrm/bpsa/Alerts%20DL/NERC%20Alert%20Level%202%20%20Large%20Loads.pdf> |
| NERC Level 3 alert, May 2026 (NYSRC copy) | <https://www.nysrc.org/wp-content/uploads/2026/04/NYSRC-LLWG-4-Att-8-NERC-Level-3-Alert.pdf> |
| PJM LCL ride-through background, Oct 2026 | <https://www.pjm.com/-/media/DotCom/committees-groups/committees/pc/2026/20261006/20261006-item-07-a---1-lcl-ride-through-requirements-background---presentation.pdf> |
| PJM LCL ride-through solution, Oct 2026 | <https://www.pjm.com/-/media/DotCom/committees-groups/committees/pc/2026/20261006/20261006-item-07-b---1-lcl-ride-through-requirements-solution---presentation.pdf> |
| ERCOT ride-through trending topic, Jul 2026 | <https://www.ercot.com/files/docs/2026/07/28/ERCOT-Trending-Topic-Ride-Through.pdf> |
| ERCOT NOGRR 282 | <https://www.ercot.com/mktrules/issues/NOGRR282> |
| ERCOT Board grid analysis update, Sep 2026 | <https://www.ercot.com/files/docs/2026/09/07/10-Interconnection-and-Grid-Analysis-Update.pdf> |
| Texas SB 6 (89R) | <https://capitol.texas.gov/tlodocs/89R/billtext/html/SB00006F.HTM> |
| EirGrid Data Centre Technical Assessment, May 2026 | <https://cms.eirgrid.ie/sites/default/files/publications/Data-Centre-Technical-Assessment-May-2026.pdf> |
| IEEE P4200 | <https://sagroups.ieee.org/p4200/> |
| IEEE P4134 | <https://standards.ieee.org/ieee/4134/12559/> |
| IEEE 2800-2022 | <https://standards.ieee.org/ieee/2800/10453/> |
| IEEE 1453-2022 | <https://standards.ieee.org/ieee/1453/10459/> |
| IEEE 1453.1-2012 | <https://standards.ieee.org/ieee/1453.1/5174/> |
| IEC 61000-4-15:2010 | <https://webstore.iec.ch/en/publication/22269> |
| IEC TR 61000-3-7:2008 | <https://webstore.iec.ch/en/publication/4156> |
| EPRI 1020280, flicker limits, 2009 | <https://restservice.epri.com/publicdownload/000000000001020280/0/Product> |
| IEEE 519-2022 | <https://standards.ieee.org/ieee/519/10677/> |
| Marcos et al., Solar Energy 2014 | <https://doi.org/10.1016/j.solener.2013.10.037> (OA: <https://oa.upm.es/35585/1/INVE_MEM_2014_191652.pdf>) |
| Liu et al., IEEE TPWRS 2016 | <https://doi.org/10.1109/TPWRS.2015.2482983> |
| Xu et al., IEEE TSG 2018 | <https://doi.org/10.1109/TSG.2016.2578950> |
| arXiv 2603.00415 / 2606.04869 / 2609.22542 | <https://arxiv.org/abs/2603.00415>, <https://arxiv.org/abs/2606.04869>, <https://arxiv.org/abs/2609.22542> |
| Larsson & Poumarède, IEEE PES WM 1999 | <https://doi.org/10.1109/PESW.1999.747380> |
| CIGRE TB 237 | <https://e-cigre.org/publication/237-static-synchronous-compensator-statcom-for-arc-furnace-and-flicker-compensation> |
| NERC GFM spec for BESS, Sep 2023 | <https://www.nerc.com/comm/RSTC_Reliability_Guidelines/White_Paper_GFM_Functional_Specification.pdf> |
| IEC 62040-3:2021 | <https://webstore.iec.ch/en/publication/60140> |
| GE Vernova MV UPS fact sheet [V] | <https://www.gevernova.com/electrification/sites/default/files/2026-08/GEA35589_Power-Stability_FS_MV-UPS-for-Data-Center_EN_Rev1_20260810.pdf> |
| Uptime Journal, fuel systems [S] | <https://journal.uptimeinstitute.com/fuel-system-design-reliability/> |
| 40 CFR 63.6640 | <https://www.ecfr.gov/current/title-40/chapter-I/subchapter-C/part-63/subpart-ZZZZ/subject-group-ECFR24cf3a3de5678d3/section-63.6640> |
| Directive (EU) 2015/2193 | <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32015L2193> |
| Hitachi Energy blog, Jun 2026 [V] | <https://www.hitachienergy.com/news-and-events/blogs/2026/06/the-data-center-and-the-grid-the-flexible-relationship-driven-by-ai-and-managed-by-automation> |

## 4. Method proposal: from preferences to sizing, electrical design, settings, and back

### 4.1 Preferences become a design intent

Add one **design intent** per scenario. It extends DC 03's `backup_mode` and DC 05's architecture into a
small, closed set of presets. Each preset says which technologies exist, which may be built (extendable), and
which **services** the plant must deliver. It does not fix sizes.

| Intent (customer words) | Technologies offered | Services required (become constraints) |
|---|---|---|
| **A. Replace diesel** | Energy BESS (GFM, 4 h class or longer); optional fast tier (high-C BESS or supercap); no genset | Backup autonomy: P_crit x T_aut (12 h default, editable); ride-through without transfer; smoothing; GFM MVA for islanding |
| **B. Keep diesel + MV smoothing BESS** | Genset (fixed or extendable, run-hour capped); high-C BESS (0.25-1 h) at MV; LV UPS kept | Smoothing and ramp only; genset covers autonomy; UPS covers the bridge (`walk_in_s`) |
| **C. Keep diesel, no new BESS** | Genset, UPS, GPU power floor (energy uplift) | Ramp met by the GPU floor alone; flicker by STATCOM; the screen shows the residual |
| **D. No UPS (BESS-EPS in parallel)** | GFM BESS sized for bridge + smoothing; optional genset | Bridge reserve from the BESS (latency ≤ GFM `LATENCY_S`); seamless ΔP headroom |
| **E. MV UPS (series)** | MV UPS with its own storage; genset optional | Rectifier ramp setting = grid ramp; ride-through by the UPS; Q from the MV UPS |

- **Several preferences** run as several scenarios through the existing compare and study machinery
  (`BE/services/compare/`, `ADQ/eh_study.py`). The result is DC 05's comparison table: capex, MW/MWh/Mvar
  per asset, transformer and STATCOM sizes, and the verdict for each check.
- **Hard constraints** ("must not have diesel") become technology bounds. **Soft constraints** ("prefer
  fewer assets") become a ranking in the comparison and do not enter the objective.

### 4.2 Capacity-expansion inputs

1. **Separate power and energy variables.** Model each storage technology k as a PyPSA `Store` (energy
   E_k) with charger and discharger `Link`s (power P_k), as PyPSA-Eur does for "battery charger" and
   "battery discharger". Bound **E_k/P_k between the technology's hours**:
   - supercap: 5 s to 1 min;
   - high-C BESS: 0.25 to 1 h;
   - energy BESS: 2 to 8 h.

   This replaces the fixed `max_hours`. Duration options 1 h and 4 h become either the bounds of a single
   technology or two technologies competing on cost. Power and energy are already priced separately in the
   defaults pack. The **supercap** is a new defaults-pack technology.
2. **C-rate.** Add P_k ≤ c_max,k x E_k. With the duration bound this is linear.
3. **Smoothing envelope → floors.**
   - Take each `DcPhase` envelope from `dynamic_envelope`: swing S, oscillation amplitude A and period T,
     checkpoint depth D and duration d. Take the grid limits from the grid-code profile: ramp R (MW/min),
     variation ΔP_lim per window (for example 10 MW per 5 s), and Stage 1 d_lim.
   - **Power floor:** Σ_{k ∈ fast} P_k^smooth ≥ max(S - ΔP_lim, A - ΔP_lim/2, residual flicker step).
     "Fast" means the technology's latency is below about 1/(2π f) of the band it must cancel. The
     high-frequency band is split at f_c: above f_c only supercap or GFM, below f_c any BESS.
   - **Energy floor:** E_k^smooth ≥ 2 x max(`step_mwh`, `checkpoint_mwh`, `oscillation_mwh`) / usable SOC
     window. The factor 2 is Marcos's unknown sign; reuse the existing functions.
   - These are capacity constraints in `extra_functionality`, written like `_reserve_row`.
4. **Hourly headroom.** Each snapshot must keep the smoothing slice free.
   - In power, discharge and charge dispatch must leave P^smooth: p_dis(t) ≤ P_k - P_k^smooth and
     p_ch(t) ≤ P_k - P_k^smooth.
   - In energy, SOC must stay within a band: E^smooth/2 ≤ e(t) ≤ E_k - E^smooth/2.
   - This stops the LP selling the same MW to arbitrage and to smoothing. Issue 06 (flex revenue) will need
     the same headroom.
5. **Backup reserve.**
   - Intent A or D: e(t) ≥ P_crit x T_aut for the backup slice. The opt-in island `_reserve_row` already
     does this; make it available outside island mode.
   - Intent B or C: genset p_nom ≥ P_crit (N, N+1 or 2N per DC 03), plus a run-hour cap Σ_t p_gen(t)·w_t ≤
     p_nom x H_max (100 or 500 h/yr, per the Uptime, EPA and MCP sources above).
6. **GPU power floor.** This is an option, not a requirement. Raising the hourly load by the floor's energy
   overhead (about 10 % at a 90 % floor per arXiv 2508.14318) lowers the swing S. The floor is a scenario
   lever that trades MWh of energy against MW of storage.
7. **Degradation.** Add a throughput cost c_wear x Σ_t (p_ch + p_dis) per technology.
   - The hourly LP sees only hourly throughput, so add the **sub-hourly smoothing throughput** as a constant
     per hour: A·T/π x 3600/T half-cycles x the share of training hours.
   - Then check the cycle life once afterwards with rainflow (Xu et al.).
   - This is where a supercap or high-C tier beats a 4 h BESS.

### 4.3 Forward pass: sized plant to electrical design and settings

After each solve, run the existing chain on the solved network with the new capacities **fixed**. Fixing them
also solves the smoothing screen's blindness to extendable units: the loop passes the solved
`p_nom_opt`/`e_nom_opt` as committed values.

| Check | Existing code | Input from the sizing |
|---|---|---|
| Transformer size | `campus_flow.size_transformers` | Hourly import and export peaks, BESS charging (EirGrid's recharge point) |
| Reactive need, STATCOM Mvar | `campus_reactive.reactive_need`, `size_compensation`, `ibr_q_capability` | BESS PCS S_k: Q_avail = √(S_k² - P_k^reserved²) |
| SCR/ESCR, fault level | `strength.escr`, `shortcircuit.fault_levels`, `scr_gate.classify_scr` | Shunt capacitor Q_c (lowers ESCR), GFM MVA |
| Flicker Pst | `flicker.pst`, `flicker_table` | **New:** residual step = S - P^smooth and rate = 60/T from the GPU envelope (today only process units feed it) |
| Harmonics, TDD | `harmonics.harmonic_table`, `current_tdd.tdd_table`, `harmonic_scan.impedance_scan` | Converter counts and topology, STATCOM/BESS active filtering (assumed) |
| Ramp, oscillation, RVC | `grid_readiness._ramp_row`, `_oscillation_row`, `_rvc_row`, `smoothing.smoothing_screen` | Sized BESS/supercap and their latency |
| Island frequency, ride-through events | `GS/static/frequency.py` (1 ms), `gfm_tuning.recommend_gfm_settings` | GFM MVA, H, droop, UPS `walk_in_s` |

The forward pass also writes a **control-settings sheet**:

- ramp limiter (MW/min at the PoC);
- smoothing filter cut-off f_c and the split between tiers;
- SOC band (backup slice and smoothing slice);
- GFM droop and H, from `gfm_tuning`;
- UPS walk-in and transfer settings, with **no disturbance counting** (ERCOT NOGRR 282);
- the preset from `select_preset`.

This sheet is what an EMT or HiL study needs (the vendor deck's Concept → SiL → HiL → Site sequence).

### 4.4 Backward pass: failed checks become cuts on the sizing

Each failed check produces a **linear cut** on capacities. The cut is added to the next solve, and **cuts are
never removed**, so the feasible set only shrinks.

| Failure | Cut added to the LP |
|---|---|
| Transformer above the largest library size or N-1 margin | Import cap: p_import(t) ≤ S_max·pf for all t; or a fixed cost for the next transformer step |
| STATCOM Mvar needed: Q_need | Q_statcom + Σ_k (S_k^pcs - P_k^reserved) ≥ Q_need, with PCS MVA S_k^pcs ≥ P_k as a new variable. This is a linear inner bound on the PQ circle and lets the LP choose between PCS oversizing and a STATCOM. |
| ESCR below the threshold | Σ_k i_max,k·S_k^gfm ≥ SCR_min·P_n + Q_c - S_sc, from ESCR = (S_sc - Q_c)/P_n with a GFM fault-current credit i_max·S added to S_sc. Shunt capacitors (Q_c) lower ESCR, and a STATCOM adds little strength. Crediting GFM converters with system strength is a modelling assumption that EMT must confirm. |
| Pst above the limit | P^smooth + κ·Q_statcom ≥ S - d_lim·S_sc. κ ≈ X/(R+X) is the voltage effectiveness of Q relative to P (ΔU ≈ R·ΔP + X·ΔQ). Take κ from the impedance at the PoC. |
| Ramp or variation limit failed | Raise the floor in 4.2.3 by the shortfall |
| GFM tuning finds no droop/H within bounds | GFM MVA ≥ (current + margin) |
| Harmonics or TDD above the limit | Not a capacity cut: add a filter cost and report it (IEEE 519 is a design issue, not a sizing one) |
| Checks the LP has no lever for (for example SCR set by the grid alone) | Report "unresolved, needs a grid reinforcement or EMT study" and stop |

**Convergence rule** (control flow as in `ADQ/coupling.py`):

1. Solve.
2. Run the forward pass.
3. If every check passes, stop with status **converged**.
4. Otherwise add the cuts.
5. **Round discrete library assets up and freeze them after iteration 2**, so a transformer cannot flip
   between two sizes.
6. Stop with **converged** when no new cut is generated, or when the relative cost change is below 0.5 % and
   no capacity moves more than 2 % (or one library step).
7. Stop with **not converged** after 5 iterations, or when a failure has no lever. Report the binding
   checks.

Each iteration only adds lower bounds or caps that make the problem tighter, so the cost cannot decrease. With
discrete assets frozen and finitely many cuts, the loop ends. Each iteration costs one LP solve plus the
campus study. On a full 8760 h network that is the dominant cost, so a tsam-reduced year for iterations 1 to
n-1 and a full-year final solve is a sensible default. This is an assumption to check against run times.

**Prototype result (AS-PROTO-1, [`prototype-as-loop/RESULTS.md`](prototype-as-loop/RESULTS.md)): converges
with changes.** A one-week campus was run for intents A, B and D on five grid cases.

- With the cuts as written above, 14 of 15 runs pass every check in 2 or 3 rounds. One run (weak grid,
  flat tariff, intent D) creeps for 9 rounds, because the PQ cut and the Pst cut both count the same
  STATCOM Mvar.
- The cuts as written also buy 2 to 9 times more reactive capacity than needed.
- With four changes, all 15 runs pass in 2 or 3 rounds and no run oscillates:
  1. split the STATCOM into Q_ss (PQ cut) and Q_dyn (Pst cut);
  2. write the PQ cut per hour and linear in the transformer flow, not as a constant Q_need from the
     failing iterate, which goes stale. In the same cut, cap S_x ≤ r·P and credit √(1+2/r) Mvar per MVA,
     a tighter inner bound on the PQ circle than (S − P);
  3. write the Pst cut on the voltage as cos ψ·ΔP_res + sin ψ·ΔQ_res ≤ d_lim·S_sc. Both residuals are
     floored at zero, and the cut includes the load's own reactive swing;
  4. set the power floor to S + A, and make the energy floor the sum of the event energies, not their max.
- The ESCR cut, the transformer import cap and the monotone-cost argument hold as written.
- Keep the capacity-move condition in rule 6: in the creeping run the cost moved under 0.02 % per round
  while Pst was six times over the limit. A tolerance stop with a check still failing is "not converged".

### 4.5 What stays outside

- **Sub-cycle and control interaction:**
  - PPC latency, whether the vendor deck's 10 ms vs 100-150 ms claim holds or not;
  - converter current limits during faults;
  - SSO and interaction with generator shafts;
  - GPU-level 50 ms transients.

  These need EMT or HiL studies. The app hands them the sized plant and the settings sheet, and marks the
  rows "needs EMT" (as `_check_dynamic` does today).
- **The actual spectrum.** The envelope is one (A, T) pair per phase. A measured or vendor-supplied power
  trace would need a PSD and a band-energy computation to set f_c and test the "20 % in-band energy" type of
  limit. That is a later issue (section 5, AS-09).

## 5. Proposed issue split (not filed)

The issues use the prefix `AS-` (asset sizing loop). The reviewer split follows GK-SPLIT-1: GK A for
gridspine, DC and adequacy; GK B for investment and finance. The suggested model per issue is a proposal.

| # | Issue | Depends on | Model | Reviewer |
|---|---|---|---|---|
| AS-01 | **Design-intent presets** A-E (section 4.1) as scenario metadata. Extends DC 03 `backup_mode` and DC 05. Data model plus validation only. | none | Sonnet | GK A |
| AS-02 | **Separate power and energy storage**: Store+Link storage with E/P-hour bounds and a C-rate cap; supercap and high-C BESS in the defaults pack; a migration keeps `StorageUnit` projects working | none | Opus | GK B |
| AS-03 | **Envelope → LP floors and headroom** (4.2.3-4.2.5): smoothing power and energy floors, per-snapshot headroom and SOC band, backup reserve outside island mode, genset run-hour cap. Reuses the `smoothing.py` functions. | AS-01, AS-02 | Opus | GK A |
| AS-04 | **GPU envelope → flicker and RVC inputs**: `flicker_units` and `_flicker_row` read the residual step and rate from the envelope after smoothing | none | Sonnet | GK A |
| AS-05 | **PCS MVA ↔ STATCOM coupling**: BESS PCS Q in `size_compensation`, and the PCS MVA variable in the LP | AS-02 | Opus | GK A |
| AS-06 | **Sizing loop runner** (4.3-4.4): forward pass on fixed capacities, the cuts table, the convergence rule, `*_loop_runner` in the `coupling.py` pattern | AS-03, AS-04, AS-05 | Opus | GK A + GK B |
| AS-07 | **Control-settings sheet**: ramp limiter, f_c, SOC bands, GFM droop/H, UPS walk-in with no disturbance counting, and a latency check against the band. Exported alongside the PSS/E dyr. | AS-03 | Sonnet | GK A |
| AS-08 | **Grid-code keys for load variation**: ramp MW/min, ΔP per window, and an LCL ride-through band in `grid_codes.yaml`, all marked "assumed" (public sources only, SAGC-1) | none | Sonnet | GK A |
| AS-09 | **Spectrum input** (later): an uploaded power trace gives a PSD, band energy and f_c | AS-03 | Opus | GK A |
| AS-10 | **Preference comparison UI**: run intents A-E as scenarios and show the DC 05 table, in Guided and Expert | AS-01, AS-06 | Sonnet | UX + GK B |

- **Start in parallel:** AS-01, AS-02, AS-04 and AS-08.
- **Then** AS-03 and AS-05, then AS-06, and AS-07 and AS-10 last.
- **Before any build:** spec questions go to the owner, in particular the default autonomy hours for intent A
  (12 h per Uptime vs a customer value) and whether "no UPS" should be allowed under PJM-type rules.

## What this note skipped, and the risk

- **IEC TR 61000-3-7 values were read via EPRI's reproduction, not the IEC text.** Risk: an edition
  difference in the planning levels or the Stage 1 table. The IEC text should be read before AS-04 and AS-08
  set defaults.
- **Not verified:** the Marcos closed-form energy equation (OCR), the IEEE 519 Isc/IL table wording, NFPA 110
  and Uptime exact wording, ERCOT's lower ride-through bands, and the vendor deck's PPC timing and EtherCAT
  claim.
- **A one-week numerical prototype was run (AS-PROTO-1, `prototype-as-loop/`); a full-year run was
  not.** It found four changes to the cuts (see the end of 4.4).
  - Remaining risks: the prototype uses illustrative costs and grids, a single envelope, and one fast
    storage tier.
  - The transformer freeze rule was never exercised.
  - The load's reactive swing (tan φ_swing) decides whether a weak grid needs a STATCOM for flicker, and
    it needs vendor data.
- **No client document was used** (SAGC-1). All limits cited are public.
