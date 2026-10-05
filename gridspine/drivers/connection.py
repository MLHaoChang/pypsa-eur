"""The connection-point assessment as the backend sees it (increment 10).

``assess_facility`` runs ``static.connection.assess_connection`` for one
facility at every selected hour of a screened run, and stores the rows keyed
by a stable ``assessment_id`` in ``connection.csv``, in the run directory and
in each hour's bundle. Assessing the same facility again replaces its rows.

The hour's network is rebuilt as the handoff pass rebuilds it
(``load_case39_res`` plus ``apply_snapshot`` from the run's own dispatch and
loads), so the assessment is of the state that was handed over. The minimum
fault level at the POC comes from that hour's bundle (``fault_levels.csv``,
IEC 60909 min case), the same number the SCR screen uses.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pandas as pd

from gridspine.drivers.status import _bundles
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.connection import CONNECTION_COLUMNS, validate_connection
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.connection import Facility, assess_connection
from gridspine.static.contingency_set import branch_contingencies, unit_contingencies
from gridspine.static.loadflow import apply_snapshot
from gridspine.templates.grid_codes import load_grid_code

CONNECTION_CSV = "connection.csv"
DEFAULT_PROFILE = "eu_rfg_dcc_ce"
FACILITY_COLUMNS = ("assessment_id", "hour", "bus", "load_mw", "load_pf", "onsite_mw",
                    "onsite_converter", "profile")
COLUMNS = FACILITY_COLUMNS + CONNECTION_COLUMNS
_KEY = ("assessment_id", "hour", "check")


def _facility(spec: dict) -> tuple[Facility, str]:
    try:
        fac = Facility(
            bus=str(spec["bus"]),
            load_mw=float(spec.get("load_mw", 0.0)),
            load_pf=float(spec.get("load_pf", 0.98)),
            onsite_mw=float(spec.get("onsite_mw", 0.0)),
            onsite_converter=bool(spec.get("onsite_converter", True)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(f"facility is malformed: {exc!r}") from exc
    return fac, str(spec.get("profile") or DEFAULT_PROFILE)


def assessment_id(fac: Facility, profile: str) -> str:
    """Stable across runs and processes: the same facility, the same id."""
    canon = json.dumps({"bus": fac.bus, "load_mw": round(fac.load_mw, 6), "load_pf": round(fac.load_pf, 6),
                        "onsite_mw": round(fac.onsite_mw, 6), "onsite_converter": fac.onsite_converter,
                        "profile": profile}, sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:12]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(COLUMNS))


def _read(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return _empty()
    return validate_connection(pd.read_csv(path, dtype={"assessment_id": str}), key=_KEY)


def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".connection-", suffix=".csv")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    keep = existing[~existing["assessment_id"].isin(set(new["assessment_id"]))]
    frames = [f for f in (keep, new) if len(f)]
    out = pd.concat(frames, ignore_index=True) if frames else _empty()
    return validate_connection(out[list(COLUMNS)], key=_KEY)


def connection_table(run_dir) -> pd.DataFrame:
    """Every stored assessment of this run (empty before the first)."""
    return _read(Path(run_dir) / CONNECTION_CSV)


def _selected_hours(run_dir: Path) -> list:
    path = run_dir / "selected.csv"
    bundles = _bundles(run_dir)
    if not path.is_file() or not bundles:
        raise ContractError(
            "a connection assessment needs a completed, screened run (selected hours and bundles)"
        )
    return sorted(int(h) for h in pd.read_csv(path)["hour"] if int(h) in bundles)


def _sk_min(bundle: Path, bus: str):
    path = bundle / "fault_levels.csv"
    if not path.is_file():
        return None
    fl = pd.read_csv(path)
    hit = fl[(fl["bus"] == bus) & (fl["case"] == "min")]
    return float(hit["sk_mva"].iloc[0]) if len(hit) else None


def assess_facility(run_dir, spec: dict, on_hour=None) -> pd.DataFrame:
    """Assess the facility ``spec`` ({bus, load_mw, load_pf, onsite_mw,
    onsite_converter, profile}) at every selected hour; store and return the rows."""
    run_dir = Path(run_dir)
    hours = _selected_hours(run_dir)
    fac, profile_name = _facility(spec)
    profile = load_grid_code(profile_name)
    net = load_case39_res()
    if fac.bus not in set(net.bus["name"]):
        raise ContractError(f"unknown bus {fac.bus!r}; the grid's buses are {list(net.bus['name'])}")
    registry = registry_from_net(net)
    dispatch = validate_dispatch(pd.read_csv(run_dir / "dispatch.csv"))
    loads = validate_loads(pd.read_csv(run_dir / "loads.csv"))
    n1_set = pd.concat([branch_contingencies(net), unit_contingencies(registry)], ignore_index=True)
    bundles = _bundles(run_dir)
    aid = assessment_id(fac, profile_name)

    frames = []
    for i, hour in enumerate(hours, start=1):
        apply_snapshot(net, dispatch, loads, hour=hour, registry=registry)
        rows = assess_connection(net, fac, n1_set, profile, sk_min_mva=_sk_min(bundles[hour], fac.bus))
        frames.append(rows.assign(
            assessment_id=aid, hour=hour, bus=fac.bus, load_mw=fac.load_mw, load_pf=fac.load_pf,
            onsite_mw=fac.onsite_mw, onsite_converter=fac.onsite_converter, profile=profile_name,
        ))
        if on_hour is not None:
            on_hour(i, len(hours))
    new = validate_connection(pd.concat(frames, ignore_index=True)[list(COLUMNS)], key=_KEY)
    _write_atomic(_upsert(connection_table(run_dir), new), run_dir / CONNECTION_CSV)
    for hour in hours:
        path = bundles[hour] / CONNECTION_CSV
        _write_atomic(_upsert(_read(path), new[new["hour"] == hour]), path)
    return new
