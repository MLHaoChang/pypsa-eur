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

**What it is.** A complete graphical front end on top of the PyPSA optimisation
stack. Draw a grid on a schematic canvas or a geographic map, attach
generators / storage / sector-coupling links, set up a multi-year investment
problem, press **Run**, and explore the results — capacity expansion, hourly
dispatch, economics, emissions, prices and load flow — all in the browser.
Every action drives a live `pypsa.Network` on the server, so what you build is
exactly what gets optimised, and the result views read straight back from
PyPSA's solved data.

**Highlights**

- **Visual network builder** — schematic (React Flow) canvas *and* a geographic
  (Leaflet) map; full CRUD for buses, lines, transformers, generators,
  storage, stores, loads and **multi-port conversion links** (electrolysers,
  heat pumps, CHP, power-to-X). Line lengths auto-update from coordinates.
- **Spreadsheet-style editing** — per-component tables with inline + **bulk
  edit**, search, sort, column toggles and CSV export.
- **Time, snapshots & investment periods** — custom snapshot weightings,
  representative-week sampling, per-asset time-series upload, and **multi-year
  capacity expansion with per-vintage capacity bounds**.
- **One-click optimisation** — solver/VOLL/CO₂/curtailment config, **pre-flight
  validation**, **LOPF + optional AC power-flow**, and a **live streaming
  solver log** with phase markers.
- **Rich result explorer** — capacity (built vs brownfield), carrier-stacked
  **dispatch** with cross-carrier link flows and weekly/monthly views,
  **economics** (LCOE/LCOS/LCOH, OPEX/CAPEX, profit), emissions, nodal prices,
  curtailment, lost load, storage cycling and load flow — all filterable by
  carrier and investment period, exportable to SVG/CSV.
- **Scenario compare** — two saved projects side by side across every metric.
- **Projects & I/O** — save/load bundles; import/export NetCDF, CSV, MATPOWER.

**Stack.** FastAPI backend wrapping a live `pypsa.Network` (PyPSA / linopy /
HiGHS-Gurobi from the pixi env, with SSE log streaming) + a React 19 +
TypeScript + Vite single-page app (React Query, Zustand, React Flow, Leaflet,
recharts).

**Run it** (Windows): `pypsa-gui/start.bat` → open http://localhost:5173.
Or manually: `uvicorn main:app` in `pypsa-gui/backend` (:8000) and
`npm run dev` in `pypsa-gui/frontend` (:5173).

➡️ **Full GUI documentation — features, architecture, setup, workflow —**
see [**`pypsa-gui/README.md`**](pypsa-gui/README.md).

---

*The remainder of this README is the upstream PyPSA-Eur documentation — the
open model that PyPSA GUI sits on top of.*

[![GitHub release (latest by date including pre-releases)](https://img.shields.io/github/v/release/pypsa/pypsa-eur?include_prereleases)](https://github.com/PyPSA/pypsa-eur/releases)
[![Documentation](https://readthedocs.org/projects/pypsa-eur/badge/?version=latest)](https://pypsa-eur.readthedocs.io/en/latest/?badge=latest)
[![Test workflows](https://github.com/pypsa/pypsa-eur/actions/workflows/test.yaml/badge.svg)](https://github.com/pypsa/pypsa-eur/actions/workflows/test.yaml)
![Size](https://img.shields.io/github/repo-size/pypsa/pypsa-eur)
[![Zenodo PyPSA-Eur](https://zenodo.org/badge/DOI/10.5281/zenodo.3520874.svg)](https://doi.org/10.5281/zenodo.3520874)
[![Zenodo PyPSA-Eur-Sec](https://zenodo.org/badge/DOI/10.5281/zenodo.3938042.svg)](https://doi.org/10.5281/zenodo.3938042)
[![Snakemake](https://img.shields.io/badge/snakemake-≥9-brightgreen.svg?style=flat)](https://snakemake.readthedocs.io)
[![Discord](https://img.shields.io/discord/911692131440148490?logo=discord)](https://discord.gg/AnuJBk23FU)
[![REUSE status](https://api.reuse.software/badge/github.com/pypsa/pypsa-eur)](https://api.reuse.software/info/github.com/pypsa/pypsa-eur)

# PyPSA-Eur: A Sector-Coupled Open Optimisation Model of the European Energy System

PyPSA-Eur is an open model dataset of the European energy system at the
transmission network level that covers the full ENTSO-E area and all energy sectors, including transport, heating, biomass, industry, and agriculture.
Besides the power grid, pipeline networks for gas, hydrogen, carbon dioxide, and liquid fuels are included.
The model is suitable both for planning studies and operational studies.
The model is built from open data using a Snakemake workflow and fully open source.
It is designed to be imported into the open-source energy system modelling framework [PyPSA](www.pypsa.org).

> [!NOTE]
> PyPSA-Eur has many contributors, with the maintenance currently led by the [Department of Digital Transformation in
> Energy Systems](https://tu.berlin/en/ensys) at the [Technical University of
> Berlin](https://www.tu.berlin).
> Previous versions were developed at the [Karlsruhe
> Institute of Technology](http://www.kit.edu/english/index.php) funded by the
> [Helmholtz Association](https://www.helmholtz.de/en/).


Among many other things, the dataset consists of:

- A power grid model based on [OpenStreetMap](https://zenodo.org/records/18619025) for voltage levels above 220kV (optional above 60kV).
- The open power plant database
  [powerplantmatching](https://github.com/PyPSA/powerplantmatching).
- Electrical demand time series from the [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/).
- Renewable time series based on ERA5 and SARAH-3, assembled using [atlite](https://github.com/PyPSA/atlite).
- Geographical potentials for wind and solar generators based land eligibility analysis in [atlite](https://github.com/PyPSA/atlite).
- Energy balances compiled from Eurostat and JRC-IDEES datasets.

The high-voltage grid and the power plant fleet are shown in this map of the unclustered model (as of 1 January 2026):

![PyPSA-Eur Unclustered](doc/img/base.png)


For computational reasons the model is usually clustered down
to 50-250 nodes. The image below shows the electricity network and power plants clustered to NUTS2 regions:

![network diagram](doc/img/elec.png)

This diagram gives an overview of the sectors and the links between
them within each model region:

![sector diagram](doc/img/multisector_figure.png)



# Warnings

PyPSA-Eur is under active development and has several
[limitations](https://pypsa-eur.readthedocs.io/en/latest/limitations.html) which
you should understand before using the model. The github repository
[issues](https://github.com/PyPSA/pypsa-eur/issues) collect known topics we are
working on (please feel free to help or make suggestions). The
[documentation](https://pypsa-eur.readthedocs.io/) remains somewhat patchy. You
can find showcases of the model's capabilities in the Joule paper [The potential
role of a hydrogen network in
Europe](https://doi.org/10.1016/j.joule.2023.06.016), another [paper in Joule
with a description of the industry
sector](https://doi.org/10.1016/j.joule.2022.04.016), or in [a 2021 presentation
at EMP-E](https://nworbmot.org/energy/brown-empe.pdf). We do not recommend to
use the full resolution network model for simulations. At high granularity the
assignment of loads and generators to the nearest network node may not be a
correct assumption, depending on the topology of the underlying distribution
grid, and local grid bottlenecks may cause unrealistic load-shedding or
generator curtailment. We recommend to cluster the network to a couple of
hundred nodes to remove these local inconsistencies. See the discussion in
Section 3.4 "Model validation" of the paper.

# Contributing and Support
We strongly welcome anyone interested in contributing to this project. If you have any ideas, suggestions or encounter problems, feel invited to file issues or make pull requests on GitHub.
-   To **discuss** with other PyPSA users, organise projects, share news, and get in touch with the community you can use the [Discord server](https://discord.gg/AnuJBk23FU).
-   For **bugs and feature requests**, please use the [PyPSA-Eur Github Issues page](https://github.com/PyPSA/pypsa-eur/issues).

# Licence

The code in PyPSA-Eur is released as free software under the
[MIT License](https://opensource.org/licenses/MIT), see [`doc/licenses.rst`](doc/licenses.rst).
However, different licenses and terms of use may apply to the various
input data, see [`doc/data_sources.rst`](doc/data_sources.rst).
