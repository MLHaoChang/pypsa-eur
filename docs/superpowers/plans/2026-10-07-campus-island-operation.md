# Campus island operation and converter control: a first pass

This plan was written before the code, as every gridspine increment is. It
will be amended wherever building it proves it wrong. It was amended once
already, after an independent review on 2026-10-07; see
[Review and resolution](#review-2026-10-07-and-resolution).

It **partly reverses** the decision of 2026-10-05 that there would be "no
dynamic modelling for now" (`2026-10-05-gridspine-campus-electrical.md:4-6`).
On 2026-10-07 the owner asked for two things:

- a **first-pass frequency and RoCoF check**, built from aggregated inertia
  and droop;
- **converter control strategies** as a first-class part of the model. They
  are to be exercised in the steady-state load flow, in the short-circuit
  analysis, and partly in that frequency check.

Multi-bus RMS (I8) waits on a legal check (Q4). EMT stays a flag.

## The goal

A client asks for a data centre that can **island**: when the grid is lost,
the site carries its critical load on its own gensets, BESS and UPS. Five
questions follow, and each has to be answered for the control strategy each
converter actually runs.

1. **Ride-through (sizing).** Is the site sized to carry its critical load
   for the required outage duration, with its genset redundancy? This covers
   energy, fuel, and the BESS and UPS charge kept in reserve.
2. **The instant of islanding (sizing and frequency).** Can the units that
   are online absorb the power step the moment the grid goes, whether it is
   an import step or an export step? Three things decide this: power
   headroom, inertia (which sets RoCoF) and droop (which sets the frequency
   it settles at).
3. **Is the island viable in steady state?**
   - Some unit must form the voltage and frequency.
   - The voltages must stay in their bands.
   - Reactive power must be shared between units.
   - No unit or branch may be overloaded.
4. **Does protection still work in the island?**
   - On the minimum side, the relays must still see a fault with only
     on-site sources, at their operating time, for both phase and earth
     faults.
   - On the maximum side, the switchgear must still withstand the fault
     current with the grid connected (C4c today).
5. **Frequency, first pass.** For each event, compute RoCoF, the nadir, the
   quasi-steady-state deviation and the recovery time, at the hours where
   islanding is hardest.

Every verdict is a **screening**, and the report says so on each table. Each
table names the model behind it: steady-state load flow, converter-aware
fault current, or single-bus aggregate frequency. None of them is a
certificate.

**Scope note.** "Support island operation" can also mean that the site acts
as grid-forming at its PCC to support the utility grid (GB GC0137, the RfG
revision). This plan covers **site islanding**. Grid support at the PCC
reuses the control-mode schema of I1 and the models of I4–I6 and I8, but its
grid-code checks are a follow-up (see Out of scope).

## Owner decisions (2026-10-07)

| question | decision |
|---|---|
| Dynamics | A **first-pass, aggregated** frequency/RoCoF check from inertia and droop (I6). Multi-bus RMS is a later, gated increment (I8). EMT stays a flag. |
| Converter control | Control strategies become a **unit attribute** (I1). They are implemented and tested in the steady-state load flow (I4), the short-circuit analysis (I5, I5b), and the frequency check (I6), and later in RMS (I8). |
| Steps | 1 sizing, 2 island critical hours, 3 island steady state and short circuit, 4 frequency. |
| Review findings (2026-10-07, third round) | **All** blocking, should-fix and nit findings are folded in, and a focused re-review follows before coding starts. |
| Re-review findings (2026-10-07, fourth round) | **All** N1–N14 and nit findings are folded in. **I1 starts without a third review**; each increment's PR keeps its own gates. |
| Storage weighting (N12) | The hub templates weight storage by 52.14 h per snapshot today. That is fixed in **its own PR before I2a** (S0, below). |

### Owner decisions (2026-10-07, second round)

All nine were answered on 2026-10-07. Each took the recommended answer.

| # | question | decision |
|---|---|---|
| Q1 | How the outage ride-through is specified | **Two durations.** A **bridge** time in seconds to minutes (UPS/BESS until the gensets are online) is reserved in I2a and checked in I6 (E4). A **sustained** time in hours (gensets, fuel, BESS) is an I2a constraint, from every start hour. Fuel autonomy is its own input. The template ships 24 h sustained, tagged `assumed`, and the panel asks for it. Critical load = IT plus the cooling the IT needs, tagged per Load; offices are not critical. |
| Q2 | Where the frequency limits come from | **From the most sensitive island equipment, with defaults.** The equipment is the UPS input window, the chiller and drive trips, and the genset ISO 8528-5 class. The class limits are user-entered, because the standard is not freely available (the same treatment as VDE). Until entered, the defaults are tagged `assumed`: a 47.5 Hz lowest frequency, ±1 Hz quasi-steady-state, and 1 Hz/s RoCoF over 500 ms. 47.5 Hz is the bottom of the RfG Continental Europe 30-minute band (47.5–48.5 Hz); the unlimited band is 49.0–51.0 Hz. The limits for the transition and for steady island operation are kept separate. The report names the binding limit. |
| Q3 | Which islanding scenarios are studied | **All three:** `ups_bridged`, `seamless` and `planned`. `ups_bridged` and `seamless` are compared side by side, which gives the client the cost of seamless islanding. In `seamless`, the gensets start and join the BESS-formed island after their start delay. |
| Q4 | Multi-bus RMS, given ANDES is GPL-3.0-or-later (confirmed from the wheel metadata) | **ANDES as an optional engine in its own process**, exchanging files as the gridspine stages do. It is not bundled in the desktop app. The **PSS/E/PowerFactory export** is built as the client-grade route. A short legal check comes before I8. |
| Q5 | Island constraints in the hub LP | **Opt-in** per project; the template ships them enabled. A regression test guards that results are unchanged when they are off. |
| Q6 | Frequency failures fed back into sizing (I7) | **Manual, with a preview.** The panel shows the failed hours, the proposed cut and its estimated cost, and the user applies it with "apply cuts". It becomes automatic only once a project's parameters are mostly datasheet-backed. |
| Q7 | Gensets running in parallel with the grid | **Cold standby by default**, a per-genset `spinning` flag, and **optional linearised unit commitment** for fixed-size gensets (I2c), so the LP can buy spinning capacity at the fuel cost of its minimum load. A full unit-commitment MILP stays out of scope. |
| Q8 | GFM short-time current limit | **1.2 pu, tagged `assumed`**, with a request for the datasheet value, and a **report sensitivity at 1.2 / 1.5 / 2.0 pu** (I5, I6). |
| Q9 | Unbalanced faults | **Their own increment, I5b, straight after I5.** |

## What exists today (surveyed 2026-10-07)

**The hub LP** (pypsa-gui; PyPSA 1.1.2 + linopy):
- The *Data Center Energy Hub* template is one representative 168 h week.
  Each snapshot is weighted 8760/168 = 52.14
  (`project_templates/eh_templates.py:18-30,94-174`).
- It has:
  - gensets: 4×10 MW fixed (`p_min_pu` 0), plus a candidate `genset_new`
    up to 40 MW;
  - a 4 h BESS candidate, `bess_new`;
  - a 15 MW / 1 h `ups_battery`, a plain StorageUnit, against about 36 MW
    of IT load (`:125,143-145`);
  - PV;
  - a 40 MW import Link;
  - `cyclic_state_of_charge=True`.
- The gensets are Generators with no fuel bus.
- Custom constraints are composed through `extra_functionality` wrappers
  (`services/solver/adequacy.py:61-222`, `objective.py:189`). I2 follows
  that pattern and its documented lessons (`adequacy.py:85-120`):
  - the `get_active_assets(P)` mask;
  - coordinate membership before `.sel`;
  - excluding the slack generators.
- `linearized_unit_commitment` exists in PyPSA 1.1.2. It is not passed
  through `services/solver_service.py` (`:1072`).
- PyPSA 1.1.2 models committable *extendable* units with big-M
  (`pypsa/optimization/constraints.py:295-330`).
- Islanding today is the DtC stress (`services/adequacy/dtc.py:75-105`).
  - The import Link goes to `p_max_pu = 0` for **the whole horizon**.
  - There is no duration, headroom, inertia or droop.
  - Its critical tags promote a whole bus; `per_load` `critical_load_ids`
    exist (P16).
- There is no UPS model.
- No component carries H, a droop, a control mode, or a GFM/GFL flag.

**gridspine campus**
- The units are `load`, `bess`, `pv`, `wind`, `genset`
  (`ingest/campus.py:126-158`).
- Every generator is a PQ `sgen`. A genset is a current source of 1/x''d,
  which is ledgered (`ingest/campus.py:168-170,401-405`).
- There is always an `ext_grid` at the PCC.
- Short circuit uses IEC 60909 max/min through pandapower `calc_sc`, bus
  results only (`static/campus_sc.py:46-73`).
- The MILP treats inverter Q as a free dispatch variable inside an octagon
  (`static/campus_milp.py:26-33`). No control law is applied.
- **The hourly contract** is `HOURLY_COLUMNS = (unit_id, period, hour, p_mw,
  status)` (`schema/campus.py:25`).
  - `status` means the asset exists in the period. It is not commitment.
  - There is no state of charge, no online status, no UPS load and no
    headroom.
- `select_campus_hours` sorts descending and skips a criterion whose values
  are nowhere > 0 (`ranking/campus.py:126-136`).
- Inertia exists only as a case39 ranking metric (`ranking/metrics.py:146-169`).
- `dyr_writer` emits GENROU/GENSAL and REGCA1+REECA1 only. It has **no
  governors and no exciters**, and the campus track exports nothing.

### Spikes (2026-10-07, pandapower 3.1.2, scratch scripts not committed)

The review re-ran these and added the facts marked *(review)*.

1. **An islanded load flow with droop-weighted distributed slack works.**
   - Setup: two `gen` units with `slack=True`, `slack_weight = P_n / R`, and
     `runpp(distributed_slack=True)`.
   - The 2.03 MW deficit split 1 : 2 in proportion to the weights.
   - *(review)* The distributed slack **ignores `max_p_mw`**: a gen with
     `max_p_mw=3` was dispatched at 8.67 MW. P limits need their own loop
     (I4).
   - *(review)* A naive Q–V droop outer loop V_set ← V0 − k_q·Q **diverges**
     on short ties. With 4 % and 8 % droop on 10 MVA units:
     - tie X = 0.001 pu: Newton–Raphson fails at iteration 3;
     - X = 0.01 pu: it fails at iteration 5;
     - X = 0.05 pu: it oscillates;
     - X = 0.2 pu: it converges.

     Campus ties are short, so I4 models the droop as a virtual reactance
     instead.
2. **An island short circuit with a genset works once the genset is a real
   `gen`.**
   - Setup: no `ext_grid`; at 33 kV, Sn 12.5 MVA, x''d 0.15, cos φ_r 0.8,
     `rdss_ohm` 0.01, `vn_kv` 33.
   - pandapower: 1.589 kA. By hand, c·Sn/(√3·Un·x''d) gives 1.604 kA
     without K_G.
   - *(review)* With K_G = 1.0261 at R_G = 0 the hand value is 1.563 kA,
     and pandapower reproduces 1.563 kA exactly at R_G = 0. The 1.589 kA
     result is therefore **not yet explained** by the hand oracle. The I5
     test must explain it before pinning it.
   - *(review)* `calc_sc` raises `AttributeError: rdss_ohm` when the gen has
     no `rdss_ohm`. Both `rdss_ohm` and `cos_phi` are required.
3. **pandapower's min case leaves out current-source sgens.**
   - With the genset plus a 10 MVA GFL inverter, Ik''max rose from 1.589 to
     1.799 kA (the difference is k·In), but Ik''min stayed at 1.445 kA
     (= 1.589/1.1).
   - This **follows IEC 60909-0:2016**, which lets full-converter sources be
     neglected in the minimum case. It is not a pandapower defect.
   - So the island minimum used for protection needs a converter-aware
     method of its own (I5). That method is labelled "not IEC 60909".
4. **An inverter-only island crashes `calc_sc`** with a `ZeroDivisionError`:
   there is no voltage source.
5. **pandapower ships `DERController`.**
   - It has `QModelQVCurve`, `QModelCosphiVCurve`, `QModelCosphiPCurve` and
     PQV capability areas.
   - *(review)* **`QModelCosphiP` is a fixed cos φ, and it computes
     q = sin φ·p**, as its own docstring says (tan φ is right). That is
     10 % low at cos φ 0.9. I4 pin-tests every QModel it uses, and
     implements a fixed power factor itself.
6. *(review)* **pandapower's `fault="1ph"` cannot be used as-is for I5b.**
   - It adds current-source sgen contributions even with no zero-sequence
     path: an unearthed delta side gave 0.315 kA with a sgen, against
     0.0 kA without one.
   - It gives every `gen` a fixed 1 kΩ + j1 kΩ zero-sequence shunt
     (`pd2ppc_zero.py:374-388`), so genset neutral earthing cannot be
     expressed.
7. **ANDES 2.0.0 is on PyPI.** It has:
   - `REGF1` (GFM droop), `REGF2` (GFM VSM), `REGF3` (dVOC);
   - `REGCV1/2` (VSG);
   - `REGCA1`/`REECA1`/`REPCA1` (GFL);
   - the governors `TGOV1`, `GAST`, `IEEEG1`;
   - the exciters `SEXS`, `ESST4B`, `AC8B` and others;
   - a pandapower importer (`andes.interop.pandapower`).

   I8 is therefore feasible, as a separate-process engine (Q4).

## The control strategies

Each converter unit gets a `control` value. Each genset gets a governor mode.
The table is the contract that I4, I5, I6 and I8 implement.

| `control` | grid-connected load flow | island load flow (I4) | short circuit (I5) | frequency check (I6) | RMS (I8: ANDES / .dyr) |
|---|---|---|---|---|---|
| `gfl_pq` | PQ sgen at its setpoint (today) | PQ. **Cannot be the reference.** | current source k·In; IEC max case only; island min: kept or dropped by `gfl_min_case` | no P response | REGCA1 + REECA1 |
| `gfl_qu` | Q(U) curve (`QModelQVCurve`, pin-tested) | same | same | none | REECA1 with Kqv |
| `gfl_pf` | fixed cos φ, own law Q = P·tan φ; or a cos φ(P) curve (`QModelCosphiPCurve`, pin-tested) | same | same | none | REECA1 (pf flag) |
| `gfl_fw` (frequency-watt, FFR) | PQ | PQ, plus ΔP = −K_fw·sign(Δf)·max(0, \|Δf\| − db), capped at `fw_p_limit`, in an outer loop | same | deadband, delay, ramp, limit | REGCA1 + REECA1 + REPCA1 (freq) |
| `gfm_droop` | `gen` at V0 behind a virtual reactance x_v = k_q (the Q–V droop); P at its setpoint | **reference**, slack weight P_n/R; Q–V droop through x_v; P and current limits in an outer loop | voltage source until its current limit, so a current source at `i_max_pu`·In; IEC 60909 current source in the max case; island min: own method at `k_gfm_min` | native droop with a filtered measured P: P = P0 − (P_n/R)(Δf + τ_f·dΔf/dt)/f0. Its τ_f term acts as inertia, H·S = τ_f·P_n/(2R), until it saturates; then it becomes a constant-P current source | REGF1 / REGFM_A1 |
| `gfm_vsm` | as `gfm_droop` | as `gfm_droop` (tested identical) | as `gfm_droop` | virtual inertia H_v on S_n, with a steady-state gain 1/R about ω0 (that damping *is* the droop) | REGF2 / REGFM_B1 |
| genset `droop` | power-factor control (today) | `gen` behind x_v, slack weight P_n/R, P limit in an outer loop | synchronous machine x''d (`gen` with `vn_kv`, `xdss_pu`, `rdss_ohm`, `cos_phi`); K_G, or K_S behind a unit transformer (`power_station_trafo`); decrement to Ik for time-delayed relays | inertia H on S_n, governor lag T_g, droop R, load-acceptance step limit, ramp, start and sync delay when cold | GENROU + GAST (ANDES) / DEGOV1 (.dyr) + SEXS |
| genset `isochronous` | as above | the **only** slack (every other weight 0), so Δf = 0 until its limit | as above | integral governor | as above |
| `ups` (new kind) | rectifier load, constant P | load: IT load (or recharge plus walk-in, after a transfer). Not a source. | none (rectifier) | stays on line unless `transfers_on_islanding`. If it transfers, it walks back in over `walk_in_s`. It drops off if f leaves its input window. | constant-P load with a ramp (no ANDES model; ledgered) |

Principles the tests pin:
- **One base for droop and one for inertia.**
  - `droop_pct` R is on the unit's rated active power P_n, so
    K = P_n/(R·f0) and the slack weight is P_n/R, in I2b, I3a, I4 and I6
    alike.
  - H and H_v are on the rated apparent power S_n.
  - With the template's power factors (0.95 inverters, 0.8 gensets),
    mixing the two bases would move Δf_qss by 5–25 %.
- **Droop and VSM are the same model in steady state.**
  - A droop GFM with power-filter time constant τ_f equals a VSM with
    H_v·S_n = τ_f·P_n/(2R) and a steady-state gain 1/R about ω0 (D'Arco &
    Suul, IEEE Trans. Smart Grid, 2014). In that VSM the damping about ω0
    *is* the droop. A VSM has no separate damping term here.
  - So the two differ only when H_v·S_n ≠ τ_f·P_n/(2R). I4 tests that they
    are identical at equilibrium.
  - I6 tests the dynamic equivalence with two independently coded models.
  - Damping against the measured grid frequency does nothing in a
    single-bus model. It is a multi-bus effect, left to I8 (REGF2
    parameters).
- **GFL and GFM differ in steady state.**
  - In the island load flow, a GFL unit can never be the reference. An
    island with no unsaturated GFM unit or genset is refused, with a reason.
  - In short circuit, a GFM unit stays energised and forming in the minimum
    case, while a GFL unit may trip on the voltage dip (`gfl_min_case`,
    ledgered).
  - The frequency response differs, as the table shows.
- **Limits turn a source into a fixed injection.** A GFM at its current
  limit, or a slack unit at its P limit, is no longer a reference.
  - I4 fixes it at its limit and re-solves.
  - I5 caps its fault current at `i_max_pu`·In.
  - I6 turns it into a constant-P source, and its inertia contribution goes.

## Increments

Each increment is its own PR, with the campus discipline:
- tests first, worked by hand or against an independent oracle;
- mutations;
- full gates;
- a browser run for anything the user sees.

Sizes are relative: S, M, L.

### I1: control modes, island data and the UPS unit (M)

**One source of island data: a hub sidecar `island_config.json`.**
- It follows the pattern of `dtc_config.json`.
- **Its schema lives in gridspine** (`gridspine/schema/island.py`), because
  gridspine's `draft_campus` and the I3a producer read it. pypsa-gui imports
  gridspine (`services/gridspine_service.py`), never the reverse, so
  pypsa-gui's `models/energy_hub.py` wraps the gridspine validator. It does
  not define its own.
- It is keyed by PyPSA component name.
- Every value is tagged `measured | datasheet | assumed`.

**Bases** (see the principles above):
- `droop_pct` and `q_droop_pct` are on P_n;
- H and H_v are on S_n;
- k_q is in pu on the unit base.

**Per converter unit:**
- `control`;
- `pf_rated`, which gives the hub's s_nom = p_nom / pf_rated;
- `droop_pct` and `q_droop_pct`;
- `tau_f_s` (droop GFM);
- `H_v_s` (VSM);
- `i_max_pu` (the GFM fault and overload current limit) and `k_gfm_min`
  (I5);
- `k_sc` (the GFL fault current, as today);
- Q-law parameters:
  - `qu_points`, the Q(U) curve;
  - `cos_phi`, a fixed power factor;
  - `cosphi_p_points`, a cos φ(P) curve;
- frequency-watt: `fw_k_mw_per_hz`, `fw_deadband_hz`, `fw_delay_s`,
  `fw_ramp_mw_per_s`, `fw_p_limit_mw`;
- `neg_seq`: GFM negative-sequence capability, default none (I5b).

**Per genset:**
- `governor` (`droop` | `isochronous`), `droop_pct`, `q_droop_pct`;
- `H_s`, `T_g_s`;
- `ramp_pu_per_s`;
- `start_s` and `sync_s`;
- `spinning` (Q7) and `p_min_pu`. `p_min_pu` must be > 0 when `spinning`
  is set or UC is on, and it is written to `Generator.p_min_pu` before the
  solve;
- `load_step_max_pct` (the ISO 8528-5 class, user-entered);
- short-circuit data:
  - `xd_pp`;
  - `cos_phi_r` (default 0.8 = `GENSET_PF`);
  - `rdss_ohm`, derived from the existing `rx_sc` and `xd_pp`;
  - `xd_sat` and `excitation` (`avr_pmg` | `avr_shunt` | `none`), which
    the λ factor needs (I5);
- `unit_mw`: the unit size of an extendable genset, for N+k (I2a);
- `neutral_earthing` (`solid` | `resistance` + R_N | `isolated`; I5b);
- `unit_transformer`. When true, IEC K_S (`power_station_trafo`) replaces
  K_G (I5).

**Per UPS** (a new `ups` kind):
- `it_load`: the Loads it protects;
- `walk_in_s`;
- `f_window_hz`;
- `transfers_on_islanding`, default false (I2b).
- Its energy, power and efficiency stay on the StorageUnit (`max_hours`,
  `p_nom`, `efficiency_dispatch`). The UPS autonomy is that StorageUnit's
  e(t−1) from I3a, one source of truth. A standalone campus file uses the
  unit's `e_mwh`.

**Per Load:**
- `critical`. This uses the DtC `per_load` `critical_load_ids`, not bus
  tags, so offices on `dc_mv` are not made critical.
- Optional `f_trip_hz` for chillers and drives (Q2).

**Per transformer** (campus YAML): `neutral_earthing` per winding, and
optional earthing transformers as elements (I5b).

**Study requirements:**
- `bridge_s`, `sustained_h` and `fuel_autonomy_h` (Q1);
- `genset_redundancy_n`: the genset N+k for ride-through, default 1,
  tagged `assumed`;
- `ride_through_storage`: the StorageUnits that may carry the sustained
  outage. The default is BESS only. The UPS is reserved for the bridge.
- `scenarios` (Q3);
- `pickup_blocks`: the MW blocks the gensets pick up under `ups_bridged`;
- `linearised_uc` (Q7, I2c);
- the frequency limits (Q2), as two sets, `transition` and `steady_island`.
  Each set holds RoCoF, the lowest and highest frequency, and the
  quasi-steady-state band.
- `rocof_window_ms`;
- `gfm_margin`;
- `gfl_min_case` (`keep` | `drop`, default `drop`);
- `i_max_sensitivity_pu` (Q8), default `[1.2, 1.5, 2.0]`.

**The campus YAML gains the same fields.**
- `ingest/campus.py`: `control` on bess/pv/wind; governor and earthing
  fields on genset; a new `ups` kind; transformer earthing.
- `draft_campus` copies them from the sidecar, so the hub and the campus
  never disagree.
- Defaults live in `producers/campus.py`, tagged `assumed`:
  - BESS: `gfm_droop`, R 4 %, τ_f 0.1 s, i_max 1.2 pu (Q8), k_gfm_min 1.0,
    pf 0.95;
  - PV: `gfl_qu`;
  - genset: `droop`, R 4 %, H 1.5 s, T_g 0.5 s, ramp 0.2 pu/s, start 10 s,
    sync 5 s, `p_min_pu` 0.3, excitation `avr_pmg`.

**Physics and consistency checks**, in the style of
`templates/unit_params.py:199-303`:
- droop > 0;
- H > 0 where required;
- i_max ≥ k_gfm_min ≥ 0;
- a GFM must have `s_mva`;
- a UPS must name Loads that exist;
- `spinning` or UC requires `p_min_pu` > 0;
- `sustained_h` ≤ the period's snapshot span (I2a);
- `genset_redundancy_n` < the number of genset units, so G(τ) cannot go
  negative (I2a);
- Σ `pickup_blocks` ≥ the peak critical load, so the blocks cover the
  load;
- each control mode has its parameters, e.g. `gfl_qu` needs `qu_points`;
- an unknown `control` is refused with the allowed list.

**The Data Center template gains:**
- `island_config.json`;
- `bess_new` marked `gfm_droop` (it already exists);
- the UPS as a `ups` unit, **re-rated to carry the IT load** in S0 (about 36 MW
  plus margin, tagged `assumed`), not today's 15 MW;
- genset governor and earthing data, tagged `assumed`;
- per-Load critical tags.

**Tests:**
- each refusal;
- draft round trip (sidecar to YAML to built net);
- pypsa-gui validates through the gridspine schema;
- the defaults are tagged `assumed`;
- the re-rated UPS still solves the template;
- every field is read by some later increment, checked against the field
  table in the test docstring (no orphan fields);
- mutations.

### S0: storage weighting in the hub templates (S; owner, before I2a)

This is its own PR, from the queued task "Fix storage weighting in the
energy-hub templates".
- `_base()` in `eh_templates.py` (around lines 63–67) sets every
  `snapshot_weightings` column to 52.14.
- PyPSA 1.1.2 uses `snapshot_weightings.stores` as elapsed hours in the
  state-of-charge balance (`pypsa/optimization/constraints.py:1521-1538`).
- So every hub solve today moves 52 hours of battery energy per hourly
  dispatch. That understates BESS and UPS value, and with it the opportunity
  cost behind I2c's cost of island capability.
- **The fix:** `stores = 1.0`, keeping `objective` at 52.14. The test is
  written first, and every changed fixture is explained.
- **The UPS re-rate moves here from I1** (owner, 2026-10-07): `ups_battery`
  becomes 40 MW / 0.25 h. 40 MW carries the ~37.5 MW IT peak with ~6 %
  margin. 15 min covers the 600 s bridge (Q1) at that peak after dispatch
  losses (6.6 MWh of 10 MWh), with room for end-of-life fade. I1 keeps the
  `ups` unit and its sidecar fields.
- Limited foresight (`services/solver/myopic.py`) wrote the objective-based
  representative weight into `stores` too. Each column now scales its own
  step weight, which is the old behaviour when the columns are equal.

### I2a: ride-through, fuel and bridge reserve in the hub LP — step 1 (M)

New module: `services/solver/island.py`, an `extra_functionality` wrapper
composed like `adequacy.py`. It runs per investment period over that
period's snapshots, T of them. S0 must have landed first.

**Where it runs (N13).** `services/solver_service.py` has three optimise
paths: SCOPF (around `:1011`), rolling horizon (around `:1042`) and the
plain solve (`:1072`).
- The island rows attach to the plain solve only.
- Under rolling horizon and SCOPF they are **refused with a reason**,
  because cyclic windows are wrong inside a rolling window.

**Units and variables.**
- Δ is the **snapshot duration in hours** (1 h on the template). It is not
  `snapshot_weightings`, which is 52.14 there.
- e_s(t) is `StorageUnit-state_of_charge`, in physical MWh.
- A StorageUnit's net output is p_s = p_dispatch − p_store, positive when
  discharging. It has no single `p` variable.
- **Windows follow each unit's own setting.**
  - With `cyclic_state_of_charge` (the template), a window that runs past
    the last snapshot continues from the first, and e(t−1) at the first
    snapshot is the last snapshot's SoC.
  - Without it, e(t−1) at the first snapshot is `state_of_charge_initial`.
    A window that runs past the period end is truncated and flagged.
- `sustained_h` > T·Δ is refused (I1).

**(a) Sustained ride-through.** Let RT be `ride_through_storage` and L_c(τ)
the critical load.
- **Genset capacity at N+k.**
  G(τ) = Σ_g avail_g(τ)·p_nom_g − k·U_max.
  - avail_g is the genset's `p_max_pu(τ)` (1 on the template). The
    occurrence derating is not applied, because the N+k covers outages
    (ledgered).
  - k is `genset_redundancy_n`. k must be smaller than the number of units
    (I1).
  - U_max is the largest unit rating: the fixed units' p_nom and the
    extendable gensets' `unit_mw`. It is a constant, so the row stays
    linear.
  - It over-subtracts when an extendable genset is not built. That is
    conservative, and ledgered.
  - The report states "N+k".
- **The deficit, one variable per snapshot.** It does not depend on the
  start hour:
  d(τ) ≥ L_c(τ) − G(τ), d(τ) ≥ 0.
- **Power:** d(τ) ≤ Σ_{s∈RT} p_nom_s.
- **Energy, for each start hour t:**
  Σ_{s∈RT} η_dis,s · e_s(t−1) ≥ Σ_{τ=t}^{t+D−1} d(τ)·Δ + B(t).
  - e(t−1) is the state of charge at the end of hour t−1.
  - B(t) is the `seamless` GFM bridge energy of (b), for storage that is in
    both RT and the GFM set. Adding it to the same row stops the same MWh
    being counted twice.
  - The SoC is held in reserve.
- The problem adds T deficit variables and 3T rows, linear in p_nom.
- **Ledgered:**
  - PV is left out, which is conservative.
  - The site is treated as a copperplate. The hub's `site_transformer` is
    one-way (`p_min_pu` 0), so a BESS on `it_bus` cannot feed cooling on
    `dc_mv`, but I2a counts it. I4's island load flow catches this at the
    island hours.

**(b) Bridge reserve (Q1).**
- **For each UPS u, and for each t:**
  e_u(t−1)·η_dis,u ≥ P_it,u(t)·`bridge_s`/3600.
- **In `seamless`:** the GFM storage also holds energy for the time until
  the gensets have joined:
  B(t) = ΔP⁺(t)·(`start_s` + `sync_s`)/3600, with ΔP⁺ from I2b. It enters
  (a)'s row when the storage is also in RT; otherwise it is its own row.

**(c) Fuel (Q1).**
- The template's gensets have no fuel bus, so the fuel check is reported as
  **"not checked"** there.
- Where a fuel Store feeds the gensets: its `e_nom` ≥ L_c,peak ·
  `fuel_autonomy_h` / η_genset. This is the draw at the critical load,
  which is conservative.

**Tests**, each on a tiny hand network where one row binds, and each able to
fail when its row is deleted:
- **Energy row binds.** 10 MW critical load, D = 4 h, no gensets, BESS
  `max_hours` = 2. The BESS needs e ≥ 40 MWh, so p_nom = 20 MW. Without the
  energy row: 10 MW.
- **Power row binds.** The same case with `max_hours` = 8: energy needs only
  5 MW, and the power row gives 10 MW. Without the power row: 5 MW.
- **N+0:** a 6 MW genset leaves a 4 MW deficit: e 16 MWh, p_nom 8 MW at
  `max_hours` 2.
- **N+1, with the genset as 2×3 MW units:** deficit 7 MW, e 28 MWh,
  p_nom 14 MW.
- **Efficiency:** at η_dis = 0.95, e = 40/0.95 = 42.1 MWh, p_nom 21.05 MW.
- **Weighting:** the same tests with 52.14-weighted snapshots give the same
  MWh.
- **Wrap-around:** a deficit window that crosses the end of the week binds
  on the hours at its start (cyclic). Non-cyclic truncation is flagged.
- **Start time:** the row reads e(t−1), not e(t).
- **UPS bridge:** a 20 MW IT load with `bridge_s` 600 gives
  e_ups ≥ 3.33 MWh at η = 1, and 3.51 MWh at 0.95, at every hour.
- **`seamless` GFM bridge:** a 10 MW step with start + sync of 15 s gives
  B = 0.0417 MWh. When the storage is in both sets, the bridge and window
  energies add in one row; with separate rows the test fails.
- **Fuel:** "not checked" on the template; the hand value with a fuel Store.
- **Paths:** the rows are refused under rolling horizon and SCOPF.
- **Constraints off:** the solution is identical to today's, a regression
  guard over all three hub templates.
- Mutations.

### I2b: the instant of islanding in the hub LP — step 1 (M)

This increment adds to the same wrapper.

**The import at the PCC, site side:** p_imp(t) = η_imp·p0_imp(t) −
p0_exp(t).
- p0 is each Link's bus0 flow, and η_imp is the import Link's efficiency.
- The export Link is subtracted when one is present.
- p_imp is positive when the site imports.

**The two steps.**
- **Import step:** ΔP⁺(t) = p_imp(t) − Σ_{u∈late} X_u(t).
  - "late" is the set of UPSs with `transfers_on_islanding` whose walk-in
    finishes after the gensets have joined
    (`walk_in_s` ≥ `start_s` + `sync_s`). An early walk-in returns its load
    before the gensets join, so it gives no relief.
  - X_u has two upper bounds, X_u ≤ L_it,u(t) and X_u ≤ p_nom_u, and
    X_u ≥ 0.
  - **X_u is used only in rows where a larger X_u loosens the row:** the
    upward headroom, the GFM bridge energy, and the import sides of (d)
    and (e). There the optimiser pushes it up to min(L_it, p_nom_u), so the
    construction is linear even for an extendable UPS.
- **Export step:** ΔP⁻(t) = −p_imp(t) + Σ_{u∈late} L_it,u(t).
  - A transferring UPS *adds* surplus, and here a larger X would tighten the
    row.
  - So the step uses the constant upper bound L_it,u ≥ min(L_it, p_nom_u),
    which is conservative.

**Which rows apply in which scenario (N14).**

| row | `seamless` | `ups_bridged` | `planned` |
|---|---|---|---|
| I2a (a) sustained ride-through | yes | yes | yes |
| I2a (b) UPS bridge | yes | yes | no |
| I2a (b) GFM bridge energy B(t) | yes | no | no |
| (b↑, b↓) headroom at t = 0⁺ | yes | — | no |
| (b■) genset pickup blocks | — | yes | no |
| (c) GFM MVA at t = 0⁺ | yes | — | no |
| (c) GFM MVA, steady island | yes | yes | yes |
| (d) RoCoF | import and export steps | largest block | no |
| (e) quasi-steady-state | import and export steps | largest block | no |
| spinning minimum load | when `spinning` | when `spinning` | when `spinning` |

**(b↑) Upward headroom at t = 0⁺.** It counts the units online at the
event: the GFM storage, plus the spinning gensets:
Σ_{s∈GFM} (p_nom_s − p_s(t)) + Σ_{g on} (p_nom_g − p_g(t)) ≥ ΔP⁺(t).

**(b↓) Downward headroom** (`seamless`):
- **Power:** Σ_{s∈GFM} (p_s(t) + p_nom_s) ≥ ΔP⁻(t).
- **SoC room**, for as long as the surplus lasts:
  Σ_{s∈GFM} (max_hours_s·p_nom_s − e_s(t−1)) ≥ ΔP⁻(t)·t_curt/3600.
  - t_curt is the time until frequency-watt PV has curtailed the surplus,
    max over the `gfl_fw` units of (`fw_delay_s` + `fw_p_limit_mw` /
    `fw_ramp_mw_per_s`).
  - With no `gfl_fw` unit, the surplus is not curtailable. Any hour with
    ΔP⁻ > 0 is then reported as "not islandable: surplus not curtailable",
    and no row is added. The remedy is frequency-watt on the PV.
- **The minimum-load row for spinning gensets** lands here, not in I2c, so
  spinning headroom is never free between the two PRs:
  p_g(t) ≥ p_min_pu·p_nom_g. It is linear for fixed and extendable gensets,
  and it is what makes spinning cost fuel.

**(b■) Pickup blocks** (`ups_bridged`). The bus goes dead, the gensets
start, and they pick up `pickup_blocks` one at a time, at N+k:
- Σ_g ls_g·p_nom_g − k·max_g(ls_g·U_g) ≥ max(`pickup_blocks`),
  where ls_g is `load_step_max_pct`.

**(c) Grid-forming MVA.** Here s_nom = p_nom / `pf_rated`, and
L_island(t) = L_c(t).
- **t = 0⁺ (`seamless`):**
  Σ_{GFM} s_nom + Σ_{g on} s_nom_g ≥ (1 + gfm_margin)·L_island(t).
  With cold gensets, this is BESS MVA alone.
- **Steady island** (all scenarios, after the gensets join):
  Σ_{GFM} s_nom + Σ_{all g} s_nom_g ≥ (1 + gfm_margin)·L_island(t).
- It is a rule of thumb, ledgered `assumed`.

**(d) RoCoF.** The inertia online at the event is
M = Σ_{g on} H_g·s_nom_g + Σ_{VSM} H_v·s_nom + Σ_{droop GFM} τ_f·p_nom/(2R).
- **`seamless`:** 2·M ≥ f0·ΔP⁺(t)/RoCoF_max, and 2·M ≥ f0·ΔP⁻(t)/RoCoF_max.
- **`ups_bridged`:** the started gensets carry the largest block.
  2·(Σ_g H_g·s_nom_g − k·H·U_max/pf) ≥ f0·max(`pickup_blocks`)/RoCoF_max.
- It is linear: H, τ_f and R are parameters, and s_nom and p_nom are
  variables.
- **Deliberately stricter than I6.** I2 limits the instantaneous RoCoF at
  0⁺; I6 judges it over the 500 ms window of Q2.

**(e) Quasi-steady-state frequency**, at the steady-island instant. It
counts the units with droop: the GFMs and the gensets once joined, less the
k largest gains:
- ΣK = Σ_i p_nom_i/(R_i·f0) − k·K_max.
- **`seamless`:** ΣK·Δf_qss,max ≥ ΔP⁺(t), and ΣK·Δf_qss,max ≥ ΔP⁻(t).
- **`ups_bridged`:** ΣK·Δf_qss,max ≥ max(`pickup_blocks`).

**The nadir is not in the LP.** I6 checks it, and I7 may feed cuts back.

**Tests**, each on a tiny hand network where one row binds:
- **(b↑):**
  - no transfer: 10 MW import, `seamless`, the UPS on line. The BESS needs
    10 MW of headroom.
  - a late-walk-in transfer of a 6 MW UPS: 4 MW. With an early walk-in:
    10 MW again.
- **(b↑), extendable UPS, both branches of min(L_it, p_nom_u):**
  - L_it 6 MW, p_nom_u 4 MW: X 4, headroom 6 MW;
  - L_it 6 MW, p_nom_u 8 MW: X 6, headroom 4 MW.
- **(b↓):**
  - −8 MW import: 8 MW of downward power;
  - with a late-transferring 6 MW UPS: 14 MW;
  - SoC room, with a fw delay of 0.5 s and 2 s of ramp (t_curt 2.5 s),
    by hand;
  - no `gfl_fw` unit: the hour is reported as not islandable.
- **(b■):** pickup blocks with N+1, by hand.
- **(c):** one case where the t = 0⁺ row binds (cold gensets) and one where
  the steady-island row binds.
- **(d):**
  - ΔP 4 MW, f0 50, RoCoF_max 1 Hz/s, a VSM with H_v 4 s gives
    s_nom ≥ 25 MVA;
  - a droop GFM instead (τ_f 0.1 s, R 0.04) gives p_nom ≥ 80 MW;
  - the export side, and the `ups_bridged` block, by hand.
- **(e):** both sides, with N+k, by hand.
- **Spinning:** the flag turns a genset's headroom on in (b↑) and costs its
  minimum-load fuel, by hand.
- Mutations.

### I2c: linearised UC, the two variants and the cost of island capability (M)

**Linearised UC (Q7)**, when `linearised_uc` is on:
- It is offered for **fixed-size gensets only**. They use PyPSA's
  `linearized_unit_commitment`, where `Generator-status` u_g(t) is
  continuous in [0, 1].
  - Their online capacity u_g(t)·p_nom_g is linear because p_nom_g is
    fixed. It replaces the spinning flag in (b↑), (d) and (e).
  - Headroom is u_g(t)·p_nom_g − p_g(t), and `p_min_pu` (from the sidecar,
    written to `Generator.p_min_pu`) makes it cost minimum-load fuel.
- Extendable gensets are refused, with a reason. PyPSA 1.1.2 uses big-M for
  committable-extendable units (`constraints.py:297-330`), so u·p_nom is
  bilinear and a fractional u relaxes the minimum load almost completely.
  They may still use `spinning` (I2b).
- The relaxation is labelled "linearised": u can be fractional.
- **Solver plumbing:** pass `linearized_unit_commitment` through the plain
  solve in `services/solver_service.py` (`:1072`). Refuse it on the SCOPF
  and rolling-horizon paths, as for the island rows.

**The two variants (Q3).**
- The study solves `ups_bridged` and `seamless` as separate solves of the
  same project.
- It reports each variant's annualised cost, and their difference as the
  **cost of seamless islanding**.

**Reported:**
- which I2 rows bind, at which hours;
- their duals;
- the **cost of island capability**: the project solved without the island
  rows, and the difference in annualised cost. That cost is the economic
  dimensioning the client asked for.
- It rests on S0's physical storage weighting; without it the reserve's
  opportunity cost is understated.

**The existing DtC stress stays.** I2 adds the duration-based requirement;
it replaces nothing.

**Tests:**
- Linearised UC buys exactly the online capacity (b↑) requires, by hand,
  with a fractional u at `p_min_pu` 0.3.
- Linearised UC on an extendable genset is refused, with a reason.
- Linearised UC on the SCOPF and rolling-horizon paths is refused.
- `ups_bridged` against `seamless` on one hand network: the reported cost
  difference equals the hand-worked cost of the extra BESS headroom.
- Mutations.

### I3a: the hourly island contract (S–M)

I3b and I6 need data the hourly table does not carry. This increment adds
two contracts in `schema/campus.py`, so the existing `HOURLY_COLUMNS` and its
consumers are untouched. Each has fixed columns, validated like the others.

**`island_unit_hourly`**, long, keyed by (period, hour, unit_id):
- `soc_start_mwh`: e(t−1), the stored energy at the start of hour t, which
  is the energy available at a loss during that hour. It is not the
  end-of-hour `state_of_charge`.
- `online`. With UC on, this is u_g(t). Otherwise it is the `spinning` flag,
  so the column does not wait on I2c.
- `headroom_up_mw` and `headroom_down_mw`;
- `p_ups_it_mw`, on UPS rows, otherwise empty.

**`island_hourly`**, keyed by (period, hour, scenario):
- `dp_up_mw` and `dp_down_mw`: ΔP⁺ and ΔP⁻ from I2b;
- `inertia_mws`: M from I2b (d);
- `k_mw_per_hz`: ΣK from I2b (e);
- `critical_load_mw`.

**The producer** is `producers/campus.py`. It stays duck-typed on the
network object, as `producers/campus.py:44-47` requires. It receives the
solved network and the sidecar; `producers/pypsa_nodal.py` stays the only
module that loads a network file. Names are mapped by `pypsa_name`.

**Tests:**
- hand values on a two-hour network, including e(t−1) at the first hour
  (cyclic and not);
- each column's source;
- each contract refuses a missing or extra column;
- the producer never imports pypsa;
- mutations.

### I3b: island critical hours — step 2 (S)

New `ISLAND_CRITERIA` in `ranking/campus.py`, used when the study has
island scenarios. They read the I3a tables:
- maximum ΔP⁺(t), the import step, and ΔP⁻(t), the export step, for
  over-frequency;
- minimum island inertia, `inertia_mws`;
- minimum GFM headroom;
- maximum island load;
- maximum GFL renewable share at low load (voltage rise and over-frequency);
- minimum stored energy at loss.

**Minimum criteria are transformed to (max_x − x)** before ranking.
`select_campus_hours` sorts descending and skips a criterion whose values
are nowhere > 0. The transformation keeps the values ≥ 0, and keeps "no
variation" meaning "nothing to rank". Plain negation would make every value
≤ 0, and the criterion would be skipped silently.

The top-k union and tie-spreading are as today (k = 3), adding about 10–18
hours per period.

**Tests:**
- hand series where each criterion picks a known hour, including each
  minimum criterion;
- a constant series picks nothing;
- ties;
- an empty island config adds nothing;
- mutations.

### I4: island steady state by control mode — step 3a (M)

**New module: `static/island_flow.py`.** The hand fixture campus from I1 lets
it be built and tested before I3b exists. Wiring it to the island hours
waits for I3b.

**Building the island net.**
- Start from `Campus.net`. Take the `ext_grid` out of service and open the
  PCC.
- Each unit is built by its `control` (see the table).
  - **GFM units and gensets:** a `gen` at V0 on an auxiliary bus, behind a
    virtual reactance x_v = k_q (pu on the unit base) to its terminal bus.
    The Q–V droop is then part of the network, and Newton–Raphson solves it
    directly. The naive outer loop diverged on short ties (spike 1).
    - x_v exists only in the load flow. I5 does not use it.
    - **x_v gives V = V0 − k_q·Q only to first order.** The x·P² term and
      the reactive power x_v itself consumes bias it. In the re-review's
      one-bus case (k_q 0.04 and 0.08), the Q-sharing ratio was 2.000 at
      P = 0, 2.173 at 0.6 pu and 2.422 at 0.9 pu, and V_t was off by up to
      0.003 pu. The bias is ledgered and reported. An optional P-only outer
      step corrects V0 per unit by (x_v·P)²/2.
  - **Slack weights:** `slack=True`, `slack_weight = P_n/R`. An isochronous
    genset is the **only** slack, and every other unit gets weight 0.
  - **GFL units:** `sgen` with their Q law (pin-tested QModels, or the own
    fixed-cos φ law).
  - **UPS units:** loads at the IT load, or at recharge plus walk-in after a
    transfer.
- The dispatch comes from the critical hour, with ΔP⁺ or ΔP⁻ redistributed by
  the distributed slack.

**Outer loops**, to a fixed point with a stated tolerance and an iteration
cap:
- **P limits:** a slack unit beyond its `max_p_mw` or its current limit is
  fixed at the limit with weight 0, and the flow is re-solved. pandapower's
  distributed slack ignores `max_p_mw` (spike 1).
  - When no unsaturated reference remains, the island is "not viable: every
    grid-forming source at its limit".
- **GFL frequency-watt:** ΔP_i = −K_fw,i·sign(Δf)·max(0, |Δf| − db),
  capped at `fw_p_limit_mw`. Inside the deadband there is no response.
- **The common frequency** Δf is solved as a fixed point that includes the
  frequency-watt response ΔP_fw(Δf). pandapower's distributed slack shares
  the residual by weight.

**One definition of ΔP, shared with I6.** ΔP_AC is the AC-measured change
in total slack P between the pre-event dispatch and the island solution. It
includes the losses and the UPS state after the event.

**Results per hour:**
- Δf_qss, the common fixed-point frequency;
- every bus voltage against its band;
- Q sharing;
- the loading of units, transformers and cables;
- saturated units;
- the static resynchronisation inputs (Δf, ΔV at the PCC);
- a verdict, labelled "steady-state screening": `viable` |
  `not viable: <reason>`.
- The reasons are: no reference, every GFM saturated, voltage out of band,
  overload, non-convergence. Non-convergence is returned, not raised, as in
  `campus_flow.py:122-128`.

**Sub-step I4b: grid-connected control-law check.**
- At the existing critical hours, run each unit's actual Q law in place of
  the MILP's free Q, and report the PCC Q and the voltages.
- Ledger the difference: the MILP assumes a plant controller that can
  dispatch any Q inside the capability.

**Tests:**
- **Droop sharing by hand:** two sources and a deficit ΔP. Each takes
  ΔP·K_i/ΣK, and Δf = ΔP/ΣK. This reproduces spike 1.
- **Q–V droop through x_v:**
  - sharing ∝ 1/k_q on a symmetrical network, pinned at P ≈ 0, where it is
    exact;
  - the documented first-order bias at 0.6 and 0.9 pu;
  - convergence on the spike-1 ties (X = 0.001 and 0.01 pu), where the
    naive loop diverged.
- **P limit:** the spike-1 case with `max_p_mw=3` ends at 3 MW, the rest
  moves to the other unit, and Δf follows from the reduced ΣK, by hand.
- **Isochronous:** it takes the whole deficit, and Δf = 0 exactly until its
  limit.
- **Frequency-watt:** no response inside the deadband, and the hand value
  outside it.
- **Droop and VSM** (the VSM's gain 1/R about ω0) give identical
  steady-state results.
- **A GFL-only island** is refused as "no reference".
- **When every GFM saturates**, the island is refused.
- **Q laws:** a Q(U) unit lands on its curve, and the fixed-cos φ law gives
  Q = P·tan φ. A pin test documents `QModelCosphiP`'s sin φ, so it is never
  used by mistake.
- **Re-close:** with every unit as a PQ sgen at its dispatch (today's
  representation) and the PCC re-closed, the result equals today's campus
  load flow.
- **Internal gate, not a tautology:**
  - every unsaturated unit's −ΔP_i/K_i equals the one common Δf;
  - every saturated unit sits at its limit;
  - the frequency-watt units sit on their law at that Δf;
  - the gate is run on a case where a unit saturates.
- Mutations.

### I5: short circuit by control mode, grid-connected and islanded — step 3b (M–L)

**Grid-connected** (`static/campus_sc.py`):
- **Gensets become real synchronous machines:** a `gen` with `xd_pp`,
  `vn_kv`, `rdss_ohm` (from `rx_sc`) and `cos_phi`. The IEC correction is
  K_G, or K_S when a genset sits behind its own unit transformer
  (`unit_transformer`). This retires the C1 ledger line that screened
  gensets as 1/x''d current sources.
- The converter fault current comes from its mode: `k_sc` for GFL, and
  `i_max_pu` for GFM.
- The max case is IEC 60909, as today.

**Islanded** (new module `static/island_sc.py`):
- **The max case, with a synchronous source:** pandapower `calc_sc` with no
  `ext_grid` (spike 2), with the converters as IEC current sources.
- **The min case uses its own converter-aware method**, labelled "not IEC
  60909". IEC 60909-0:2016 lets full-converter sources be neglected in the
  min case, which pandapower follows (spike 3). For protection in an island
  that would be wrong, so:
  - GFM units stay energised at `k_gfm_min`·In;
  - GFL units are kept or dropped by `gfl_min_case` (default dropped,
    ledgered);
  - the gensets are taken from pandapower's min case.
  - Converter currents are added in phase **only** where the units share a
    fault-current control (reactive injection), and the report says so.
  - The verdict also uses the minimum online set at the island hour, with
    the largest source out.
- **An inverter-only island:** pandapower crashes here (spike 4). For a
  bolted fault fed by current sources through a passive network, all the
  injected current flows into the fault. So Ik'' = |Σ k_i·In_i|, referred
  to the fault bus's voltage, with the phase rule above.

**Protection sensitivity**, a screening and not a protection study:
- **Relay current, not bus current.**
  - The fault is computed with `branch_results=True`, and the check reads
    the current through each relay's branch.
  - On-feeder DER infeed reduces the current the upstream relay sees, and
    that is reported as "blinding".
  - The data are optional `i_pickup_ka` and `relay_branch` per relay
    (tagged), and the sensitivity factor s (default 1.5, assumed).
- **Current at the relay's operating time.**
  - A synchronous machine's current decays from Ik'' to Ik before a
    time-delayed overcurrent relay trips. Genset decrement is the classic
    way island protection fails.
  - Instantaneous relays are checked against I_b (the IEC μ factor).
  - Time-delayed relays are checked against Ik (the IEC λ factor), with the
    genset excitation as a tagged assumption: AVR with PMG field forcing, or
    without.
  - Converters stay at their current limit, with no decay.
  - pandapower does not compute μ or λ, so a small own module does, checked
    against the IEC formulas by hand.
- **The verdict** is "protection blind in island" when relay current < s ×
  pickup.
- **The ratio of grid-connected Ik''max to island relay current**, reported
  per relay. It is the setting-group question for the protection engineer.
- **The GFM current-limit sensitivity (Q8):** the island min case and the
  verdicts are rerun at each `i_max_sensitivity_pu` value. The report shows
  whether oversizing the inverter turns a blind relay into a pass.

**Tests:**
- **Genset-only island:** the IEC formula with K_G and R_G, by hand, using
  the spike-2 parameters. The 1.589 kA against 1.563 kA residual is
  explained before it is pinned.
- **K_S:** a genset behind a unit transformer, by hand.
- **Inverter-only island** equals Σ k·In by hand. A mixed island is the
  superposition.
- **A pin test** documents pandapower's IEC min-case omission, so an
  upgrade that changes it fails loudly.
- **Branch current:** a feeder with DER infeed shows the blinding by hand.
- **Decrement:** Ik with λ, by hand for both excitation assumptions.
- **The protection verdict** on both sides of s.
- **Grid-connected regression:** results are unchanged for converters, and
  changed for gensets only by the `gen` model, with the delta explained in
  the test.
- **The sensitivity:** in an inverter-only island, relay current scales
  linearly with i_max, by hand.
- Mutations.

### I5b: unbalanced faults (L; owner Q9)

This increment comes straight after I5, in its own PR.

**Its own sequence-network code** (`static/island_sc_seq.py`). pandapower's
`fault="1ph"` cannot be used as-is (spike 6): it adds converter current
with no zero-sequence path, and it fixes every `gen`'s zero-sequence
earthing.
- **Positive and negative sequence:** from the campus network and the unit
  models.
- **Zero sequence:** built from the campus data:
  - transformer vector groups and neutral earthing per winding;
  - genset neutral earthing (solid, resistance + R_N, isolated);
  - earthing transformers;
  - cable `r0`, `x0`, `c0`. Typical values are drafted, tagged `assumed`.
- **Converters inject positive sequence only** by default, the common GFL
  behaviour. A GFM `neg_seq` capability is an optional, tagged field.
- **Faults:** single-phase-to-earth and phase-to-phase, grid-connected and
  islanded.
- pandapower's `fault="1ph"` is used only as a cross-check, where it is
  valid: solidly earthed, with no converters.

**The earth reference.**
- Opening the PCC can remove the system's only earth reference, e.g. when
  the MV system is earthed only through the grid transformer's star point.
- The code detects "no zero-sequence path in the island" and reports it as
  a **headline finding for the island design**.
- That is a client-facing risk, not a footnote.

**Earth-fault protection sensitivity:**
- an optional `i_pickup_e_ka` per relay;
- the island earth-fault relay current ≥ s × pickup, on branch current, as
  in I5;
- the verdict "earth-fault protection blind in island" when it fails.

**Tests:**
- 1-phase and 2-phase faults with a solidly earthed transformer, against
  the symmetrical-component formulas by hand. pandapower agrees on that
  case.
- A positive-sequence-only converter adds nothing to the zero-sequence
  current, by hand.
- An isolated neutral gives zero earth-fault current, by hand.
- A pin test documents pandapower's 0.315 kA on an unearthed side with a
  sgen.
- An island that loses its only earth reference is reported as such.
- Mutations.

### I6: first-pass frequency check — aggregated inertia and droop — step 4a (M)

**New module: `static/frequency.py`** (numpy/scipy only; no pandapower).

**The model.** A single-bus aggregate swing equation:

  (2·H_sync·S_sync / f0) · dΔf/dt = Σ_i ΔP_i(t) − ΔP_event(t) − D·P_load·Δf/f0

- H_sync·S_sync is the sum of H·S for the online gensets and H_v·S for the
  VSMs.
- Each ΔP_i follows its unit's `control` (table above):
  - **genset:** governor lag T_g, droop R, a load-acceptance step limit, a
    start delay when cold, P ≤ P_max;
  - **GFM droop, in its native form:**
    - a filtered measured-power state, τ_f·dP_f/dt = P − P_f;
    - the unit imposes the bus frequency, f = f0 − R·f0·(P_f − P0)/P_n;
    - on the shared bus this gives P = P0 − (P_n/R)(Δf + τ_f·dΔf/dt)/f0, so
      its τ_f/R term acts as inertia.
    - Above i_max or its P limit, it becomes a constant-P current source,
      and its inertia term goes.
    - So a `seamless` island with only a droop BESS is well-posed, while
      H_sync = 0.
  - **VSM:** H_v·S_n in the swing, with its steady-state gain 1/R about ω0
    (that damping is the droop; no separate damping term);
  - **GFL frequency-watt:** deadband, delay, ramp and limit;
  - **GFL PQ:** none;
  - **UPS:** on line unless `transfers_on_islanding`. If it transfers, it
    walks back in over `walk_in_s`. If |Δf| leaves its window, it drops off
    (flagged), and its battery autonomy is checked against the time to
    recover.
- The load damping D defaults to 0. A data centre is mostly constant power,
  so this is the conservative choice; ledgered.
- **When every grid-forming source saturates** and no synchronous machine
  is online, the result is "frequency collapse: no grid-forming headroom",
  not a number.

**ΔP_event.**
- At the I3b island hours, E1 uses I4's ΔP_AC, the one shared definition.
- At other hours, E1 uses the lossless `dp_up_mw` / `dp_down_mw` from I3a.
- The difference, the island losses, is reported.

**Events:**
- **E1: unplanned islanding at hour t.** An exporting hour gives
  over-frequency.
  - Under `seamless`, the BESS forms the island at t = 0, and the gensets
    join after `start_s` + `sync_s` and take load at `ramp_pu_per_s`. The BESS energy used
    before they join is reported against the I2a bridge reserve.
- **E2:** the largest load step: a chiller restart, or a UPS walk-in block.
- **E3:** the largest unit trip in the island.
- **E4: `ups_bridged` start.** The bus is dead, the gensets start after
  `start_s`, and the load is picked up in `pickup_blocks`.
  - **The bridge check (Q1):** the UPS's stored energy e(t−1) (from I3a)
    must last ≥ `bridge_s`, and ≥ `start_s`
    plus the time to pick up the last block.
- **E5:** PV loss in the island (cloud).
- **E6: `planned` islanding.** The gensets and BESS ramp to cover the
  import, then the PCC opens with ΔP ≈ 0. The ramp time is reported.

**Metrics:**
- RoCoF at t = 0⁺, and RoCoF over `rocof_window_ms`;
- the nadir and the time to reach it;
- Δf_qss;
- the time to return to within the band;
- headroom and current saturation flags;
- UPS window violations.

**Limits (Q2).**
- Every metric is judged against the most sensitive equipment in the
  island: the UPS input window, the chiller and drive `f_trip_hz`, and the
  genset class limits. The profile defaults apply only where nothing is
  entered.
- The `transition` limits apply during the event; the `steady_island`
  limits apply after it.
- The report names the **binding limit** for each failure, e.g. "UPS
  window, 18:00, nadir 47.1 Hz against 47.5 Hz".
- **The GFM current-limit sensitivity (Q8):** E1 and E2 are rerun at each
  `i_max_sensitivity_pu` value.

**Where it runs.**
- E1 runs at **every hour**: it takes milliseconds, so it doubles as a
  check on I3b's choice of hours.
- E2–E6 run at the island critical hours.

**EMT/RMS recommendation flags:**
- the nadir margin is below a set threshold;
- GFM units saturate during the event;
- SCR < 3 at the PCC with a large GFL share;
- a GFM share is high in a weak island.

**Labelled a screening.** It has no voltage dynamics, no inter-unit
oscillation and no current-limit dynamics.

**Tests against analytical results:**
- **Inertia only:** no response and a constant ΔP give a linear ramp, with
  RoCoF exactly ΔP·f0/(2·H·S).
- **One governor:** a single first-order governor with inertia and no
  limit. The nadir and its time match the closed-form second-order
  solution within 0.1 %.
- **Settled frequency:** Δf_qss = ΔP / Σ K.
- **D'Arco–Suul**, with two **independently coded** models: native droop
  (filtered P, imposed frequency) against a VSM with H_v·S_n = τ_f·P_n/(2R)
  and a steady-state gain 1/R about ω0. The traces agree.
  - **The case:** a genset with H 1.5 s on 10 MVA, plus a BESS with P_n
    10 MW, R 0.04 and τ_f 0.1 s, and a 4 MW step.
  - **Pinned in closed form:**
    - RoCoF at 0⁺ = ΔP·f0 / (2·H·S + τ_f·P_n/R) = 4·50/(30 + 25)
      = 3.636 Hz/s;
    - Δf_qss = ΔP/ΣK = 4/(5 + 5) = 0.40 Hz.
  - **Pinned from the simulation of both models** (the re-review's values,
    with the I1 genset defaults R 4 % and T_g 0.5 s):
    - RoCoF 2.88 Hz/s over 100 ms and 1.09 Hz/s over 500 ms;
    - a nadir of −0.555 Hz at 0.415 s;
    - settling at −0.40 Hz.
  - **The rejected lag model** gives 6.667 Hz/s at 0⁺, so the test tells
    the two apart.
- **Droop BESS alone:** a droop-only island (H_sync = 0) gives the
  closed-form first-order response.
- **Saturation:** it deepens the nadir monotonically, and an isochronous
  genset recovers to 0.
- **Consistency with I4:** E1 run with I4's ΔP_AC at an island hour gives
  I4's Δf_qss within 1 mHz.
  - These are two code paths, the pandapower slack with its outer loops
    and the ODE's steady state, fed the same ΔP and the same P_n-based K
    (N1).
  - It is run on a case without saturation and on a case where a unit
    saturates.
- Mutations.

### I7: closing the loop — frequency cuts back into sizing (S–M; manual, owner Q6)

When I6 fails at hours T_fail, **heuristic** cuts are proposed for I2b at
those hours:
- **From a failed nadir:** a required H_sys(t) or headroom, by inverting the
  I6 closed-form nadir for the units' response parameters.
  - The nadir is nonlinear in H and K together when both scale with p_nom.
  - So the cut fixes the response parameters at the current solution, is
    linear in s_nom, and is labelled heuristic.
- **From a failed recovery:** a required droop gain.

**The user applies the cuts (Q6).**
- The panel previews the failed hours, each proposed cut and its estimated
  added cost.
- A cut not yet in the model has no dual, so the estimate is **primal**:
  the cheapest candidate asset that satisfies the cut, times the required
  ΔS. Optionally, a preview solve gives the exact figure. Either way, it is
  labelled an estimate.
- Only "apply cuts" re-solves the hub. Nothing applies automatically.
- Automatic application is reconsidered once a project's frequency
  parameters are mostly `datasheet` or `measured`.

Once the user applies the cuts, the hub is re-solved, and I3a, I3b, I4 and
I6 rerun. This repeats at most N times, by default 3. It is reported like
C11's loop: trial, verdict, cost.

**Tests:**
- a hand case that fails once and passes after one cut;
- the primal estimate equals the hand-worked asset cost;
- termination at N;
- no cuts when everything passes.

### I8: multi-bus RMS and the client-grade handoff — step 4b (L; legal check first, owner Q4)

**The licence (Q4).**
- ANDES is GPL-3.0-or-later, confirmed from the 2.0.0 wheel metadata.
- So it runs **as an optional engine in its own process**: gridspine writes
  the island case to files, a separate `andes` environment runs it, and the
  results come back as files. This is the same file-contract style as the
  gridspine stages.
- gridspine never imports `andes`, and the desktop app does not bundle it.
  A test asserts that no gridspine module imports `andes`.
- A short legal check comes before any ANDES code lands.
- The PSS/E path below has no licence question, so it can proceed first.

**ANDES path** (`handoff/andes_campus.py` writes the case; a runner script
in the separate environment runs it):
- Convert the island net (I4) through `andes.interop.pandapower`, inside
  the ANDES process.
- Attach dynamic models by `control`:
  - REGCA1+REECA1(+REPCA1) for GFL;
  - REGF1 for GFM droop;
  - REGF2 for VSM;
  - GENROU + GAST + SEXS for gensets (a diesel or gas engine as GAST is an
    approximation; ledgered);
  - a constant-P load with a ramp for the UPS.
- Run E1–E6 at the island critical hours.
- **Gate:** the frequency nadir agrees with I6 within a tolerance. Where it
  does not, the difference is reported, not hidden.
- New results: per-bus voltage dips and current-limit hits.

**PSS/E path:**
- Extend `dyr_writer` with governors and exciters, which are missing today.
  For a diesel genset the governor is DEGOV1, the PSS/E-native diesel
  model.
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
  - ride-through at N+k, the bridge reserve, fuel ("not checked" when there
    is no fuel bus), and the cost of island capability and of seamless
    islanding (I2a–I2c);
  - the island hours (I3b);
  - the island load-flow verdicts (I4);
  - short circuit, relay sensitivity and the earth reference (I5, I5b);
  - the frequency traces per event, with their binding limits (I6).

**Copilot tools.** Read and write chat tools, as in increments 9 and 10:
- `campus_island_config` (read);
- `campus_set_island_config` (write);
- `campus_run_island_study` (write).

**The report:**
- On every table, it states that the study is a steady-state,
  converter-aware fault-current and aggregate-frequency screening, not a
  certificate.
- It names the model behind each verdict.
- Every `assumed` row is marked.

**Tests:**
- the tool contracts;
- the report golden fixture;
- a browser run of the panel.

## Order, PRs and agents

- **I1** comes first: everything reads the control modes and the data.
- **S0** (storage weighting) lands as its own PR before I2a. It can run in
  parallel with I1.
- Then two tracks in parallel, sharing only the I1 schema:
  - **pypsa-gui:** I2a → I2b → I2c;
  - **gridspine:** I4 → I5 → I5b, built and tested on the I1 hand fixture.
- **I3a** needs I1 and a solved network. Its `online` column reads UC from
  I2c once that lands, and the `spinning` flag before then.
- **I3b** needs I3a. Wiring I4, I5 and I5b to the island hours waits for it.
- **I6** needs I3a, and I4 for the shared ΔP_AC and the consistency gate.
- **I9** comes next, then **I7**, as a manual action (Q6).
- **I8** starts with its PSS/E path. The ANDES path waits for the legal
  check (Q4).

The first deliverable of I1 is the sidecar contract and the campus YAML
fields. It is tested against a hand-built campus with one GFM BESS, one
genset, one GFL PV and one UPS. That campus is reused as the fixture
through I4–I6.

| increment | independent oracle |
|---|---|
| I1 | each refusal; round trip against the hand fixture; no orphan fields |
| S0 | a hand SoC step of p·1 h/η |
| I2a | hand-worked tiny LPs, one row binding each; separate cases where the energy row and the power row bind |
| I2b | hand-worked tiny LPs per scenario and per row of the applicability matrix, import and export |
| I2c | hand-worked spinning and linearised-UC cases; variant cost difference |
| I3a | hand values on a two-hour network |
| I3b | hand series, including minimum criteria |
| I4 | droop sharing and P limits by hand; spike 1 ties; re-close equals today's flow; per-unit gate on a saturating case |
| I5 | IEC 60909 by hand (K_G, K_S, μ, λ); Σ k·In; branch infeed by hand; pandapower min-case pin |
| I5b | symmetrical-component fault formulas by hand; pandapower on its valid case |
| I6 | closed-form RoCoF at 0⁺ and Δf_qss; second-order nadir; independently coded D'Arco–Suul; I4 agreement with and without saturation |
| I7 | a hand case that passes after one cut; primal estimate by hand |
| I8 | I6 agreement; ANDES's own examples for each model |
| I9 | tool contracts; report golden fixture; browser run |

## Out of scope, and ledgered

- **EMT:**
  - sub-cycle current limiting;
  - converter control interaction;
  - weak-island harmonics.
- **Protection coordination:** only sensitivity is checked (I5, I5b).
- **Resynchronisation dynamics.** I4 reports the static sync-check inputs
  (Δf, ΔV), not the transient.
- **Grid-forming support of the utility grid at the PCC** (GC0137, the RfG
  revision):
  - grid-code extraction of GFM requirements;
  - an ESCR or impedance screen.

  It reuses I1, I5, I6 and I8. It is a follow-up plan.
- **Genset unit commitment as a MILP**, and committable extendable gensets.
  Linearised UC for fixed-size gensets is in I2c (Q7).
- **Occurrence derating of gensets inside ride-through.** The N+k covers
  it.
- Black start of the utility grid.
- Tap-changer dynamics.
- Harmonics and flicker.

## Review (2026-10-07) and resolution

An independent review returned **GO WITH CONDITIONS**. Its evidence is in
the session scratchpad. Every finding is folded in, as below.

| finding | resolution |
|---|---|
| B1 droop GFM modelled as a lag in I6; it contradicts I2(d) | I6 uses native droop (a filtered measured P, imposed frequency). D'Arco–Suul is tested with two independently coded models. The control-table principle is reworded. |
| B2 ride-through under-specified | I2a: Δ is the snapshot duration, not the weighting; cyclic windows; D ≤ T; the RT storage set is named; η_dis; e(t−1); d(τ) without t; N+k; avail_g defined; the test uses `max_hours` ≠ D. |
| B3 ΔP_isl unconservative | I2b: no UPS reduction in `seamless` unless `transfers_on_islanding` with a late walk-in; min(L_it, p_nom_u) through two rows; export headroom; `pickup_blocks`. The template UPS is re-rated (S0, moved from I1). |
| B4 linearised UC gives spinning for free | I2c: spinning implies p ≥ p_min·p_nom with p_min > 0; UC for fixed-size gensets only; solver plumbing listed. |
| B5 the hourly data does not exist | New I3a `island_hourly` contract. Order updated. Minimum criteria use the (max − x) transformation (I3b). |
| S1 Q–V droop loop diverges | I4 uses a virtual reactance x_v, tested on the spike-1 ties. |
| S2 distributed slack ignores `max_p_mw`; isochronous; frequency-watt sign | I4 P-limit loop; isochronous as the only slack; the fw law with sign and deadband. |
| S3 `QModelCosphiP` uses sin φ | Own fixed-cos φ law; `QModelCosphiPCurve` for curves; pin tests (table, spike 5). |
| S4 IEC framing, `rdss_ohm`/`cos_phi`, K_S | Min case labelled converter-aware, not IEC; phase rule stated; `rdss_ohm` from `rx_sc`, `cos_phi_r`, `unit_transformer` (I1, I5). |
| S5 protection on bus current; genset decrement | Relay branch current with infeed blinding; I_b and Ik with μ and λ; the excitation assumption tagged (I5). |
| S6 pandapower `1ph` unusable as-is; earth reference | Own sequence-network code; genset and transformer earthing data; "no earth reference" as a headline finding (I5b). |
| S7 circular I4/I6 gate | One ΔP_AC definition; the I4 internal gate; I6 fed the same ΔP through a different code path; tolerance 1 mHz. |
| S8 contradictory re-close test | "With every unit in its grid-connected representation" (I4). |
| S9 I7 cost has no dual | A primal estimate or a preview solve; cuts labelled heuristic. |
| S10 UPS bridge energy not reserved | I2a (b): e_ups(t−1)·η ≥ P_it·bridge_s/3600, plus the GFM energy until the gensets join. |
| S11 Q2 wording | 47.5 Hz is the bottom of the RfG CE 30-minute band; the unlimited band is 49.0–51.0 Hz. |
| S12 I2(c) has no instant | Two instants, t = 0⁺ and steady island (I2b). |
| S13 I1 missing fields | All added to I1. |
| S14 I2 undersized | Split into I2a, I2b, I2c, each M; the adequacy-wrapper lessons cited. |
| Nits | Spike 2 parameters recorded and the residual is to be explained; (d) stricter than I6; `adequacy.py:61-222`; events E1–E6 in order; oracle rows for I1, I7 and I9; fuel "not checked" on the template; per-Load critical tags; `bess_new` reused; DEGOV1 in `.dyr`; I4's verdict labelled a screening. |

### Re-review (2026-10-07) and resolution

A focused re-review returned **GO WITH CONDITIONS**. It verified the hand
numbers with PyPSA 1.1.2 + HiGHS and pandapower 3.1.2. The owner chose to
fold in every finding and start I1 without a third review.

| finding | resolution |
|---|---|
| N1 droop base mixed (S vs P_n); the I4↔I6 gate cannot pass | One base: droop on P_n, H on S_n. The droop GFM's inertia term is τ_f·P_n/(2R) in the table, I2b (d), I3a and I6 (principles, I1). |
| N2 VSM damping principle backwards | A VSM's damping about ω0 is the droop; there is no separate D_v; grid-frequency damping is left to I8. |
| N3 D'Arco–Suul numbers underspecified | RoCoF at 0⁺ and Δf_qss pinned in closed form (3.636 Hz/s, 0.40 Hz); window RoCoF and nadir pinned from both models, with the stated governor defaults. |
| N4 X_u in rows where it tightens | X_u only where it loosens; ΔP⁻ uses the constant L_it,u; "late" walk-in defined with `sync_s`; Σ `pickup_blocks` ≥ peak critical load (I1). |
| N5 export SoC room | Duration t_curt from the `gfl_fw` delay and ramp; max_hours·p_nom; "surplus not curtailable" when there is no `gfl_fw` unit. |
| N6 spinning free between PRs | The minimum-load row moved into I2b. |
| N7 tests that cannot catch a deleted row | A power-row-binds test (`max_hours` 8); (c) tested at both instants; the `seamless` bridge row tested. |
| N8 I3a contract shape; package boundary | A long per-unit table plus a fixed per-hour table; the producer stays duck-typed; the island schema lives in `gridspine/schema/island.py`, wrapped by pypsa-gui; `soc_start_mwh` is e(t−1). |
| N9 x_v only first-order | The bias is stated and ledgered; ∝ 1/k_q pinned at P ≈ 0; an optional V0 correction. |
| N10 the I4 gate is a tautology | A per-unit gate (common Δf, saturated units at their limit, fw on its law), with a fixed point that includes fw, run on a saturating case. |
| N11 I1 field gaps | Added: genset droop, FFR gain and limit, Q-law points, `pf_rated`, `linearised_uc`, `sync_s`, `xd_sat`, excitation, ramp. Removed: UPS efficiency, `autonomy_min`, `v_trip_pu`. Names aligned (`k_sc`, `i_max_pu`, `walk_in_s`). |
| N12 template storage weighting | S0, its own PR before I2a (the queued task). |
| N13 three solver paths | Island rows and linearised UC attach to the plain solve only, and are refused on SCOPF and rolling horizon. |
| N14 scenario × row applicability | A matrix in I2b. |
| Nits | k < units refused; U_max over-subtraction ledgered; windows follow each unit's cyclic setting; the UPS bridge test at η = 1 (3.33) and 0.95 (3.51); 3T rows; p_imp site side with the export Link subtracted; StorageUnit p = p_dispatch − p_store; `xdss_pu`; the bridge and sustained reserves summed in one row; the one-way site transformer ledgered; P0 in the droop formula; both branches of min(L_it, p_nom) tested; re-close against PQ sgens. |

