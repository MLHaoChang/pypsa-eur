<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# 04 — Results chart: the axis maximum '3.6 h/yr' sits beside 'Goal' and reads as the goal value

Status: needs-triage
Impact: low
Templates: DC, microgrid
Found by: template walk, 2026-11-20 (master 1f6905e3)
Shot: ../shots/gdc-afterrun.png

## What

The Expected-shortfall bar is headed 'goal 3 h/yr', with a marker labelled 'Goal' near the right end. The right end of the axis is labelled '3.6 h/yr'. Read together, 'Goal 3.6 h/yr' contradicts the 3 h/yr goal in the verdict above.

## Why it matters

A number that looks wrong in the main result card, on the first screen a user reads after a run.

## Suggested fix

Label the axis end as an axis end (or drop it) and put the goal value on the marker ('Goal 3 h/yr').
