# AGENTS.md

## Cursor Cloud specific instructions

### What this repo is
This is a **PyPSA-Eur** fork. The primary product is **`pypsa-gui/`** — a FastAPI
backend (`pypsa-gui/backend`, uvicorn on `:8000`) plus a React 19 + Vite SPA
(`pypsa-gui/frontend`, dev server on `:5173`). The upstream PyPSA-Eur Snakemake
model (`scripts/`, `rules/`, `config/`, `Snakefile`) sits underneath; `gui_streamlit/`
is legacy and frozen. When a task is ambiguous, assume `pypsa-gui`.

### Toolchain (already installed by the update script)
- The whole science + web stack and Node come from the repo-root **pixi** env
  (`pixi.toml` / `pixi.lock`). `pixi` lives at `~/.pixi/bin/pixi` (not on the
  default non-interactive `PATH`). Prefer full path or an interactive login shell.
- Environments: `default` (run the app), `test` (adds `pywebview`; required for
  `pixi run gui-tests`). Node/npm come from the pixi env — do not rely on a global npm.
- The backend needs **no separate `pip install`**: every package in
  `pypsa-gui/backend/requirements.txt` (fastapi, anthropic, sqlalchemy, pwdlib, …)
  is already in the pixi `default` env.

### Running the app (two modes; the frontend follows the backend)
The Vite auth gate decides what to serve by polling backend `GET /api/health`
(`auth_enabled`), **not** just `VITE_AUTH_ENABLED`. So flipping the backend mode
flips the UI automatically.

Quickest path — **single-user local mode** (no login, project creation works out
of the box because it seeds an org/user/schema on startup):
```
# backend — env vars MUST be set BEFORE launch (settings are lru_cached at import)
PYPSAGUI_LOCAL_MODE=1 MPLBACKEND=Agg \
  ~/.pixi/bin/pixi run -e default python -m uvicorn main:app --host 127.0.0.1 --port 8000
  # (run from pypsa-gui/backend)

# frontend (from pypsa-gui/frontend) — bind 0.0.0.0 for cloud preview
~/.pixi/bin/pixi run -e default npm run dev -- --host 0.0.0.0
```
Then open `http://localhost:5173/` → it lands directly in the workbench.

- `MPLBACKEND=Agg` is **required**: matplotlib otherwise picks a GUI backend and
  crashes when charts render off the main thread.
- The boot line `Unknown option 'gui_auth_enabled' from env var 'PYPSA_GUI_AUTH_ENABLED'`
  is **harmless** (PyPSA claims the whole `PYPSA_*` option namespace).

Multi-user **auth mode** is the default `npm run dev` experience (see
`pypsa-gui/README.md`): create `pypsa-gui/backend/.env` (gitignored) with
`PYPSA_GUI_AUTH_ENABLED=true`, a SQLite `DATABASE_URL` (no Docker needed), and a
`SECRET_KEY`, then bootstrap an admin with `tools/bootstrap_super_admin.py`.
Caveat: the bootstrapped super-admin is **not** auto-added to an org, so creating
projects requires the org/invite flow — local mode above is the simpler single-user path.

### Templates (needed for the "From template" flow)
Bundled starter networks ship without their `network.nc`; build them once with:
```
~/.pixi/bin/pixi run -e default python pypsa-gui/backend/project_templates/_build.py
```
This creates `3bus` and `ieee14` (both solve out of the box). `belgium` is skipped
unless the PyPSA-Eur workflow output `resources/test-elec/networks/base_s_5_elec_.nc`
exists. These `.nc` files are generated artifacts and are intentionally not committed.

### Tests / build
- Backend: `pixi run gui-tests` (from repo root; resolves to the `test` env, cwd
  `pypsa-gui/backend`; ~7 min). **Known:** the 2 failures in
  `tests/test_app_paths.py` (`..._keeps_its_data_after_the_rename`,
  `test_the_new_location_wins_once_it_exists`) are **macOS-only** — they assert
  `~/Library/Application Support/...` paths and fail by design on Linux, where
  `app_data_dir()` correctly returns the XDG `~/.local/share/PyPSA Studio`. Not a regression.
- Frontend (from `pypsa-gui/frontend`): `npm run test` (vitest) and
  `npm run build` (`tsc -b && vite build`). Per the frontend rule, run `npm run build`
  after non-trivial TS changes.
