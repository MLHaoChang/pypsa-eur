"""
PSS/E import (``gridspine.ingest.psse_import``): RAW file to ``pypsa.Network``.

Fixtures are PSS/E v33 files from PowerModels.jl's test data (BSD-3, see
``fixtures/psse/README.md``); ``case14`` and ``case30`` carry a solved PSS/E
power flow, so their bus voltages are the ground truth for ours. Our own
exports of case39 round-trip through the importer.
"""

import json
import logging
import math
from pathlib import Path

import numpy as np
import pandapower as pp
import pytest

from gridspine.handoff.raw_writer import write_raw
from gridspine.ingest.import_result import ImportFormatError, ImportResult
from gridspine.ingest.psse_import import read_psse
from gridspine.ingest.psse_raw_parse import read_raw

FIX = Path(__file__).parent / "fixtures" / "psse"
logging.getLogger("pypsa").setLevel(logging.ERROR)


def _read(name, **options):
    return read_psse([FIX / f"{name}.raw"], options)


# ---------------------------------------------------------------- contract
def test_result_keeps_the_contract():
    res = _read("case14")
    assert isinstance(res, ImportResult)
    rep = res.report
    assert rep.source_format == "psse" and rep.source_files == ["case14.raw"]
    json.dumps(rep.to_dict(), allow_nan=False)
    side = res.sidecars["psse_case.json"]
    json.dumps(side, allow_nan=False)
    assert (
        side["psse_version"],
        side["system_base_mva"],
        side["base_frequency_hz"],
    ) == (33, 100.0, 60.0)
    assert side["areas"] == [
        {"I": 1, "ARNAME": "1", "ISW": 0, "PDES": 0.0, "PTOL": 1.0}
    ]


@pytest.mark.parametrize(
    "name, counts",
    [
        # hand-counted from the files (see test_psse_raw_parse for the records)
        (
            "case14",
            {
                "Buses": 14,
                "Loads": 11,
                "Generators": 5,
                "Lines": 17,
                "Two-winding transformers": 3,
                "Switched shunts": 1,
                "Areas": 1,
                "Zones": 1,
                "Owners": 1,
            },
        ),
        (
            "three_winding_test",
            {"Buses": 3, "Loads": 1, "Generators": 2, "Three-winding transformers": 5},
        ),
        (
            "vsc-hvdc_test",
            {
                "Buses": 2,
                "Loads": 1,
                "Generators": 1,
                "Branches imported as transformers": 1,
            },
        ),
    ],
)
def test_counts_in_plain_names(name, counts):
    assert _read(name).report.counts == counts


def test_every_record_accounted(tmp_path):
    """Records read = assets made + rows listed as not mapped, section by section."""
    case = read_raw(FIX / "case30.raw")
    n = _read("case30").network
    t = case.tables
    assert len(n.buses) == len(t["bus"])
    assert len(n.loads) == len(t["load"])
    assert len(n.generators) == len(t["generator"])
    assert len(n.lines) == len(t["branch"])
    assert len(n.transformers) == len(t["transformer"])
    assert (n.shunt_impedances["shunt_kind"] == "switched").sum() == len(
        t["switched_shunt"]
    )


def test_unmapped_dc_link_listed():
    rep = _read("two-terminal-hvdc_test").report
    assert len(rep.unmapped) == 1 and "no DC link mapping" in rep.unmapped[0].reason


def test_dyr_alongside_is_listed_not_ignored(tmp_path):
    dyr = tmp_path / "case14.dyr"
    dyr.write_text("1 'GENCLS' 1 6.0 0.0 /\n")
    rep = read_psse([FIX / "case14.raw", dyr], {}).report
    assert [u.source_item for u in rep.unmapped] == ["case14.dyr"]
    assert rep.source_files == ["case14.raw", "case14.dyr"]


@pytest.mark.parametrize("files", [[], ["a.raw", "b.raw"], ["a.dyr"]])
def test_needs_exactly_one_raw(files, tmp_path):
    with pytest.raises(ImportFormatError, match="exactly one .raw"):
        read_psse([tmp_path / f for f in files], {})


def test_unreadable_file_names_it(tmp_path):
    bad = tmp_path / "broken.raw"
    bad.write_text(" 0, 100.0, 34, 0, 0, 50.0\nt\nt\nQ\n")
    with pytest.raises(
        ImportFormatError,
        match="broken.raw: RAW line 1: PSS/E version 34 not supported",
    ):
        read_psse([bad], {})


def test_columns_survive_netcdf(tmp_path):
    import pypsa

    n = _read("case14").network
    n.export_to_netcdf(tmp_path / "n.nc")
    m = pypsa.Network(tmp_path / "n.nc")
    assert list(m.buses["psse_number"]) == list(n.buses["psse_number"])
    assert list(m.buses["area"]) == list(n.buses["area"])
    assert list(m.generators["q_max_mvar"]) == list(n.generators["q_max_mvar"])


# ---------------------------------------------------------------- mapping
def test_buses_and_generators_case14():
    n = _read("case14").network
    b = n.buses.loc["Bus 1     HV"]
    assert (b["psse_number"], b["v_nom"], b["bus_type"], b["area"]) == (
        1,
        138.0,
        "swing bus",
        "1",
    )
    g = n.generators
    assert g.loc["Gen Bus 1     HV 1", "control"] == "Slack"
    assert (
        g.loc["Gen Bus 2     HV 1", "control"] == "PV"
    )  # QT 50 > QB -40 on a type-2 bus
    assert g.loc["Gen Bus 2     HV 1", "p_nom"] == 140.0  # PT
    assert n.buses.at["Bus 2     HV", "v_mag_pu_set"] == 1.045  # VS holds the bus


def test_switched_shunt_held_at_binit():
    res = _read("case14")
    sh = res.network.shunt_impedances.loc["Switched shunt Bus 9     LV 1"]
    assert sh["b"] == pytest.approx(19.0 / 138.0**2)  # BINIT Mvar at 1.0 pu -> S
    assert sh["blocks_mvar"] == "1x19"
    assert any(
        a.parameter == "susceptance setting" and a.value == 19.0
        for a in res.report.assumed
    )


def _two_winding_raw(tmp_path, cm, mag, sb=100.0, nomv1=0.0, stat=1):
    text = (
        f""" 0, 100.00, 33, 0, 0, 50.00
mag test
second
1,'HV', 110.0,3
2,'LV', 20.0,1
0
2,'1',1,1,1,10.0,2.0
0
0
1,'1',0.0,0.0,99.0,-99.0,1.0,0,100.0
0
0
1,2,0,'1',1,1,{cm},{mag[0]},{mag[1]},2,'T',{stat}
0.004,0.1,{sb}
1.0,{nomv1},0.0,50.0,50.0,50.0
1.0,0.0
0
"""
        + "0\n" * 13
        + "Q\n"
    )
    p = tmp_path / "t.raw"
    p.write_text(text)
    return read_psse([p], {}).network


def test_magnetising_cm1_exact_shunt_at_winding1_bus(tmp_path):
    n = _two_winding_raw(tmp_path, cm=1, mag=(0.002, -0.01))
    (nm,) = n.shunt_impedances.index
    sh = n.shunt_impedances.loc[nm]
    assert sh["bus"] == "HV"  # never at bus J, never split
    assert (sh["g"], sh["b"]) == pytest.approx(
        (0.002 * 100.0 / 110.0**2, -0.01 * 100.0 / 110.0**2)
    )
    tr = n.transformers.iloc[0]
    assert (tr["g"], tr["b"]) == (0.0, 0.0)


def test_magnetising_cm2_from_loss_and_current(tmp_path):
    n = _two_winding_raw(tmp_path, cm=2, mag=(30000.0, 0.01), sb=40.0, nomv1=121.0)
    sh = n.shunt_impedances.iloc[0]
    vs = (110.0 / 121.0) ** 2
    g_pu = 30000.0 * 1e-6 / 100.0 * vs
    y_pu = 0.01 * 40.0 / 100.0 * vs
    b_pu = -math.sqrt(y_pu**2 - g_pu**2)
    assert sh["bus"] == "HV"
    assert (sh["g"], sh["b"]) == pytest.approx(
        (g_pu * 100.0 / 110.0**2, b_pu * 100.0 / 110.0**2)
    )


def test_magnetising_follows_transformer_status(tmp_path):
    n = _two_winding_raw(tmp_path, cm=1, mag=(0.002, -0.01), stat=0)
    assert not n.transformers["active"].iloc[0]
    assert not n.shunt_impedances["active"].iloc[0]


def test_transformer_per_unit_on_s_nom(tmp_path):
    n = _two_winding_raw(tmp_path, cm=1, mag=(0.0, 0.0))
    tr = n.transformers.iloc[0]
    assert tr["s_nom"] == 50.0
    assert (tr["r"], tr["x"]) == pytest.approx(
        (0.004 * 50.0 / 100.0, 0.1 * 50.0 / 100.0)
    )
    assert len(n.shunt_impedances) == 0


def test_three_winding_star():
    n = _read("three_winding_test").network
    stars = n.buses.index[n.buses["bus_type"] == "three-winding star point"]
    assert len(stars) == 5
    tr = n.transformers
    assert len(tr) == 15 and set(tr["bus1"]) == set(stars)
    # STAT 2: winding 2 out, the others in
    a = tr[tr.index.str.startswith("T1 3W (3) ")]
    assert list(a["active"]) == [True, False, True]


def test_three_winding_star_lone_bus_warned():
    rep = _read("three_winding_test").report
    assert any("T1 3W star" in w and "cannot solve" in w for w in rep.warnings)


# ---------------------------------------------------------------- power flow against PSS/E
@pytest.mark.parametrize(
    "name, dv, da", [("case14", 2e-3, 0.05), ("case30", 2e-4, 0.01)]
)
def test_power_flow_matches_the_psse_solution(name, dv, da):
    n = _read(name).network
    assert n.pf()["converged"].all().all()
    bus = read_raw(FIX / f"{name}.raw").tables["bus"]
    names = n.buses.index[n.buses["psse_number"] > 0]
    v = n.buses_t.v_mag_pu.iloc[0][names].to_numpy()
    a = np.degrees(n.buses_t.v_ang.iloc[0][names].to_numpy())
    slack = list(bus["IDE"]).index(3)
    a = a - a[slack] + bus["VA"].iloc[slack]
    assert np.abs(v - bus["VM"].to_numpy()).max() < dv
    assert np.abs(a - bus["VA"].to_numpy()).max() < da


# ---------------------------------------------------------------- round trip of our export
@pytest.fixture(scope="module")
def case39():
    from gridspine.ingest.pandapower_source import load_case39

    net = load_case39()
    pp.runpp(net, numba=False)
    return net


@pytest.mark.parametrize("version", [33, 35, 36])
def test_case39_round_trip(case39, tmp_path, version):
    path = tmp_path / "c39.raw"
    write_raw(case39, path, f_hz=60.0, version=version)
    res = read_psse([path], {})
    n, net = res.network, case39
    assert res.report.unmapped == [] and res.report.warnings == []
    assert list(n.buses.index) == list(net.bus["name"])
    assert np.allclose(n.buses["v_nom"], net.bus["vn_kv"])
    assert np.allclose(n.loads["p_set"], net.load["p_mw"], atol=1e-3)
    assert np.allclose(n.loads["q_set"], net.load["q_mvar"], atol=1e-3)
    ln = net.line
    assert np.allclose(
        n.lines["r"],
        ln["r_ohm_per_km"] * ln["length_km"] / ln["parallel"],
        rtol=1e-4,
        atol=1e-4,
    )
    assert np.allclose(
        n.lines["x"],
        ln["x_ohm_per_km"] * ln["length_km"] / ln["parallel"],
        rtol=1e-4,
        atol=1e-4,
    )
    b = 2 * np.pi * 60.0 * ln["c_nf_per_km"] * 1e-9 * ln["length_km"] * ln["parallel"]
    assert np.allclose(n.lines["b"], b, rtol=1e-4, atol=1e-8)
    assert len(n.transformers) == len(net.trafo)
    assert (n.generators["control"] == "Slack").sum() == len(net.ext_grid)
    assert (n.generators["control"] == "PV").sum() == len(net.gen)
    # the power flow of the import agrees with pandapower's on the source
    assert n.pf()["converged"].all().all()
    v = n.buses_t.v_mag_pu.iloc[0][list(net.bus["name"])].to_numpy()
    a = np.degrees(n.buses_t.v_ang.iloc[0][list(net.bus["name"])].to_numpy())
    slack = net.ext_grid["bus"].iloc[0]
    a = a - a[slack] + net.res_bus["va_degree"].iloc[slack]
    assert np.abs(v - net.res_bus["vm_pu"].to_numpy()).max() < 1e-3
    assert np.abs(a - net.res_bus["va_degree"].to_numpy()).max() < 0.01


def test_case39_res_converters_are_pq(tmp_path):
    """The RES ledger's sgens are written fixed-Q (QT == QB) and come back as PQ machines."""
    from gridspine.ingest.pandapower_source import load_case39_res

    net = load_case39_res()
    path = tmp_path / "c39res.raw"
    write_raw(net, path, f_hz=60.0)
    g = read_psse([path], {}).network.generators
    sg = g.iloc[len(net.gen) + len(net.ext_grid) :]
    assert len(sg) == len(net.sgen)
    assert (sg["control"] == "PQ").all()
    assert np.allclose(sg["p_set"], net.sgen["p_mw"]) and np.allclose(
        sg["p_nom"], net.sgen["sn_mva"]
    )
