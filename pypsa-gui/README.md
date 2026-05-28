# PyPSA GUI

An interactive web application for **building, editing, solving, and analysing
[PyPSA](https://pypsa.org) energy-system networks** — without writing Python.
It wraps the PyPSA optimisation toolchain (built on top of this
[PyPSA-Eur](https://github.com/PyPSA/pypsa-eur) checkout) behind a FastAPI
backend and a React single-page front end, so an analyst can draw a grid on a
canvas, attach generators / storage / sector-coupling links, configure a
multi-period investment problem, run a linear optimal power flow (LOPF) plus an
AC power-flow verification, and explore the results — capacity expansion,
hourly dispatch, economics, emissions, prices and load flow — all in the
browser.

---

## Table of contents

- [What it is](#what-it-is)
- [Architecture & implementation](#architecture--implementation)
- [Features](#features)
- [Getting started](#getting-started)
- [Using the app — a typical workflow](#using-the-app--a-typical-workflow)
- [Project & repository layout](#project--repository-layout)
- [API surface](#api-surface)
- [Design notes & known constraints](#design-notes--known-constraints)
- [Attribution & licence](#attribution--licence)

---

## What it is

PyPSA is a powerful Python framework for modelling electricity and
sector-coupled energy systems, but it is normally driven through scripts and
config files. **PyPSA GUI** turns it into an interactive tool:

- **Model builder** — place buses on a schematic canvas or a geographic map,
  wire up lines, transformers, generators, storage units, stores, loads and
  multi-port conversion links (electrolysers, heat pumps, CHP, P2X).
- **Scenario engine** — manage snapshots, representative periods and
  multi-year **investment periods** (with per-vintage capacity bounds), set
  solver options, and run **overnight / myopic / perfect-foresight** capacity
  expansion.
- **Results explorer** — once solved, inspect built capacity, hourly dispatch
  stacks, per-carrier economics (LCOE/LCOS/LCOH, OPEX/CAPEX), curtailment,
  emissions, nodal prices, storage cycling and AC load-flow convergence — and
  **compare two scenarios side by side**.

The target user is an energy-system analyst or student who wants PyPSA's
modelling power with the immediacy of a GUI.

---

## Architecture & implementation

The app is two processes that talk over HTTP/JSON (plus Server-Sent Events for
live solver logs):

```
┌──────────────────────────┐         HTTP / JSON / SSE        ┌───────────────────────────┐
│  Front end (Vite dev)    │  ───────────────────────────►   │  Backend (FastAPI)        │
│  React 19 + TypeScript   │   /api/network, /api/results,    │  uvicorn :8000            │
│  :5173                   │   /api/simulation (SSE logs)…    │                           │
│                          │  ◄───────────────────────────   │  in-memory pypsa.Network  │
└──────────────────────────┘                                  │  + project bundles on disk│
                                                              └───────────────────────────┘
                                                                          │
                                                                  PyPSA / linopy / solver
                                                                  (HiGHS / Gurobi / …)
```

### Backend — FastAPI + PyPSA

- **Web tier**: `fastapi`, `uvicorn[standard]`, `sse-starlette` (streaming
  solver logs), `aiofiles`, `python-multipart`. Everything heavy (PyPSA,
  pandas, linopy, the solvers) comes from the repo-root **pixi** environment.
- **Single in-memory network**: `services/pypsa_service.py` holds one
  `pypsa.Network` singleton behind a lock. **Writes take the lock; reads never
  do.** All mutations flow through three generic CRUD helpers in
  `routers/network.py` (`_create_component` / `_update_component` /
  `_delete_component`), so cross-cutting concerns — audit logging, undo
  snapshots, time-series and vintage-bound cleanup — live in exactly one place.
- **Solving**: `services/solver_service.py` runs `n.optimize()` (LOPF) in a
  background thread, streams `[PHASE]` / `[CURT]` / `TRACEBACK:` lines to the
  front end over SSE, and optionally chains an **AC power-flow** verification
  stage (`n.pf()`). It implements custom extra-functionality such as a
  **curtailment-cost** term and **per-period vintage expansion** for
  multi-investment-period runs.
- **Supporting services**: `validation_service` (pre-flight checks),
  `vintage_service` (per-period capacity bounds via transient vintage rows),
  `change_log_service` (in-memory audit log), `undo_service`,
  `dispatch_status` (fresh/stale result detection),
  `time_aggregation_service` (representative-period sampling),
  `carrier_catalog` (carrier metadata).
- **Projects**: networks are saved/loaded as bundles (`network.nc` +
  `user_ts.json` + `solver_config.json` + metadata) under
  `backend/projects/`. The compare endpoints load two bundles and compute a
  full side-by-side summary.

### Front end — React + TypeScript

- **Framework**: React 19 + TypeScript, bundled with **Vite**.
- **Server state**: **TanStack React Query** caches every `/api/*` resource;
  mutations spread the full cached object before `PUT` so partial updates never
  reset omitted fields.
- **UI state**: **Zustand** stores (`uiStore`, `simulationStore`) hold the
  current selection, panels and live solve status.
- **Canvas**: `@xyflow/react` (React Flow) for the schematic topology editor;
  **Leaflet** / `react-leaflet` for the geographic map view.
- **Charts**: **recharts** (dispatch stacks, duration curves, seasonal grids,
  per-carrier KPIs); `@tanstack/react-table` + `react-virtual` for large asset
  tables.
- **Routing / UX**: `react-router-dom`, `react-hot-toast` (undo toasts),
  `lucide-react` icons.

---

## Features

### Network building & editing
- Schematic **topology canvas** (drag to place/move buses, draw lines/links).
- **Geographic map** view (Leaflet) with coordinate-aware editing; line
  lengths auto-recompute from bus coordinates (haversine).
- Full CRUD for **buses, lines, transformers, generators, links (multi-port),
  storage units, stores, loads, carriers and global constraints**.
- **Bulk edit** of asset tables, inline editing, search/filter, column
  visibility, CSV export (RFC-4180, formula-injection-safe).
- **Undo** for destructive actions and an **audit/change log**.

### Time, snapshots & scenarios
- Snapshot management with **custom snapshot weightings**.
- **Representative-period sampling** (e.g. representative weeks) for fast runs.
- **Multi-investment-period** modelling: promote a flat network to multiple
  investment periods, with **per-vintage capacity bounds** per build year.
- **Load-profile manager** and time-series upload (`user_ts`) that follows
  component renames/deletes.

### Optimisation
- **LOPF** (linear optimal power flow / capacity expansion) via PyPSA + linopy.
- **AC power-flow** verification stage (`n.pf()`) chained after the LP, with
  per-snapshot convergence reporting.
- **Foresight modes**: overnight, myopic (rolling), and perfect.
- Solver configuration UI (solver choice, VOLL, discount rate, CO₂ limits,
  curtailment cost, SCLOPF, multi-period toggles).
- **Pre-flight validation** (Validate button + blocking checks at run start)
  and **live streaming logs** over SSE with coloured phase markers.

### Results & analysis
- **Capacity expansion** — built vs brownfield capacity per carrier and per
  investment period (generators, storage and links each accounted separately).
- **Dispatch** — carrier-stacked hourly generation with load, storage
  charge/discharge and cross-carrier link flows; **weekly / monthly / full-
  horizon** seasonal views; per-carrier KPI panels (demand, generation,
  storage, curtailment %, OPEX/CAPEX, LCOE).
- **Economics** — per-asset revenue, OPEX, annuitised CAPEX, net profit and
  LCOE/LCOS/LCOH, with multi-period breakdowns.
- **Emissions**, **nodal prices**, **curtailment**, **lost load (VOLL)**,
  **storage cycling**, and **load-flow** (line loading, voltage, convergence).
- **Compare view** — two saved scenarios side by side across capacity,
  dispatch, economics, emissions, prices, curtailment, storage and lost load.

---

## Getting started

### Prerequisites

This GUI lives inside a **PyPSA-Eur** checkout and reuses its toolchain:

1. **pixi** environment at the repository root (provides PyPSA, pandas,
   linopy and at least one solver such as HiGHS):
   ```bash
   pixi install          # from the repo root
   ```
2. **Backend web dependencies** (into the same pixi env):
   ```bash
   pixi run -- pip install -r pypsa-gui/backend/requirements.txt
   ```
3. **Node.js + front-end packages**. A self-contained Node toolchain is
   expected under `pypsa-gui/.nodeenv` (or use a system Node ≥ 18):
   ```bash
   cd pypsa-gui/frontend
   npm install
   ```

### Run it

**Windows (one click):** from `pypsa-gui/` run

```bat
start.bat
```

which opens the backend (`uvicorn main:app` on `:8000`) and the front end
(`vite` on `:5173`) in separate windows.

**Manually / cross-platform:**

```bash
# Terminal 1 — backend
cd pypsa-gui/backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — front end
cd pypsa-gui/frontend
npm run dev
```

Then open **http://localhost:5173**. The API and its interactive docs are at
**http://localhost:8000** and **http://localhost:8000/docs**.

> Production build of the front end: `npm run build` (emits `frontend/dist/`).

---

## Using the app — a typical workflow

1. **Create or open a project** (Projects panel). Start from a template or an
   empty network, or import a PyPSA `.nc` / CSV / MATPOWER case.
2. **Build the network** — add buses on the canvas/map, connect lines and
   conversion links, and attach generators, storage and loads. Set carriers,
   costs and capacity bounds.
3. **Define the time dimension** — set snapshots/weightings (optionally sample
   representative periods) and, for capacity expansion, configure
   **investment periods** and per-vintage bounds.
4. **Configure the solver** — choose foresight mode, solver, VOLL, discount
   rate, CO₂ limits, curtailment cost, etc. Click **Validate** to catch issues.
5. **Run** — start the LOPF (optionally with the AC power-flow stage). Watch
   the live log stream; phase markers show Loading → Validation → Optimising →
   Storing → Summary.
6. **Analyse** — open the Results tabs (capacity, dispatch, economics,
   emissions, prices, curtailment, load flow, storage). Filter by carrier and
   by investment period; export charts/tables to SVG/CSV.
7. **Save & compare** — save the solved network as a project bundle, tweak a
   copy, re-solve, and use **Compare** to see the deltas side by side.

---

## Project & repository layout

```
pypsa-gui/
├── backend/                 # FastAPI service wrapping PyPSA
│   ├── main.py              # app + router registration + lifespan
│   ├── routers/             # HTTP endpoints
│   │   ├── network.py       #   component CRUD, bulk edit, global constraints
│   │   ├── clustering.py    #   network clustering
│   │   ├── vintage.py       #   per-period vintage capacity bounds
│   │   ├── snapshots.py     #   snapshots & investment periods
│   │   ├── simulation.py    #   run LOPF/AC-PF, SSE logs, /api/results/*
│   │   ├── projects.py      #   save/load/compare project bundles
│   │   ├── io.py            #   import/export (nc, CSV, MATPOWER)
│   │   └── changelog.py     #   audit log
│   ├── services/            # business logic (see "Backend" above)
│   ├── models/schemas.py    # Pydantic request/response models
│   ├── project_templates/   # sample starter networks
│   └── requirements.txt     # web-tier deps (rest come from pixi)
├── frontend/                # React + TypeScript + Vite SPA
│   └── src/
│       ├── pages/           # canvas, panels, editors
│       │   └── results/     # Dispatch, CapacityExpansion, Economics, …
│       ├── components/       # shared UI
│       ├── api/             # typed API client (React Query)
│       ├── store/           # Zustand state
│       └── utils/
├── scripts/                 # helper scripts (e.g. scaffold templates)
└── start.bat                # launches backend + frontend (Windows)
```

---

## API surface

All endpoints are under `http://localhost:8000` (browse them at `/docs`):

| Prefix | Purpose |
|---|---|
| `/api/network`        | Component CRUD (buses, lines, links, generators, storage, stores, loads, carriers), bulk edit, clustering, vintage bounds |
| `/api/projects`       | Save / load / list project bundles, snapshots & investment periods, compare-state and results-summary |
| `/api/simulation`     | Run LOPF / AC-PF, solver config, pre-flight validation, status, **SSE log stream** |
| `/api/results`        | Dispatch, cost breakdown, per-carrier economics, emissions, prices, curtailment, lost load, statistics, load flow |
| `/api/io`             | Import / export networks |
| `/api/changelog`      | Audit log of edits |

---

## Design notes & known constraints

- **Single active network**: the backend serves one in-memory network at a
  time; concurrent users would share it. Project bundles on disk provide
  persistence and switching.
- **Pickled solver state** (`results_state.pkl`) and saved `network.nc` blobs
  live under `backend/projects/` and are **git-ignored** (binary, user data).
- **OneDrive + pixi**: when the repo lives in a OneDrive-synced folder, file
  locks can interfere with `uvicorn --reload` and pixi env relinking; a manual
  backend restart is the reliable workaround.
- The GUI targets PyPSA's modern API (≥ 0.34) and multi-investment-period
  vintage modelling; see the in-repo `CLAUDE.md` for the detailed engineering
  notes and pitfalls captured during development.

---

## Attribution & licence

PyPSA GUI is built on and distributed alongside
[**PyPSA-Eur**](https://github.com/PyPSA/pypsa-eur) and the
[**PyPSA**](https://github.com/PyPSA/PyPSA) framework by the PyPSA developers.
Please retain their upstream licences and citations. This `pypsa-gui`
subproject is an interactive layer on top of that toolchain; consult the
repository's existing licence files for terms.
