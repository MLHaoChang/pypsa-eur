<!-- SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur> -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Focus drops when the assistant opens Results with the dock unpinned

Status: needs-triage
Type: bug

## Problem

When the assistant opens a Results tab, focus goes back to the chat input at the end of the turn (A5.5). With the dock unpinned, the dock steps aside over Results (owner decision Q5a), and its input goes with it. The focus then has nowhere to land, so it falls to the page body. A keyboard or screen-reader user loses their place.

Nothing tests this or fixes it. The S5 browser journey (`pypsa-gui/frontend/scripts/smoke-result-navigation.mjs`) pins the dock open, so it covers only the pinned case.

## Found in

Assistant-assurance 06 S5 (#2050), raised by UX 31.

## Acceptance

- With the dock unpinned, after the assistant opens a Results tab, focus lands on a named, visible control. Which control (the dock's launcher, or the tab it opened) is for the UX reviewer to rule on.
- A browser test covers the unpinned case alongside the pinned one.
