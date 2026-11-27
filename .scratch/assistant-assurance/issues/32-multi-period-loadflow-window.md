<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Load flow window comes back wrong on multi-period networks

Status: needs-triage
Type: bug

## Problem

When the assistant opens Load flow with a window (`ui_open_panel`, `window_from`/`window_to`), Results maps the window's snapshot positions to timestamps and back again. On a multi-period network the timesteps repeat in each period, so that round trip lands on the wrong positions. Asking for positions 2–5 shows 2–29.

## Repro

In `pypsa-gui/frontend/scripts/smoke-result-navigation.mjs`, run the "Load flow window" step on the `multi` fixture: a navigate frame with `results_tab: loadflow`, `window_from: 2`, `window_to: 5`. The `branch-loading` binding reads 2–29.

## Found in

Assistant-assurance 06 S5 (#2050). On GK B2's ruling, the journey checks the window on single-period networks only and pins no wrong value.

## Acceptance

- On a multi-period network, a window of positions a–b shows a–b, with "Window a–b".
- The S5 journey runs its window step on `multi` as well.
