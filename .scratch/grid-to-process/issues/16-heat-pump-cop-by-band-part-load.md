<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Heat-pump COP against the served band's sink temperature, plus part-load

Status: needs-triage
Type: task
Blocked by: 15
Gated: no
Model tier: sonnet
Size: S–M

**Do not start until Codex task G-a has merged.** G-a (branch `outsource/profile-tuning-backend`,
`docs/agents/outsourced.md`) edits `services/profile_gen`, the same package this ticket changes.
Also read [the design](../design.md) §2.3, §2.6 and the
[research note](../research/se-electrifying-refining-desw024.md) §6a (gap 5).

## Why

The COP series uses one fixed sink temperature (`sink_c`, default 45 degC,
`services/profile_gen/series.py:61`). A process heat pump serves a band with its own sink
temperature, and COP falls steeply with lift (note §6 table, heat pumps). The note's gap 5 says the
Carnot-fraction form already exists; what is missing is the sink per duty and part-load. Part-load
is refused today as "needs a piecewise or committable formulation" (`series.py:273`). Issue 03
already built the committable-Link path, so this reuses it.

## What to build

1. **Sink temperature from the band.** `cop` reads the sink temperature of the band the heat pump
   serves (from issue 15) instead of a constant. A constant `sink_c` stays as the fallback, so
   existing series are byte-identical.
2. **Part-load.** Replace the refusal (`PartLoadNotASeriesError`) for heat pumps with a committable
   Link: `p_min_pu` as the minimum load, start-up and shut-down costs as in issue 03. Decide with
   the gatekeeper whether COP at part load is a single derating factor or a piecewise curve.
3. **Clipping and reporting stay as they are:** COP clipped to [1, `cop_max`], and the count of
   hours raised to 1 is reported (`series.py:18`).
4. **Validator tie-in:** a COP series whose sink is above the device's reach is an error (rule from
   issue 15).

## Acceptance (stated first)

- For source 10 degC, eta 0.45, sink 45 degC the series equals today's output. At sink 90 degC the
  COP is lower, by the Carnot formula, and equals η·T_sink/(T_sink−T_source) in kelvin to 1e-9.
- Two heat pumps on two bands get two different COP series from the same weather.
- A heat pump with `part_load` no longer raises; it becomes a committable Link, and a solve with
  demand below `p_min_pu` shows it off or cycling, not running below minimum.
- Weighted-snapshot networks still count start-up costs once (the issue 03 rule).

## File pointers (checked on origin/master)

- `services/profile_gen/series.py:18` (docstring of the COP form), `:47` (`KINDS`), `:61` (defaults),
  `:220` (`_cop`), `:264` (`_TARGETS`, links `efficiency`), `:273` (the part-load refusal).
- `services/asset_schema/fields.py:163-175` (committable rows).
- `scripts/scaffold_dc_heatpump.py:1-35` (heat pump as a Link with `efficiency2 = -(COP-1)`).
- `.scratch/grid-to-process/issues/03-solve-path-committable-links.md`.

## Open questions

- Gatekeeper: constant derating factor or piecewise part-load curve? Design §2.6 says piecewise is
  "later".
- Gatekeeper: should part-load stay out of the series generator and live in the Link fields, since
  a series cannot express it?
- Owner: is a COP source for high-temperature heat pumps needed first (the note lists Arpagaus
  et al. and the DEA catalogue)? Without one the fixture values are `demo`.

## Dependencies

- Blocked by 15 (it supplies the band's sink temperature).
- Must not start before Codex task G-a merges (shared files in `services/profile_gen`).
- Uses issue 03 (done).
