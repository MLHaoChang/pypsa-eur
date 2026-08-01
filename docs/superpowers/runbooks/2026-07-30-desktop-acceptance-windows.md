# Desktop acceptance run-book — Windows

**Why this run-book carries more weight than the macOS one.** Every
Windows-specific decision in the desktop shell is currently *reasoned from
documentation*, not measured. Five of them cannot be exercised on macOS at all,
and one of them — the close-handler ordering — macOS **provably** cannot
exercise, because `destroy()` does not re-fire `closing` on cocoa. So the
platform with the most load-bearing behaviour has the least coverage.

**What this does NOT verify:** a packaged app. Nothing is frozen — no `.exe`, no
installer, no single executable. Workstreams I and J are untouched. You are
running the shell out of the source tree in the pixi `desktop` environment.

**Run this BEFORE packaging, not after.** If the frozen build fails, you need to
already know whether the app works on Windows unfrozen — otherwise the failure is
ambiguous and you debug two things at once.

---

## 0. Hard rules

1. **Set all THREE isolation variables**, and never point any at `Documents`.
   `PYPSAGUI_APP_DATA_DIR` moves the database *default*, log, lock and *flat*
   store; `PYPSAGUI_PROJECTS_ROOT` governs projects — setting one and assuming
   isolation put seven throwaway projects into a real Documents folder once.

   `DATABASE_URL` is the third, and this run-book used to omit it.
   `settings.py` declares `env_file=backend\.env`, and pydantic-settings ranks
   the env file **above** the `default_factory` that reads
   `PYPSAGUI_APP_DATA_DIR` — so the cwd-relative `DATABASE_URL` in `.env` wins
   and the auth database lands next to wherever the harness was launched. On
   macOS that produced a stray `auth_dev.db` at the repo root, a file on the
   credential gate's own forbidden list because it carries a password hash.

   `backend\smoke\isolation.py` is the single guard all three harnesses call;
   it refuses to start unless every variable is set and disposable, and also
   refuses bare `PROJECTS_ROOT` / `FLAT_PROJECTS_ROOT` / `LEGACY_ROOT`, which
   pydantic binds *above* the `PYPSAGUI_*` override.
2. **Never write to or delete under `pypsa-gui\backend\projects\`.**
3. **`POST /api/projects/{name}` is a destructive save**, not a load.
4. **`npm run build` before any frontend check** — the app serves the built SPA
   from `frontend\dist\`, which is gitignored.
5. **Do not copy `.pixi\envs\` or `frontend\node_modules\` from the Mac.** Both
   are platform-specific build artifacts (`python.exe` and `*.dll`;
   `@rollup/rollup-*`, `@esbuild/*`, `@tailwindcss/oxide-*`). Delete and
   re-install.

---

## 1. Preconditions

```powershell
cd <repo>                                  # the pypsa-eur checkout
git branch --show-current                  # expect: feature/local-app-impl
git config core.autocrlf                   # MUST print false (or nothing)
```

CRLF is not cosmetic here. `.gitattributes` pins `* text=auto eol=lf`; the repo
once accumulated CRLF on 344 files, which silently broke the multi-line `bash -c`
task bodies in `pixi.toml` (a stray `\r` reaches the shell). If `git status`
shows hundreds of untouched files as modified, fix line endings before anything
else.

```powershell
pixi --version                             # new enough for pixi.lock format v7
pixi install
```

If `pixi install` wants to **re-solve**, stop. The lock is pinned (pypsa 1.1.2
across one solve group) and re-solving is a separate decision. Upgrade pixi
instead. Confirm the pin survived once the app is up: `/api/health` must report
**pypsa 1.1.2** — an unpinned `desktop` environment reports 1.2.4.


**node and npm come from pixi, not the system.** `nodejs >=22` is a pixi
dependency and there is no separate nodeenv (CLAUDE.md), so a bare `npm` is
`command not found` on a box provisioned with pixi alone — measured, while
writing `build-macos.sh`. Put the environment's bin directory on PATH first:

```powershell
$env:PATH = "$PWD\.pixi\envs\default;$PWD\.pixi\envs\default\Scripts;$env:PATH"
cd pypsa-gui\frontend
npm install
npm run build
Test-Path dist\index.html                  # must be True
cd ..\..
```

Note the Windows layout differs: pixi puts executables in the environment root
and in `Scripts\`, not in `bin/`.

**WebView2 runtime.** pywebview's Windows backend is EdgeChromium. Windows 11
ships the Evergreen runtime; on Windows 10 it may be absent. Check:

```powershell
$g = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
"HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$g",
"HKCU:\Software\Microsoft\EdgeUpdate\Clients\$g" |
  ForEach-Object { Get-ItemProperty $_ -ErrorAction SilentlyContinue } |
  Select-Object pv, name
```

**Both hives, not just HKLM** — the Evergreen runtime also installs per-user
under HKCU, where the HKLM key is simply absent. Checking only HKLM tells a
reader with a working runtime that it is missing, which is then the first
(wrong) thing they blame for any launch failure.

Nothing returned from EITHER means install the *Evergreen Standalone Installer*
first; otherwise the window never appears and the log shows a
backend-initialisation failure.

Throwaway roots and absolute script paths:

```powershell
$env:ACC = "$env:USERPROFILE\pypsa-acceptance"
Remove-Item -Recurse -Force $env:ACC -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force "$env:ACC\appdata", "$env:ACC\projects" | Out-Null
$env:COLD = "$PWD\pypsa-gui\backend\smoke\accept_coldstart.py"
$env:SHUT = "$PWD\pypsa-gui\backend\smoke\accept_shutdown.py"
$env:PYPSAGUI_APP_DATA_DIR = "$env:ACC\appdata"
$env:PYPSAGUI_PROJECTS_ROOT = "$env:ACC\projects"
# Forward slashes on purpose: this is a SQLAlchemy URL, not a Windows path.
$env:DATABASE_URL = "sqlite+pysqlite:///$($env:ACC -replace '\\','/')/appdata/acceptance.db"
```

Verify `$env:ACC` is **not** inside a OneDrive-redirected folder. Check where
Documents actually points:

```powershell
(Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders").Personal
```

If that resolves under OneDrive, note it in the results — it changes the timing
of §4's first-run copy and of every flush, because the store becomes a
sync-on-write filesystem.

---

## 2. A — automated: suites

| # | Command | Pass criteria |
|---|---|---|
| A1 | `pixi run gui-tests -q -p no:warnings -rs` | 1650 collected, **1 skipped** (the intentional `KNOWN_BROKEN is empty` placeholder), 0 failed |
| A2 | `cd pypsa-gui\frontend; npm test` | 28 files / 189 tests, 0 failed |
| A3 | `cd pypsa-gui\frontend; npx tsc --noEmit -p tsconfig.json` | no output |

`-rs` prints skip reasons. **Any skip beyond that one is a finding** — two
desktop-download tests were silently skipped for a while because `pywebview` was
missing from the `test` environment, and a skipped test reads as a green suite.

**A1 is also the first time `pandas 3.0.3` on win-64 has executed anything in
this project.** If pandas-related failures appear in tests that pass on macOS,
that is a genuine platform finding, not a flake — record the full failure.

---

## 3. B — automated: cold start from an unwritable working directory

The macOS equivalent of this test uses cwd `/`. On Windows use
`C:\Windows\System32`, which a standard (non-elevated) user cannot create files
in — the same property, which is what the test needs. **Run these from a
non-elevated PowerShell.** As Administrator the directory *is* writable and the
test passes vacuously.

```powershell
pixi run -e desktop cmd /c "cd /d C:\Windows\System32 && python ""%COLD%"" first"
pixi run -e desktop cmd /c "cd /d C:\Windows\System32 && python ""%COLD%"" relaunch"
```

**The inner quotes around `%COLD%` are load-bearing** and were missing in the
first version of this file. `%COLD%` is built from `$PWD`, and both a checkout
path and `%USERPROFILE%` commonly contain a space — the developer's own macOS
checkout is `…/Desktop/Code Test/pypsa-eur`. Unquoted, cmd splits the argument
and Python reports `can't open file 'C:\Users\Hao'`, which reads as a missing
file rather than a quoting bug. (The macOS book always quoted `"$COLD"`.)

If pixi's argument passthrough still mangles it, resolve the interpreter
through pixi instead of hardcoding it, then call it directly:

```powershell
$py = pixi run -e desktop python -c "import sys; print(sys.executable)"
Push-Location C:\Windows\System32; & $py $env:COLD first; Pop-Location
```

A window opens and closes itself each time. Each run prints one `RESULT {…}` and
one `EXITED {…}` line.

**B1 pass criteria:**

| Field | `first` | `relaunch` |
|---|---|---|
| `cwd` | `C:\Windows\System32` | same |
| `config.db_file_resolved` | inside `%ACC%\appdata` | the same file |
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

`buses` is the load-bearing row: `SurvivedColdStart` is created *after* the save,
so only the quit-flush can have persisted it. **This is the first Windows
execution of the flush-destination fix** (`shutdown.make_saver`) — before that
fix the flush wrote to `flat_projects_root` while the load read `projects_root`,
and the report still said `unflushed: []`.

When `never_up: true` appears, start with `%ACC%\appdata\pypsa-gui.log` — but
read caveat 3 first. On Windows the lock cause is the likely one: this file
exports `PYPSAGUI_APP_DATA_DIR` session-wide, so §3's harnesses and §4's real
app share one `single-instance.lock`, and C6 tells you to hard-kill and
relaunch.

**B2 — solve, cancel, quit** (~4 min; runs a real 8760-snapshot LP):

```powershell
Remove-Item -Recurse -Force $env:ACC; New-Item -ItemType Directory -Force "$env:ACC\appdata","$env:ACC\projects" | Out-Null
pixi run -e desktop cmd /c "cd /d C:\Windows\System32 && python %SHUT% solve-cancel-quit"
pixi run -e desktop cmd /c "cd /d C:\Windows\System32 && python %SHUT% relaunch"
```

Pass: `in_flight_at_close` non-empty (**empty means the step proved nothing** —
HiGHS finished before the close; re-run), `close1_vetoed: true`,
`cancel.window_hidden: 0`, `cancel.still_gated: false`,
`mutation_after_cancel.status: 409` with `code: "solver_in_flight"` (**not**
503), `quit.abort_timed_out: false`, `quit.unflushed: []`, and on relaunch
`buses` contains `SurvivedQuit`.

---

## 4. C — interactive: the Windows-only behaviour

```powershell
pixi run -e desktop desktop-gui
```

**`-e desktop` is required.** Bare `pixi run desktop-gui` fails with *"the task
'desktop-gui' is ambiguous"* — the `test` environment also carries the `desktop`
feature, so both provide the task. (Measured on macOS; the resolution is
platform-independent.)

**Expect a first-run import on this launch.** Unlike the §3 harnesses, which stub
the legacy root, the real app runs D10: it inventories
`pypsa-gui\backend\projects` and **copies** what it finds into
`PYPSAGUI_PROJECTS_ROOT` — about 113 MB, so §7's cleanup matters. The report
lands at `%ACC%\appdata\last-import-report.json`. Expected shape, from the macOS
run: `collisions: []`, `failed: []`, and any `skipped` entry carrying a reason.
**On Windows watch the timing**: if Documents is OneDrive-redirected, this copy
hits a sync-on-write filesystem and is a plausible source of a slow first
launch. Record how long it takes.

- [ ] **C1 — splash, then window.** 460×260 splash with a progress line,
      replaced by the 1440×900 main window. **Record the wall-clock time.** The
      6–10 s budget was measured on warm macOS arm64, unfrozen. Windows adds
      Defender scanning of ~287 MB of native extensions and WebView2 first-init.
      A first launch materially slower than 10 s is a finding for workstream I's
      estimate, not a failure.
- [ ] **C2 — minimum size is enforced.** Drag smaller; it must stop at
      1024×700.
- [ ] **C3 — a real download.** Open a project and export a CSV. A save dialog
      must appear and the file must land where you point it. **This is the first
      execution of the Windows download gate** — `ALLOW_DOWNLOADS` is read at
      `edgechromium.py:316` (`if not webview_settings['ALLOW_DOWNLOADS']:
      args.Cancel = True`) and that line has never run. If the export silently
      does nothing, the setting is not reaching the EdgeChromium backend.
- [ ] **C4 — confirm, Cancel, then Quit.** Start a solve, close the window. A
      native dialog must read *"A solve is still running (<project>). Quitting
      will stop it."* **Cancel** → window stays visible, app still usable.
      Close again → **Quit** → app exits.
      **Watch for a double shutdown.** On winforms `destroy()` re-fires
      `closing`, so `CloseHandler`'s "flip to complete *before* destroy"
      ordering is load-bearing here and inert on macOS. A regression shows up as
      the shutdown sequence running **twice** — two confirm dialogs, or
      duplicate shutdown blocks at the end of `pypsa-gui.log`. This is the one
      behaviour macOS cannot test even in principle.
- [ ] **C5 — second launch is refused.** With the app open, `pixi run -e desktop
      desktop-gui` in a second terminal. Expect a 440×200 window: *"PyPSA GUI is
      already running"* — **not** *"could not start"* (that is the different,
      lock-failure message, and confusing the two is how a filesystem problem
      gets misreported as a running instance). The first window must be
      unaffected.
- [ ] **C6 — the lock survives a hard kill, then releases.** Kill the app
      without letting it quit: `Stop-Process -Name python -Force` (from a
      terminal, while it runs). Then immediately `pixi run -e desktop
      desktop-gui` again.
      It **must eventually start.** `msvcrt.locking` is released by the kernel
      when the process dies, but Windows can take a moment; if the second launch
      shows "already running", wait 5 s and retry, and **record how long it
      took**. This delay is documented but unmeasured.
- [ ] **C7 — two launches never share the port.** `SO_REUSEADDR` is deliberately
      **not** set, because on Windows it permits binding a port another socket is
      actively listening on — the opposite of its POSIX meaning — so two
      launches would both succeed and one would silently receive nothing. C5
      covers this indirectly; if C5 ever shows two working windows, this is why.
- [ ] **C8 — the process actually exits.** After quitting:
      `Get-Process python -ErrorAction SilentlyContinue` shows no stray.
- [ ] **C9 — no `Unknown option` spam.** Watch the console and log for
      `Unknown option 'PYPSA_GUI_RESIDENT_CAP'`. The variable uses a prefix that
      collides with PyPSA's option namespace. On a windowless build, writing to
      a closed stderr can raise — which is why this matters more here than on
      macOS.

---

## 5. Results — fill this in

```
machine / CPU / Windows version / build:
Documents redirected to OneDrive?      yes / no  (paste the registry value)
WebView2 runtime present before start?  yes / no
Windows Defender real-time protection:  on / off
date:
commit (git rev-parse --short HEAD):

A1 backend suite      :        collected / passed / skipped / failed
    any pandas 3.0.3 failures?           (paste in full)
A2 frontend suite     :
A3 tsc                :
B1 cold start first   :  PASS / FAIL   (paste the RESULT line)
B1 cold start relaunch:  PASS / FAIL   (paste the RESULT line)
B2 solve-cancel-quit  :  PASS / FAIL   (paste the RESULT line)
B2 relaunch           :  PASS / FAIL
C1 splash -> window   :  PASS / FAIL   FIRST launch seconds:      warm:
C2 minimum size       :  PASS / FAIL
C3 download dialog    :  PASS / FAIL
C4 confirm / cancel   :  PASS / FAIL   double shutdown seen?  yes / no
C5 second launch      :  PASS / FAIL
C6 lock after kill    :  PASS / FAIL   seconds until it started:
C7 port never shared  :  PASS / FAIL
C8 process exits      :  PASS / FAIL
C9 Unknown option spam:  seen / not seen

anything that looked wrong but passed:
```

---

## 6. If something fails — likely diagnoses

| Symptom | First thing to check |
|---|---|
| No window, log ends in a WebView2 / EdgeChromium error | Evergreen runtime missing — §1 |
| `sqlite3.OperationalError: unable to open database file` | A cwd-relative `DATABASE_URL` reaching `Settings`. Fixed in `f51e5649` by pinning it in `build_environment`; if it recurs, check `pypsa-gui\backend\.env` and whether `DATABASE_URL` is exported in your shell |
| Launch dies with `BackendAlreadyImported` | Something imported `pypsa`/`matplotlib` before `apply_environment`. Only relevant if you edited a harness |
| App starts but `/` shows a 503 page | `frontend\dist\` missing or stale — §1 |
| `/api/health` reports pypsa 1.2.4 | The solve-group pin did not hold; the environment was re-solved |
| Two confirm dialogs on quit | The `destroy()` re-entrancy — C4. This is the expected place for it to break |
| Second launch says *"could not start"* rather than *"already running"* | Not a duplicate instance — advisory locking failed on that filesystem (network drive, unusual FS). The distinction is deliberate |
| Export does nothing, no dialog | The download gate — C3 |
| Hundreds of unmodified files show as modified in git | CRLF — §1 |

---

## 7. Clean up

```powershell
Remove-Item -Recurse -Force $env:ACC
Get-ChildItem "$env:USERPROFILE\Documents\PyPSA GUI\Projects" -ErrorAction SilentlyContinue
```

The second line must return nothing. It is the check that would have caught the
seven stray projects the first time.
