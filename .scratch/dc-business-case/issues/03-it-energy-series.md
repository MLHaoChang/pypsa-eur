<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 03 — The IT energy series for revenue

Status: done (#1319)
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

## Comments

### 2026-11-10: GK A (19) plan GO, conditions BC3-1..8

Facts (a) and (b) confirmed. Network loads are facility MWh, so revenue reads the
spec (facility = it_mw × u(t) × PUE(T), `dc_load_profile.hourly_profile`). Caveat for the
revenue line's note: holding IT revenue whole under DC 07 curtailment is the
model's claim that the curtailable share is non-IT load.

- Q4 rule: billable IT shortfall(t) = involuntary unserved facility MWh(t) ÷ PUE(T(t)).
  Facility unserved = involuntary slack on `it_bus` + the cooling share of the
  involuntary slack on `dc_mv` (pro rata cooling(t) ÷ all loads on `dc_mv`(t), since
  offices sit there too). Exclude `dc_curtailment` and every non-involuntary
  SLACK_CARRIER. Cap at the period's billable IT (never negative).
- Q5: no reader of `workload_shift_window_h` outside `models/flex_archetypes.py:166`;
  DC 06 moves no load. Revenue reads the spec plus the unserved.
- BC3-1 One source: reuse `dc_load_profile.utilisation` (with the project's timezone)
  and the template's in-service/COD row rule (`eh_templates.py` ~620), lifted to a
  shared helper if needed. `ups_distribution_loss_share` is inside PUE; not added.
- BC3-2 Energy = Σ IT MW × u × the generators snapshot weighting; tests use non-unit
  weightings and a non-UTC timezone.
- BC3-3 Identity test against the BUILT network: (it_load + cooling)(t) ÷ PUE(T(t)) ==
  IT MW(t), on the AI-profile template and the staged campus.
- BC3-4 Unserved per Q4, with tests for it_bus slack, dc_mv pro rata with offices,
  curtailment lowering nothing, the cap; mutants (drop ÷PUE, drop the dc_mv share,
  count curtailment) each killed.
- BC3-5 Unserved IT MWh is a quantity only: never priced, no VoLL, never in the IC or
  adequacy facts; a test that no BC line reads a price on it.
- BC3-6 Pin Q5: `workload_shift_window_h` set leaves it_energy and the built loads
  unchanged.
- BC3-7 Rack count = it_mw ÷ rack_kw × 1000 only where rack_kw is set, else "not set".
  Contracted IT kW per phase from COD; a mid-horizon COD bills from its first
  in-service snapshot (test).
- BC3-8 No change to the DC template's IC basis or DC 06 L6–L9; the DC template's IC
  and adequacy suites byte-equal.
- The slice's PR head goes to GK A.
