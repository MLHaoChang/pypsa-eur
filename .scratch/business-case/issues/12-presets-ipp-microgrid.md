<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 12 — IPP, IPP hybrid and microgrid presets

Status: needs-triage (blocked by 06, 07)
Type: task
Blocked by: 06, 07
Gated: yes (GK B; GK A for the linked outputs)
Model tier: sonnet
Size: S

Read [the spec](../spec.md) §6.6–§6.7 and [research.md](../research.md) §4–§5 first.

## What to build

IPP and IPP hybrid: the PPAs, merchant export and CfD as locked ledger rows
(never lines); capacity payment on `capacity:bess` with a typed de-rating; GOs;
land lease; insurance. Microgrid (both): tariff rows on `served_load:residential`
and `served_load:hospital_load`, EaaS fee off by default, opex rows.

## Tests

A linked export line on the IPP is refused `double_count` (G2); IPP business-case
revenue includes the PPA exactly once; unserved microgrid load earns no tariff and
no VoLL appears (B4).
