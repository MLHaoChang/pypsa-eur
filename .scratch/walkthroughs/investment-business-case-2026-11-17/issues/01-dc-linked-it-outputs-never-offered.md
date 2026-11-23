<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 01 — Data-centre business case: the IT-capacity output is never offered

Status: done

Done in #1679 (9100d3cb).
Severity: high (the data-centre business case cannot run from its own preset)
Group: Investment and business case. Walk 2026-11-17, master 9491878d.
Breaks: `.scratch/business-case/spec.md` §6.2 and §5 (linked outputs); `.scratch/dc-business-case/issues/03-it-energy-series.md` and `06-business-case-view.md` (the IT reader is the only IT source, LO-10); owner decision HOLISTIC-1 (the results trace to the inputs).

## Repro

1. New project › From template › Data Center Energy Hub › Create project (IT 36 MW, the form defaults). Or `POST /api/projects/from_template/eh_datacenter`.
2. Expert › Run LOPF (or `POST /api/simulation/run`); wait for `optimal`.
3. Results › Commercial & finance › Business case › pick "Colocation (tenant owns IT, leased per kW-month)" › Apply preset. Or `GET /api/results/business_case/preset?variant=colocation`.
4. Open "Quantity from" on the line "Colocation lease". Or `GET /api/results/business_case/options`.

## Actual

- The preset's lines "Colocation lease", "Staff" and "Network and bandwidth" carry `quantity_source = linked:contracted_it_kw@1`.
- `/options` offers only `served_load.it_load`, `served_load.cooling`, `served_load.offices` and `capacity.*`. `contracted_it_kw` and `it_energy` are absent, with `outputs_reason: null`.
- `GET /options` then lists `linked_output_missing` for `lines[0]`, `lines[3]`, `lines[4]` (`.quantity_source`). `POST /api/results/business_case` refuses `business_case_refused:linked_output_missing: contracted_it_kw`.
- Same on the staged template (`eh_datacenter_staged`) and when the project is created through the data-centre form.
- Cause (read in code): `services/business_case/linked_outputs.py:561` adds both outputs only `if solve.dc is not None`; `bind(ctx, *, dc=None)` (line 85) sets `dc` only when the caller passes it. The only callers outside tests, `routers/results.py:2552` and `:2693`, call `L.bind(ctx)` with no `dc`. The IT spec the form captured is never handed over.

## Expected

The preset that ships with the template runs without the user knowing a hidden prerequisite. The IT-capacity and IT-energy outputs are offered whenever the project carries a data-centre load spec, and the lines resolve in `POST /results/business_case`. If a project has no spec, the line says in plain words which input is missing and where to set it, not `linked_output_missing`.

## Acceptance

- A test on the route (not the reader) that creates the data-centre template, solves, and asserts `contracted_it_kw` is in `/options` and the colocation preset runs with typed prices only.
- Same for `gpu_cloud` (`it_energy`, GPU_h needs `kw_per_gpu`).
- A project with no spec shows the missing input in words.

## Comments

Hand check done around it (all agree): with the lease typed as 432 000 kW-month (36 MW × 1000 × 12), price 170, indexation 2 %, the 2027 lease revenue is 74 908 800 = 432 000 × 170 × 1.02; pass-through 250 000 MWh × 95 × 1.02 = 24 225 000; staff 432 000 × 12 × 1.02 = 5 287 680. All match the report.
