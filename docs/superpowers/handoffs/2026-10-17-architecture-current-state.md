# Architecture & Setup — Current State

**Compiled:** 2026-10-17, by reading the live repository on `master`
(tip `3f45c0e7`, PR #357).
**Supersedes:** root `GRIDSPINE_HANDOFF.md` (stale, increment-2 era) and
partially `docs/superpowers/handoffs/2026-09-05-gridspine-increment-3-handoff.md`
(accurate for gridspine increments 1–3, predates increments 4–10, the campus
work, and the assistant/Guided-mode expansion).
**Companion:** `docs/REPO-GUIDE.md` — the section map with per-section
last-changed dates.

---

## 1. What this repository is

A fork of **PyPSA-Eur** (upstream: `PyPSA/pypsa-eur`) carrying three bodies of
original work:

| Body | Location | What it is |
|---|---|---|
| **PyPSA GUI** | `pypsa-gui/` | A full no-code web workbench for PyPSA: visual network builder, solver runner, result explorer, multi-user auth, desktop app, in-app AI assistant |
| **gridspine** | `gridspine/` | Headless pipeline: PyPSA (capacity expansion / unit commitment) → pandapower (AC load flow, N-1/N-2 screening, IEC 60909 short circuit) → snapshot ranking → PSS/E `.raw`/`.dyr` handoff bundles for PowerFactory |
| **Upstream PyPSA-Eur** | `config/`, `rules/`, `scripts/`, `envs/`, `doc/` | Preserved in full; GUI and gridspine reuse its pixi toolchain |

GitHub Issues is **disabled** — `docs/superpowers/OPEN-ITEMS.md` is the defect
queue.

---

## 2. Environment, tooling, and the gates

Everything runs through **pixi** (`pixi.toml` at root). Bare `python`/`pytest`
resolves the wrong environment and proves nothing.

### Environments (one solve group)

`default`, `desktop`, `test`, `dev` share one solve group (`gui`) — identical
versions by construction, because the app that ships must be the app that was
tested. `doc` stays outside (`no-default-feature`, own pypsa 1.0.3).

### Critical pins (each with its documented reason in `pixi.toml`)

| Package | Pin | Why |
|---|---|---|
| `pypsa` | `==1.1.2` | Multi-port Link vintage workarounds scoped to this version |
| `linopy` | `==0.8.0` | LP modelling layer between PyPSA and the solver |
| `highspy` / `scip` | `==1.14.0` / `==10.0.2` | A solver move changes optimisation results |
| `atlite` | `==0.6.1` | Builds capacity factors — a direct optimisation input |
| `pandapower` | `==3.1.2` (PyPI) | conda-forge 3.x declares unsatisfiable scipy; exact pin keeps macOS/Windows aligned |
| `lightsim2grid` | `==0.10.1` (PyPI) | The version pandapower 3.1.2's `performance` extra names |
| `uvicorn` | `==0.51.0` | Desktop launcher shutdown verified against its internals |
| `pywebview` | `==6.2.1` | Download/shutdown behaviour reads non-public API |
| `tsam` | `==2.3.9` | Unpinned, it major-jumped on win-64 only |
| `pandas` | `>=2.1,<3` | pandas 3's default string dtype breaks xarray's netCDF writer — the GUI cannot save projects (measured: 484 failures) |

`gridspine` is installed **editable** from the repo root
(`gridspine = { path = ".", editable = true }`, inc-4 decision D5), so
`import gridspine` works from any working directory.

### The gates

| Command | What it gates |
|---|---|
| `pixi run gridspine-tests` | `tests/gridspine` (574 passed / 2 documented skips as of Sept 10) |
| `pixi run gui-tests` | The full backend suite — lives in `[feature.test.tasks]` so it resolves to the `test` env (only env with `pywebview`) |
| `pixi run gui-qa-drivers` | The 19 `qa_*.py` PASS/FAIL scripts pytest never collects |
| `pixi run unit-tests` / `integration-tests` | Upstream PyPSA-Eur (snakemake) |
| `pixi run -e desktop desktop-gui` | Launch the desktop shell (`-e desktop` is load-bearing) |

### CI (`.github/workflows/`)

`test.yaml` (Unit; Integration ×2; Gridspine; GUI backend — sharded across
four jobs since PR #192), `codeql.yaml`, `security-scan.yaml` (grype),
`desktop-macos.yaml`, `release.yaml`, `validate.yaml`, assistant
inventory/control-matrix checks.

**Untrusted signals** (OPEN-ITEMS #11, open): the Gridspine and both
Integration jobs are path-filtered and report green while skipping
everything; `cancel-in-progress` can kill the GUI-backend verdict mid-flight.
**The local failing-set comparison is the primary gate.** The local suite
carries 124 pre-existing `No module named 'gridspine'` failures outside the
pixi env.

---

## 3. pypsa-gui — the web workbench

Two processes: FastAPI backend wrapping a live `pypsa.Network`; React 19 +
TypeScript + Vite SPA. `./pypsa-gui/start.sh` (or `start.bat`): backend :8000,
frontend :5173.

```
React SPA :5173  ──HTTP/JSON/SSE──►  FastAPI :8000  ──►  pypsa.Network (in-memory)
                                        │
                                        ├─ PostgreSQL or SQLite (tenancy, projects, jobs)
                                        └─ project directories on disk (network.nc + sidecars)
```

**Core principle:** every GUI action is a real operation on the server-side
network. The GUI never re-implements physics.

### Backend (`pypsa-gui/backend/`)

- `main.py` — app assembly, router registration, lifespan, CSRF double-submit.
- `routers/` (35 modules) — thin HTTP: auth/`Depends`, then a service call.
  Families: network editing (`network` with the three generic CRUD helpers,
  `network_bulk`, `network_profiles`, `network_time_axis`, `clustering`,
  `vintage`); projects & persistence (`projects`, `project_network`,
  `snapshots`, `io`, `uploads`, `changelog`, `compare`); solving & results
  (`simulation`, `solve_queue`, `results`, `asset_results`); studies
  (`studies`, `adequacy_worksheet`, `campus_electrical`, `gridspine`,
  `guides`, `library`, `reports`); assistant (`chat`, `durable_conversation`,
  `assistant_activity`, `dictation`, `mcp`); platform (`auth`, `admin`,
  `local_settings`, `geocode`).
- `services/` (~90 modules + 9 packages) — the business logic:
  - `pypsa_service.py` — one live network behind a lock; writes take it
  - `solver_service.py` + `solver/` — `n.optimize()` off the request thread,
    SSE phase-marked logs, curtailment cost, vintage expansion, optional
    chained AC power flow
  - `solve_queue.py` — job queue (`queued|running|completed|failed|aborted|
    interrupted`); two job kinds: `solve` and `gridspine` (inc-4, D2)
  - `gridspine_service.py` — the action layer: plain functions called by BOTH
    the router and the chat tools (structural parity)
  - project/context machinery: `project_context`, `active_project`,
    `project_registry`, `storage_paths`; project directories hold
    `network.nc`, `solver_config.json`, `results_state.pkl`, `user_ts.json`,
    `chat.jsonl`, `sites.json`, `studies/`, `experiments/`, `run_evidence/`,
    `gridspine/`
  - domain packages: `adequacy/`, `commercial/`, `finance/`, `library/`,
    `reports/`, `results/`, `study/`, `asset_results/`, `asset_schema/`
  - tenancy: `auth_service`, `tenancy_service`, `project_acl`,
    `project_locks` (edit locks), `email_service`
- `db/` + `alembic/` — SQLAlchemy models (User, Org, OrgMembership, Project
  with `project_kind`, SolveJobRow, ChatSession with `owner_user_id`, …).
- `desktop/` — pywebview shell (§6 of REPO-GUIDE).

### Deployment modes

1. **Single-user workbench** — no login (`PYPSA_GUI_AUTH_ENABLED=false`).
2. **Multi-user server** — orgs, memberships, SMTP invites (Mailpit in dev),
   Postgres or SQLite. Static `index.html` login page; the SPA boots from
   `spa.html` only after a valid session cookie (`npm run test:auth-gate`
   pins this).
3. **Desktop local mode** — `PYPSAGUI_LOCAL_MODE=1` before `import main`;
   fixed seeded org/user UUIDs; absolute SQLite `DATABASE_URL`; prefix is
   `PYPSAGUI_` (PyPSA claims the `PYPSA_*` env namespace).

**Known v1 limitation:** the live network is process-global — concurrent users
on a shared backend edit the same network. Edit locks mitigate; strong
isolation needs one backend process per tenant.

### Frontend (`pypsa-gui/frontend/src/`)

React 19, TS, Vite; React Query + Zustand; React Flow + Leaflet; recharts;
react-table + react-virtual.

- `pages/` — TopologyCanvas, MapCanvas (haversine line lengths on drag),
  Results + `results/` (Dispatch, CapacityExpansion, Economics, Emissions,
  Prices, Curtailment, LostLoad, Storage, LoadFlow), CompareView,
  GridspinePanel, CampusElectricalPanel + island/grid-code/investment/library
  sections, SolveQueuePanel, SolverSettings, SnapshotsPanel, ModelHorizon,
  ReportsPanel, SiteCanvas (3D), ProjectsHomePage, `decision/`, `hubDesign/`,
  `admin/`, `auth/`
- `api/` (~45 modules) — typed clients; `client.ts` is CSRF-aware
- `site3d/` — asset library, packer (placement rules), placement check
  (advisory findings), per-timestep results gated on dispatch freshness
- `utils/assetWrite.ts` — the ONLY sanctioned asset-write idiom
  (fetch → spread full row → PUT → invalidate); the backend's remove+add
  resets omitted fields to defaults

### Domain vocabulary

`pypsa-gui/CONTEXT.md` settles every overloaded term (Project vs Scenario vs
Context; "study" names three things — decision study / gridspine study /
adequacy studies; Snapshot has three meanings; Site ≠ campus study;
Unavailable ≠ zero per ADR-0001). Read it before writing specs or UI copy.

---

## 4. gridspine — the planning→dynamics pipeline

### The chain

```
case39 ingest ──► PyPSA nodal UC (rolling horizon, 168h/24h, 1% MIP gap)
     │                    │
     │              dispatch.csv + loads.csv   (validated stage contract)
     │                    ▼
     │         metrics & ranking (5 criteria) → selected critical hours
     │                    ▼
     │         pandapower AC load flow per hour (non-convergence is a RESULT)
     │                    ▼
     │         AC N-1 (lightsim2grid) + DC LODF N-2 screen
     │         IEC 60909 fault levels + SCR
     │                    ▼
     │         bundle per hour: .raw v33 + .dyr + contingencies.csv + ledger
     └────────────────► PowerFactory (read-back; manual validation step)
```

### Modules and the engine cage (test-enforced)

| Module | May import | Contents |
|---|---|---|
| `schema/` | neither engine | `validate_dispatch`, `validate_loads`, `validate_contingency_*`, `validate_fault_levels`, DC sensitivities (.npz), `ContractError`, `StageError` |
| `ingest/` | pandapower | `load_case39`, `load_case39_res`, registry, synthetic year profiles, ledgers |
| `templates/` | neither | Per-field-provenance unit params; `grid_codes.yaml` (EU RfG/DCC limits with clause refs) |
| `producers/` | **pypsa only** | `to_pypsa`, `run_uc`, `run_uc_rolling`, `to_dispatch_table`, `to_loads_table` |
| `static/` | pandapower, lightsim2grid | `apply_snapshot`, `run_lf`, contingency set, `screen_n1`, `n1_severity_ac`, `screen_n2`, LODF, `shortcircuit.fault_levels`, `strength.scr`, `connection.py` (inc-10) |
| `ranking/` | neither | `snapshot_metrics`, `select_snapshots` (5 criteria; k..5k, boundary-tie spreading), `n1_severity_dc` |
| `handoff/` | pandapower | `write_raw` (12 verified deviations), `write_dyr` (reuses RAW numbering), `write_contingencies`, ledger, `export_bundle` |
| `readback/` | neither | `compare_lf`, `compare_branch_flows` vs PowerFactory CSVs |
| `drivers/` | orchestration only | `planning`, `year_study`, `study` (job-shaped: config/progress/abort), `status`, `connection` |

Canonical names cross every stage boundary; positional indexes never do.

### Increments landed

| Inc | Delivered |
|---|---|
| 1 | case39 vertical slice: ingest → UC → LF → `.raw` → PowerFactory harness |
| 2 | Synthetic profiles, RES fixture, rolling UC, loads artifact, ranking, branch read-back, year-study driver |
| 3 | AC N-1, DC LODF N-2, IEC 60909 + SCR, `.dyr`, bundles; full-year v3 run (23 hours) and v4 re-study after the lightsim2grid in-service bug (ruling 31) |
| 4 | GUI integration: action layer, queue job kind, `/api/gridspine`, 10 chat tools, GridspinePanel, config editing, `project_kind` |
| 5 | Packaging: root `pyproject.toml`, editable install |
| 6–7 | Follow-ons (`docs/superpowers/plans/`) |
| 8 | REGCA1/REECA1 records in the `.dyr` |
| 9 | Hosting-capacity assessment (MW a bus can take) |
| 10 | Connection-point assessment: facility trip, 3% RVC, DCC ±0.48 Q/Pmax reactive range, SCR at POC; limits verified against EU RfG/DCC text with clause refs |

### Hard-won rulings (abridged; full list in the inc-2/inc-3 handoffs)

- `mip_rel_gap=0.01` on rolling solves is mandatory; constraints stay hard at
  any gap.
- HiGHS returns 0.9999999 for committed units — statuses are rounded first.
- lightsim2grid 0.10.1 drops `gen.in_service` when slack comes from `ext_grid`
  (ruling 31); `gridmodel_for` re-applies the flags. Every engine-agreement
  test needs a case with a unit off.
- The DC N-1 severity proxy is anticorrelated with AC severity (rho −0.57):
  a voltage term DC cannot see. `max_n1_severity` ranks on the AC number.
- Never `pp.from_json`/`from_pickle` client files (RCE); canonical IDs are
  `[A-Za-z0-9_-]` via `fullmatch`.
- A green negative test proves nothing — mutate and watch it go red.

---

## 5. The chat harness (`pypsa-gui/backend/harness/`)

Everything the in-app assistant needs that is not a language model; providers
plug in underneath. `harness/README.md` is the contract.

- `protocol.py` — the provider seam (LLMProvider, LLMRequest, LLMEvent,
  ERROR_KINDS)
- `catalogue.py` / `toolsets.py` — tools, Safety tiers, TOOL_ROUTES
- `loop.py`, `session.py`, `history.py`, `sse.py` — the turn loop
- `eventlog.py` — append-only record of everything the model saw; the
  conversation's source of truth (`chat.jsonl` is its projection)
- `confirm.py`, `approvals.py`, `access.py`, `budget.py`, `ratelimit.py` —
  gating; approvals bound to fixed actions with complete previews (PR #357)
- `assurance*.py`, `calculations.py`, `publication.py` — checked
  answers/drafts, typed scopes, evidence, publication
- `experiment_*.py` — typed experiment plans, execution, reconciliation,
  comparison (PRs #351–355)
- `goals.py` — multi-round autonomous objectives (every change still needs
  its card)
- `workflows/` — Markdown-defined Guided flows; `skills/` — on-demand
  SKILL.md procedures
- `mcp_client.py` — admin-listed external MCP tools in; `mcp_server.py` —
  `/api/mcp` for local external agents (every write awaits human approval)

**Conventions (test-enforced):** `Safety:` tier + `TOOL_ROUTES` row +
`DISPATCHERS` entry per tool; `len(TOOLS) == len(DISPATCHERS)`; `_h` import
alias; `gridspine_*` gated to `planning_dynamics` projects. **ADR-0002:**
chat-tool changes need a live API probe — the gridspine tools' probe is still
outstanding.

---

## 6. How the pieces interconnect

```
                    ┌──────────────────────────────────────────┐
                    │            React SPA (:5173)             │
                    └───────────────┬──────────────────────────┘
                                    │  /api/* (JSON, SSE)
    ┌───────────────────────────────┼────────────────────────────┐
    │                               ▼                            │
    │  ROUTERS (35) ── thin: auth, Depends, then a service call  │
    │                               ▼                            │
    │  SERVICES (~90)                                            │
    │   pypsa_service (live network + lock)                      │
    │   solver_service ──► solve_queue (kinds: solve|gridspine)  │
    │   gridspine_service ─── action layer: one function per     │
    │   campus_*/adequacy/   action, called by router AND chat   │
    │   study/finance/…                   │                      │
    │   harness/ (turn loop, tools, confirmation, eventlog)      │
    │     │ provider seam      │ gridspine_* tools               │
    │     ▼                    ▼                                 │
    │   LLM providers    gridspine.drivers / gridspine.schema    │
    │   (Claude/OpenAI/  (ONLY those two — cage-tested)          │
    │    Kimi/fake)            ▼                                 │
    │              engines: PyPSA ─ pandapower ─ lightsim2grid   │
    │              ─ PSS/E .raw/.dyr ─► PowerFactory             │
    │                                                            │
    │  Persistence: Postgres/SQLite + per-project directories    │
    └────────────────────────────────────────────────────────────┘
```

The five contracts:

1. **GUI ↔ PyPSA** — direct manipulation of one locked in-memory network;
   bundles on disk. v1: process-global live network.
2. **GUI ↔ gridspine** — backend imports only `gridspine.drivers` and
   `gridspine.schema` (cage-tested both directions); a gridspine study is a
   Project with `project_kind = planning_dynamics` (D1); runs go through the
   same queue as job kind `gridspine` (D2); template edits are a per-project
   JSON overlay (D4).
3. **Assistant ↔ everything** — chat tools call the same service functions
   the routers wrap; writes produce confirmation cards bound to fixed
   actions; external agents via `/api/mcp` under the same approval regime.
4. **Site ↔ campus study** — Site is drawn geometry (`sites.json`); campus
   study runs electrical analysis on the Site's member buses; neither holds
   the other's data.
5. **Expert ↔ Guided** — two faces over one investment engine (ADR-0004).

---

## 7. Current state and open items (2026-10-17)

- **Master tip:** `3f45c0e7` (PR #357 — chat approvals bound to fixed actions
  with complete change previews). Active work: Guided-mode UX sweep,
  assistant experiment machinery, test coverage.
- **Open items:** nothing High. Low: #9 (refused `/stream` still switches the
  session model), #10 (cookie policy hardcoded to a preview vendor hostname),
  #15 (network CSV/Excel exports don't guard formula-named components).
  Standing: #11 (untrusted CI signals — the local failing-set comparison is
  the primary gate).
- **Outstanding human steps:**
  1. **PowerFactory validation of a v4 gridspine bundle** (`.raw` + `.dyr` +
     `contingencies.csv` + `ledger.md`) — the <1% gate has never been closed
     against an independent oracle.
  2. **ADR-0002 live probe of the ten `gridspine_*` chat tools** (create →
     run → status → snapshots → ledger → template edit → export, through the
     chat panel, transcript recorded in the plan).
  3. Owner modelling decisions (inc-3 handoff §6): the committable
     interconnection equivalent, inverter reactive control (PQ vs PV/Q(V)),
     SCR case choice, islanded-outage semantics, decorrelating `wind_cf`
     across the three farms.

---

## 8. Working conventions

- TDD with RED/GREEN evidence and a mutation check per task; path-limited
  commits; measure-don't-assume; the 32 rulings are binding unless
  deliberately revisited.
- Release notes: new file under `doc/release-notes.d/`; never edit
  `doc/release_notes.md`.
- Vocabulary disputes are settled in `pypsa-gui/CONTEXT.md`.
- Secrets: never commit; Moonshot/Kimi key as `MOONSHOT_API_KEY`
  (`KIMI_KPI_KEY` is aliased); the chat panel needs `ANTHROPIC_API_KEY`.
