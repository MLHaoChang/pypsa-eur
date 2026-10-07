"""Campus electrical study for a capacity-expansion (hub) project (plan C6).

A solved, saved hub project is taken to its electrical design by the
gridspine campus engine (``gridspine.drivers.campus_study``). This service
keeps everything under ``<project dir>/campus_electrical/``:

``campus_input.yaml``
    The campus description the user edits. ``draft`` writes it from the
    project's network. Every value is tagged; see
    ``gridspine.ingest.campus``.
``draft_notes.json``
    What the draft skipped, and why.
``settings.json``
    The last run's settings: k, power factor, profile, margin, N-1.
``run/``
    The engine's run directory.
``campus_assets.yaml``
    The project's copy of the asset library (plan C9), once the user has saved
    one. Until then the shipped default is used. Validated by the engine's
    loader before it is kept.
``grid_codes/``
    The project's own grid codes: uploaded documents, drafts and published
    profiles (``services/campus_grid_code_service.py``, plan C10). A
    published profile is offered next to the shipped ones and accepted by
    ``run``.

The functions:

* ``get_state``: the campus file, the profiles a study can use, the last
  settings, and the results with a ``stale`` flag. Results are stale when
  the campus file or the project's network has changed since the run;
  the engine's manifest hashes are compared.
* ``draft``: writes the campus file from the network. It refuses with 409
  rather than overwrite an existing file unless asked.
* ``save_campus``: validates by building the campus, then writes it.
* ``run``: prepare, rank and size, then (unless ``invest`` is off) buy the
  electrical assets from the library at least cost, AC-checked, in one call.
  The library a run used is kept as ``run/campus_assets_used.yaml``, so a
  later change to the library flags the results as stale.
* ``get_library``, ``save_library``, ``reset_library``: the asset library.
* ``hub_cost``, in ``get_state``: the solved hub's own system cost, from the
  helper the results pages use, for the panel to show beside the electrical
  annualised cost. It never fails the state: when it cannot be computed it is
  ``null``, with ``hub_cost_reason``.

Every function refuses a project of another kind with 409, the way the
planning study refuses a capacity-expansion project. A missing network, an
unsolved network, a description that does not build, and bad settings are
422s, each naming what to fix. A pypsa NetCDF read happens under
``PyPSAService.get_netcdf_io_lock()``, and the run under a per-project lock.
"""
import hashlib
import json
import math
import os
import tempfile
import threading
from pathlib import Path

import pandas as pd
import yaml
from fastapi import HTTPException

from services import project_registry
from services.gridspine_service import CAPACITY_EXPANSION, kind_of

# Guarded like services/gridspine_service.py, for the same reason: the frozen
# desktop app is built from a pip venv that ships neither gridspine nor its
# engines, so there this import really fails. Every action then answers 503
# instead of 500ing three frames down.
try:
    from gridspine.drivers import campus_study as cs
    from gridspine.drivers.campus_study import SizingCriteria
    from gridspine.schema.contracts import ContractError

    CAMPUS_AVAILABLE = True
    CAMPUS_IMPORT_ERROR = None
except ImportError as exc:                      # pragma: no cover - build-shape branch
    CAMPUS_AVAILABLE = False
    CAMPUS_IMPORT_ERROR = str(exc)
    cs = SizingCriteria = None

    class ContractError(Exception):
        """Stand-in so the `except ContractError` clauses below still parse."""

SUBDIR = "campus_electrical"
CAMPUS_FILE = "campus_input.yaml"
NOTES_FILE = "draft_notes.json"
SETTINGS_FILE = "settings.json"
NETWORK_FILE = "network.nc"
GRID_CODES_SUBDIR = "grid_codes"
LIBRARY_FILE = "campus_assets.yaml"
USED_LIBRARY_FILE = "campus_assets_used.yaml"
COST_BASIS_FILE = "campus_cost_basis.json"
SOLVER_CONFIG_FILE = "solver_config.json"
MAX_CAMPUS_BYTES = 1_000_000
MAX_LIBRARY_BYTES = 1_000_000
DEFAULTS = {"k": 3, "pf": None, "profile": "eu_rfg_dcc_ce", "margin": 0.2, "n_minus_1": True, "invest": True,
            "pcc_switchgear_by_operator": False}

_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.Lock())


def require_engine() -> None:
    """503 rather than 500 when this build does not carry gridspine."""
    if not CAMPUS_AVAILABLE:
        raise HTTPException(
            status_code=503,
            detail=f"The campus electrical study is not available in this build: {CAMPUS_IMPORT_ERROR}",
        )


def require_capacity_expansion(project) -> None:
    require_engine()
    kind = kind_of(project)
    if kind != CAPACITY_EXPANSION:
        raise HTTPException(
            status_code=409,
            detail=f"Project '{project.name}' is a {kind} project; the campus electrical study is for "
                   "capacity-expansion (hub) projects",
        )


def campus_dir(project) -> Path:
    path = project_registry.ensure_project_dir(project) / SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def grid_codes_dir(project) -> Path:
    """Where the project's grid codes live; its ``*.yaml`` are the published
    profiles the engine loads next to the shipped ones."""
    return campus_dir(project) / GRID_CODES_SUBDIR


def profiles(project) -> dict:
    """``{profile: title}`` a study of this project can be held to: the
    shipped profiles and the project's published ones."""
    try:
        return cs.grid_code_profiles(extra_dirs=(grid_codes_dir(project),))
    except ContractError as exc:
        raise _unprocessable(exc)


def _network(project) -> Path:
    nc = project_registry.project_dir(project) / NETWORK_FILE
    if not nc.is_file():
        raise HTTPException(status_code=422, detail=f"Project '{project.name}' has no saved network yet — save it first")
    return nc


def _netcdf_lock():
    from services.pypsa_service import PyPSAService
    return PyPSAService.get_netcdf_io_lock()


def _unprocessable(exc: ContractError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


def _records(df: pd.DataFrame) -> list:
    """JSON-ready rows: NaN and NaT leave as None."""
    out = []
    for row in df.to_dict(orient="records"):
        out.append({k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()})
    return out


def _read_csv(path: Path):
    return _records(pd.read_csv(path)) if path.is_file() else None


def _unresolved(investment):
    """``[{need, reason}]`` of the needs the investment could not meet, or
    None when the investment did not run."""
    if investment is None:
        return None
    return [{"need": r["need"], "reason": r["reason"]} for r in investment if r["status"] == "unresolved"]


def _results(run: Path):
    if not (run / cs.COMPLIANCE_CSV).is_file():
        return None
    selection = pd.read_csv(run / cs.SELECTED_CSV)
    selection["reasons"] = selection["reasons"].str.split(";")
    investment = _read_csv(run / cs.INVESTMENT_CSV)
    return {
        "selection": _records(selection),
        "transformers": _read_csv(run / cs.SIZING_TRAFO_CSV),
        "compensation": _read_csv(run / cs.SIZING_COMP_CSV),
        "short_circuit": _read_csv(run / cs.SHORT_CIRCUIT_CSV),
        "compliance": _read_csv(run / cs.COMPLIANCE_CSV),
        "reactive": _read_csv(run / cs.REACTIVE_CSV),
        "requirement": json.loads((run / cs.REQUIREMENT_JSON).read_text()),
        # Part two: what the library was drawn on. All None when the run did not invest.
        "investment": investment,
        "cost": _read_csv(run / cs.COST_CSV),
        "compliance_invested": _read_csv(run / cs.COMPLIANCE_INVESTED_CSV),
        "history": _read_csv(run / cs.INVEST_HISTORY_CSV),
        "unresolved": _unresolved(investment),
        # Who buys the PCC switchgear: {"pcc_switchgear": "campus" | "grid_operator"}.
        "scope": (json.loads((run / cs.INVEST_SCOPE_JSON).read_text())
                  if investment is not None and (run / cs.INVEST_SCOPE_JSON).is_file() else None),
        # Part three D1a: the rate, money year and currency the costs were annualised on, and where
        # each came from. None when the run did not invest.
        "cost_basis": (json.loads((run / COST_BASIS_FILE).read_text())
                       if investment is not None and (run / COST_BASIS_FILE).is_file() else None),
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _effective_library(d: Path) -> Path:
    """The library a run now would use: the project's copy, else the shipped one."""
    own = d / LIBRARY_FILE
    return own if own.is_file() else Path(cs.ASSET_LIBRARY_PATH)


# ── the discount rate and the price year come from the project (part three, D1a) ──

FROM_SOLVER_CONFIG = "project solver config"
FROM_FINANCE_INPUTS = "project finance inputs"
FROM_LIBRARY = "asset library"


def _saved_solver_config(project):
    """The project's saved solver config, or None when it has none (the
    default is then not "the project's")."""
    if not (project_registry.project_dir(project) / SOLVER_CONFIG_FILE).is_file():
        return None
    return _solver_config(project)


def _money_year(cfg):
    """``FinanceInputs.currency_year`` of the project's stored finance inputs
    (``SolverConfig.finance``, the dict ``PUT /api/simulation/finance``
    validated), or None when there are none or the year is not stated."""
    fin = getattr(cfg, "finance", None)
    year = fin.get("currency_year") if isinstance(fin, dict) else None
    return year if isinstance(year, int) and not isinstance(year, bool) else None


def _project_rate(cfg) -> float:
    rate = getattr(cfg, "discount_rate", None)
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate < 1:
        raise HTTPException(
            status_code=422,
            detail=f"the project's discount rate {rate!r} (solver config) must lie in [0, 1) for the campus "
                   "asset library; change it in the solver settings")
    return float(rate)


def _engine_library(project, d: Path) -> tuple:
    """``(text, overridden, cost_basis)``: the library a run would hand to the
    engine now, and what its costs are annualised on.

    The library is the project's copy, else the shipped one. When the project
    has a saved solver config, its ``discount_rate`` replaces the library's
    (the library's own value is a stand-alone default for gridspine-only use);
    otherwise the library is handed over byte for byte. When the project has
    finance inputs with a ``currency_year``, that is the price year reported;
    it is never used to convert a cost: the library's costs stay in the
    library's price year, with no escalation, and ``price_year_mismatch`` says
    when the two differ. Raises ``ContractError`` when the library does not load."""
    path = _effective_library(d)
    lib = cs.load_asset_library(path)
    cfg = _saved_solver_config(project)
    rate, rate_from, text = lib["discount_rate"]["value"], FROM_LIBRARY, path.read_bytes().decode()
    overridden = cfg is not None
    if overridden:
        rate, rate_from = _project_rate(cfg), FROM_SOLVER_CONFIG
        data = yaml.safe_load(text)
        data["discount_rate"] = {"value": rate, "source": "assumed", "note": "the project's solver config discount rate"}
        text = yaml.safe_dump(data, sort_keys=False)
    year = _money_year(cfg) if cfg is not None else None
    library_year = lib["price_year"]
    basis = {"discount_rate": rate, "discount_rate_from": rate_from,
             "price_year": library_year if year is None else year,
             "price_year_from": FROM_LIBRARY if year is None else FROM_FINANCE_INPUTS,
             "library_price_year": library_year,
             "price_year_mismatch": year is not None and year != library_year,
             "currency": lib["currency"]}
    return text, overridden, basis


def _library_stale(project, d: Path) -> bool:
    """A run that invested kept the library it used and the basis it was
    costed on; the results are stale when the library a run would hand over
    now, or that basis, is another one. A run that did not invest kept
    neither, and does not depend on the library. A side file that cannot be
    read now means a rerun would not reproduce the run, so: stale."""
    used = d / "run" / USED_LIBRARY_FILE
    if not used.is_file():
        return False
    try:
        text, _overridden, basis = _engine_library(project, d)
        kept = d / "run" / COST_BASIS_FILE
        return (text.encode() != used.read_bytes()
                or (kept.is_file() and json.loads(kept.read_text()) != basis))
    except Exception:                                          # noqa: BLE001 - see the docstring
        return True


def _stale(project, d: Path) -> bool:
    manifest = d / "run" / cs.CAMPUS_MANIFEST
    campus = d / CAMPUS_FILE
    nc = project_registry.project_dir(project) / NETWORK_FILE
    if not manifest.is_file() or not campus.is_file() or not nc.is_file():
        return False
    m = json.loads(manifest.read_text())
    text = yaml.safe_dump(yaml.safe_load(campus.read_text()), sort_keys=False)
    return (hashlib.sha256(text.encode()).hexdigest() != m.get("campus_sha256")
            or _sha256_file(nc) != m.get("network_sha256")
            or _library_stale(project, d))


# ── the hub's own system cost ────────────────────────────────────────────────

_HUB_COST_CACHE: dict = {}
_HUB_COST_CACHE_MAX = 32


def hub_cost_of(n, cfg) -> dict:
    """The system cost of the solved network ``n``, from
    ``services.results.cost_breakdown.compute_cost_breakdown``, the helper
    behind the results pages and the value ledger. Capex (annualised, with the
    fixed O&M) plus opex, as solved.

    * a multi-period network: ``basis`` ``per_period`` and ``per_period``
      ``{period: cost per year}``, each period's years-weighted cost divided by
      its years (as ``value_flows`` reconciles it). ``total`` is the whole
      horizon;
    * a single-period network has no split: ``basis`` ``single_period``,
      ``per_period`` None, and ``total`` the cost as solved.

    Raises ``ValueError`` (with the reason) when there is nothing to report."""
    from services.period_utils import period_years_map, years_for_period
    from services.results.cost_breakdown import compute_cost_breakdown
    from services.solver_service import SolverConfig

    cb = compute_cost_breakdown(n, cfg if cfg is not None else SolverConfig())
    if cb is None or cb.get("total") is None or not math.isfinite(cb["total"]):
        raise ValueError("no cost statistics: the project's network carries none (not solved, or nothing priced)")
    by_period = cb.get("by_period") or []
    if not by_period:
        return {"basis": "single_period", "per_period": None, "total": float(cb["total"])}
    years = period_years_map(n)
    per_period = {}
    for entry in by_period:
        y = years_for_period(years, entry["period"])
        per_period[str(int(entry["period"]))] = float(entry["total"]) / y
    return {"basis": "per_period", "per_period": per_period, "total": float(cb["total"])}


def _solver_config(project):
    """The project's saved solver config, else the default; the same one the
    results pages price with."""
    from services.solver_service import SolverConfig

    path = project_registry.project_dir(project) / SOLVER_CONFIG_FILE
    if not path.is_file():
        return SolverConfig()
    from routers.projects import _solver_config_from_dict            # the loader the solve queue uses
    return _solver_config_from_dict(json.loads(path.read_text()))


def hub_cost(project) -> tuple:
    """``(hub_cost, reason)`` for the project's saved network: the one is None
    when the other is a string. Never raises."""
    nc = project_registry.project_dir(project) / NETWORK_FILE
    try:
        if not nc.is_file():
            return None, "the project has no saved network"
        cfg_path = nc.parent / SOLVER_CONFIG_FILE
        stamp = [(p.stat().st_mtime_ns, p.stat().st_size) if p.is_file() else None for p in (nc, cfg_path)]
        key = (str(nc), *stamp)
        if key in _HUB_COST_CACHE:
            return _HUB_COST_CACHE[key], None
        import pypsa
        with _netcdf_lock():
            n = pypsa.Network(str(nc))
        out = hub_cost_of(n, _solver_config(project))
    except Exception as exc:                                    # a comparison figure must never fail the state
        return None, str(exc) or type(exc).__name__
    if len(_HUB_COST_CACHE) >= _HUB_COST_CACHE_MAX:
        _HUB_COST_CACHE.clear()
    _HUB_COST_CACHE[key] = out
    return out, None


def get_state(project) -> dict:
    require_capacity_expansion(project)
    d = campus_dir(project)
    campus = d / CAMPUS_FILE
    notes = d / NOTES_FILE
    settings = d / SETTINGS_FILE
    results = _results(d / "run")
    cost, reason = hub_cost(project) if results else (None, "no study has run yet")
    return {
        "campus_yaml": campus.read_text() if campus.is_file() else None,
        "skipped": json.loads(notes.read_text()) if notes.is_file() else [],
        "profiles": profiles(project),
        "settings": json.loads(settings.read_text()) if settings.is_file() else None,
        "results": results,
        "stale": bool(results) and _stale(project, d),
        "hub_cost": cost,
        "hub_cost_reason": reason,
    }


INVESTMENT_NOTES = (
    "Every cost in the shipped asset library is an assumed order-of-magnitude placeholder, tagged assumed; "
    "replace it with quotes before relying on any figure in euro.",
    "Voltage excursions are met by compensation, not by changing a transformer tap: tap changers are not "
    "optimised, and an excursion that compensation cannot shrink is reported unresolved.",
    "A steady-state study at the critical hours, with an asset bought from the first period that needs it and "
    "no replacement; unresolved needs are priced but not summed.",
)


def get_investment(project) -> dict:
    """What the last run bought, for the copilot: the investment table, the
    electrical cost per period beside the hub's system cost, the unresolved
    needs, the escalation history and the compliance re-checked with the
    assets, with the caveats that go with the figures. ``investment`` is None,
    with a ``reason``, before a run or when the run did not invest."""
    state = get_state(project)
    res = state["results"]
    if res is None:
        return {"investment": None, "reason": "no study has run yet: run the study (campus_run_study) first",
                "notes": list(INVESTMENT_NOTES)}
    if res["investment"] is None:
        return {"investment": None, "reason": "the last run did not invest: run the study again with invest on",
                "stale": state["stale"], "notes": list(INVESTMENT_NOTES)}
    return {
        "investment": res["investment"], "cost": res["cost"], "unresolved": res["unresolved"],
        "history": res["history"], "compliance_invested": res["compliance_invested"], "scope": res["scope"],
        "hub_cost": state["hub_cost"], "hub_cost_reason": state["hub_cost_reason"],
        "library_is_default": _library_text(campus_dir(project))[1], "cost_basis": res["cost_basis"],
        "stale": state["stale"], "notes": list(INVESTMENT_NOTES),
    }


def draft(project, overwrite: bool = False) -> dict:
    """Write the campus file from the project's solved network."""
    require_capacity_expansion(project)
    nc = _network(project)
    d = campus_dir(project)
    if (d / CAMPUS_FILE).is_file() and not overwrite:
        raise HTTPException(status_code=409, detail="a campus file already exists; draft again with overwrite to replace your edits")
    with _netcdf_lock():
        try:
            result = cs.draft_from_project(nc)
        except ContractError as exc:
            raise _unprocessable(exc)
    text = yaml.safe_dump(result.spec, sort_keys=False)
    (d / CAMPUS_FILE).write_text(text)
    (d / NOTES_FILE).write_text(json.dumps(result.skipped))
    return {"campus_yaml": text, "skipped": result.skipped}


def save_campus(project, text: str) -> dict:
    """Validate the description by building it, then keep it."""
    require_capacity_expansion(project)
    if len(text.encode()) > MAX_CAMPUS_BYTES:
        raise HTTPException(status_code=413, detail=f"the campus file is larger than {MAX_CAMPUS_BYTES} bytes")
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"the campus file is not valid YAML: {exc}")
    try:
        cs.check_campus(spec)
    except ContractError as exc:
        raise _unprocessable(exc)
    (campus_dir(project) / CAMPUS_FILE).write_text(text)
    return {"campus_yaml": text}


# ── the asset library (plan C9) ──────────────────────────────────────────────

def _library_text(d: Path) -> tuple:
    own = d / LIBRARY_FILE
    if own.is_file():
        return own.read_text(), False
    return Path(cs.ASSET_LIBRARY_PATH).read_text(), True


def get_library(project) -> dict:
    """The library a study of this project buys from: ``{yaml, is_default}``.
    ``is_default`` is True until the user saves a copy."""
    require_capacity_expansion(project)
    text, is_default = _library_text(campus_dir(project))
    return {"yaml": text, "is_default": is_default}


def save_library(project, text: str) -> dict:
    """Validate the text with the engine's loader, then keep it as the
    project's copy. The loader's message names the entry and the field. A
    refused text leaves the previous copy as it was."""
    require_capacity_expansion(project)
    if len(text.encode()) > MAX_LIBRARY_BYTES:
        raise HTTPException(status_code=413, detail=f"the asset library is larger than {MAX_LIBRARY_BYTES} bytes")
    d = campus_dir(project)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".library-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        try:
            cs.load_asset_library(tmp)
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=422, detail=f"the asset library is not valid YAML: {exc}")
        except ContractError as exc:
            raise _unprocessable(exc)
        os.replace(tmp, d / LIBRARY_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {"yaml": text, "is_default": False}


def reset_library(project) -> dict:
    """Delete the project's copy, so the shipped default is used again."""
    require_capacity_expansion(project)
    d = campus_dir(project)
    (d / LIBRARY_FILE).unlink(missing_ok=True)
    return get_library(project)


def _settings(raw: dict, known: dict) -> dict:
    s = {**DEFAULTS, **{k: v for k, v in (raw or {}).items() if v is not None or k == "pf"}}
    unknown = sorted(set(s) - set(DEFAULTS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown setting(s) {unknown}")
    k = s["k"]
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise HTTPException(status_code=422, detail=f"k must be a positive integer, got {k!r}")
    if s["pf"] is not None and not (isinstance(s["pf"], (int, float)) and 0 < s["pf"] <= 1):
        raise HTTPException(status_code=422, detail=f"pf must be in (0, 1], got {s['pf']!r}")
    if not isinstance(s["margin"], (int, float)) or not 0 <= s["margin"] <= 2:
        raise HTTPException(status_code=422, detail=f"margin must be between 0 and 2, got {s['margin']!r}")
    if s["profile"] not in known:
        raise HTTPException(status_code=422, detail=f"unknown grid-code profile {s['profile']!r}; known {sorted(known)}")
    s["n_minus_1"] = bool(s["n_minus_1"])
    s["invest"] = bool(s["invest"])
    s["pcc_switchgear_by_operator"] = bool(s["pcc_switchgear_by_operator"])
    return s


def _clear_investment(run_dir: Path) -> None:
    """Remove the investment files of an earlier run, so that a run that does
    not invest, or fails part way, never sits beside another run's purchases."""
    for name in (cs.INVESTMENT_CSV, cs.COST_CSV, cs.INVESTED_YAML, cs.COMPLIANCE_INVESTED_CSV,
                 cs.INVEST_HISTORY_CSV, cs.INVEST_DISPATCH_CSV, cs.INVEST_SCOPE_JSON, USED_LIBRARY_FILE,
                 COST_BASIS_FILE):
        (run_dir / name).unlink(missing_ok=True)


def run(project, settings: dict) -> dict:
    """Prepare, rank and size the campus, then buy the electrical assets from
    the library (``invest``, default on); returns ``get_state``."""
    require_capacity_expansion(project)
    d = campus_dir(project)
    campus = d / CAMPUS_FILE
    if not campus.is_file():
        raise HTTPException(status_code=422, detail="there is no campus file yet; draft one from the project or save your own")
    s = _settings(settings, profiles(project))
    nc = _network(project)
    spec = yaml.safe_load(campus.read_text())
    run_dir = d / "run"
    criteria = SizingCriteria(margin=float(s["margin"]), n_minus_1=s["n_minus_1"])
    profile_dirs = (grid_codes_dir(project),)
    with _lock(run_dir):
        try:
            with _netcdf_lock():
                cs.prepare_campus(run_dir, spec, nc)
            cs.rank_campus(run_dir, k=s["k"])
            cs.size_campus(run_dir, criteria, profile=s["profile"], pf=s["pf"], profile_dirs=profile_dirs)
            _clear_investment(run_dir)               # the earlier run's purchases belong to its sizing, not this one's
            if s["invest"]:
                # The library is read once and kept: the engine buys from this
                # copy, and a later change to the library is then a different
                # file from the one the results were made with.
                used = run_dir / USED_LIBRARY_FILE
                try:
                    text, overridden, basis = _engine_library(project, d)
                    used.write_bytes(text.encode())
                    cs.invest_campus(run_dir, used if overridden or (d / LIBRARY_FILE).is_file() else None,
                                     criteria=criteria, profile=s["profile"], pf=s["pf"],
                                     profile_dirs=profile_dirs, pcc_switchgear=not s["pcc_switchgear_by_operator"])
                    (run_dir / COST_BASIS_FILE).write_text(json.dumps(basis))
                except BaseException:
                    _clear_investment(run_dir)       # never half a purchase beside the new sizing
                    raise
        except ContractError as exc:
            raise _unprocessable(exc)
        (d / SETTINGS_FILE).write_text(json.dumps(s))
    return get_state(project)
