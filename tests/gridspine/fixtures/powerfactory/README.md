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
5. Run `pixi run gridspine-tests` — the vertical-slice test picks the
   fixture up automatically; it SKIPS while no fixture exists.

Gate: |Vm| within 1% relative AND angle within 0.5 deg absolute, per bus.
A single failing bus fails the slice.
