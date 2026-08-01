# The frozen app shipped without five of its dependencies

**Date:** 2026-07-31
**Reported by:** the user — "cannot download any time series template, error code 500"
**Status:** FIXED for the reported defect and its blast radius (`20c33915`), rebuilt
and verified in the artifact. Two related findings below are deliberately NOT
fixed and need a decision.

## Root cause

`gui-requirements.txt` — the pip venv the DMG is frozen from (D14) — omitted
every dependency the backend imports *inside a function body*. Measured in the
user's own log, `~/Library/Application Support/PyPSA GUI/pypsa-gui.log`:

```
File "routers/network.py", line 3568, in download_load_profile_template
File "routers/network.py", line 441, in _xlsx_response
File "pandas/io/excel/_openpyxl.py", line 57, in __init__
ModuleNotFoundError: No module named 'openpyxl'
```

`openpyxl` is in `pixi.toml`, so every test and every dev run had it. Measured
in the build venv, five were missing: `openpyxl`, `anthropic`, `python-magic`,
`pypdf`, `tsam`. `jinja2` was present only as an undeclared transitive of an
undeclared transitive.

**What makes this class ship.** A missing top-level import crashes the app at
launch and cannot be missed. A missing function-local one launches clean and
500s when the user reaches the feature. Both are invisible to PyInstaller's
static analysis when, as at `network.py:441`, the module is reached through
pandas' engine registry — a string lookup, not an import statement.

**Second half of the same defect.** `build-macos.sh` ran
`pip install -r gui-requirements.txt` only inside the `if [ ! -x "$VENV/bin/python" ]`
branch. The venv is cached by design, so after the first build, editing
`gui-requirements.txt` had NO EFFECT on the artifact — and the build reported
success either way. Fixing the manifest alone would have changed nothing.

## Why no test caught it

Every existing check runs in the pixi environment, where all six modules are
present:

- the backend suite, including `test_desktop_downloads.py`
- `smoke/accept_downloads.py`, which despite driving the real `gui.main()`
  does `sys.path.insert(0, BACKEND)` and runs from SOURCE in pixi

So no automated check has ever exercised the frozen bundle's dependency set.
`backend/tests/test_packaging_requirements.py` now fails the suite if shipped
code imports an unguarded third-party module that is not pinned.

## What was verified in the artifact

Rebuilt 2026-07-31 21:59. From `.build-work/pypsa-gui/PYZ-00.toc`:
`openpyxl` 176 modules, `jinja2` 23, `anthropic` 1051, `pypdf` 51,
`et_xmlfile` 3; `magic` and `tsam` 0, as intended. The exact failing call
(`df.to_excel(buf, engine="openpyxl")`) round-trips in the build venv at the
pinned versions.

NOT verified: the endpoint returning 200 inside the running frozen app. There
is no headless flag on `desktop/gui.py`, so checking it means opening a GUI
window — left for the user's click-through.

Note for whoever checks a bundle next: `find` for a pure-Python package inside
the `.app` returns nothing whether or not it shipped, because PyInstaller
archives those into the PYZ. Grep `PYZ-00.toc`, not the bundle tree.

## Blast radius of the openpyxl omission

Every `.xlsx` path in the packaged app was broken, not only the reported one:

| Path | Site |
|---|---|
| load / generator / link template download | `network.py:441` (reported) |
| template UPLOAD — the other half of the round trip | `network.py:2978` |
| asset-results Excel export | `services/asset_results/export.py:160` |
| PyPSA network Excel export | `routers/io.py:91` |
| chat `read_excel_sheet` / `export_to_excel` | `services/chat_tools.py` |

## NOT fixed — two decisions for the owner

**1. `tsam` absence is silent.** `time_aggregation_service.py:170` catches
ImportError and falls back to the full period. The result is correct and the
solve is slower — but the only notice is a `logger.warning`. A user who asked
for representative periods gets a full-period solve and is never told. Pinning
`tsam` would pull **pyomo and scikit-learn** into the bundle to service a path
that already works, so the fix here is to surface the fallback in the UI, not
to add the dependency. Left unpinned deliberately.

**2. `python-magic` absence softens upload validation.** `uploads.py:92`
catches `(ImportError, OSError)` and falls through to the **client-declared**
content-type. The wheel is only ctypes bindings over a system `libmagic` that
this bundle does not ship, so pinning it alone changes nothing on a clean Mac —
shipping the sniffer means bundling the dylib too. Consequence is narrower than
a crash but is not nothing: MIME sniffing is skipped, and the declared type is
exactly what the Office-zip upgrade at `_ZIP_EXTENSION_UPGRADES` exists to
backstop. Left unpinned deliberately.

**3. `anthropic` was pinned but chat still will not work.** The SDK was
missing, which is a real gap now closed — but `check_bundle.py` deliberately
keeps `.env` out of the bundle, so the packaged app has no `ANTHROPIC_API_KEY`
and `_make_client` returns `missing_api_key` *before* reaching the import. This
matches the user's earlier report that "chat agent does not work as it reports
that the anthropic key is not available". Shipping the SDK removes the second
blocker behind that message; the key story is workstream K and still needs its
own spec.
