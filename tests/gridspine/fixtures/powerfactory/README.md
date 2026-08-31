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
export and saves a whole repeat session later.

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

**This CSV is now a live gate, not a capture-for-later.** As of increment 2 it
is read by `gridspine.readback.pf_compare.compare_branch_flows`, which joins it
onto the pandapower load flow and flags every branch:

| check | tolerance |
|---|---|
| active power `p_from_mw` | within **1%** relative to the PowerFactory value, whose magnitude is floored at **1 MW** so a branch idling near zero is not judged against ~0 |
| reactive power `q_from_mvar` | within **5 Mvar** absolute — reactive flow crosses zero, so a relative test has no stable reference |
| `loading_percent` | carried through side by side for eyeballing; **not** part of the pass/fail, since it is a ratio against a rating the two tools need not share |

A branch passes only if BOTH the P and the Q check pass; the result frame's
`ok` column is that AND. The comparison refuses to run at all — raising
`ContractError` rather than reporting differences — when the load flow did not
converge, when a required column is missing, or when the two sides disagree on
the branch key set.

### Getting the keys right — this is where the export goes wrong

Each branch is identified by the triple `(from_bus, to_bus, ckt)`, and all
three must match the `.raw` exactly:

- **Orientation is part of the key.** Export the branch as the record appears
  in the `.raw` (`I` then `J`; for a transformer that is HV then LV). A
  reversed row does not join — it shows up as one branch missing on each side.
- **`ckt` is part of the key.** Parallel circuits share a bus pair and are
  told apart only by the circuit id. Copy the `.raw` CKT field verbatim;
  do not renumber, and do not drop the column for single circuits.
- Lines and transformers live in separate `.raw` sections and their circuit
  ids are numbered independently, so seeing `1` twice across the two sections
  is expected.

A key that cannot be matched fails the run with `branch set mismatch` listing
both sides, so a bad export is caught immediately rather than being quietly
skipped.
