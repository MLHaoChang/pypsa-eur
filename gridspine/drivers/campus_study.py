"""The campus study as the backend sees it (plan C2).

A campus study is separate from the case39 year study. It runs no unit
commitment, no N-2 and no PSS/E handoff. It takes two inputs:

* a **solved capacity-expansion project** (``network.nc``), the assets and
  their hourly dispatch per investment period;
* a **campus description** (``gridspine.ingest.campus``), the electrical
  plant between those assets and the grid.

From these it prepares a run directory that the ranking (C3) and the sizing
(C4) read:

``campus.yaml``
    The campus exactly as used.
``campus_hourly.csv``, ``campus_pcc.csv``
    The hourly tables (``gridspine.schema.campus``).
``campus_manifest.json``
    Both inputs by sha256, the periods and the unit count. A later stage can
    then recognise a run whose project or campus has changed since.

``rank_campus`` then ranks the prepared hours (``gridspine.ranking.campus``).
It writes ``campus_metrics.csv`` and ``campus_selected.csv``, one row per
selected (period, hour), with its reasons joined by ``;``.

``size_campus`` solves every selected hour, intact and transformer N-1
(``gridspine.static.campus_flow``). It writes the per-hour results
(``campus_lf_trafo.csv``, ``campus_lf_bus.csv`` and ``campus_lf_pcc.csv``,
with a ``case`` column) and the transformer sizing
(``campus_sizing_trafo.csv``). It also corrects every selected hour into
the PCC reactive band (``gridspine.static.campus_reactive``), writing
``campus_reactive.csv``, the compensation sizing
(``campus_sizing_compensation.csv``), and the requirement it applied
(``campus_requirement.json``).
- The band comes from the grid-code profile, or from the study's
  connection-agreement power factor.
- Its P_ref is the campus file's ``p_connection_mw``, or else the year's
  peak import. The requirement records which one was used.
Finally it runs IEC 60909 at every bus for each period, with the units
installed in that period energised, against any switchgear ratings
(``campus_short_circuit.csv``, ``gridspine.static.campus_sc``), and
assembles the PCC compliance report (``campus_compliance.csv``,
``gridspine.static.campus_compliance``) against the chosen profile.

``draft_from_project`` is the "generate" step. It drafts a campus from the
saved project, for the user to edit before ``prepare_campus``.

Everything is validated before anything is written: the campus is built,
the tables are made and checked, and only then do the files land. Each file
is replaced atomically, so a refused campus leaves the run as it was.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from gridspine.ingest.campus import Campus, build_campus
from gridspine.producers.campus import CampusDraft, campus_hourly, draft_campus
from gridspine.producers.pypsa_nodal import load_solved_network
from gridspine.ranking.campus import campus_metrics, select_campus_hours
from gridspine.schema.campus import HOURLY_CSV, PCC_CSV, validate_hourly, validate_pcc
from gridspine.schema.contracts import ContractError
from gridspine.static.campus_flow import SizingCriteria, size_transformers, solve_cases
from gridspine.static.campus_reactive import reactive_need, requirement_from, size_compensation
from gridspine.static.campus_compliance import campus_compliance
from gridspine.static.campus_sc import campus_fault_levels
from gridspine.templates.grid_codes import load_grid_code

CAMPUS_YAML = "campus.yaml"
CAMPUS_MANIFEST = "campus_manifest.json"
METRICS_CSV = "campus_metrics.csv"
SELECTED_CSV = "campus_selected.csv"
DEFAULT_K = 3
LF_TRAFO_CSV = "campus_lf_trafo.csv"
LF_BUS_CSV = "campus_lf_bus.csv"
LF_PCC_CSV = "campus_lf_pcc.csv"
SIZING_TRAFO_CSV = "campus_sizing_trafo.csv"
REACTIVE_CSV = "campus_reactive.csv"
SIZING_COMP_CSV = "campus_sizing_compensation.csv"
REQUIREMENT_JSON = "campus_requirement.json"
SHORT_CIRCUIT_CSV = "campus_short_circuit.csv"
COMPLIANCE_CSV = "campus_compliance.csv"
DEFAULT_PROFILE = "eu_rfg_dcc_ce"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def draft_from_project(network_path) -> CampusDraft:
    """A campus draft from the saved, solved project at ``network_path``."""
    return draft_campus(load_solved_network(network_path))


def prepare_campus(run_dir, campus: dict, network_path) -> dict:
    """Validate ``campus`` against the project at ``network_path``, then write
    the run (see the module docstring). Returns a summary: ``periods``,
    ``units``, ``hours_per_period``."""
    network_path = Path(network_path)
    build_campus(campus)                                   # refuse before writing
    n = load_solved_network(network_path)
    hourly, pcc = campus_hourly(n, campus)
    periods = sorted(int(p) for p in pcc["period"].unique())
    campus_text = yaml.safe_dump(campus, sort_keys=False)
    manifest = {
        "campus_sha256": hashlib.sha256(campus_text.encode()).hexdigest(),
        "network_path": str(network_path),
        "network_sha256": _sha256_file(network_path),
        "periods": periods,
        "units": int(hourly["unit_id"].nunique()),
        "hours_per_period": int(pcc.groupby("period").size().max()),
    }
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(run_dir / CAMPUS_YAML, campus_text)
    _write_text_atomic(run_dir / HOURLY_CSV, hourly.to_csv(index=False))
    _write_text_atomic(run_dir / PCC_CSV, pcc.to_csv(index=False))
    _write_text_atomic(run_dir / CAMPUS_MANIFEST, json.dumps(manifest, indent=2))
    return {"periods": periods, "units": manifest["units"], "hours_per_period": manifest["hours_per_period"]}


def _require(run_dir: Path, name: str) -> Path:
    path = run_dir / name
    if not path.is_file():
        raise ContractError(f"{path} is missing; prepare the campus study first")
    return path


def campus_tables(run_dir):
    """``(hourly, pcc)`` of a prepared run, validated."""
    run_dir = Path(run_dir)
    hourly = validate_hourly(pd.read_csv(_require(run_dir, HOURLY_CSV), dtype={"unit_id": str}))
    pcc = validate_pcc(pd.read_csv(_require(run_dir, PCC_CSV)))
    return hourly, pcc


def load_run_campus(run_dir) -> Campus:
    """The campus of a prepared run, built from its stored ``campus.yaml``."""
    return build_campus(yaml.safe_load(_require(Path(run_dir), CAMPUS_YAML).read_text()))


def rank_campus(run_dir, k: int = DEFAULT_K) -> pd.DataFrame:
    """Rank the prepared run's hours and select the critical ones (k per
    criterion, per period). Writes ``campus_metrics.csv`` and
    ``campus_selected.csv``; returns the selection."""
    run_dir = Path(run_dir)
    hourly, pcc = campus_tables(run_dir)
    spec = yaml.safe_load(_require(run_dir, CAMPUS_YAML).read_text())
    metrics = campus_metrics(hourly, pcc, spec)
    selection = select_campus_hours(metrics, k=k)
    _write_text_atomic(run_dir / METRICS_CSV, metrics.reset_index().to_csv(index=False))
    _write_text_atomic(run_dir / SELECTED_CSV,
                       selection.assign(reasons=selection["reasons"].map(";".join)).to_csv(index=False))
    return selection


def selected_hours(run_dir) -> pd.DataFrame:
    """The stored selection: ``period``, ``hour``, ``reasons`` (a list)."""
    df = pd.read_csv(_require(Path(run_dir), SELECTED_CSV))
    return df.assign(reasons=df["reasons"].str.split(";"))


def _p_ref(campus, pcc: pd.DataFrame):
    p = campus.params
    hit = p[(p["param"] == "p_connection_mw")]
    if len(hit):
        return float(hit["value"].iloc[0]), "campus file p_connection_mw"
    return float(pcc["import_mw"].abs().max()), "peak |import| over every hour of the prepared periods"


def size_campus(run_dir, criteria: SizingCriteria = SizingCriteria(), profile: str = DEFAULT_PROFILE,
                pf: float | None = None) -> dict:
    """Solve the selected hours, size the transformers, and correct each hour
    into the PCC reactive band. Writes the per-hour tables, the two sizing
    tables and the requirement. Returns ``{"transformers", "compensation",
    "requirement"}``."""
    run_dir = Path(run_dir)
    hourly, pcc = campus_tables(run_dir)
    selection = selected_hours(run_dir)
    campus = load_run_campus(run_dir)
    p_ref, p_ref_from = _p_ref(campus, pcc)
    req = requirement_from(load_grid_code(profile), p_ref, pf=pf)
    flows, reactive, trafo_rows, bus_rows, pcc_rows, q_rows = {}, {}, [], [], [], []
    for period, hour in selection[["period", "hour"]].itertuples(index=False):
        period, hour = int(period), int(hour)
        rows = hourly[(hourly["period"] == period) & (hourly["hour"] == hour)]
        cases = solve_cases(campus, rows)
        flows[(period, hour)] = cases
        for case, f in cases.items():
            key = {"period": period, "hour": hour, "case": case}
            pcc_rows.append({**key, "converged": f.converged, "p_mw": f.pcc_p_mw, "q_mvar": f.pcc_q_mvar,
                             "losses_mw": f.losses_mw})
            trafo_rows += [{**key, **r} for r in f.trafo.to_dict("records")]
            bus_rows += [{**key, **r} for r in f.bus.to_dict("records")]
        r = reactive_need(campus, rows, req)
        reactive[(period, hour)] = r
        q_rows.append({"period": period, "hour": hour, "converged": r.converged, "q0_mvar": r.q0_mvar,
                       "q_final_mvar": r.q_final_mvar, "q_inverters_mvar": r.q_inverters_mvar,
                       "q_comp_mvar": r.q_comp_mvar, "compliant_without": r.compliant_without})
    sizing = size_transformers(flows, campus, criteria)
    comp = size_compensation(reactive, criteria)
    sc_frames = []
    for period, block in hourly.groupby("period"):
        installed = set(block.loc[block["status"] == 1, "unit_id"])
        sc_frames.append(campus_fault_levels(campus, installed).assign(period=int(period)))
    short_circuit = pd.concat(sc_frames, ignore_index=True)
    requirement = {"q_limit_mvar": req.q_limit_mvar, "clause": req.clause, "source": req.source,
                   "profile": profile, "pf": pf, "p_ref_mw": p_ref, "p_ref_from": p_ref_from}
    _write_text_atomic(run_dir / LF_TRAFO_CSV, pd.DataFrame(trafo_rows).to_csv(index=False))
    _write_text_atomic(run_dir / LF_BUS_CSV, pd.DataFrame(bus_rows).to_csv(index=False))
    _write_text_atomic(run_dir / LF_PCC_CSV, pd.DataFrame(pcc_rows).to_csv(index=False))
    _write_text_atomic(run_dir / SIZING_TRAFO_CSV, sizing.to_csv(index=False))
    _write_text_atomic(run_dir / REACTIVE_CSV, pd.DataFrame(q_rows).to_csv(index=False))
    _write_text_atomic(run_dir / SIZING_COMP_CSV, comp.to_csv(index=False))
    _write_text_atomic(run_dir / REQUIREMENT_JSON, json.dumps(requirement, indent=2))
    _write_text_atomic(run_dir / SHORT_CIRCUIT_CSV, short_circuit.to_csv(index=False))
    net = campus.net
    compliance = campus_compliance(
        bus=pd.DataFrame(bus_rows), trafo=pd.DataFrame(trafo_rows), reactive=pd.DataFrame(q_rows),
        sizing=sizing, compensation=comp, short_circuit=short_circuit, requirement=requirement,
        profile=load_grid_code(profile), pcc_bus=str(net.bus.at[int(net.ext_grid["bus"].iloc[0]), "name"]),
        bus_kv=dict(zip(net.bus["name"].astype(str), net.bus["vn_kv"].astype(float))),
    )
    _write_text_atomic(run_dir / COMPLIANCE_CSV, compliance.to_csv(index=False))
    return {"transformers": sizing, "compensation": comp, "requirement": requirement,
            "short_circuit": short_circuit, "compliance": compliance}
