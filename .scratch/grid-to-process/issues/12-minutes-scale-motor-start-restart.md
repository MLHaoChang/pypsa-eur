# 12b: minutes-scale dips, the process restart sequence and lost tonnes

Status: ready-for-agent (owner G2P-12-SPLIT, 2026-10-28: this file is 12b; 12a is issue 18 and starts now)
Type: task
Blocked by: 10, 18 (12a: the motor fields and the ΔU check)
Gated: **yes** (gridspine)
Model tier: opus
Size: M–L

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

**Split (owner G2P-12-SPLIT, 2026-10-28).** Items 1 (the motor fields) and 2 (the ΔU check) of "What to build" moved to [12a, issue 18](18-12a-motor-start-fields-and-du-check.md). This file is 12b: the ride-through fields and items 3–5, plus the B acceptance. Issue 13 waits for this file.

### Agent brief (12b)

**Category:** enhancement (gated gridspine: send GK A the plan before code)
**Summary:** turn grid dips and island transitions into lost tonnes, through ride-through curves and a discrete-event restart model that reuses 12a's ΔU check.

**Desired behaviour:**
- **Process-unit fields:** a ride-through curve (depth % against duration s) and `restart_time_h`. Unset means "no input" and reads unknown, never pass.
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
- [ ] The B template's motor-start check: a DOL mill start fails on 150 MVA and a VFD start passes, with the hand values of 12a's formula (design §3.B).
- [ ] The restart timeline's lost tonnes equal Σ restart time × rate × mass pull over the units that tripped.
- [ ] A unit with no ride-through curve reads unknown, not "rides through".
- [ ] The assistant inventory and the attrs-doc compact cap test pass. If the cap is exceeded, ask the owner; do not raise it unasked.

**Out of scope:** the motor fields and the ΔU check (12a); dynamic (RMS/EMT) motor start; any LP feedback; the coupling report (13).

**Likely files:** `pypsa-gui/backend/models/process_unit.py`, `pypsa-gui/backend/services/asset_schema/fields.py`, `pypsa-gui/backend/services/asset_schema/attrs_doc.py`, new `gridspine/static/restart_sequence.py`, `gridspine/static/motor_start.py` (from 12a), `pypsa-gui/backend/services/campus_island_report.py`, `pypsa-gui/frontend/src/pages/` (a results section), tests under `tests/gridspine/` and `pypsa-gui/backend/tests/`.
**Blocked by:** 10 and 18 (12a).
**Model:** Opus (gridspine, gated). The results section needs the UX reviewer's OK.
