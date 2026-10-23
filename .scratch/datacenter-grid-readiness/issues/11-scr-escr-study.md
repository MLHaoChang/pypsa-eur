<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SCR / ESCR study under outages and for several nearby connections

Status: ready-for-agent
Type: task
Blocked by: none (09 is the owner's priority, not a dependency)
Gated: **yes** (gridspine)
Model tier: opus
Size: M
Owner decision: SAGC-1 (G4), 2026-10-23

**Confidentiality.** Cite only the public Saudi Arabian Grid Code (SAGC, May 2026) and generic engineering. Never name a client, its network, its MW figures or any scope-of-work text, here, in code, tests, fixtures or PR bodies.

## Why

`gridspine/static/strength.py` gives SCR from one intact-network fault level. Grid codes such as
SAGC judge system strength with reactive compensation removed, under outages, and across several
converter connections close together.

## What to build

1. **ESCR** = (S_sc − Q_c) / P_n, with Q_c the shunt compensation and filters at the PoC.
2. **SCR under N-1 / N-2:** rerun `fault_levels` (minimum case) over the contingency set
   (`static/contingency_set.py`), report the worst outage per PoC, and apply the rule "lose the
   highest-contributing line".
3. **Several PoCs:** a weighted SCR (WSCR) for several nearby converter-connected loads or plants.
4. **By stage:** a table per development stage (phase), since load grows in steps.

## Done when

- strength.py returns SCR, ESCR and WSCR per PoC, intact and worst outage, with the outage named.
- Tests on a small fixture check ESCR by hand and that the worst-outage SCR ≤ intact SCR.
- Labelled as a static screen; the binding answer stays with the RMS / EMT study.

## Triage (gatekeeper, 2026-10-23)

**Status `ready-for-agent`, gated, size M.** Fault levels (IEC 60909) do not depend on the nominal
frequency, so `Blocked by: 09` is dropped.

Rulings:

- **ESCR** = (S_sc − Q_c) / P_n, where Q_c is the PoC shunt compensation and filter Mvar at
  nominal voltage. It is read from the campus `compensation` block. With no compensation,
  ESCR = SCR, and the row says so.
- **WSCR uses the common weighted definition:** WSCR = Σ_i (S_sc,i · P_i) / (Σ_i P_i)², over the
  PoCs the user groups as "electrically close". Grouping is user input; do not auto-cluster.
- **Outages: use the existing contingency set read-only.** Use the N-1 branches and the capped
  N-2 pairs from `static/contingency_set.py`, rerun the minimum-case fault levels for each and
  report the worst outage per PoC, named. Issue 13 extends that module, so 11 must not change its
  enumeration.
  - "Lose the highest-contributing line" is a cheaper pre-screen for large nets: compute the
    branch contributions once and outage the top one.
  - The full N-1 stays the reported answer whenever its count is under a user cap. Above the cap,
    the row says the pre-screen was used.
- **By stage:** one row per development stage (phase) from the campus phasing. The load P_n
  changes per stage; S_sc is recomputed only if the network changes.
- **Labelling:** every row says "static screen; binding answer is the RMS/EMT study", as the
  acceptance asks. The bands come from `strength.band`.
