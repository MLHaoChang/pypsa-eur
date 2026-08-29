# PowerFactory validation fixtures (manual export — Hao)

1. In PowerFactory: File > Import > PSS/E raw, pick the `case39_dispatch.raw`
   produced by the Task 10 CLI (artifact dir printed on run).
2. Run a Newton-Raphson AC load flow (balanced, positive sequence),
   default convergence settings.
3. Export per-bus results to CSV with EXACTLY these columns:
   `bus_name,vm_pu,va_degree`
   - `bus_name` = the canonical name from the .raw NAME field (BUS_01…).
   - `vm_pu` = voltage magnitude p.u.; `va_degree` = angle in degrees.
4. Save as `case39_h<hour>.csv` in THIS directory (e.g. `case39_h19.csv`).
5. **Before closing the session, do the branch export below as well.**
6. Run `pixi run gridspine-tests` — the vertical-slice test picks the
   bus fixture up automatically; it SKIPS while no fixture exists.

Gate: |Vm| within 1% relative AND angle within 0.5 deg absolute, per bus.
A single failing bus fails the slice.

## Also export branch flows — same session, second CSV

The load flow is already solved on screen at step 2, so this costs one more
export and saves a whole repeat session later. Do NOT skip it because the
increment-1 tests do not read it yet.

Export per-branch results to CSV with EXACTLY these columns:

`from_bus,to_bus,ckt,p_from_mw,q_from_mvar,loading_percent`

- `from_bus` / `to_bus` = the canonical bus names from the .raw NAME field
  (`BUS_01`…), oriented as the record appears in the .raw.
- `ckt` = the circuit id exactly as written in the .raw CKT field (a parallel
  branch is only identified by the `(from_bus, to_bus, ckt)` triple).
- `p_from_mw` / `q_from_mvar` = flow at the FROM end; `loading_percent` = the
  branch loading PowerFactory reports.

Save as `case39_h<hour>_branches.csv` in THIS directory (e.g.
`case39_h19_branches.csv`), alongside the bus CSV from the same run.

**Captured now, compared in increment 2; the increment-1 automated gate covers
bus voltages/angles only (ledger ruling 2026-08-29).** The spec's phase-1 gate
names branch flows <1%, and that comparison is increment-2 task #1 — the data
has to exist by then, and re-running the manual PowerFactory session is the one
cost worth paying zero times.
