"""
The campus PowerFactory handoff (DGS + harmonic sources), its closed-loop
read-back, and the field background in the harmonic screen (GC-05).
"""

import json

import pandas as pd
import pytest

from gridspine.drivers.campus_study import (
    HARMONIC_SOURCES_CSV,
    HARMONICS_CSV,
    campus_tables,
    harmonics_campus,
    import_background_campus,
    load_run_campus,
    powerfactory_campus,
)
from gridspine.ingest.background_harmonics import combine
from gridspine.readback.results import DGS_NAMES_CSV, read_back
from gridspine.schema.contracts import ContractError
from tests.gridspine.test_dgs_writer import read_dgs
from tests.gridspine.test_harmonics_campus import _hub, _run


@pytest.fixture
def run(tmp_path):
    run, _ = _run(tmp_path, _hub())
    return run


def _hour(run):
    hourly, _ = campus_tables(run)
    return int(hourly["period"].iloc[0]), int(hourly["hour"].iloc[0])


@pytest.fixture
def case(run):
    (out,) = powerfactory_campus(run, hours=[_hour(run)])
    return out


def test_one_directory_per_hour_with_the_network_and_its_load_flow(run, case):
    period, hour = _hour(run)
    name = load_run_campus(run).name
    assert case.name == f"{name}_p{period}_h{hour}"
    manifest = json.loads((case / "manifest.json").read_text())
    assert all((case / f).is_file() for f in manifest["files"])
    assert (
        manifest["converged"]
        and manifest["hour"] == hour
        and manifest["pending_verification"]
    )
    dgs = read_dgs(case / f"{case.name}.dgs")
    net = load_run_campus(run).net
    assert len(dgs["ElmTerm"]) == len(net.bus) and len(dgs["ElmXnet"]) == 1
    assert dgs["ElmXnet"][0]["snssmin"] == float(net.ext_grid["s_sc_min_mva"].iloc[0])
    with pytest.raises(ContractError, match="no hour 9999"):
        powerfactory_campus(run, hours=[(period, 9999)])


def test_the_process_units_carry_their_spectra_and_bare_converters_are_listed(case):
    hs = pd.read_csv(case / HARMONIC_SOURCES_CSV, keep_default_na=False).set_index(
        "pp_name"
    )
    statuses = hs["status"].to_dict()
    # network elements are the campus unit ids; the project's links map onto them by pypsa_name
    assert (
        statuses["COMPRESSOR"] == "spectrum"
        and hs.at["COMPRESSOR", "spectrum_source"] == "demo default (6-pulse)"
    )
    assert statuses["PUMP"] == "spectrum"
    assert statuses["PV"] == "no spectrum" and statuses["BESS"] == "no spectrum"
    assert "DC_LOAD" not in statuses  # a plain load: nothing to list
    manifest = json.loads((case / "manifest.json").read_text())
    assert manifest["no_spectrum"] == ["BESS", "PV"]
    dgs = read_dgs(case / f"{case.name}.dgs")
    by_id = {r["ID"]: r for r in dgs["TypHmccur"]}
    compressor = next(r for r in dgs["ElmLod"] if r["loc_name"] == "COMPRESSOR")
    assert by_id[compressor["phmc"]]["norder"][:2] == [5, 7]


def _pf_exports(case, tmp_path, vm_shift=0.0, bad_bus=None):
    """A synthetic PowerFactory export of the DGS network: keyed by DGS names."""
    names = pd.read_csv(case / DGS_NAMES_CSV, keep_default_na=False)
    to_dgs = dict(
        zip(
            names.loc[names["pp_table"] == "bus", "pp_name"],
            names.loc[names["pp_table"] == "bus", "dgs_name"],
        )
    )
    bus = pd.read_csv(case / "lf_bus.csv")
    pf = pd.DataFrame(
        {
            "bus_name": bus["bus"].map(to_dgs),
            "vm_pu": bus["vm_pu"] + vm_shift,
            "va_degree": bus["va_degree"],
        }
    )
    if bad_bus:
        pf.loc[0, "bus_name"] = bad_bus
    br = pd.read_csv(case / "lf_branch_flow.csv", dtype={"ckt": str})
    keyed = names[names["pp_table"].isin(["line", "trafo"])]
    by_triple = {
        (r.from_bus, r.to_bus, str(r.ckt)): r.dgs_name for r in keyed.itertuples()
    }
    pfb = pd.DataFrame(
        {
            "branch_name": [
                by_triple[(r.from_bus, r.to_bus, str(r.ckt))] for r in br.itertuples()
            ],
            "p_from_mw": br["p_from_mw"] + 0.05,
            "q_from_mvar": br["q_from_mvar"],
            "loading_percent": br["loading_percent"],
        }
    )
    pf.to_csv(tmp_path / "pf_bus_in.csv", index=False)
    pfb.to_csv(tmp_path / "pf_br_in.csv", index=False)
    return tmp_path / "pf_bus_in.csv", tmp_path / "pf_br_in.csv"


def test_a_powerfactory_export_keyed_by_dgs_names_reads_back_within_tolerance(
    case, tmp_path
):
    bus_csv, br_csv = _pf_exports(case, tmp_path, vm_shift=0.001)
    summary = read_back(case, bus_csv, br_csv)
    assert summary["pass"] and summary["bus"]["n"] == summary["bus"]["n_ok"]
    assert summary["branches"]["n"] >= 1 and summary["branches"]["pass"]
    bus_csv, _ = _pf_exports(case, tmp_path, vm_shift=0.05)
    assert not read_back(case, bus_csv)["pass"]  # 5 % off: the gate catches it


def test_a_name_the_dgs_never_wrote_is_refused(case, tmp_path):
    bus_csv, _ = _pf_exports(case, tmp_path, bad_bus="Terminal(1)")
    with pytest.raises(ContractError, match="not in the DGS export"):
        read_back(case, bus_csv)


BG = "order,u_h_pct_p95\n5,2.0\n7,0.9\n"


def test_the_background_adds_a_with_background_row_by_the_iec_sum(run, tmp_path):
    plain = harmonics_campus(run)
    rec = import_background_campus(
        run, BG, source="PQ survey", measured_on="2026-09-30"
    )
    assert rec["poc"] == plain.iloc[0]["poc"]
    out = harmonics_campus(run)
    poc_rows = out[out["level"] == "poc"]
    assert len(poc_rows) == 2 and len(out) == len(plain) + 1
    plant, bg = poc_rows.iloc[0], poc_rows.iloc[1]
    assert bg["label"].startswith("with background") and not str(
        plant["label"]
    ).startswith("with background")
    expected = combine(plant["u_h_pct"], {5: 2.0, 7: 0.9})
    assert bg["u_h_pct"] == pytest.approx(expected)
    assert bg["u_h_pct"][5] > plant["u_h_pct"][5]
    assert "PQ survey" in bg["note"] and "2026-09-30" in bg["note"]
    assert "no background measured at order(s) 11" in bg["note"]
    assert "with background" in (run / HARMONICS_CSV).read_text()


def test_a_bad_background_is_refused_and_stores_nothing(run):
    with pytest.raises(ContractError, match="not a finite value >= 0"):
        import_background_campus(
            run,
            "order,u_h_pct_p95\n5,-1\n",
            source="PQ survey",
            measured_on="2026-09-30",
        )
    assert len(harmonics_campus(run).query("level == 'poc'")) == 1
