# Desktop acceptance run-book — macOS

**What this verifies:** that the desktop shell launches, serves the SPA, saves,
quits without losing work, and refuses a second instance — on *your* machine
rather than the one it was developed on.

**What this does NOT verify:** a packaged app. Nothing is frozen yet — no
`.app`, no `.dmg`, no single executable. Workstreams I and J are untouched. What
you are running is the shell out of the source tree, in the pixi `desktop`
environment. See §7.

**Status when written (2026-07-30):** A1–A3, B1 and B2 already passed on macOS
arm64 in the development session. They are here so you can reproduce them as a
regression gate. **C1–C6 have never been run by anyone** — they need a human to
look at a window and click things. C3 in particular (a real save panel) was
only ever driven through `evaluate_js`, never with a mouse.

---

## 0. Hard rules

These are not style preferences. Each one is here because it was violated once.

1. **Set all THREE isolation variables, always** — and never point any of them
   at `~/Documents`.

   - `PYPSAGUI_APP_DATA_DIR` moves the database *default*, the log, the lock
     and the *flat* store.
   - `PYPSAGUI_PROJECTS_ROOT` governs projects. An earlier harness set only the
     first and wrote seven throwaway projects into the developer's real
     `~/Documents/PyPSA GUI/Projects`.
   - `DATABASE_URL` — **the one this run-book used to omit.** `settings.py`
     declares `env_file=backend/.env`, and pydantic-settings ranks the env file
     **above** the `default_factory` that reads `PYPSAGUI_APP_DATA_DIR`. So the
     cwd-relative `DATABASE_URL` in `backend/.env` wins, and an otherwise
     isolated harness writes its auth database next to wherever it was
     launched. That produced a stray `auth_dev.db` at the repo root — a file on
     the credential gate's own forbidden list, because it carries a password
     hash.

   `backend/smoke/isolation.py` is the single guard; all three harnesses call
   it and refuse to start unless every variable is set and disposable. It also
   refuses bare `PROJECTS_ROOT` / `FLAT_PROJECTS_ROOT` / `LEGACY_ROOT`, which
   pydantic binds *above* the `PYPSAGUI_*` override.
2. **Never write to or delete under `pypsa-gui/backend/projects/`.** 113 MB of
   irreplaceable projects, inside a gitignored directory inside a git checkout —
   `git clean -xdf` would take it silently.
3. **`POST /api/projects/{name}` is a destructive save**, not a load. It
   serialises the current in-memory network over whatever is at that name.
4. **`npm run build` before any frontend check.** The desktop app serves the
   *built* SPA from `frontend/dist/`, which is gitignored. A stale `dist/` once
   produced a run where the feature under test fired nothing, with no error.

---

## 1. Preconditions

```bash
cd "<repo>"                       # the pypsa-eur checkout
git branch --show-current         # expect: feature/local-app-impl
pixi --version                    # must be new enough for pixi.lock format v7
pixi install
```

If `pixi install` wants to re-solve, **stop** — the lockfile is pinned (pypsa
1.1.2 across one solve group) and re-solving it is a separate decision. Upgrade
pixi rather than regenerating the lock.


**node and npm come from pixi, not the system.** `nodejs >=22` is a pixi
dependency and there is no separate nodeenv (CLAUDE.md), so a bare `npm` is
`command not found` on a box provisioned with pixi alone — measured, while
writing `build-macos.sh`. Put the environment's bin directory on PATH first:

```bash
export PATH="$PWD/.pixi/envs/default/bin:$PATH"
cd pypsa-gui/frontend && npm install && npm run build && cd ../..
ls -la pypsa-gui/frontend/dist/index.html      # must exist and be newer than src/
```

Or skip §1-§3 entirely and run `bash pypsa-gui/build-macos.sh`, which does the
SPA build, the freeze and the secret-scan as one gated step.

Set up a throwaway pair of roots **outside Documents** and an absolute path to
the harness, because the harnesses deliberately run from `/`:

```bash
export ACC="$HOME/pypsa-acceptance"
rm -rf "$ACC" && mkdir -p "$ACC/appdata" "$ACC/projects"
export COLD="$PWD/pypsa-gui/backend/smoke/accept_coldstart.py"
export SHUT="$PWD/pypsa-gui/backend/smoke/accept_shutdown.py"
```

---

## 2. A — automated: suites

| # | Command | Pass criteria |
|---|---|---|
| A1 | `pixi run gui-tests -q -p no:warnings` | 1650 collected, **1 skipped** (the intentional `KNOWN_BROKEN is empty` placeholder), 0 failed |
| A2 | `cd pypsa-gui/frontend && npm test` | 28 files / 189 tests, 0 failed |
| A3 | `cd pypsa-gui/frontend && PATH="$(git rev-parse --show-toplevel)/.pixi/envs/default/bin:$PATH" npx tsc --noEmit -p tsconfig.json` | no output |

A1 must **not** report skips beyond that one. Two desktop-download tests were
silently skipped for a while because `pywebview` was missing from the `test`
environment, and a skipped test reads as a green suite. Add `-rs` if you want
the skip reasons printed.

---

## 3. B — automated: cold start from `/` (the packaging precondition)

This is the only harness that runs from a working directory the app cannot
write. It is what found the launch-blocking `DATABASE_URL` defect.

```bash
PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
  DATABASE_URL="sqlite+pysqlite:///$ACC/appdata/acceptance.db" \
  pixi run -e desktop bash -c 'cd / && python "$COLD" first'

PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
  DATABASE_URL="sqlite+pysqlite:///$ACC/appdata/acceptance.db" \
  pixi run -e desktop bash -c 'cd / && python "$COLD" relaunch'
```

A window opens and closes itself each time. Each run prints one `RESULT {…}`
line and one `EXITED {…}` line.

**B1 pass criteria** — read them off the JSON:

| Field | `first` | `relaunch` |
|---|---|---|
| `cwd` | `"/"` | `"/"` |
| `config.db_file_resolved` | inside `$ACC/appdata` | the same file |
| `config.db_file_exists` | `true` | `true` |
| `root_page.is_spa` | `true` (**not** the 503 page) | `true` |
| `health.auth_enabled` | `false` | `false` |
| `projects_seen` | `[]` | `["ColdStart"]` |
| `save` / `load` | `200` | `200` |
| `buses` | — | `["ColdBus","SurvivedColdStart"]` |
| `quit` | `unflushed: []`, `errors: []`, `server_stage: "clean"` | same |
| `bundle.looks_like_js` | `true` | `true` |
| `reset` / `bus` | `200` / `201` | — |
| `edit_after_save` | **`201`** | — |
| `EXITED.status` | `0` | `0` |

**Three caveats that decide whether a FAIL is real.** All three come from an
adversarial review of this harness, and each one produced a wrong reading:

1. **`edit_after_save` must be `201`.** If that POST is refused (a 503 from the
   shutdown gate, a 409 from the solver gate, a 500), `first` still satisfies
   every other criterion — `save: 200`, clean quit, exit 0 — and `relaunch`
   then reports `buses: ["ColdBus"]`. Read naively that says "the flush lost
   the edit"; in fact the edit was never created. Check it before filing
   anything against the flush.
2. **`relaunch` overwrites its own evidence.** It loads the project and then
   quits cleanly, and a clean quit flushes memory back to disk. So re-running
   `relaunch` to confirm a FAIL cannot confirm anything — the first run already
   rewrote `network.nc` from whatever it loaded. Copy the project directory
   aside before re-running. (`accept_shutdown.py` has a dedicated `load-only`
   mode for this reason; `accept_coldstart.py` does not.)
3. **`never_up: true` is not always "the launch failed".** Two reachable causes
   leave the log empty or absent: another instance holding the single-instance
   lock (`gui.py` catches `AlreadyRunning` with no logging and opens a blocking
   message window — likely here, because the same app-data directory is shared
   with §4), and an app-data directory that cannot be written (file logging
   returns None by design, since the handler that would record it is what
   failed). A genuinely slow first launch is a third: the health budget is
   3600 s, this harness gives up at 150 s.

**`projects_seen: []` on `first` is a property of the harness, not of a first
launch.** It stubs `resolve_legacy_root` to `None`, so D10's first-run import
never runs. The real first launch imports whatever is in the legacy tree — see
the note under §4.

`buses` is the load-bearing one: `SurvivedColdStart` was created *after* the
save, so only the quit-flush can have persisted it.

When `never_up: true` appears, start with `$ACC/appdata/pypsa-gui.log` — but
read caveat 3 first: for two of the reachable causes that file is empty or
absent, and the absence is itself the diagnosis.

**B2 — solve, cancel, quit** (about 4 minutes; runs a real 8760-snapshot LP):

```bash
rm -rf "$ACC" && mkdir -p "$ACC/appdata" "$ACC/projects"
PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
  DATABASE_URL="sqlite+pysqlite:///$ACC/appdata/acceptance.db" \
  pixi run -e desktop bash -c 'cd / && python "$SHUT" solve-cancel-quit'
PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
  DATABASE_URL="sqlite+pysqlite:///$ACC/appdata/acceptance.db" \
  pixi run -e desktop bash -c 'cd / && python "$SHUT" relaunch'
```

Pass: `in_flight_at_close` non-empty (if it is empty the step proved nothing and
must be re-run), `close1_vetoed: true`, `cancel.window_hidden: 0`,
`cancel.still_gated: false`, `mutation_after_cancel.status: 409` with
`code: "solver_in_flight"` (**not** 503), `quit.abort_timed_out: false`,
`quit.unflushed: []`, and on relaunch `buses` contains `SurvivedQuit`.

---

## 4. C — interactive: what no script has checked

Launch the real app and drive it by hand:

```bash
PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
  DATABASE_URL="sqlite+pysqlite:///$ACC/appdata/acceptance.db" \
  pixi run -e desktop desktop-gui
```

**`-e desktop` is required.** Bare `pixi run desktop-gui` fails with *"the task
'desktop-gui' is ambiguous"* — the `test` environment also carries the `desktop`
feature, so both provide the task.

(Leave both variables off once you are ready to use your real data — the
defaults are `~/Library/Application Support/PyPSA GUI` and
`~/Documents/PyPSA GUI/Projects`. Do that only after C1–C6 pass.)

**Expect a first-run import on this launch.** Unlike the §3 harnesses, which
stub the legacy root, the real app runs D10: it inventories
`pypsa-gui/backend/projects` and **copies** what it finds into
`PYPSAGUI_PROJECTS_ROOT`. Measured 2026-07-30: 11 projects imported, 2 skipped
with reasons (`3_nodes_system.pypsaproj.zip: not a directory`,
`860edcb4-…: org-scoped tree`), 0 collisions, 0 failures, one honest warning
about a missing parent imported as a root — and the source tree unmodified
(copy, not move). That is ~113 MB into your throwaway root, so §6's cleanup
matters. The report is `$ACC/appdata/last-import-report.json`.

- [ ] **C1 — splash, then window.** A small 460×260 splash appears with a
      progress line, is replaced by the 1440×900 main window, and the splash
      does not linger. Expected wall-clock on a warm machine: 6–10 s.
- [ ] **C2 — minimum size is enforced.** Drag the window smaller. It must stop
      at 1024×700. Below that the side panels and canvas overlap, which is why
      this is enforced rather than advisory.
- [ ] **C3 — a real download.** Open a project, then export any CSV (a results
      table, the change log). A **save panel** must appear, and the file must
      land where you point it. The failure this catches: with downloads
      disabled the webview *navigates to the file* and the SPA disappears —
      you would be looking at a blob URL, not the app.
- [ ] **C4 — a real confirm dialog.** Start a solve, then close the window
      while it runs. A native dialog must read *"A solve is still running
      (<project>). Quitting will stop it."* Click **Cancel**: the window must
      stay visible and the app must keep working. Close again and **Quit**:
      the app exits.
- [ ] **C5 — second launch is refused.** With the app open, run `pixi run -e
      desktop desktop-gui` again in another terminal. Expect a 440×200 window reading
      *"PyPSA GUI is already running"* — **not** *"could not start"*, which is
      the different, filesystem-lock failure. The first window must be
      unaffected. Raising the incumbent window is not implemented; a message is
      the whole feature.
- [ ] **C6 — the process actually exits.** After quitting: `ps aux | grep -i
      "desktop.gui" | grep -v grep` prints nothing.

---

## 5. Results — fill this in

```
machine / chip / macOS version:
date:
commit (git rev-parse --short HEAD):

A1 backend suite      :        collected / passed / skipped / failed
A2 frontend suite     :
A3 tsc                :
B1 cold start first   :  PASS / FAIL   (paste the RESULT line)
B1 cold start relaunch:  PASS / FAIL   (paste the RESULT line)
B2 solve-cancel-quit  :  PASS / FAIL   (paste the RESULT line)
B2 relaunch           :  PASS / FAIL
C1 splash -> window   :  PASS / FAIL   observed seconds:
C2 minimum size       :  PASS / FAIL
C3 download save panel:  PASS / FAIL
C4 confirm / cancel   :  PASS / FAIL
C5 second launch      :  PASS / FAIL
C6 process exits      :  PASS / FAIL

anything that looked wrong but passed:
```

---

## 6. Clean up

```bash
rm -rf "$ACC"
ls "$HOME/Documents/PyPSA GUI/Projects" 2>/dev/null    # must be empty or absent
```

That second line is not paranoia. It is the check that would have caught the
seven stray projects the first time.

---

## 7. What is still missing on macOS

- **No package.** No `.app` bundle, no `.dmg`, no single executable, no code
  signing or notarisation. Launching still requires the repo, pixi, the conda
  environment and a built `dist/`. Per D14 the frozen app is to be built from a
  separate pip-wheel venv rather than by freezing the conda environment,
  because the latter produces a build that works on the developer's box and
  fails on a clean machine.
- **Gatekeeper is therefore untested.** An unsigned, un-notarised `.app` is
  quarantined on first open with *"cannot be opened because the developer
  cannot be verified"*. That is a workstream J problem and it is not solved by
  anything here.
- **`~500–600 MB` install** is the accepted size per D15.
- The 6–10 s budget in C1 is measured warm and unfrozen. A frozen build adds
  first-run extraction.
