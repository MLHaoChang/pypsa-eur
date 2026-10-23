<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# SCR / ESCR study under outages and for several nearby connections

Status: needs-triage
Type: task
Blocked by: 09
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
