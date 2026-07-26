# PyPSA GUI

**A full graphical workbench for [PyPSA](https://pypsa.org) — build, edit,
solve and analyse energy-system networks entirely in your browser, no Python
scripting required.**

PyPSA is normally driven by hand-written Python and YAML. **PyPSA GUI** puts a
complete interactive layer on top of it: draw the grid on a canvas or a map,
attach generators / storage / sector-coupling links, set up a multi-year
investment problem, press **Run**, and watch the solver stream its progress —
then explore capacity, dispatch, economics, emissions, prices and load-flow in
rich, filterable result views, and compare scenarios side by side.

It ships as two processes: a **FastAPI** backend that wraps a live
`pypsa.Network` (and the PyPSA / linopy / solver stack from this PyPSA-Eur
checkout), and a **React + TypeScript** single-page app.

---

## Table of contents

- [Why it exists](#why-it-exists)
- [What you can do with it — features](#what-you-can-do-with-it--features)
  - [1. Build & edit the network visually](#1-build--edit-the-network-visually)
  - [2. Inspect & bulk-edit every component](#2-inspect--bulk-edit-every-component)
  - [3. Time series, snapshots & investment periods](#3-time-series-snapshots--investment-periods)
  - [4. Configure & run the optimisation](#4-configure--run-the-optimisation)
  - [5. Explore the results](#5-explore-the-results)
  - [6. Compare scenarios](#6-compare-scenarios)
  - [7. Projects, import & export](#7-projects-import--export)
  - [8. Quality-of-life](#8-quality-of-life)
- [How it interacts with PyPSA](#how-it-interacts-with-pypsa)
- [Architecture & implementation](#architecture--implementation)
- [Getting started](#getting-started)
- [A typical workflow](#a-typical-workflow)
- [Project & repository layout](#project--repository-layout)
- [API surface](#api-surface)
- [Design notes & known constraints](#design-notes--known-constraints)
- [Attribution & licence](#attribution--licence)

---

## Why it exists

PyPSA is enormously capable but has a steep ramp: you assemble a network in
code, manage time-series in DataFrames, wire up the optimisation, and
post-process results yourself. That is great for reproducible studies and
hard for quick exploration, teaching, or stakeholder demos.

PyPSA GUI keeps the full modelling power of PyPSA while removing the scripting
barrier. Every action in the UI maps onto a real operation on a live
`pypsa.Network` on the server — so what you build in the browser is exactly
what gets optimised, and what you see in the result views is read straight
back from PyPSA's solved DataFrames.

---

## What you can do with it — features

### 1. Build & edit the network visually

- **Schematic topology canvas** (React Flow): drag buses around, draw lines and
  conversion links between them, click any element to select and edit it. Edges
  are colour-coded by voltage level; the canvas is the fast way to lay out grid
  topology.
- **Geographic map view** (Leaflet): place and drag buses by real
  latitude/longitude on a slippy map. Line **lengths recompute automatically**
  from bus coordinates (haversine) when you move a bus, so distances stay
  consistent with geometry.
- **Asset palette / creation forms** for every PyPSA component:
  - **Buses** (with carrier, nominal voltage, coordinates, control type)
  - **Lines** and **Transformers** (impedances, ratings, extendable bounds)
  - **Generators** (dispatchable & renewable, with `p_max_pu` profiles,
    marginal/curtailment cost, unit-commitment fields)
  - **Links**, including **multi-port** links for sector coupling —
    electrolysers, heat pumps (with negative `efficiency2` for the cold side),
    CHP, datacentre waste-heat, and other power-to-X converters
  - **Storage units** and **Stores** (energy reservoirs)
  - **Loads** on any carrier (electricity / H₂ / heat / …)
- **Properties panel** to edit every PyPSA attribute of the selected asset:
  nominal capacity, extendable on/off and min/max bounds, capital &
  marginal cost (or overnight cost + discount rate + lifetime), efficiencies,
  `p_min_pu`/`p_max_pu`, carriers, build year, and more — with sensible
  "no-bound" handling (blank a field to clear it back to ±∞).
- **Carriers & global constraints** editor — define carriers (with CO₂
  intensity, colour, nice name) and add global constraints such as CO₂ caps.

### 2. Inspect & bulk-edit every component

- A **spreadsheet-style bottom panel** lists every component class in tabs
  (buses, lines, generators, links, storage, stores, loads, carriers …).
- **Inline cell editing**, **multi-select + bulk edit** (apply a value to many
  rows at once), **search-by-name**, **column sorting**, and **per-tab column
  visibility** (show only the fields you care about).
- **CSV export** of any table (RFC-4180, with formula-injection protection).
- Numeric coercion is enforced so a stray string can't corrupt a numeric
  column (which would otherwise break the NetCDF save).

### 3. Time series, snapshots & investment periods

- **Snapshot manager** to define the model's time index and **custom snapshot
  weightings** (e.g. representative-week scaling).
- **Representative-period sampling** to compress a full year into a handful of
  representative weeks for fast iteration.
- **Load-profile manager** and **time-series upload** for per-asset profiles
  (`p_max_pu`, `p_set`); uploaded series follow component renames and deletes
  so profiles never silently detach.
- **Investment periods / model horizon** for multi-year capacity expansion,
  with a dedicated **per-vintage capacity-bounds editor** — set different
  build limits per build year, and the backend expands them into vintage rows
  for the optimiser transparently.

### 4. Configure & run the optimisation

- **Solver settings** UI: choose the solver (HiGHS / Gurobi / … from the pixi
  env), and set value of lost load (VOLL), discount rate, CO₂ price/limit,
  curtailment cost, SCLOPF, and **foresight mode** (overnight / myopic /
  perfect) and multi-period toggles.
- **Pre-flight validation** — a **Validate** button (and a blocking check at
  run start) surfaces modelling problems (missing slack, infeasible bounds,
  inconsistent multi-period setup …) before you burn solve time.
- **Run LOPF** (linear optimal power flow / capacity expansion) and,
  optionally, a chained **AC power-flow** verification stage.
- **Live solver log** streamed over Server-Sent Events with coloured **phase
  markers** (Loading → Validation → Optimising → Storing → Summary), plus
  curtailment diagnostics and full tracebacks on failure.

### 5. Explore the results

Once solved, dedicated result tabs read straight from PyPSA's solved
DataFrames. Every view supports **filtering by carrier and by investment
period**, and chart/table **export to SVG / CSV**:

- **Capacity expansion** — built vs inherited (brownfield) capacity per
  carrier and per period, with **generators, storage and links accounted
  separately** so the numbers reconcile.
- **Dispatch** — carrier-stacked hourly generation with the load line, storage
  charge/discharge, and **cross-carrier link flows** (e.g. an electrolyser's H₂
  output appears on the H₂ balance, a heat pump's heat on the heat balance);
  switch between **weekly / monthly / full-horizon** seasonal views; per-carrier
  **KPI panels** (total demand, generation by type, storage, curtailment %,
  OPEX/CAPEX, LCOE) for electricity, H₂ and heat.
- **Economics** — per-asset revenue, OPEX, annuitised CAPEX, net profit and
  **LCOE / LCOS / LCOH**, with multi-period breakdowns and fleet roll-ups.
- **Emissions** — CO₂ per carrier and per period.
- **Prices** — nodal/marginal prices over time, with duration curves.
- **Curtailment** — curtailed renewable energy and share of potential.
- **Lost load** — unserved demand (VOLL slack) when the system can't meet load.
- **Storage cycling** — charge/discharge heatmaps and cycle counts.
- **Load flow** — line loading, voltages and **per-snapshot AC convergence**.

### 6. Compare scenarios

- Pick two saved projects and get a **full side-by-side comparison**: objective
  and solve time, total energy and peak demand, capacity (generators / storage
  / links), dispatch energy by carrier, economics, emissions, prices,
  curtailment, storage and lost load — each with deltas and per-period views.

### 7. Projects, import & export

- **Save / load project bundles** (the network, uploaded time series, solver
  config and metadata) so you can park a scenario and come back to it.
- **Import / export** networks as PyPSA **NetCDF**, **CSV**, or **MATPOWER**.
- Start from **built-in templates** or an empty network.

### 8. Quality-of-life

- **Undo** for destructive edits (with toast notifications) and an
  **audit/change log** of every modification.
- Crash-recovery banner, command palette, keyboard-friendly tables, and a
  persistent UI state (selection, panels, snapshot index) per project.

---

## How it interacts with PyPSA

Every GUI action is a real operation on a server-side `pypsa.Network`:

| In the GUI you… | …on the backend this does |
|---|---|
| Add/drag/edit an asset | `n.add(...)` / `n.remove(...)` on the live network (with type coercion, undo snapshot, audit log) |
| Upload a profile | writes into `n.<comp>_t.p_max_pu` / `p_set` and a persistent `user_ts` store |
| Set snapshots / periods | `n.set_snapshots(...)` / `n.set_investment_periods(...)` with MultiIndex rebuilds |
| Set per-vintage bounds | transient vintage rows expanded before `n.optimize()` |
| Press **Run** | `n.optimize()` (LOPF) in a worker thread, then optional `n.pf()` (AC PF) |
| Open a result tab | reads `n.generators_t.p`, `n.statistics()`, `buses_t.marginal_price`, … |
| Save a project | `n.export_to_netcdf()` + sidecar JSON; load re-imports it |

Because the GUI never re-implements the physics — it drives PyPSA directly —
the results you see are PyPSA's results.

---

## Architecture & implementation

```
┌──────────────────────────┐         HTTP / JSON / SSE        ┌───────────────────────────┐
│  Front end (Vite)        │  ───────────────────────────►   │  Backend (FastAPI)        │
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
  solver logs), `aiofiles`, `python-multipart`. The heavy science stack (PyPSA,
  pandas, linopy, solvers) comes from the repo-root **pixi** environment.
- **One live network behind a lock** (`services/pypsa_service.py`): writes take
  the lock, reads never do. All mutations go through three generic CRUD helpers
  in `routers/network.py` (`_create_component` / `_update_component` /
  `_delete_component`), so audit logging, undo snapshots, and time-series /
  vintage cleanup live in exactly one place.
- **Solving** (`services/solver_service.py`): runs `n.optimize()` in a thread,
  streams `[PHASE]` / `[CURT]` / `TRACEBACK:` lines over SSE, supports a
  curtailment-cost objective term, per-period **vintage expansion**, and an
  optional chained **AC power-flow** stage.
- **Supporting services**: validation, vintage bounds, change-log, undo,
  dispatch freshness, representative-period aggregation, carrier catalog.
- **Projects** are saved as bundles (`network.nc` + `user_ts.json` +
  `solver_config.json` + metadata) under `backend/projects/`.

### Front end — React + TypeScript

- **React 19 + TypeScript**, bundled with **Vite**.
- **TanStack React Query** for all server state (mutations spread the full
  cached object before `PUT`, so partial edits never reset other fields).
- **Zustand** for UI state (selection, panels, live solve status).
- **@xyflow/react** (React Flow) schematic canvas; **Leaflet** map view.
- **recharts** for charts; **@tanstack/react-table** + **react-virtual** for
  large asset tables; `react-hot-toast`, `lucide-react`, `react-router-dom`.

---

## Getting started

### Prerequisites

This GUI lives inside a **PyPSA-Eur** checkout and reuses its toolchain.

1. **pixi** environment at the repo root (PyPSA, pandas, linopy, a solver):
   ```bash
   pixi install          # from the repo root
   ```
2. **Backend web dependencies** (into the same pixi env):
   ```bash
   pixi run -- pip install -r pypsa-gui/backend/requirements.txt
   ```
3. **Node.js + front-end packages** (Node comes from the **pixi** env —
   there is no separate global `npm` required):
   ```bash
   # from the repo root
   pixi install
   cd pypsa-gui/frontend
   pixi run -- npm install
   ```
   If a bare `npm` says “command not found”, always prefix with `pixi run --`.
   Alternatively use the helper script (starts backend + frontend together):
   ```bash
   # from the repo root
   ./pypsa-gui/start.sh
   ```

### Optional: enable multi-user auth locally

Auth is **on by default on this branch** (`VITE_AUTH_ENABLED` treated as enabled
unless set to `false`). A fresh `npm run dev` shows `/login`. To force the
classic single-user workbench, put `VITE_AUTH_ENABLED=false` in
`frontend/.env.local`.

Backend auth remains opt-in via `PYPSA_GUI_AUTH_ENABLED` — when the API has
auth on, the SPA also upgrades itself at runtime from `/api/health` so a stale
browser session cannot stay stuck on the workbench toasting
“Authentication required”.

To exercise invited-user flows, org/project tenancy, edit locks, and password
emails locally, wire the backend to Postgres (or SQLite), point SMTP at Mailpit,
and keep frontend auth enabled.

#### 1) One-time stack setup

```bash
# Backend env (Postgres + SMTP + auth flag)
cp pypsa-gui/backend/.env.example pypsa-gui/backend/.env
# Optional quick path without Docker: edit DATABASE_URL to
#   sqlite+pysqlite:///./auth_dev.db

# Frontend: `frontend/.env.development` already sets VITE_AUTH_ENABLED=true
# for this branch. Override with frontend/.env.local if needed.

# Postgres + Mailpit (requires Docker on your machine) — skip if using SQLite
cd pypsa-gui
docker compose -f docker-compose.auth.yml up -d

# Schema (Postgres). For SQLite, bootstrap_super_admin creates tables automatically.
cd backend
pixi run alembic -c alembic.ini upgrade head   # Postgres only
```

Mailpit inbox UI: **http://localhost:8025**. Invite / set-password / reset emails
go to SMTP `:1025`. Links use `PUBLIC_BASE_URL` (default `http://localhost:5173`).

**Important:** after pulling this branch, fully restart `npm run dev` so Vite
reloads `.env.development`. A hard refresh alone will not enable the login page.

### Hard reset if the preview is stuck on the workbench

`frontend/index.html` is now the **static login page** (it does not load React).
The React workbench only boots from `spa.html` after a valid session cookie.

1. Stop any old Vite on your machine (`Ctrl+C` in that terminal).
2. From this branch (repo root), install once if needed, then start frontend:
   ```bash
   pixi install
   cd pypsa-gui/frontend
   pixi run -- npm install          # first time / after pull
   pixi run -- npm run dev
   ```
   Or start both servers: `./pypsa-gui/start.sh` from the repo root.
3. Open `http://localhost:5173/` (port **5173**, not 8000).
4. You must see a dark green badge: **“Auth gate · not the workbench”**.
5. Sign in: `admin@example.com` / your bootstrap password → `/projects`.

Verify locally anytime:

```bash
cd pypsa-gui/frontend
pixi run -- npm run test:auth-gate
```

That fails if `/`, `/app`, or `/projects` would still serve the React workbench
to anonymous users.

#### 2) Create the platform super-admin (CLI — no signup UI)

There is **no self-registration**. The first account is created with:

```bash
cd pypsa-gui/backend
pixi run python tools/bootstrap_super_admin.py \
  --email admin@example.com \
  --password 'your-secure-password'
```

This creates (or upgrades) an **active** `is_super_admin` user with that password.

##### Fastest path — SQLite, no Docker

For a local machine that only needs sign-in to work, skip Postgres and Mailpit
entirely. Append to `pypsa-gui/backend/.env` (**append** — that file also holds
`ANTHROPIC_API_KEY`; overwriting it drops the chatbot key):

```bash
PYPSA_GUI_AUTH_ENABLED=true
DATABASE_URL=sqlite+pysqlite:///./auth_dev.db
SECRET_KEY=local-dev-only-change-for-any-shared-deployment
```

Then seed the admin and **fully restart the backend** — `.env` is read once at
import, so `uvicorn --reload` does not pick it up:

```bash
cd pypsa-gui/backend
pixi run python tools/bootstrap_super_admin.py \
  --email admin@example.com --password 'admin-pass-123'
```

`auth_dev.db` is gitignored (`pypsa-gui/.gitignore`: `backend/*.db`). No
`alembic upgrade head` is needed — the bootstrap tool creates the tables.

Caveats with no SMTP server running: invite / reset **emails** fail, so create
users from the admin console (which surfaces the set-password link directly)
rather than relying on the mail. Start Mailpit with
`docker compose -f docker-compose.auth.yml up -d` if you want them delivered.

To go back to single-user, set `PYPSA_GUI_AUTH_ENABLED=false` and restart. The
SPA follows `/api/health` in both directions, so no frontend change is needed.
The **test suite** is unaffected either way — `tests/conftest.py` pins
single-user mode before importing `main`, and the auth suites opt back in
per-test.

#### 3) Start the app

```bash
# Terminal 1 — backend
cd pypsa-gui/backend
pixi run python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — frontend (npm comes from pixi — do not rely on a global npm)
cd pypsa-gui/frontend
pixi run -- npm run dev

# Or both at once from the repo root:
# ./pypsa-gui/start.sh
```

Open **http://localhost:5173** → you should see the split-brand **Sign in** page
(not the bare workbench).

#### 4) Browser walkthrough (landing → org → invite → member)

1. **Sign in** at `/login` as `admin@example.com`.
2. Open **Admin** (`/admin`). As super-admin:
   - **Organizations** → create e.g. `Acme Energy`.
   - **Users** → create a user with email + role (`admin` or `member`) + that org.
3. Open Mailpit (**http://localhost:8025**) → open the invite email → click the
   **set-password** link (`/set-password?token=…`).
4. Choose a password → you should land on **Projects home** (`/projects`).
5. Sign out; sign in as the invited user → `/projects` again.
6. Confirm a **member** cannot manage orgs (Admin is hidden / `/admin` forbidden).
7. Optional: create a project, open workbench (`/app`), assign members, verify
   edit lock (second browser = read-only).

#### 5) Automated checks

API journey (in-memory SQLite — no Docker required):

```bash
cd pypsa-gui/backend
pixi run python -m pytest -m auth_smoke
# Includes test_auth_journey_e2e.py: login → org → invite → set-password → member login
```

Live API helper against a running stack (prints the set-password URL for browser finish):

```bash
cd pypsa-gui/backend
pixi run python tools/auth_e2e_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --super-email admin@example.com \
  --super-password 'your-secure-password'
```

#### Known limitation (v1): process-global active network

Persistent tenancy data (users, orgs, projects, saved scenarios, edit locks) is
fully isolated per org/project in Postgres. However, the **in-memory active
network** — the working PyPSA network behind the live `/api/network` edit
endpoints — is **process-global** on a shared backend process. Full per-user
resident context isolation is out of scope for v1.

Practically, this means: when auth is enabled on a **single shared backend
process**, concurrent users all edit the *same* in-memory active network for
live `/api/network` operations. Edit locks reduce accidental collisions, but you
must **not** treat this as strong isolation for concurrent live edits.

Recommended v1 deployment:

- One trusted organization / low concurrency on a shared process, **or**
- A separate backend process per tenant (stronger isolation), which can later be
  formalized into per-user resident contexts.

Durable, per-project data (saved scenarios, results, tenancy) is unaffected by
this limitation — the caveat applies only to the single live in-memory network.

### Run it

**Windows (one click):** from `pypsa-gui/` run `start.bat` — it opens the
backend (`uvicorn` on `:8000`) and the front end (`vite` on `:5173`).

**Manually / cross-platform:**
```bash
# Terminal 1 — backend
cd pypsa-gui/backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

# Terminal 2 — front end
cd pypsa-gui/frontend
npm run dev
```

Open **http://localhost:5173**. API + interactive docs at
**http://localhost:8000** and **/docs**. Production build: `npm run build`.
With `VITE_AUTH_ENABLED=true`, the SPA routes through `/login`, `/projects`,
`/app`, and `/admin` instead of dropping straight into the single-user
workbench.

---

## A typical workflow

1. **Create or open a project** — template, empty network, or import a `.nc` /
   CSV / MATPOWER case.
2. **Build the network** — add buses (canvas/map), wire lines and conversion
   links, attach generators/storage/loads, set carriers, costs and bounds.
3. **Define time** — snapshots + weightings (optionally representative
   periods), and investment periods + per-vintage bounds for expansion.
4. **Configure the solver** — foresight, solver, VOLL, discount rate, CO₂,
   curtailment cost; click **Validate**.
5. **Run** — LOPF (optionally + AC PF); follow the live log.
6. **Analyse** — open the result tabs; filter by carrier / period; export.
7. **Save & compare** — save the scenario, tweak a copy, re-solve, and use
   **Compare** to see the deltas.

---

## Project & repository layout

```
pypsa-gui/
├── backend/                 # FastAPI service wrapping PyPSA
│   ├── main.py              # app + router registration + lifespan
│   ├── routers/             # network, clustering, vintage, snapshots,
│   │                        #   simulation (+ /api/results), projects, io, changelog
│   ├── services/            # pypsa_service, solver_service, validation,
│   │                        #   vintage, change_log, undo, dispatch_status, …
│   ├── models/schemas.py    # Pydantic request/response models
│   ├── project_templates/   # sample starter networks
│   └── requirements.txt     # web-tier deps (rest come from pixi)
├── frontend/                # React + TypeScript + Vite SPA
│   └── src/
│       ├── pages/           # canvas, map, panels, editors
│       │   └── results/     # Dispatch, CapacityExpansion, Economics, …
│       ├── components/ api/ store/ utils/
├── scripts/                 # helper + maintenance scripts
└── start.bat                # launches backend + frontend (Windows)
```

---

## API surface

All under `http://localhost:8000` (browse at `/docs`):

| Prefix | Purpose |
|---|---|
| `/api/network`     | Component CRUD (buses, lines, links, generators, storage, stores, loads, carriers), bulk edit, clustering, vintage bounds |
| `/api/projects`    | Save / load / list projects, snapshots & investment periods, compare-state, results-summary |
| `/api/simulation`  | Run LOPF / AC-PF, solver config, pre-flight validation, status, **SSE log stream** |
| `/api/results`     | Dispatch, cost breakdown, per-carrier economics, emissions, prices, curtailment, lost load, statistics, load flow |
| `/api/io`          | Import / export networks (NetCDF / CSV / MATPOWER) |
| `/api/changelog`   | Audit log of edits |

---

## Design notes & known constraints

- **Single active network**: the backend serves one in-memory network at a
  time; project bundles on disk handle persistence and switching.
- **Binary/user data is git-ignored**: `backend/projects/` (`network.nc`,
  `results_state.pkl`, uploaded TS), `node_modules`, `.nodeenv`.
- **OneDrive + pixi**: file locks in a OneDrive-synced folder can interfere
  with `uvicorn --reload` and pixi env relinking — a manual backend restart is
  the reliable workaround.
- Targets PyPSA's modern API (≥ 0.34) and multi-investment-period vintage
  modelling. See the in-repo `CLAUDE.md` for detailed engineering notes.

---

## Attribution & licence

PyPSA GUI is built on and distributed alongside
[**PyPSA-Eur**](https://github.com/PyPSA/pypsa-eur) and the
[**PyPSA**](https://github.com/PyPSA/PyPSA) framework by the PyPSA developers.
Please retain their upstream licences and citations. This `pypsa-gui`
subproject is an interactive layer on top of that toolchain.
