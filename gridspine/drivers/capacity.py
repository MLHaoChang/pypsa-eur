"""Connection capacity as the backend sees it (increment 9).

The study writes a DC estimate for every bus, both kinds, at each selected
hour (``capacity.csv`` in the run directory and in each hour's bundle). That
screen is cheap enough to cover everything. The AC answer is computed on
demand for the bus an engineer picks, because it costs about half a second per
bus, kind and hour, and it is cached back into the run directory and into the
bundles, so a downloaded bundle carries it.

The hour's network is rebuilt exactly as the handoff pass rebuilds it:
``load_case39_res`` plus ``apply_snapshot`` from the run's own
``dispatch.csv`` and ``loads.csv``. The capacity is therefore measured
against the state that was handed over, not a re-solve.
"""
import os
import tempfile
from pathlib import Path

import pandas as pd

from gridspine.drivers.status import _bundles
from gridspine.ingest.pandapower_source import load_case39_res, registry_from_net
from gridspine.schema.capacity import CAPACITY_CSV, KINDS, validate_capacity
from gridspine.schema.contracts import ContractError
from gridspine.schema.dispatch import validate_dispatch, validate_loads
from gridspine.static.capacity import DEFAULT_CRITERIA, capacity_ac
from gridspine.static.contingency_set import branch_contingencies, unit_contingencies
from gridspine.static.loadflow import apply_snapshot

def _write_atomic(df: pd.DataFrame, path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".capacity-", suffix=".csv")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return validate_capacity(pd.DataFrame(columns=[
            "bus", "hour", "kind", "capacity_mw", "dc_estimate_mw", "binding_kind",
            "binding_element", "binding_contingency", "binding_preexisting", "method"]))
    return validate_capacity(pd.read_csv(path, dtype={"binding_element": object, "binding_contingency": object}))


def _upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Replace rows keyed (bus, hour, kind) with ``new``'s, keep the rest."""
    key = ["bus", "hour", "kind"]
    keep = existing.merge(new[key], on=key, how="left", indicator=True)
    keep = existing[(keep["_merge"] == "left_only").to_numpy()]
    out = pd.concat([keep, new], ignore_index=True).sort_values(key, kind="mergesort")
    return validate_capacity(out)


def capacity_table(run_dir) -> pd.DataFrame:
    """The run's capacity table: the DC screen, with any AC answers computed since."""
    run_dir = Path(run_dir)
    path = run_dir / CAPACITY_CSV
    if not path.is_file():
        raise ContractError("this run has no capacity table; run the study with screening on")
    return _read(path)


def compute_capacity_ac(run_dir, bus: str, kind: str, criteria=DEFAULT_CRITERIA, on_hour=None) -> pd.DataFrame:
    """AC capacity at ``bus`` for ``kind`` at every selected hour of the run,
    cached into ``capacity.csv`` and into each hour's bundle. Returns the rows."""
    run_dir = Path(run_dir)
    table = capacity_table(run_dir)
    if kind not in KINDS:
        raise ContractError(f"kind must be one of {list(KINDS)}, got {kind!r}")
    dispatch = validate_dispatch(pd.read_csv(run_dir / "dispatch.csv"))
    loads = validate_loads(pd.read_csv(run_dir / "loads.csv"))
    hours = sorted(int(h) for h in table["hour"].unique())
    net = load_case39_res()
    if bus not in set(net.bus["name"]):
        raise ContractError(f"unknown bus {bus!r}; the grid's buses are {list(net.bus['name'])}")
    registry = registry_from_net(net)
    n1_set = pd.concat([branch_contingencies(net), unit_contingencies(registry)], ignore_index=True)

    rows = []
    for i, hour in enumerate(hours, start=1):
        apply_snapshot(net, dispatch, loads, hour=hour, registry=registry)
        dc = table[(table["bus"] == bus) & (table["hour"] == hour) & (table["kind"] == kind)]
        start = float(dc["dc_estimate_mw"].iloc[0]) if len(dc) else None
        r = capacity_ac(net, bus, kind, n1_set, criteria, start_mw=start)
        rows.append({
            "bus": bus, "hour": hour, "kind": kind, "capacity_mw": r["capacity_mw"],
            "dc_estimate_mw": start if start is not None else float("nan"),
            "binding_kind": r["binding_kind"], "binding_element": r["binding_element"],
            "binding_contingency": r["binding_contingency"],
            "binding_preexisting": r["binding_preexisting"], "method": "ac",
        })
        if on_hour is not None:
            on_hour(i, len(hours))
    new = validate_capacity(pd.DataFrame(rows))
    _write_atomic(_upsert(table, new), run_dir / CAPACITY_CSV)
    for hour, bundle in _bundles(run_dir).items():
        per_hour = new[new["hour"] == hour]
        if len(per_hour):
            _write_atomic(_upsert(_read(bundle / CAPACITY_CSV), per_hour), bundle / CAPACITY_CSV)
    return new
