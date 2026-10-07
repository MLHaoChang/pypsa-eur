# Campus island operation and converter control: a first pass

This plan was written before the code, as every gridspine increment is. It
will be amended wherever building it proves it wrong.

It **partly reverses** the decision of 2026-10-05 that there would be "no
dynamic modelling for now" (`2026-10-05-gridspine-campus-electrical.md:4-6`).
On 2026-10-07 the owner asked for two things:

- a **first-pass frequency and RoCoF check**, built from aggregated inertia
  and droop;
- **converter control strategies** as a first-class part of the model. They
  are to be exercised in the steady-state load flow, in the short-circuit
  analysis, and partly in that frequency check.

Multi-bus RMS (I8) is gated behind its own decision. EMT stays a flag.

## The goal

A client asks for a data centre that can **island**: when the grid is lost,
the site carries its critical load on its own gensets, BESS and UPS. Five
questions follow, and each has to be answered for the control strategy each
converter actually runs.

1. **Ride-through (sizing).** Is the site sized to carry its critical load
   for the required outage duration? This covers energy, fuel and the BESS
   charge kept in reserve.
2. **The instant of islanding (sizing and frequency).** Can the units that
   are online absorb the power step the moment the grid goes? Three things
   decide this: power headroom, inertia (which sets RoCoF) and droop (which
   sets the frequency it settles at).
3. **Is the island viable in steady state?**
   - Some unit must form the voltage and frequency.
   - The voltages must stay in their bands.
   - Reactive power must be shared between units.
   - No unit or branch may be overloaded.
4. **Does protection still work in the island?**
   - On the minimum side, the fault current with only on-site sources must
     still be large enough for the overcurrent pickups to see a fault.
   - On the maximum side, the switchgear must still withstand the fault
     current with the grid connected (C4c today).
5. **Frequency, first pass.** For each event, compute RoCoF, the nadir, the
   quasi-steady-state deviation and the recovery time, at the hours where
   islanding is hardest.

**Scope note.** "Support island operation" can also mean that the site acts
as grid-forming at its PCC to support the utility grid (GB GC0137, the RfG
revision). This plan covers **site islanding**. Grid support at the PCC
reuses the control-mode schema of I1 and the models of I4–I6 and I8, but its
grid-code checks are a follow-up (see Out of scope).

## Owner decisions (2026-10-07)

| question | decision |
|---|---|
| Dynamics | A **first-pass, aggregated** frequency/RoCoF check from inertia and droop (I6). Multi-bus RMS is a later, gated increment (I8). EMT stays a flag. |
| Converter control | Control strategies become a **unit attribute** (I1). They are implemented and tested in the steady-state load flow (I4), the short-circuit analysis (I5), and the frequency check (I6), and later in RMS (I8). |
| Steps | 1 sizing, 2 island critical hours, 3 island steady state and short circuit, 4 frequency. |

### Open questions for the owner (recommended defaults, used until answered)

| # | question | recommended default |
|---|---|---|
| Q1 | Ride-through duration required | No silent default. The template ships 24 h, tagged `assumed`, and the panel asks for it. |
| Q2 | Frequency limits (RoCoF, nadir, quasi-steady-state, UPS input window) | Profile settings tagged `assumed` (1 Hz/s over 500 ms, ±2.5 Hz nadir, ±1 Hz quasi-steady-state). ISO 8528-5 class limits are entered by the user, not shipped: the standard is not freely available, so it gets the same treatment as VDE in the campus plan. |
| Q3 | Which islanding scenarios are studied | All three, as an enum per study: `seamless` (an online GFM BESS takes the step), `ups_bridged` (dead bus, gensets start, UPS carries the IT load meanwhile), `planned` (the site ramps up first, then opens the PCC). The template default is `ups_bridged` plus `seamless`. |
| Q4 | ANDES is GPL-3.0 (I8) | Check the licence policy before I8, as was done for PowSyBl's MPL. I1–I7 do not depend on it. |
| Q5 | Island constraints in the hub LP | **Opt-in** per project. The template ships them enabled, in its sidecar. |
| Q6 | Frequency results fed back into sizing (I7) | Off by default. The user applies them with an explicit "apply cuts" action, which re-solves the hub. This respects the 2026-10-05 rule that gridspine does not silently re-size the hub. |
| Q7 | Gensets running in parallel with the grid ("spinning") | Per genset, a flag in the sidecar, default off (cold standby). Turning on genset unit commitment (MILP) is ledgered, not default. |

## What exists today (surveyed 2026-10-07)

**The hub LP** (pypsa-gui, PyPSA + linopy):
- The *Data Center Energy Hub* template is one representative 168 h week
  (`project_templates/eh_templates.py:18-30,94-174`).
- It has:
  - gensets: 4×10 MW fixed, plus a candidate up to 40 MW;
  - a 4 h BESS candidate;
  - a 15 MW / 1 h `ups_battery`, which is a plain StorageUnit;
  - PV;
  - a 40 MW import Link.
- Custom constraints are composed through `extra_functionality` wrappers
  (`services/solver/adequacy.py:63-201`, `objective.py:189`). This is the
  pattern I2 follows.
- Islanding today is the DtC stress (`services/adequacy/dtc.py:75-105`). The
  import Link goes to `p_max_pu = 0` for **the whole horizon**. There is no
  duration, headroom, inertia or droop.
- There is no UPS model.
- No component carries H, a droop, a control mode, or a GFM/GFL flag.

**gridspine campus**
- The units are `load`, `bess`, `pv`, `wind`, `genset`
  (`ingest/campus.py:126-158`).
- Every generator is a PQ `sgen`. A genset is a current source of 1/x''d,
  which is ledgered (`ingest/campus.py:168-170,401-405`).
- There is always an `ext_grid` at the PCC.
- Short circuit uses IEC 60909 max/min through pandapower `calc_sc`
  (`static/campus_sc.py:46-73`).
- The MILP treats inverter Q as a free dispatch variable inside an octagon
  (`static/campus_milp.py:26-33`). No control law is applied.
- Inertia exists only as a case39 ranking metric (`ranking/metrics.py:146-169`).
- `dyr_writer` emits GENROU/GENSAL and REGCA1+REECA1 only. It has **no
  governors and no exciters**, and the campus track exports nothing.

**Spikes (2026-10-07, pandapower 3.1.2, scratch scripts not committed)**

1. **Islanded load flow with droop-weighted distributed slack works.**
   - Setup: two `gen` units with `slack=True`, `slack_weight = P_n / R`, and
     `runpp(distributed_slack=True)`.
   - The 2.03 MW deficit split 1 : 2 in proportion to the weights, as droop
     says it should.
   - Both units held 1.0 pu, so Q sharing between them is arbitrary. A Q–V
     droop needs an outer loop (I4).
2. **Island short circuit with a genset works once the genset is a real `gen`.**
   - Setup: `vn_kv` and `xdss_pu` set, no `ext_grid`.
   - Result: 1.589 kA at 33 kV, against 1.604 kA by hand from
     c·Sn/(√3·Un·x''d). The difference is IEC's K_G correction.
3. **pandapower's min case drops current-source sgens.**
   - With the genset plus a 10 MVA GFL inverter, Ik''max rose from 1.589 to
     1.799 kA, but Ik''min stayed at 1.445 kA.
   - So in an inverter-heavy island, the protection-sensitivity check cannot
     rely on pandapower's min case (I5).
4. **An inverter-only island crashes `calc_sc`** with a `ZeroDivisionError`:
   there is no voltage source. I5 needs its own method for it.
5. **pandapower ships `DERController`.** It has `QModelQVCurve`,
   `QModelCosphiP`, `QModelCosphiVCurve` and others, and PQV capability
   areas. GFL Q(U) and cosφ(P) laws come from there (I4).
6. **ANDES 2.0.0 is on PyPI.** It has:
   - `REGF1` (GFM droop), `REGF2` (GFM VSM), `REGF3` (dVOC);
   - `REGCV1/2` (VSG);
   - `REGCA1`/`REECA1`/`REPCA1` (GFL);
   - the governors `TGOV1`, `GAST`, `IEEEG1`;
   - the exciters `SEXS`, `ESST4B`, `AC8B` and others;
   - a pandapower importer (`andes.interop.pandapower`).

   I8 is therefore feasible in-tree, subject to Q4.

## The control strategies

Each converter unit gets a `control` value. Each genset gets a governor mode.
The table is the contract that I4, I5, I6 and I8 implement.

| `control` | grid-connected load flow | island load flow (I4) | short circuit (I5) | frequency check (I6) | RMS model (I8) |
|---|---|---|---|---|---|
| `gfl_pq` | PQ sgen at its setpoint (today) | PQ. **Cannot be the reference.** | current source k·In; max case only | no P response | REGCA1 + REECA1 |
| `gfl_qu` | Q(U) curve (`DERController`, `QModelQVCurve`) | same | same | none | REECA1 with Kqv |
| `gfl_pf` | cosφ(P) (`QModelCosphiP`) | same | same | none | REECA1 (pf flag) |
| `gfl_fw` (frequency-watt, FFR) | PQ | PQ, with P(f) after a deadband in an outer loop | same | deadband, delay, ramp, limit | REGCA1 + REECA1 + REPCA1 (freq) |
| `gfm_droop` | voltage source with Q–V droop (outer loop); P at its setpoint | **reference**, slack weight P_n/R; Q–V droop | voltage source to its current limit. IEC 60909 current source k_gfm·In; island: own method | first-order P response (τ_f, 1/R), capped by headroom and current | REGF1 |
| `gfm_vsm` | as `gfm_droop` | as `gfm_droop` (tested identical) | as `gfm_droop` | virtual inertia H_v, damping D_v, droop | REGF2 |
| genset `droop` | power-factor control (today) | `gen` reference, slack weight P_n/R, AVR on voltage | synchronous machine x''d (`gen`, `vn_kv`) | inertia H, governor lag T_g, droop R, load-acceptance step limit, start delay when cold | GENROU + GAST/TGOV1 + SEXS |
| genset `isochronous` | as above | sole P reference, so Δf = 0 until it hits its limit | as above | integral governor | as above |
| `ups` (new kind) | rectifier load, constant P | load: recharge plus walk-in. Not a source. | none (rectifier) | IT load leaves the island at the event and walks back in at r_walkin; drops off if f leaves its input window | constant-P load with a ramp (no ANDES model; ledgered) |

Principles the tests pin:
- **Steady state cannot tell droop from VSM.** They are identical at
  equilibrium, and a test asserts it. They differ only in I6 and I8. A droop
  GFM with power-filter time constant τ_f behaves like a VSM with
  H_eq = τ_f / (2·R_pu) (D'Arco & Suul, IEEE Trans. Smart Grid, 2014).
  I6 tests that equivalence.
- **GFL and GFM differ in steady state.**
  - In the island load flow, a GFL unit can never be the reference. An
    island with no unsaturated GFM unit or genset is refused, with a reason.
  - In short circuit, a GFM unit stays energised and forming in the minimum
    case, while a GFL unit may trip on the voltage dip (ledgered).
  - The frequency response differs, as the table shows.
- **Current limits turn a GFM into a current source.** A GFM at its current
  limit is no longer a voltage source. I4 treats it as PQ at its limit and
  re-solves. I5 caps its fault current at k_gfm·In.

## Increments

Each increment is its own PR, with the campus discipline:
- tests first, worked by hand or against an independent oracle;
- mutations;
- full gates;
- a browser run for anything the user sees.

Sizes are relative: S, M, L.

### I1: control modes, island data and the UPS unit (S–M)

**One source of island data: a hub sidecar `island_config.json`.** It follows
the pattern of `dtc_config.json` and is validated by a pydantic model in
`models/energy_hub.py`. It is keyed by PyPSA component name and carries:
- per unit:
  - `control`;
  - `H_s` or `H_v_s`;
  - `droop_pct`;
  - `q_droop_pct`;
  - `tau_f_s`;
  - `i_max_pu`;
  - `k_sc`;
  - FFR deadband, delay and ramp;
  - for gensets: `governor` (`droop` | `isochronous`), `T_g_s`,
    `start_s`, `spinning` (Q7), `load_step_max_pct` (the ISO 8528-5 class,
    user-entered), `xd_pp`;
- for UPS units: `it_load` (the Load it protects), `autonomy_min`,
  `walk_in_s`, `f_window_hz`, `efficiency`;
- the requirements:
  - `ride_through_h` (Q1);
  - `scenarios` (Q3);
  - the RoCoF, nadir and quasi-steady-state limits (Q2);
  - `rocof_window_ms`;
  - `gfm_margin`;
  - the critical loads (reusing the DtC critical tags).

**Every value is tagged** `measured | datasheet | assumed`.

**The campus YAML gains the same fields.**
- `ingest/campus.py`: `control` on bess/pv/wind; governor fields on genset;
  a new `ups` kind.
- `draft_campus` copies them from the sidecar, so the hub and the campus
  never disagree.
- Defaults live in `producers/campus.py`, tagged `assumed`:
  - BESS `gfm_droop`, R 4 %, τ_f 0.1 s, i_max 1.2 pu;
  - PV `gfl_qu`;
  - genset `droop`, R 4 %, H 1.5 s, T_g 0.5 s, start 10 s.

**Physics checks**, in the style of `templates/unit_params.py:199-303`:
- droop > 0;
- H > 0 where required;
- i_max ≥ 1;
- a GFM must have `s_mva`;
- a UPS must name a Load that exists;
- an unknown `control` is refused with the allowed list.

**The Data Center template gains:**
- `island_config.json`;
- a candidate GFM BESS;
- the UPS as a `ups` unit;
- genset governor data, tagged `assumed`.

**Tests:**
- each refusal;
- draft round trip (sidecar to YAML to built net);
- the defaults are tagged `assumed`;
- mutations.

### I2: sizing for islanding in the hub LP — step 1 (M–L)

New module: `services/solver/island.py`, an `extra_functionality` wrapper
composed like `adequacy.py`. It runs per period. Δ is the hourly weight. For
each hour t, with the critical load L_c(t) and the import flow p_imp(t):

**(a) Ride-through energy.**
- For an outage of duration D starting at t, define the deficit the gensets
  leave, d(t,τ) ≥ L_c(τ) − Σ_g avail_g(τ)·p_nom_g for τ ∈ [t, t+D), with
  d ≥ 0.
- Storage must cover it in power and in energy:
  - d(t,τ) ≤ Σ_s p_nom_s;
  - Σ_s e_s(t) ≥ Σ_τ d(t,τ)·Δ. The SoC is held in reserve.
- PV is left out, which is conservative and ledgered.
- The start hours are strided (`ride_through_stride_h`, default 1 on the
  168 h week) to bound the O(T·D) rows.
- With `fuel_store` set, the genset fuel draw over D must fit the fuel
  Store's `e_nom`.

**(b) Power headroom at the instant of islanding** (scenario `seamless`).
- Define ΔP_isl(t) = p_imp(t) − P_ups,it(t). The IT load the UPS carries
  leaves the island at t = 0.
- The constraint is:
  Σ_{s∈GFM} (p_nom_s − p_s(t)) + Σ_{g spinning} (avail_g·p_nom_g − p_g(t)) ≥ ΔP_isl(t).
- Under `ups_bridged`, the step is instead the largest pickup block onto the
  started gensets: Σ_g load_step_max_g·p_nom_g ≥ the largest block.

**(c) Grid-forming MVA.**
- Σ_{s∈GFM} s_nom_s + Σ_{g online in island} s_nom_g ≥ (1 + gfm_margin)·L_island(t).
- Here s_nom = p_nom / pf.
- This is a rule of thumb, ledgered `assumed`.

**(d) RoCoF.**
- 2·(Σ_g H_g·s_nom_g + Σ_{VSM} H_v·s_nom + Σ_{droop GFM} H_eq·s_nom) ≥ f0·ΔP_isl(t) / RoCoF_max.
- Only units online at t count.
- It is linear because H is a parameter and s_nom is linear in p_nom.

**(e) Quasi-steady-state frequency.**
- Σ_i p_nom_i / (R_i·f0) · Δf_qss,max ≥ ΔP_isl(t).

**The nadir is not in the LP.** I6 checks it, and I7 may feed cuts back.

**Reported:**
- which constraint binds, at which hours;
- its dual;
- the **cost of island capability**: the same project solved without the
  island constraints, and the difference in annualised cost.
- That cost is the economic dimensioning the client asked for.

**The existing DtC stress stays.** I2 adds the duration-based requirement;
it replaces nothing.

**Tests**, each on a tiny hand network where one constraint binds and the
answer is worked by hand:
- (a) 10 MW critical load, D = 4 h, no gensets: BESS p_nom ≥ 10 MW and
  e_nom ≥ 40 MWh. With a 6 MW genset: 4 MW and 16 MWh.
- (b) 10 MW import, UPS carries 6 MW: the BESS needs 4 MW of headroom.
- (d) ΔP 4 MW, f0 50, RoCoF_max 1 Hz/s, H_v 4 s: s_nom ≥ 25 MVA.
- (e) by hand.
- Constraints off: the solution is identical to today's (a regression guard
  over all three hub templates).
- Mutations.

### I3: island critical hours — step 2 (S)

New `ISLAND_CRITERIA` in `ranking/campus.py`, used when the study has
island scenarios:
- maximum ΔP_isl(t), the step at grid loss (import; export too, for
  over-frequency);
- minimum island inertia Σ H·S online;
- minimum GFM headroom;
- maximum island load;
- maximum GFL renewable share at low load (voltage rise and over-frequency);
- minimum stored energy at loss (ride-through).

These come from the C2 hourly tables plus the sidecar data. The top-k union
and tie-spreading are as today (k = 3), adding about 10–18 hours per period.

**Tests:** hand series where each criterion picks a known hour; ties; an
empty island config adds nothing.

### I4: island steady state by control mode — step 3a (M)

**New module: `static/island_flow.py`.**

**Building the island net.**
- Start from `Campus.net`. Take the `ext_grid` out of service and open the
  PCC.
- Each unit is built by its `control` (see the table):
  - GFM units and gensets become `gen` with `slack=True` and
    `slack_weight = P_n/R`;
  - an isochronous genset gets a dominant weight;
  - GFL units become `sgen` with their `DERController` law;
  - UPS units become loads at recharge plus walk-in.
- The dispatch comes from the critical hour, with ΔP_isl redistributed by
  the distributed slack.

**Outer loops**, to a fixed point with a tolerance:
- **Q–V droop:** V_set,i = V0 − k_q,i·Q_i.
- **GFL frequency-watt:** P_i = P0 − K_i·(Δf − deadband).
- **Current limit:** a GFM above i_max becomes PQ at its limit, and the
  load flow is re-solved.

**Results per hour:**
- Δf_qss = −ΔP / Σ K;
- every bus voltage against its band;
- Q sharing;
- loading of units, transformers and cables;
- saturated units;
- a verdict: `viable` | `not viable: <reason>`.
- The reasons are: no reference, every GFM saturated, voltage out of band,
  overload, non-convergence (returned, not raised, as in
  `campus_flow.py:122-128`).

**Sub-step I4b: grid-connected control-law check.**
- At the existing critical hours, run each unit's actual Q law in place of
  the MILP's free Q, and report the PCC Q and the voltages.
- Ledger the difference: the MILP assumes a plant controller that can
  dispatch any Q inside the capability.

**Tests:**
- Droop sharing by hand: two sources, deficit ΔP. Each takes
  ΔP·K_i/ΣK, and Δf = ΔP/ΣK. This reproduces spike 1 exactly.
- Q–V droop sharing ∝ 1/k_q on a symmetrical network.
- `gfm_droop` and `gfm_vsm` give identical steady-state results.
- A GFL-only island is refused as "no reference".
- An isochronous genset takes the whole deficit, and Δf = 0 until its limit.
- A saturated GFM hands over to the next unit. When every GFM saturates,
  the island is refused.
- A Q(U) GFL unit lands on its curve.
- The island built with the PCC re-closed equals today's campus load flow.
- Mutations.

### I5: short circuit by control mode, grid-connected and islanded — step 3b (M)

**Grid-connected** (`static/campus_sc.py`):
- **Gensets become real synchronous machines** (`gen` with `xd_pp`,
  `vn_kv`). This retires the C1 ledger line that screened them as 1/x''d
  current sources.
- The converter k comes from its mode (`k_gfl`, `k_gfm`).
- The max case is as today.

**Islanded** (new module `static/island_sc.py`):
- **With a synchronous source:** pandapower `calc_sc` with no `ext_grid`
  (spike 2). The converters are current sources in the max case.
- **Minimum case, own method.** pandapower drops converters in its min case
  (spike 3). Its own method keeps the GFM units energised at
  k_gfm,min·In; the GFL units are kept or dropped by the `gfl_min_case`
  setting (default: dropped, ledgered).
- **Inverter-only island:** pandapower crashes here (spike 4). For a bolted
  fault fed by ideal current sources through a passive network, all the
  injected current flows into the fault: Ik'' = |Σ k_i·In_i|, referred to
  the fault bus's voltage. The max case adds them in phase; the min case
  takes the minimum online set at the island critical hour with k_min.
  Both are ledgered.
- **Protection sensitivity:**
  - a new optional bus/feeder field, `i_pickup_ka` (overcurrent pickup,
    tagged);
  - a check that Ik''min,island ≥ s·I_pickup, with s = 1.5 by default
    (assumed);
  - a "protection blind in island" verdict when it fails.
- **The ratio of grid-connected Ik''max to island Ik''min**, reported per
  bus. It is the setting-group question for the protection engineer.

**Tests:**
- Genset-only island against the IEC formula with K_G, worked by hand
  (spike 2: 1.589 against 1.604 kA before K_G).
- Inverter-only island equals Σk·In by hand.
- A mixed island is the superposition.
- A pin test on pandapower's min-case behaviour, so an upgrade that
  changes it fails loudly.
- The protection verdict on both sides of s.
- Grid-connected results are unchanged for converters, and changed for
  gensets only by the `gen` model, with the delta explained in the test.
- Mutations.

### I6: first-pass frequency check — aggregated inertia and droop — step 4a (M)

**New module: `static/frequency.py`** (numpy/scipy only; no pandapower).

**The model.** A single-bus aggregate swing equation:

  (2·H_sys·S_sys / f0) · dΔf/dt = Σ_i ΔP_i(t) − ΔP_event(t) − D·P_load·Δf/f0

- H_sys·S_sys is the sum of synchronous H·S for the online gensets and
  H_v·S for the VSMs.
- Each ΔP_i follows its unit's `control` (table above):
  - **genset:** governor lag T_g, droop R, a load-acceptance step limit, a
    start delay when cold, P ≤ P_max;
  - **GFM droop:** a first-order lag τ_f with gain 1/R, capped by headroom
    and i_max;
  - **VSM:** its H_v in the swing, plus damping and droop;
  - **GFL frequency-watt:** deadband, delay, ramp and limit;
  - **GFL PQ:** none;
  - **UPS:** leaves at the event and walks back in at r_walkin. If |Δf|
    exceeds its window, it drops off (flagged). Its battery autonomy is
    checked against the time to recover.
- The load damping D defaults to 0. A data centre is mostly constant power,
  so this is the conservative choice; ledgered.

**Events:**
- **E1:** unplanned islanding at hour t, with ΔP = ΔP_isl(t). An exporting
  hour gives over-frequency.
- **E2:** the largest load step (a chiller restart, a UPS walk-in block).
- **E3:** the largest unit trip in the island.
- **E4:** `ups_bridged` start. The bus is dead, the gensets start after
  `start_s`, and the load is picked up in blocks.
- **E5:** PV loss in the island (cloud).

**Metrics:**
- RoCoF at t = 0⁺, and RoCoF over `rocof_window_ms`;
- the nadir and the time to reach it;
- Δf_qss;
- the time to return to within the band;
- headroom and current saturation flags;
- UPS window violations.

**Where it runs.**
- E1 runs at **every hour**: it takes milliseconds, so it doubles as a
  check on I3's choice of hours.
- E2–E5 run at the island critical hours.

**EMT/RMS recommendation flags:**
- the nadir margin is below a set threshold;
- GFM units saturate during the event;
- SCR < 3 at the PCC with a large GFL share;
- a GFM share is high in a weak island.

**Labelled a screening.** It has no voltage dynamics, no inter-unit
oscillation and no current-limit dynamics.

**Tests against analytical results:**
- No response and a constant ΔP give a linear ramp, with RoCoF exactly
  ΔP·f0/(2·H·S).
- A single first-order governor with inertia and no limit: the nadir and
  its time against the closed-form second-order solution, within 0.1 %.
- Δf_qss = ΔP / Σ K.
- The D'Arco–Suul equivalence: droop with τ_f against a VSM with
  H = τ_f/(2R). The small-signal step responses agree.
- Saturation makes the nadir deeper monotonically, and an isochronous
  genset recovers to 0.
- The I6 E1 Δf_qss equals the I4 island load flow Δf_qss at the same hour.
  This consistency gate between the two models must hold.
- Mutations.

### I7: closing the loop — frequency cuts back into sizing (S–M; gated by Q6)

When I6 fails at hours T_fail, cuts are added to I2 at those hours:
- **From a failed nadir:** a required H_sys(t) or headroom, obtained by
  inverting the I6 closed-form nadir for the units' response parameters.
  It is linear in s_nom.
- **From a failed recovery:** a required droop gain.

Then the hub is re-solved, and I3, I4 and I6 rerun. This repeats at most N
times, by default 3. It is reported like C11's loop: trial, verdict, cost.

**Tests:**
- a hand case that fails once and passes after one cut;
- termination at N;
- no cuts when everything passes.

### I8: multi-bus RMS and the client-grade handoff — step 4b (L; gated by Q4)

**ANDES path** (`handoff/andes_campus.py` plus `static/rms.py`):
- Convert the island net (I4) through `andes.interop.pandapower`.
- Attach dynamic models by `control`:
  - REGCA1+REECA1(+REPCA1) for GFL;
  - REGF1 for GFM droop;
  - REGF2 for VSM;
  - GENROU + GAST/TGOV1 + SEXS for gensets (the diesel or gas engine as
    GAST is an approximation; ledgered);
  - a constant-P load with a ramp for the UPS.
- Run E1–E5 at the island critical hours.
- **Gate:** the frequency nadir agrees with I6 within a tolerance. Where it
  does not, the difference is reported, not hidden.
- New results: per-bus voltage dips and current-limit hits.

**PSS/E path:**
- Extend `dyr_writer` with governors and exciters, which are missing today.
- Add the WECC grid-forming REGFM_A1 (droop) and REGFM_B1 (VSM) records.
- Emit a campus `.raw`/`.dyr` bundle per island hour, so that PowerFactory
  or PSS/E can run the client-grade study.

**EMT stays a flag.** It is raised by I6 or I8.

### I9: panel, copilot and report (M)

This follows the C6 and C9 conventions.

**An "Island" section in the campus panel.** It holds:
- the requirements form (Q1–Q3 settings);
- the control mode per unit;
- the results:
  - ride-through and the cost of island capability (I2);
  - the island hours (I3);
  - the island load-flow verdicts (I4);
  - short circuit and protection sensitivity (I5);
  - the frequency traces per event, with their limits (I6).

**Copilot tools.** Read and write chat tools, as in increments 9 and 10:
- `campus_island_config` (read);
- `campus_set_island_config` (write);
- `campus_run_island_study` (write).

**The report** states that the study is a steady-state and aggregate-
frequency screening, not a certificate. Every `assumed` row is marked.

## Order, PRs and agents

- **I1** comes first: everything reads the control modes.
- Then **I2** in pypsa-gui and **I3 → I4 → I5** in gridspine, in parallel.
  They share only the I1 schema.
- Then **I6**, which needs the I4 Δf_qss consistency gate, and then **I9**.
- **I7** and **I8** follow their owner gates (Q6, Q4).

The first deliverable of I1 is the sidecar contract and the campus YAML
fields. It is tested against a hand-built campus with one GFM BESS, one
genset, one GFL PV and one UPS. That campus is reused as the fixture
through I4–I6.

| increment | independent oracle |
|---|---|
| I2 | hand-worked tiny LPs, one constraint binding each |
| I3 | hand series |
| I4 | droop sharing by hand; spike 1; PCC re-closed equals today's flow |
| I5 | IEC 60909 by hand (with K_G); Σk·In; pandapower min-case pin |
| I6 | closed-form RoCoF, second-order nadir, Δf_qss; D'Arco–Suul; I4 agreement |
| I8 | I6 agreement; ANDES's own examples for each model |

## Out of scope, and ledgered

- **EMT:**
  - sub-cycle current limiting;
  - converter control interaction;
  - weak-island harmonics.
- **Asymmetric faults:** only 3-phase is computed. GFL converters inject
  mostly positive sequence, which matters for earth-fault protection in an
  island. **This is a strong ledger item.**
- **Protection coordination:** only sensitivity is checked (I5).
- **Resynchronisation dynamics.** I4 reports the static sync-check inputs
  (Δf, ΔV), not the transient.
- **Grid-forming support of the utility grid at the PCC** (GC0137, the RfG
  revision):
  - grid-code extraction of GFM requirements;
  - an ESCR or impedance screen.

  It reuses I1, I5, I6 and I8. It is a follow-up plan.
- **Genset unit commitment as a MILP** in the hub (Q7).
- Black start of the utility grid.
- Tap-changer dynamics.
- Harmonics and flicker.
