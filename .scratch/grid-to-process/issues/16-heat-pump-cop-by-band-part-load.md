<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Heat-pump COP against the served band's sink temperature, plus part-load

Status: ready-for-agent (owner 2026-10-23, Q16: demo eta + reach from the #679 rows; still blocked by 15)
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

- For `source: ground`, `source_c` 10 degC, eta 0.45, sink 45 degC the series equals today's output (an
  `air` source needs the site ambient, so it cannot be the fixed case). At sink 90 degC the
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

## Triage (gatekeeper, 2026-10-23)

**Status `needs-info`:** one owner question is open (below). After that it is `ready-for-agent`, still
blocked by 15.

Answers to the gatekeeper questions:

- **No part-load COP at all in this ticket: no derating factor and no piecewise curve.** Design §2.6
  keeps efficiency constant per unit, and piecewise part load stays in §7. A single derating factor
  would also lower the COP at full load, which is wrong. If the owner later wants part-load COP, it
  is the §7 piecewise item, not this ticket.
- **Part-load lives in the Link fields, not in the series generator.** A heat pump with a minimum
  load is a committable Link (`committable`, `p_min_pu`, start-up and shut-down costs:
  `fields.py:163-175`, the issue 03 path). The `part_load` series kind keeps its refusal, because a
  series still cannot express it. Reword `PartLoadNotASeriesError`'s message to point at those Link
  fields. "What to build" item 2 therefore shrinks to: the refusal message, plus a test that the
  committable heat pump solves as the acceptance says.
- **Overlap with Codex G-a is real, but it is not `series.py`.** G-a changes `presets.py`,
  `engine.py`, `routers/profiles.py`, `tool-error-kinds.json` and the capability inventory, not
  `series.py`. Keep the "after G-a merges" rule, because a reworded refusal and any new kind touch
  `tool-error-kinds.json` and the inventory too.
- **Size drops to S** with part-load reduced as above.

Owner question (send it as these options):

- **Is a sourced COP for high-temperature heat pumps needed before this starts?**
  - *(Recommended)* No. Ship with `eta` and the reach labelled `demo`, and source them when the
    heat-kit library rows (session_01FeAyYSfvoFrV9xVAWtRjDv) land. The Carnot form is exact; only
    the parameters are unsourced.
  - Yes. Wait for a sourced `eta` per band (Arpagaus et al., or the DEA technology catalogue)
    before building. Safer numbers, but the ticket waits on research.
