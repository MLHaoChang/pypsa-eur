# Minutes-scale: motor-start ΔU and the process restart sequence

Status: ready-for-agent (triage 2026-10-28: still blocked by 10; owner question G2P-12-SPLIT may start 12a sooner)
Type: task
Blocked by: 10
Gated: **yes** (gridspine)
Model tier: opus
Size: L

Read [the design](../design.md) §4.3 first. (The old "stay out of the island and tuning code while #332 is open"
no longer applies: #332 merged on 2026-10-16.)

## What to build

1. **Process-unit fields:**
   - motor kW;
   - start method (`dol | soft | vfd`) and start current multiple;
   - a ride-through curve (depth against duration);
   - restart time.
2. **A gridspine static check**, beside `static/capacity.py`: ΔU at the PoC ≈ S_start / S_k.
   - Subtract the reactive support of grid-forming storage.
   - Check against the operator's rapid-voltage-change limit. The demo is 3 %, ledgered; the
     client's grid code governs.
3. **Dip to trips to lost tonnes.**
   - Dip statistics per PoC (demo counts by depth and duration class, in the ledger).
   - Each process unit's ride-through curve decides whether it trips.
   - The result is expected lost tonnes per year.
4. **A restart sequence model.** A discrete-event model running from 1 s to 4 h after a trip or
   an island transition:
   - the pickup order;
   - each restart's motor start (check 2);
   - the timeline and the lost tonnes.
5. **Results:** a campus-study output file and a results section.

## Acceptance

On the B template:
- a direct-on-line mill start fails on 150 MVA;
- a VFD start passes;
- the restart timeline's lost tonnes equal Σ restart time × rate × mass pull for the units that
  tripped.

## Triage (2026-10-28)

**Verified on master (41ade3be):** not built.
- No motor, start-method or ride-through-curve fields exist on the process unit (`models/process_unit.py` `PROCESS_CUSTOM_COLUMNS`).
- No gridspine ΔU module exists.
- What exists and should be reused:
  - the flicker screen (#727, `gridspine/static/flicker.py`) already computes a voltage step as S/S_k per PoC from the per-PoC short-circuit level (`_sk_of`) and judges it against ledgered limits from the grid-code profile;
  - the island schema already has pickup blocks;
  - the process-unit field pattern of 17a (fields, Link-form reveal, attrs doc, inventory) is the model for item 1.

**Size L, so ship it as two PRs on this one issue (as 17 did):**
- **12a:** items 1 and 2 (fields and the ΔU check).
- **12b:** items 3–5 (dip to trips to tonnes, the restart sequence, results), plus the B acceptance.

Owner question **G2P-12-SPLIT** (sent 2026-10-28) asks whether 12a may start now on a fixture network. Until it is answered, both wait for 10.

### Agent brief

**Category:** enhancement (gated gridspine: send GK A the plan before code)
**Summary:** motor-start ΔU at the PoC and a discrete-event restart model that turn grid dips and island transitions into lost tonnes.

**Desired behaviour:**
- **Process-unit fields:**
  - `motor_kw`;
  - `start_method` ∈ {`dol`, `soft`, `vfd`}, with a default start-current multiple of 6 / 3 / 1 that a per-unit field overrides;
  - a ride-through curve (depth % against duration s);
  - `restart_time_h`.

  An unset field means "no input": the check reads unknown, never pass (as 17a's harmonic fields do).
- **A gridspine static check** beside `static/capacity.py` and `static/flicker.py`:
  - ΔU% = S_start / S_k at the unit's PoC, less the reactive support of grid-forming storage at that PoC;
  - judged against the profile's rapid-voltage-change limit (demo 3 %, ledgered; the client's grid code governs);
  - one row per unit and start method on the grid-readiness scorecard.
- **Dip to trips:**
  - demo dip statistics per PoC (counts per year by depth and duration class, in the ledger);
  - a unit trips when a class falls outside its ride-through curve;
  - expected lost tonnes per year = Σ trips × restart time × rate × mass pull.
- **Restart sequence:**
  - a discrete-event model from 1 s to 4 h after a trip or island transition;
  - pickup order from the island pickup blocks;
  - each restart's motor start runs through the ΔU check;
  - output is a timeline and lost tonnes.

  It is not dynamics, and it does not feed back into the LP (design D5).
- **Results:** a campus-study output file and a results section.

**Acceptance (on the B template):**
- [ ] A DOL mill start fails on 150 MVA; a VFD start passes.
- [ ] The restart timeline's lost tonnes equal Σ restart time × rate × mass pull over the units that tripped.
- [ ] A unit with no motor fields reads unknown on the scorecard, not pass.
- [ ] The assistant inventory and the attrs-doc compact cap test pass. If the cap is exceeded, ask the owner, as G2P-17A-DOCCAP did; do not raise it unasked.

**Out of scope:** dynamic (RMS/EMT) motor start; any LP feedback; the coupling report (13).

**Likely files:** `pypsa-gui/backend/models/process_unit.py`, `pypsa-gui/backend/services/asset_schema/fields.py`, `pypsa-gui/backend/services/asset_schema/attrs_doc.py`, new `gridspine/static/motor_start.py`, `gridspine/static/flicker.py` (shared S_k lookup only), `pypsa-gui/backend/services/adequacy/grid_readiness.py`, `pypsa-gui/backend/services/campus_island_report.py`, `pypsa-gui/frontend/src/pages/` (a results section), tests under `tests/gridspine/` and `pypsa-gui/backend/tests/`.
**Blocked by:** 10 (12a may start sooner if the owner picks the split in G2P-12-SPLIT).
**Model:** Opus (gridspine, gated). The results section needs the UX reviewer's OK.
