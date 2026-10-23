# Repository Guide — what lives where, and when it last changed

**Compiled 2026-10-17** against `master` @ `3f45c0e7` (PR #357). Every "last
updated" date below is the newest commit touching that section's paths,
read from git history — not from the section's own documentation.

This fork is upstream **PyPSA-Eur** plus two original bodies of work —
**PyPSA GUI** (a no-code web workbench for PyPSA) and **gridspine** (a
planning→dynamics pipeline) — plus the paper trail that the agent-driven
development process produces. GitHub Issues is disabled; the defect queue is
[`docs/superpowers/OPEN-ITEMS.md`](superpowers/OPEN-ITEMS.md).

For the deep architecture document (module maps, interconnection contracts,
rulings): [`docs/superpowers/handoffs/2026-10-17-architecture-current-state.md`](superpowers/handoffs/2026-10-17-architecture-current-state.md).

---

## Sections at a glance

| # | Section | Paths | Last updated (git) |
|---|---|---|---|
| 1 | GUI backend (FastAPI) | `pypsa-gui/backend/` | **2026-10-17** — PR #357, chat approvals bound to fixed actions |
| 2 | GUI frontend (React SPA) | `pypsa-gui/frontend/` | **2026-10-17** — PR #357 |
| 3 | Chat harness (assistant) | `pypsa-gui/backend/harness/` | **2026-10-17** — PR #357 |
| 4 | gridspine pipeline | `gridspine/`, `tests/gridspine/` | **2026-10-16** — `b8aabc09` (GFM report JSON fix, campus era) |
| 5 | Campus electrical studies | `backend/routers/campus_electrical.py`, `backend/services/campus_*`, `frontend/src/pages/Campus*` | **2026-10-16** (via gridspine) |
| 6 | Desktop app | `pypsa-gui/backend/desktop/` | **2026-09-29** — `d6539133` (shutdown/eviction user_ts fix) |
| 7 | Upstream PyPSA-Eur workflow | `config/`, `rules/`, `scripts/`, `envs/`, `doc/` | **2026-06-30** — upstream PR #2189 (data-version files in config) |
| 8 | Agent coordination infra | `scripts/coord/`, `templates/agent-coordination/`, `.claude/skills/` | **2026-10-15** — `c04fd832` (coordinator bootstrap template) |
| 9 | CI & environments | `.github/workflows/`, `pixi.toml` | **2026-10-13** — PR #192 (GUI backend suite sharded ×4) |
| 10 | Process paper trail | `docs/superpowers/` | **2026-10-17** — updated continuously |

---

## 1. GUI backend — `pypsa-gui/backend/`

FastAPI service wrapping a live, lock-guarded `pypsa.Network`. Serves :8000.

**Where to find what:**

| You want… | Look at |
|---|---|
| HTTP endpoints | `routers/` (35 modules; `network`, `projects`, `simulation`, `solve_queue`, `results`, `gridspine`, `campus_electrical`, `studies`, `chat`, `auth`, `admin`, …) |
| Business logic | `services/` (~90 modules; `pypsa_service` = the live network, `solver_service`, `solve_queue`, `gridspine_service` = the action layer) |
| Data model / migrations | `db/models.py`, `alembic/` |
| Request/response schemas | `models/schemas.py` |
| Desktop shell | `desktop/` (see §6) |
| Backend tests | `tests/` (run with `pixi run gui-tests` — **never** bare pytest) |
| Bootstrapping (admin user, local mode) | `tools/bootstrap_super_admin.py`, `tools/bootstrap_local.py` |

**Know before you touch it:** one process-global live network (v1);
`Asset write` corruption class → always the fetch-spread-PUT idiom;
auth modes and env vars in `pypsa-gui/README.md`; vocabulary in
`pypsa-gui/CONTEXT.md`.

## 2. GUI frontend — `pypsa-gui/frontend/`

React 19 + TypeScript + Vite SPA on :5173. React Query (server state),
Zustand (UI state), React Flow (schematic canvas), Leaflet (map), recharts.

**Where to find what:**

| You want… | Look at |
|---|---|
| Pages & panels | `src/pages/` — TopologyCanvas, MapCanvas, Results(+`results/`), CompareView, GridspinePanel, Campus* sections, SolveQueuePanel, SolverSettings, SiteCanvas (3D), `decision/`, `hubDesign/`, `admin/`, `auth/` |
| Typed API clients | `src/api/` (~45 modules, one per backend family; `client.ts` is CSRF-aware) |
| The asset-write idiom | `src/utils/assetWrite.ts` (the only sanctioned way) |
| 3D site view | `src/site3d/` (asset library, packer, placement check, freshness gating) |
| Login gate | `index.html` is the static login page; the SPA boots from `spa.html` (`npm run test:auth-gate` pins this) |

## 3. Chat harness — `pypsa-gui/backend/harness/`

Everything the in-app assistant needs that is not a language model.
**`harness/README.md` is the contract — read it first.** Providers (Claude,
OpenAI-compatible, Kimi, fake) plug into `protocol.py`'s seam.

**Where to find what:**

| You want… | Look at |
|---|---|
| Provider seam | `protocol.py` (LLMProvider, LLMRequest, LLMEvent, ERROR_KINDS) |
| Tool catalogue, safety tiers, routes | `catalogue.py`, `toolsets.py` |
| Turn loop, sessions, history | `loop.py`, `session.py`, `history.py`, `sse.py` |
| Conversation source of truth | `eventlog.py` (`chat.jsonl` is only its projection) |
| Write/run gating | `confirm.py`, `approvals.py`, `access.py`, `budget.py` |
| Checked answers / evidence | `assurance*.py`, `calculations.py`, `publication.py`, `run_evidence` (service) |
| Experiments | `experiment_*.py` (typed plans, execution, reconciliation, comparison) |
| Guided flows | `workflows/` (one Markdown file per flow) |
| On-demand procedures | `skills/` (SKILL.md layout) |
| External tools / agents | `mcp_client.py` (admin-listed MCP servers in), `mcp_server.py` (`/api/mcp` for local external agents) |

**Conventions, test-enforced:** every tool has a `Safety:` tier, a
`TOOL_ROUTES` row, a `DISPATCHERS` entry; `_h` import alias; `gridspine_*`
tools gated to `planning_dynamics` projects. **ADR-0002: chat-tool changes
need a live API probe.** The ten `gridspine_*` tools' probe is still
outstanding (unverified against a live model).

## 4. gridspine — `gridspine/`

Headless pipeline: PyPSA (UC/expansion) → pandapower (AC LF, N-1/N-2,
IEC 60909) → snapshot ranking → PSS/E `.raw`/`.dyr` bundles for PowerFactory.
Installed editable (`pyproject.toml`, inc-5); gate: `pixi run gridspine-tests`.

**Where to find what:**

| You want… | Look at |
|---|---|
| Stage contracts / validators | `gridspine/schema/` (no engine imports) |
| case39 ingest + synthetic profiles | `gridspine/ingest/` (pandapower allowed) |
| Unit & grid-code parameters (provenance-tagged) | `gridspine/templates/`, `templates/data/grid_codes.yaml` |
| PyPSA build + rolling UC | `gridspine/producers/` (the only module importing pypsa) |
| Load flow, N-1/N-2 screens, fault levels, SCR, connection assessment | `gridspine/static/` (pandapower + lightsim2grid) |
| Critical-hour ranking (5 criteria) | `gridspine/ranking/` (numpy/pandas only) |
| PSS/E writers + bundles + ledger | `gridspine/handoff/` |
| PowerFactory comparison | `gridspine/readback/` |
| Study drivers (job-shaped entry, status, year study, connection) | `gridspine/drivers/` |

**Status:** increments 1–10 landed (through hosting-capacity and
connection-point assessment, Sept 29). Rulings 1–32 in the handoffs are
binding. **Open owner step: PowerFactory validation of a v4 bundle.**

## 5. Campus electrical studies

gridspine-style electrical analysis (transformer, compensation, short
circuit, grid code, islanding, GFM) of a solved hub project's **Site** buses.
A Site is drawn geometry (`sites.json`); a campus study holds no geometry.

**Where:** backend `routers/campus_electrical.py` + `services/campus_*`;
frontend `src/pages/Campus*`; chat tools `campus_*` (write tools check the
edit lock of the project they name — OPEN-ITEMS 18 class, closed).

## 6. Desktop app — `pypsa-gui/backend/desktop/`

pywebview shell over the same backend, no login. Local mode env vars
(`PYPSAGUI_LOCAL_MODE=1` etc.) **before** `import main`. Launch:
`pixi run -e desktop desktop-gui` (the `-e` is load-bearing). Frozen builds
use a separate pip-wheel venv, not PyInstaller on the conda env.

## 7. Upstream PyPSA-Eur — `config/ rules/ scripts/ envs/ doc/`

The open European energy-system model (Snakemake workflow), preserved in
full. `upstream` remote is the public PyPSA project — **never push there**.
Upstream docs: https://pypsa-eur.readthedocs.io/. Release notes: new file
under `doc/release-notes.d/`, never edit `doc/release_notes.md`.

## 8. Agent coordination infra

`scripts/coord/` (coordinator tooling), `templates/agent-coordination/`
(project bootstrap template), `.claude/skills/` (vendored skills + steward),
`AGENTS.md` (working rules), `docs/agents/` (issue tracker, triage, domain
docs). `scripts/coord/premerge.sh` blocks template/skills drift.

## 9. CI & environments

- **pixi** runs everything; `default`/`desktop`/`test`/`dev` share one solve
  group so the shipped app is the tested app. Critical pins (`pypsa==1.1.2`,
  `linopy==0.8.0`, `highspy==1.14.0`, `pandapower==3.1.2`,
  `lightsim2grid==0.10.1`, `uvicorn==0.51.0`, `pywebview==6.2.1`,
  `pandas>=2.1,<3`, …) each carry their reason in `pixi.toml` — bump
  deliberately.
- **Gates:** `pixi run gridspine-tests`, `pixi run gui-tests` (test env),
  `pixi run gui-qa-drivers`, `pixi run unit-tests` / `integration-tests`.
- **Workflows:** `.github/workflows/` — `test.yaml` (Unit, Integration ×2,
  Gridspine, GUI backend ×4 shards), `codeql.yaml`, `security-scan.yaml`,
  `desktop-macos.yaml`, `release.yaml`, `validate.yaml`.
- **Untrusted signals** (OPEN-ITEMS #11): the path-filtered Gridspine and
  Integration jobs read green while running nothing; `cancel-in-progress`
  can kill the GUI-backend verdict mid-flight. **The local failing-set
  comparison is the primary gate.**

## 10. Process paper trail — `docs/superpowers/`

| You want… | Look at |
|---|---|
| Implementation plans (~130, Jul 25 → Oct 14) | `plans/` |
| Session continuation documents | `handoffs/`, `handovers/` |
| Defect/decision analyses (status lines truthful) | `findings/` |
| The defect queue (Issues is disabled) | `OPEN-ITEMS.md` |
| Design specs | `specs/` |

Conventions: TDD with RED/GREEN + mutation evidence; path-limited commits;
measure-don't-assume; closed items keep their numbers forever.

---

*To update this guide: refresh the "last updated" column from
`git log -1 --format=%cs -- <path>` per section, and re-read the section's
own README/CONTEXT before changing what it says about itself.*
