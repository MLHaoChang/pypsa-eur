# Minutes-scale: motor-start ΔU and the process restart sequence

Status: needs-triage
Type: task
Blocked by: 10
Gated: **yes** (gridspine)
Model tier: opus
Size: L

Read [the design](../design.md) §4.3 first. Stay out of the island and tuning code while #332
is open.

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
