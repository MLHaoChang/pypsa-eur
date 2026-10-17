<!--
SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
SPDX-License-Identifier: CC-BY-4.0
-->

# PyPSA GUI — an interactive workbench for PyPSA

> This repository is a fork of **PyPSA-Eur** that adds **PyPSA GUI**, a
> graphical web application for building, solving and analysing
> [PyPSA](https://pypsa.org) energy-system networks **without writing Python**.
> The original PyPSA-Eur model and documentation are preserved in full
> [below](#pypsa-eur-a-sector-coupled-open-optimisation-model-of-the-european-energy-system).

🧭 **Finding your way around this repo:** see
[**`docs/REPO-GUIDE.md`**](docs/REPO-GUIDE.md) — what each section is, where
to find what, and when it last changed. Architecture deep-dive:
[`docs/superpowers/handoffs/2026-10-17-architecture-current-state.md`](docs/superpowers/handoffs/2026-10-17-architecture-current-state.md).

**What it is.** A complete graphical front end on top of the PyPSA optimisation
stack. Draw a grid on a schematic canvas or a geographic map, attach
generators / storage / sector-coupling links, set up a multi-year investment
problem, press **Run**, and explore the results — capacity expansion, hourly
dispatch, economics, emissions, prices and load flow — all in the browser.
Every action drives a live `pypsa.Network` on the server, so what you build is
exactly what gets optimised, and the result views read straight back from
PyPSA's solved data.