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

1. **Never point either storage variable at `~/Documents`.** An earlier harness
   set only `PYPSAGUI_APP_DATA_DIR` and wrote seven throwaway projects into the
   developer's real `~/Documents/PyPSA GUI/Projects`. `PYPSAGUI_APP_DATA_DIR`
   moves the database, the log, the lock and the *flat* store.
   `PYPSAGUI_PROJECTS_ROOT` is what governs projects. **Set both, always.** The
   harnesses now refuse to start otherwise.
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

```bash
cd pypsa-gui/frontend && npm install && npm run build && cd ../..
ls -la pypsa-gui/frontend/dist/index.html      # must exist and be newer than src/
```

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
  pixi run -e desktop bash -c 'cd / && python "$COLD" first'

PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
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
| `EXITED.status` | `0` | `0` |

`buses` is the load-bearing one: `SurvivedColdStart` was created *after* the
save, so only the quit-flush can have persisted it.

If `never_up: true` appears, the launch failed — the reason is in
`$ACC/appdata/pypsa-gui.log`, near the end.

**B2 — solve, cancel, quit** (about 4 minutes; runs a real 8760-snapshot LP):

```bash
rm -rf "$ACC" && mkdir -p "$ACC/appdata" "$ACC/projects"
PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
  pixi run -e desktop bash -c 'cd / && python "$SHUT" solve-cancel-quit'
PYPSAGUI_APP_DATA_DIR="$ACC/appdata" PYPSAGUI_PROJECTS_ROOT="$ACC/projects" \
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
