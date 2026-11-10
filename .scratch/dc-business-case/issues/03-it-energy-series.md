<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — The IT energy series for revenue

Status: needs-triage (blocked by 01)
Type: task
Blocked by: 01
Gated: yes (GK A)
Model tier: opus
Size: S

Read [the spec](../spec.md) §2 (facts 1–2) and §4 first.

## What to build

`services/dc_load_profile.py` (or a sibling): `it_energy(spec, snapshots, weights,
unserved=None)` returning per period the IT MWh billable, the contracted IT kW
per phase in service, the rack count where `rack_kw` is set, and the unserved IT
MWh (quantity only, never priced). It reads the spec (IT MW × u(t)), the same
series the facility load is built from, and the post-solve unserved slack per
GK A's answer to 01 Q4–Q5.

## Tests

Facility load ÷ PUE(T) equals the IT series within float tolerance; curtailment
on (DC 07) leaves it unchanged; unserved energy lowers billable MWh by exactly
its IT share; a phase COD mid-horizon starts billing at its COD.
