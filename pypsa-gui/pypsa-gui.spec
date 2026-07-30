# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the PyPSA GUI desktop app (workstream I, macOS first).

Build from the PIP-WHEEL venv described by `gui-requirements.txt`, never from
the pixi/conda environment (D14). Freezing a conda env produces a build that
works on the developer's box and fails on a clean machine — and it drags in
xarray's installed backend entry points (boto3, rasterio, cfgrib, distributed),
which the app never uses and which were measured loading at netCDF-IO time only
because they were present.

    pyinstaller pypsa-gui/pypsa-gui.spec --noconfirm
    python pypsa-gui/backend/smoke/check_bundle.py "dist/PyPSA GUI.app"

**The second command is not optional.** See `datas` below.
"""
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

# `SPECPATH` is what PyInstaller defines; `__file__` is not available here.
ROOT = Path(SPECPATH).resolve()          # noqa: F821 - injected by PyInstaller
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"

if not FRONTEND_DIST.is_dir():
    raise SystemExit(
        f"{FRONTEND_DIST} is missing. `npm run build` first — `dist/` is "
        f"gitignored, and without it the app serves a 503 page instead of the "
        f"SPA, which looks like a broken backend."
    )

# ── datas: an ALLOWLIST, never a directory sweep ────────────────────────────
#
# The tidy-looking line is `('backend', '.')`. It also ships:
#
#   backend/.env        a real ANTHROPIC_API_KEY and the SECRET_KEY that signs
#                       session cookies — identical in every install
#   backend/auth_dev.db a password hash and absolute developer paths
#   backend/projects/   113 MB of real user projects
#
# All three are gitignored, so a sweep is invisible in a code review too. Every
# other packaging mistake is a broken build you fix; this one is a published
# credential with no recall. `smoke/check_bundle.py` fails the build on any of
# them — run it on the output, every time.
#
# Note the hazard got WORSE, not better, when the launch was fixed: a bundled
# `.env` carries a cwd-relative DATABASE_URL that used to crash the frozen app
# at startup. That crash was accidentally the only thing catching a sweep.
# `build_environment` now pins DATABASE_URL, so a bundled `.env` yields an app
# that works perfectly and leaks.
datas = [
    (str(BACKEND / "project_templates"), "project_templates"),
    (str(BACKEND / "templates" / "matpower.jinja2"), "templates"),
    # `local_bootstrap` runs `alembic upgrade head` on every launch and points
    # `script_location` at `<backend>/alembic`, so both must ship.
    (str(BACKEND / "alembic"), "alembic"),
    (str(BACKEND / "alembic.ini"), "."),
    # The SPA the backend serves in local mode. `settings.frontend_dist`
    # resolves `<backend>/../frontend/dist`, which under _MEIPASS means this
    # exact layout.
    (str(FRONTEND_DIST), "frontend/dist"),
]

# Distributions whose `importlib.metadata` is read at runtime. Without these the
# frozen app raises PackageNotFoundError instead of starting.
for dist in ("pypsa", "linopy", "xarray", "fastapi", "uvicorn", "starlette",
             "pydantic", "SQLAlchemy", "alembic", "pywebview"):
    try:
        datas += copy_metadata(dist)
    except Exception:                      # noqa: BLE001 - absent in some builds
        pass

# xarray and pypsa both resolve backends through entry points at first use.
datas += collect_data_files("xarray", includes=["**/*.yaml", "**/*.yml"])
datas += collect_data_files("pypsa", includes=["**/*.csv", "**/*.yaml"])

# ── hiddenimports: MEASURED, not guessed ────────────────────────────────────
#
# Taken from a run that built a tiny network, solved it with HiGHS, wrote a
# netCDF and read it back, diffing `sys.modules` at each stage. PyInstaller's
# static analysis sees none of these, so each omission is a runtime failure in
# the shipped app rather than a build error.
#
# The spec's original guess was "uvicorn, xarray backends, highspy, netCDF4,
# unpickler targets". The measurement found 36 distributions loading only at
# solve or IO time.
hiddenimports = [
    # solve time
    "highspy",
    "dask", "dask.array", "dask.dataframe",
    "toolz", "cytoolz", "tlz",
    "yaml", "psutil", "tblib",
    # netCDF IO
    "netCDF4", "netCDF4.utils", "cftime",
    "scipy.sparse.csgraph._validation",
    "scipy.special._cdflib",
    # uvicorn's own late imports
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # database drivers resolved by URL string, never imported by name
    "sqlalchemy.dialects.sqlite", "pysqlite3",
    # the desktop shell
    "webview", "webview.platforms.cocoa",
]

# Present in the CONDA env and pulled in by xarray's entry-point scan; absent
# from the pip venv this is built from. Listed here so the next reader knows
# they were considered and deliberately excluded rather than forgotten.
excludes = [
    "boto3", "botocore", "s3transfer", "distributed",
    "rasterio", "rioxarray", "cfgrib", "eccodes", "gribapi",
    "cartopy",                     # spec §I.2: defensive, large, unused
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "IPython", "jupyter", "notebook", "nbformat",
    "pytest", "_pytest",
]

a = Analysis(                              # noqa: F821 - injected
    [str(BACKEND / "desktop" / "gui.py")],
    pathex=[str(BACKEND)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    # ── packages that must exist as REAL DIRECTORIES on disk ────────────────
    #
    # Measured on the first frozen build, which died on the splash with:
    #
    #   FileNotFoundError: .../Contents/Frameworks/pypsa/optimization/../data/variables.csv
    #
    # `pypsa/optimization/constraints.py` does a `__file__`-relative
    # `read_csv("../data/variables.csv")` AT IMPORT TIME. The data file was
    # collected correctly and is present — but by default the package's code
    # goes into the PYZ archive, so `pypsa/optimization/` never exists as a
    # directory, and POSIX cannot resolve `optimization/../data` through a
    # path component that is not there. The file being present is exactly why
    # this is confusing to debug: the error names a path whose target exists.
    #
    # `pyz+py` keeps the archive copy and ALSO writes the package out, so
    # `__file__` resolves the way the library assumes.
    module_collection_mode={
        "pypsa": "pyz+py",
        "linopy": "pyz+py",
    },
)

pyz = PYZ(a.pure)                          # noqa: F821 - injected

exe = EXE(                                 # noqa: F821 - injected
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PyPSA GUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                             # UPX breaks code signing on macOS
    console=False,                         # a windowed app: see the note below
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,                      # host arch; no universal2 fat build
    codesign_identity=None,                # workstream J
    entitlements_file=None,
)

coll = COLLECT(                            # noqa: F821 - injected
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="PyPSA GUI",
)

app = BUNDLE(                              # noqa: F821 - injected
    coll,
    name="PyPSA GUI.app",
    icon=None,                             # workstream J
    bundle_identifier="org.pypsa.gui",
    info_plist={
        # A Finder-launched .app has cwd `/`. Everything the app resolves must
        # be absolute — acceptance step 9 exists for exactly this, and found a
        # cwd-relative DATABASE_URL that killed the launch.
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",   # netCDF4's arm64 wheel floor
        "CFBundleShortVersionString": "0.1.0",
    },
)

# `console=False` means stdout and stderr are closed handles. Two consequences
# already handled elsewhere, recorded here because they are easy to undo:
#   * file logging is installed before anything else (`bootstrap.install_file_logging`)
#   * `PYPSA_GUI_RESIDENT_CAP` prints `Unknown option` on every boot, and
#     writing to a closed stderr can raise. Tracked in the phase 2a plan.
