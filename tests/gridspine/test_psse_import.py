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

from gridspine.handoff.raw_writer import SECTIONS, write_raw
from gridspine.ingest.import_result import ImportFormatError, ImportResult
from gridspine.ingest.psse_import import THRSHZ, read_psse
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


def test_unmapped_dc_link_listed():
    rep = _read("two-terminal-hvdc_test").report
    assert len(rep.unmapped) == 1 and "no DC link mapping" in rep.unmapped[0].reason


def test_dyr_alongside_is_read(tmp_path):
    dyr = tmp_path / "case14.dyr"
    dyr.write_text("1 'GENCLS' 1 6.0 0.0 /\n")
    res = read_psse([FIX / "case14.raw", dyr], {})
    assert res.report.unmapped == []
    assert res.report.source_files == ["case14.raw", "case14.dyr"]
    (rec,) = res.sidecars["psse_dynamics.json"]["records"]
    assert rec["parameters"] == {"h": 6.0, "d": 0.0} and rec["generator"]


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


def test_three_winding_magnetising_at_winding1_bus():
    """
    MAG1/MAG2 on a three-winding unit go to bus I (the winding-1 bus), not the star point.
    The PSS/E manual is licensed; this follows PowerModels.jl (magnetising on the winding-1
    branch's bus-I end) and the analysis in eigenergy/powerio issue #741.
    """
    n = _read("three_winding_mag_test").network
    (nm,) = n.shunt_impedances.index
    sh = n.shunt_impedances.loc[nm]
    assert sh["bus"] == "FAV SPOT 01" and sh["shunt_kind"] == "magnetising"
    assert (sh["g"], sh["b"]) == pytest.approx(
        (5.0e-3 * 100.0 / 250.0**2, 6.74e-3 * 100.0 / 250.0**2)
    )
    assert (n.transformers[["g", "b"]] == 0).all().all()


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


# ---------------------------------------------------------------- assumed rows and row accounting
#: Hand-written v33 case: every record below is counted by hand in the tests.
_FEATURES = (
    """ 0, 100.00, 33, 0, 0, 50.00
features
second
1,'A', 110.0,3
2,'B', 110.0,2
3,'C', 20.0,1
0 / END OF BUS DATA
2,'Z',1,1,1,10.0,2.0,1.0,0.5,0.5,-0.25
3,'1',1,1,1,5.0,1.0
0 / END OF LOAD DATA
3,'1',1,0.0,5.0
0 / END OF FIXED SHUNT DATA
1,'1',50.0,0.0,99.0,-99.0,1.0,0,100.0,0,1,0,0,1,1,100,200.0,0.0
2,'1',20.0,0.0,50.0,-50.0,1.02,3,100.0,0,1,0,0,1,1,100,200.0,0.0
0 / END OF GENERATOR DATA
1,2,'1',0.01,0.1,0.02,100.0,100.0,100.0
2,3,'1',0.01,0.1,0.0,100.0,100.0,100.0
0 / END OF BRANCH DATA
0 / END OF TRANSFORMER DATA
"""
    + "0\n" * 10
    + """2,1,0,1,1.05,0.95,0,100.0,'',12.0,2,6.0
3,1,0,1,1.05,0.95,0,100.0,'',-4.0,1,-4.0
0 / END OF SWITCHED SHUNT DATA
0
0
Q
"""
)


@pytest.fixture
def features(tmp_path):
    p = tmp_path / "features.raw"
    p.write_text(_FEATURES)
    return read_psse([p], {})


def _assumed(rep, parameter):
    return sorted(a.asset for a in rep.assumed if a.parameter == parameter)


def test_zip_load_listed_as_assumed(features):
    rep, n = features.report, features.network
    assert _assumed(rep, "voltage dependence") == ["Load B Z"]
    # P = PL + IP + YP, Q = QL + IQ - YQ, at 1.0 pu
    assert (
        n.loads.at["Load B Z", "p_set"],
        n.loads.at["Load B Z", "q_set"],
    ) == pytest.approx((11.5, 2.75))


def test_different_kv_branch_is_a_named_one_to_one_transformer(features):
    rep, n = features.report, features.network
    assert _assumed(rep, "branch type") == ["Line B - C 1"]
    tr = n.transformers.loc["Line B - C 1"]
    assert (tr["bus0"], tr["bus1"], tr["tap_ratio"], tr["s_nom"]) == (
        "B",
        "C",
        1.0,
        100.0,
    )
    assert (tr["r"], tr["x"]) == pytest.approx((0.01, 0.1))  # pu on s_nom = SBASE


def test_each_switched_shunt_and_remote_regulator_assumed(features):
    rep = features.report
    assert _assumed(rep, "susceptance setting") == [
        "Switched shunt B 1",
        "Switched shunt C 2",
    ]
    assert _assumed(rep, "regulated bus") == ["Gen B 1"]
    sh = features.network.shunt_impedances
    assert sh.at["Switched shunt B 1", "b"] == pytest.approx(12.0 / 110.0**2)
    assert sh.at["Switched shunt C 2", "b"] == pytest.approx(-4.0 / 20.0**2)


def test_no_other_assumptions_in_features(features):
    params = sorted({a.parameter for a in features.report.assumed})
    assert params == [
        "branch type",
        "regulated bus",
        "susceptance setting",
        "voltage dependence",
    ]
    assert len(features.report.assumed) == 1 + 1 + 2 + 1


def test_row_accounting_features(features):
    """Rows in per section (hand-counted above) = assets made + unmapped; nothing refused here."""
    rep, n = features.report, features.network
    assert rep.counts == {
        "Buses": 3,
        "Loads": 2,
        "Generators": 2,
        "Lines": 1,
        "Branches imported as transformers": 1,
        "Fixed shunts": 1,
        "Switched shunts": 2,
    }
    assert rep.unmapped == []
    assert (
        len(n.buses),
        len(n.loads),
        len(n.generators),
        len(n.lines),
        len(n.transformers),
    ) == (3, 2, 2, 1, 1)
    assert len(n.shunt_impedances) == 1 + 2


def test_row_accounting_case30():
    """
    case30.raw, hand-counted: 30 buses, 21 loads, 6 machines, 34 branches, 7 transformers,
    2 switched shunts, no fixed shunts, nothing unmapped.
    """
    res = _read("case30")
    n = res.network
    assert (
        len(n.buses),
        len(n.loads),
        len(n.generators),
        len(n.lines),
        len(n.transformers),
    ) == (30, 21, 6, 34, 7)
    assert (n.shunt_impedances["shunt_kind"] == "switched").sum() == 2
    assert len(n.shunt_impedances) == 2 and res.report.unmapped == []


# ---------------------------------------------------------------- engine cage (GK A R8)
def test_reader_and_registry_import_without_pypsa():
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['pypsa'] = None\n"
        "import gridspine.ingest.psse_raw_parse, gridspine.ingest.import_result\n"
        "import gridspine.ingest.psse_dyr_parse\n"
        "from gridspine.ingest.psse_raw_parse import read_raw\n"
        f"read_raw({str(FIX / 'case14.raw')!r})\n"
        "print('ok')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[2],
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


# ---------------------------------------------------------------- off-nominal transformer round trip (GK A R7)
#: Cases whose exact equality waits on a writer fix: id -> ticket. They are not skipped:
#: the test pins today's error at exactly WINDV2**2, so the fix turns it red and the
#: case moves to the equality branch.
_PENDING: dict[str, str] = {}


@pytest.mark.parametrize("tap_side", ["hv", "lv"])
def test_off_nominal_transformer_round_trip(tmp_path, tap_side, monkeypatch):
    """
    A transformer whose LV rating differs from its bus kV (21 kV on a 20 kV bus), tapped
    on either side, against pandapower's own branch model (``_ppc``, Z at the to-bus base).
    """
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    from test_raw_v35 import _trafo_net

    net = _trafo_net(tap_side)
    pp.create_ext_grid(net, 0)
    pp.create_load(net, 1, p_mw=5.0)
    net.bus["name"] = ["HV", "LV"]
    pp.runpp(net, numba=False)
    br = net._ppc["branch"][-1]
    x_pp = br[3].real * 100.0 / net._ppc["baseMVA"]
    write_raw(net, tmp_path / "t.raw")
    raw_case = read_raw(tmp_path / "t.raw")
    windv2 = raw_case.tables["transformer"]["WINDV2"].iloc[0]
    tr = read_psse([tmp_path / "t.raw"], {}).network.transformers.iloc[0]
    x_ours = tr["x"] * 100.0 / tr["s_nom"]
    assert tr["tap_ratio"] == pytest.approx(br[8].real, rel=1e-6)
    if tap_side in _PENDING:
        assert x_ours == pytest.approx(x_pp * windv2**2, rel=1e-4), _PENDING[tap_side]
    else:
        assert x_ours == pytest.approx(x_pp, rel=1e-4)


# ---------------------------------------------------------------- hand-written edge cases
#: Two 110 kV buses, a swing machine and a rated line: the base most cases below extend.
_BUSES = ["1,'A', 110.0,3", "2,'B', 110.0,1"]
_SWING = "1,'1',50.0,0.0,99.0,-99.0,1.0,0,100.0,0,1,0,0,1,1,100,200.0,0.0"
_LINE = "1,2,'1',0.01,0.1,0.0,100.0"


def _case(tmp_path, rev=33, head=None, title="t", **sections):
    """
    Import a RAW file of version ``rev``: each section's lines (keyword = section
    name with ``_`` for spaces, e.g. ``FIXED_SHUNT``), then its ``0``.
    """
    lines = [head or f" 0, 100.00, {rev}, 0, 0, 50.00", title, "second"]
    for name in SECTIONS[rev]:
        lines += sections.get(name.replace(" ", "_").replace("-", "_"), [])
        lines.append("0")
    p = tmp_path / "c.raw"
    p.write_text("\n".join(lines) + "\nQ\n")
    return read_psse([p], {})


def _gen(
    bus, gid, qt=99.0, qb=-99.0, vs=1.0, ireg=0, mbase=100.0, stat=1, pt=200.0, pb=0.0
):
    return f"{bus},'{gid}',10.0,0.0,{qt},{qb},{vs},{ireg},{mbase},0,1,0,0,1,{stat},100,{pt},{pb}"


def test_path_given_as_text():
    rep = read_psse([str(FIX / "case14.raw")], {}).report
    assert rep.source_files == ["case14.raw"]


def test_no_files_named_as_none():
    with pytest.raises(ImportFormatError, match="got 0: none$"):
        read_psse([], {})
    with pytest.raises(ImportFormatError, match="got 2: a.raw, b.raw$"):
        read_psse([Path("a.raw"), Path("b.raw")], {})


@pytest.mark.parametrize("make_dir", [False, True])
def test_unreadable_raw_names_it(tmp_path, make_dir):
    path = tmp_path / "gone.raw"
    if make_dir:
        path.mkdir()
    with pytest.raises(ImportFormatError, match="gone.raw: cannot be read"):
        read_psse([path], {})


def test_case_refused_while_mapping_names_the_file(tmp_path):
    # CM=2 with exciting current below its no-load loss reads, but cannot be mapped
    with pytest.raises(ImportFormatError, match="t.raw: .*below its no-load loss"):
        _two_winding_raw(tmp_path, cm=2, mag=(30000.0, 0.0))


def test_reader_assumptions_reach_the_report(tmp_path):
    rep = _case(
        tmp_path,
        head=" 0, 100.00, 33, 0, 0",
        BUS=_BUSES,
        GENERATOR=[_SWING],
        BRANCH=[_LINE],
    ).report
    assert _assumed(rep, "base frequency (Hz)") == ["case"]


def test_area_zone_owner_tables_and_bus_columns(tmp_path):
    res = _case(
        tmp_path,
        title="Area test",
        BUS=["1,'A', 110.0,3,1,1,1", "2,'B', 110.0,1,2,2,2"],
        GENERATOR=[_SWING],
        BRANCH=[_LINE],
        AREA=["1,1,12.5,2.5,'NORTH'", "2,0,0.0,1.0,''"],
        ZONE=["1,'Z1'", "2,''"],
        OWNER=["1,'O1'", "2,'  '"],
    )
    side = res.sidecars["psse_case.json"]
    assert side == {
        "psse_version": 33,
        "system_base_mva": 100.0,
        "base_frequency_hz": 50.0,
        "title": "Area test",
        "areas": [
            {"I": 1, "ARNAME": "NORTH", "ISW": 1, "PDES": 12.5, "PTOL": 2.5},
            {"I": 2, "ARNAME": "", "ISW": 0, "PDES": 0.0, "PTOL": 1.0},
        ],
        "zones": [{"I": 1, "ZONAME": "Z1"}, {"I": 2, "ZONAME": ""}],
        "owners": [{"I": 1, "OWNAME": "O1"}, {"I": 2, "OWNAME": ""}],
    }
    assert [type(side["areas"][0][k]) for k in ("I", "ISW")] == [int, int]
    b = res.network.buses
    assert list(b["area"]) == ["NORTH", "2"]  # a blank name falls back to the number
    assert list(b["zone"]) == ["Z1", "2"]
    assert list(b["owner"]) == ["O1", "2"]


def test_bus_names_repeated_or_blank(tmp_path):
    n = _case(
        tmp_path,
        BUS=["1,'X', 110.0,3", "2,'X', 110.0,1", "3,'', 110.0,1", "4,'Y', 110.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[
            "1,2,'1',0.01,0.1,0.0,100.0",
            "2,3,'1',0.01,0.1,0.0,100.0",
            "3,4,'1',0.01,0.1,0.0,100.0",
        ],
    ).network
    assert list(n.buses.index) == ["X #1", "X #2", "Bus 3", "Y"]


@pytest.mark.parametrize(
    "title, name", [("My case", "My case"), ("   ", "PSS/E import")]
)
def test_network_named_after_title(tmp_path, title, name):
    n = _case(
        tmp_path, title=title, BUS=_BUSES, GENERATOR=[_SWING], BRANCH=[_LINE]
    ).network
    assert n.name == name


def test_isolated_bus_switches_off_everything_touching_it(tmp_path):
    def tr(i, j):
        return [
            f"{i},{j},0,'1',1,1,1,0.0,0.0,2,'',1",
            "0.004,0.1,100.0",
            "1.0,0.0,0.0,50.0",
            "1.0,0.0",
        ]

    n = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 110.0,4", "4,'D', 20.0,1", "5,'E', 20.0,4"],
        LOAD=["2,'1',1,1,1,10.0,2.0", "3,'1',1,1,1,10.0,2.0", "2,'2',0,1,1,10.0,2.0"],
        GENERATOR=[_SWING],
        BRANCH=[_LINE, "2,3,'1',0.01,0.1,0.0,100.0", "3,2,'2',0.01,0.1,0.0,100.0"],
        TRANSFORMER=tr(1, 4) + tr(2, 5) + tr(5, 2),
    ).network
    assert list(n.loads["active"]) == [True, False, False]
    assert list(n.lines["active"]) == [True, False, False]
    assert list(n.transformers["active"]) == [True, False, False]


def test_slack_goes_to_the_first_in_service_machine_on_the_swing_bus(tmp_path):
    n = _case(
        tmp_path,
        BUS=["1,'A', 110.0,3", "2,'B', 110.0,1", "3,'C', 110.0,2"],
        GENERATOR=[
            _gen(1, 1, stat=0),
            _gen(1, 2),
            _gen(1, 3),
            _gen(2, 1),  # QT > QB on a load bus
            _gen(3, 1, qt=5.0, qb=5.0),  # fixed Q on a generator bus
        ],
        BRANCH=[_LINE, "2,3,'1',0.01,0.1,0.0,100.0"],
    ).network
    assert list(n.generators["control"]) == ["PV", "Slack", "PV", "PQ", "PQ"]


def test_rated_power_and_minimum_output(tmp_path):
    res = _case(
        tmp_path,
        BUS=_BUSES,
        GENERATOR=[
            _gen(1, 1, pt=50.0, pb=10.0),
            _gen(1, 2, pt=0.0, mbase=80.0),
            _gen(1, 3, pt=9999.0, mbase=90.0),
            _gen(1, 4, pt=0.0, mbase=0.0, pb=0.0),
            _gen(1, 5, pt=60.0, pb=-9999.0),
        ],
        BRANCH=[_LINE],
    )
    g = res.network.generators
    assert list(g["p_nom"]) == [50.0, 80.0, 90.0, 0.0, 60.0]
    assert list(g["p_min_pu"]) == pytest.approx([0.2, 0.0, 0.0, 0.0, 0.0])
    assert _assumed(res.report, "rated power (MW)") == ["Gen A 2", "Gen A 3", "Gen A 4"]


def test_remote_regulation_assumed_only_for_voltage_holders(tmp_path):
    rep = _case(
        tmp_path,
        BUS=["1,'A', 110.0,3", "2,'B', 110.0,1", "3,'C', 110.0,2"],
        GENERATOR=[
            _gen(1, 1, ireg=1),
            _gen(2, 1, ireg=1),
            _gen(3, 1, ireg=3),
            _gen(3, 2, ireg=2),
        ],
        BRANCH=[_LINE, "2,3,'1',0.01,0.1,0.0,100.0"],
    ).report
    assert _assumed(rep, "regulated bus") == ["Gen C 2"]


def test_bus_voltage_set_only_by_in_service_voltage_holders(tmp_path):
    n = _case(
        tmp_path,
        BUS=[
            "1,'A', 110.0,3",
            "2,'B', 110.0,2,1,1,1,1.01",
            "3,'C', 110.0,1,1,1,1,1.02",
        ],
        GENERATOR=[_SWING, _gen(2, 1, vs=1.05, stat=0), _gen(3, 1, vs=1.07)],
        BRANCH=[_LINE, "2,3,'1',0.01,0.1,0.0,100.0"],
    ).network
    assert list(n.buses["v_mag_pu_set"]) == [1.0, 1.01, 1.02]


def test_only_r_and_x_zero_is_a_jumper(tmp_path):
    res = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 20.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[
            "1,2,'1',0.0,0.1,0.0,100.0",
            "1,2,'2',0.01,0.0,0.0,100.0",
            "1,2,'3',0.0,0.0,0.0,100.0",
            "2,3,'1',0.0,0.0,0.0,50.0",
        ],
    )
    n, zb = res.network, 110.0**2 / 100.0
    assert list(n.lines["x"]) == pytest.approx([0.1 * zb, 0.0, THRSHZ * zb])
    assert n.transformers.at["Line B - C 1", "x"] == pytest.approx(
        THRSHZ * 50.0 / 100.0
    )
    assert _assumed(res.report, "reactance (pu)") == ["Line A - B 3", "Line B - C 1"]


def test_different_kv_branch_rating_and_charging(tmp_path):
    n = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 20.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[_LINE, "2,3,'1',0.01,0.1,0.02,0.0", "2,3,'2',0.01,0.1,0.02,50.0"],
    ).network
    tr = n.transformers
    assert list(tr["s_nom"]) == [100.0, 50.0]  # no rating: SBASE
    # impedance re-bases with s_nom / SBASE, admittance with SBASE / s_nom
    assert list(tr["r"]) == pytest.approx([0.01, 0.005])
    assert list(tr["b"]) == pytest.approx([0.02, 0.04])
    # on PyPSA's 1 MVA base the charging is B * SBASE, as a line's would be
    n.calculate_dependent_values()
    assert list(n.transformers["b_pu"]) == pytest.approx([2.0, 2.0])


def test_unrated_one_to_one_transformer_assumed(tmp_path):
    res = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 20.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[_LINE, "2,3,'1',0.01,0.1,0.0,0.0", "2,3,'2',0.01,0.1,0.0,50.0"],
    )
    assert _assumed(res.report, "thermal rating (MVA)") == ["Line B - C 1"]
    (row,) = [a for a in res.report.assumed if a.parameter == "thermal rating (MVA)"]
    assert row.value == 100.0


def test_switched_shunt_regulating_a_remote_bus_says_so(tmp_path):
    res = _case(
        tmp_path,
        BUS=_BUSES,
        GENERATOR=[_SWING],
        BRANCH=[_LINE],
        SWITCHED_SHUNT=[
            "2,1,0,1,1.05,0.95,1,100.0,'',12.0,2,6.0",
            "2,1,0,1,1.05,0.95,2,100.0,'',6.0,1,6.0",
        ],
    )
    rows = {
        a.asset: a for a in res.report.assumed if a.parameter == "susceptance setting"
    }
    assert len(rows) == 2  # still one row per shunt
    remote, local = sorted(rows.values(), key=lambda a: a.value)[::-1]
    assert "regulate remote bus 1" in remote.why
    assert "remote" not in local.why


@pytest.mark.parametrize(
    "cod, parameter, value",
    [
        (1, "winding 1 tap ratio", 1.05),
        (3, "winding 1 phase angle (deg)", 30.0),
        (0, None, None),
        (-1, None, None),
    ],
)
def test_transformer_control_held_at_file_setting(tmp_path, cod, parameter, value):
    res = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 20.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[_LINE],
        TRANSFORMER=[
            "2,3,0,'1',1,1,1,0.0,0.0,2,'T',1",
            "0.01,0.1,100.0",
            f"1.05,0.0,30.0,0.0,0.0,0.0,{cod},3,1.1,0.9",
            "1.0,0.0",
        ],
    )
    rows = [a for a in res.report.assumed if a.parameter.startswith("winding 1")]
    if parameter is None:
        assert rows == []
    else:
        (row,) = rows
        assert (row.asset, row.parameter, row.value) == ("T", parameter, value)
        assert f"COD1 = {cod}" in row.why


def test_unrated_line_assumed(tmp_path):
    res = _case(
        tmp_path, BUS=_BUSES, GENERATOR=[_SWING], BRANCH=["1,2,'1',0.01,0.1,0.0,0.0"]
    )
    # no load, so the total load floors at SBASE 100 MVA: 10 × 100 = 1000 MVA,
    # above the 110 kV class floor (250 MVA)
    assert res.network.lines.at["Line A - B 1", "s_nom"] == 1000.0
    assert res.network.lines.at["Line A - B 1", "s_nom_source"] == "assumed"
    assert _assumed(res.report, "thermal rating (MVA)") == ["Line A - B 1"]
    (row,) = [a for a in res.report.assumed if a.parameter == "thermal rating (MVA)"]
    assert row.value == 1000.0 and "SBASE" in row.why


def test_v35_zero_rate1_line_uses_the_first_positive_rate(tmp_path):
    res = _case(
        tmp_path,
        rev=35,
        BUS=_BUSES,
        GENERATOR=[_SWING],
        BRANCH=["1,2,'1',0.01,0.1,0.0,'',0.0,0.0,30.0"],
    )
    assert res.network.lines.at["Line A - B 1", "s_nom"] == 30.0
    (row,) = [a for a in res.report.assumed if a.parameter == "thermal rating (MVA)"]
    assert row.value == 30.0 and "RATE1 is 0; RATE3 = 30 MVA is used" in row.why


def test_line_end_shunts_rebased_to_each_end(tmp_path):
    # GI, BI, GJ, BJ after RATEA..RATEC: pu on the from-bus (110 kV) base
    n = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 20.0,1"],
        GENERATOR=[_SWING],
        BRANCH=[
            _LINE,
            "2,3,'1',0.01,0.1,0.0,50.0,0.0,0.0,0.0,0.01,0.02,0.03",
            "2,3,'2',0.01,0.1,0.0,50.0,0.0,0.0,0.04",
        ],
    ).network
    sh = n.shunt_impedances
    zb, scale = 110.0**2 / 100.0, (110.0 / 20.0) ** 2
    assert list(sh.index) == [
        "Line B - C 1 from-end shunt",
        "Line B - C 1 to-end shunt",
        "Line B - C 2 from-end shunt",
    ]
    assert list(sh["bus"]) == ["B", "C", "B"]
    assert list(sh["g"]) == pytest.approx([0.0, 0.02 / zb * scale, 0.04 / zb])
    assert list(sh["b"]) == pytest.approx([0.01 / zb, 0.03 / zb * scale, 0.0])


def test_three_winding_unrated_windings_take_their_pair_base(tmp_path):
    res = _case(
        tmp_path,
        BUS=["1,'A', 110.0,3", "2,'B', 20.0,1", "3,'C', 10.0,1"],
        GENERATOR=[_SWING],
        TRANSFORMER=[
            "1,2,3,'1',1,1,1,0.0,0.0,2,'T3',1",
            "0.01,0.1,100.0,0.01,0.1,50.0,0.01,0.1,30.0,1.0,0.0",
            "1.0,0.0,0.0,0.0",
            "1.0,0.0,0.0,0.0",
            "1.0,0.0,0.0,0.0",
        ],
    )
    tr = res.network.transformers
    assert list(tr.index) == ["T3 winding 1", "T3 winding 2", "T3 winding 3"]
    assert list(tr["s_nom"]) == [100.0, 50.0, 30.0]
    assert list(tr["active"]) == [True, True, True]
    assert _assumed(res.report, "rating (MVA)") == list(tr.index)


@pytest.mark.parametrize("mag", [(0.002, 0.0), (0.0, -0.01)])
def test_magnetising_with_one_part_only(tmp_path, mag):
    n = _two_winding_raw(tmp_path, cm=1, mag=mag)
    (nm,) = n.shunt_impedances.index
    sh = n.shunt_impedances.loc[nm]
    assert (sh["g"], sh["b"]) == pytest.approx(
        (mag[0] * 100.0 / 110.0**2, mag[1] * 100.0 / 110.0**2)
    )


def test_lone_bus_with_a_generator_not_warned(tmp_path):
    rep = _case(
        tmp_path,
        BUS=_BUSES + ["3,'C', 110.0,1", "4,'D', 110.0,1"],
        LOAD=["4,'1',1,1,1,10.0,2.0"],
        GENERATOR=[_SWING, _gen(3, 1)],
        BRANCH=[_LINE],
    ).report
    assert rep.warnings == [
        "1 bus(es) have no in-service connection and no generator (D); PyPSA's power "
        "flow cannot solve such a bus on its own, so switch it out of the study or "
        "connect it"
    ]


@pytest.mark.parametrize("rev", [35, 36])
def test_v35_branch_name_used_when_set(tmp_path, rev):
    n = _case(
        tmp_path,
        rev=rev,
        BUS=_BUSES,
        GENERATOR=["1,'1',50.0,0.0,99.0,-99.0,1.0,0,0,100.0"],
        BRANCH=[
            "1,2,'1',0.01,0.1,0.0,'FEEDER',100.0",
            "1,2,'2',0.01,0.1,0.0,' ',100.0",
        ],
    ).network
    assert list(n.lines.index) == ["FEEDER", "Line A - B 2"]


# ---------------------------------------------------------------- repeated PSS/E identities (issue 03)
_LOAD = "2,'1',1,1,1,10.0,2.0"


@pytest.mark.parametrize(
    "sections, match",
    [
        (
            {"BRANCH": [_LINE, "1,2,'1',0.02,0.2,0.0,100.0"]},
            "RAW line 12: branch 1-2 circuit '1' repeats the one at RAW line 11",
        ),
        (
            {"BRANCH": [_LINE, "2,1,'1',0.02,0.2,0.0,100.0"]},
            "RAW line 12: branch 2-1 circuit '1' repeats the one at RAW line 11",
        ),
        (
            {"GENERATOR": [_SWING, _SWING]},
            "RAW line 10: generator '1' at bus 1 repeats the one at RAW line 9",
        ),
        (
            {"LOAD": [_LOAD, _LOAD]},
            "RAW line 8: load '1' at bus 2 repeats the one at RAW line 7",
        ),
    ],
    ids=["branch", "branch-reversed", "generator", "load"],
)
def test_repeated_identity_is_refused_naming_both_lines(tmp_path, sections, match):
    """A parallel circuit with the same CKT would share a name and be lost (issue 03)."""
    base = {"BUS": _BUSES, "GENERATOR": [_SWING], "BRANCH": [_LINE]}
    with pytest.raises(ImportFormatError, match=match):
        _case(tmp_path, **(base | sections))


def test_parallel_circuits_with_distinct_ckt_are_both_kept(tmp_path):
    res = _case(
        tmp_path,
        BUS=_BUSES,
        GENERATOR=[_SWING],
        BRANCH=[_LINE, "1,2,'2',0.02,0.2,0.0,100.0", "2,1,'3',0.03,0.3,0.0,100.0"],
    )
    n = res.network
    assert len(n.lines) == res.report.counts["Lines"] == 3
    assert n.lines["r"].nunique() == 3


# ---------------------------------------------------------------- unrated lines (issue psse-import 04)
#: case14: Σ|S| of its 11 loads is 275.65 MVA; 10 × that, rounded up to a whole
#: MVA, is above the 138 kV class floor (250 MVA).
CASE14_ASSUMED_LINE_MVA = 2757.0


def _rating_rows(rep):
    return {a.asset: a for a in rep.assumed if a.parameter == "thermal rating (MVA)"}


def test_case14_unrated_lines_get_a_stated_bound():
    res = _read("case14")
    n = res.network
    assert len(n.lines) == 17
    assert n.lines["s_nom"].tolist() == [CASE14_ASSUMED_LINE_MVA] * 17
    assert n.lines["s_nom_source"].tolist() == ["assumed"] * 17
    rows = _rating_rows(res.report)
    assert sorted(rows) == sorted(n.lines.index)
    for a in rows.values():
        assert a.value == 2757.0
        assert "RATEA = 0 means unrated" in a.why
        assert "275.7 MVA" in a.why  # the total load the bound is 10 times
    # the transformers keep their SBASE1-2 base, as before
    assert n.transformers["s_nom"].tolist() == [100.0, 100.0, 100.0]
    assert "s_nom_source" not in n.transformers.columns


def test_a_zero_ratea_line_falls_back_to_a_positive_rateb(tmp_path):
    text = (FIX / "case14.raw").read_text().splitlines()
    first = (
        "     1,     2,'1 ',1.93800E-2,5.91700E-2,5.28000E-2,   0.00,   0.00,   0.00,"
    )
    assert text[37].startswith(first)
    text[37] = text[37].replace(
        first, first[: -len("   0.00,   0.00,")] + "  45.00,   0.00,", 1
    )
    p = tmp_path / "case14_rateb.raw"
    p.write_text("\n".join(text) + "\n")
    res = read_psse([p], {})
    n = res.network
    line = "Line Bus 1     HV - Bus 2     HV 1"
    assert n.lines.at[line, "s_nom"] == 45.0
    assert n.lines.at[line, "s_nom_source"] == "assumed"
    assert set(n.lines.drop(line)["s_nom_source"]) == {"assumed"}
    row = _rating_rows(res.report)[line]
    assert row.value == 45.0
    assert "RATEA is 0; RATEB = 45 MVA is used" in row.why
    others = n.lines.drop(line)
    assert others["s_nom"].tolist() == [2757.0] * 16


def test_a_fully_rated_case_is_unchanged():
    """case30 rates every line: its frames equal master 3b9ea4f9's, column for column."""
    golden = json.loads((FIX / "case30_frames_3b9ea4f9.json").read_text())
    n = _read("case30").network
    for comp in ("lines", "generators", "transformers"):
        df = getattr(n, comp)
        assert list(df.columns) == golden[comp]["columns"], comp
        for name, row in golden[comp]["rows"].items():
            for col, want in row.items():
                got = df.at[name, col]
                if hasattr(got, "item"):
                    got = got.item()
                if want == "NaN":
                    assert isinstance(got, float) and math.isnan(got), (comp, name, col)
                elif want in ("inf", "-inf"):
                    assert got == float(want), (comp, name, col)
                else:
                    assert got == want, (comp, name, col, got, want)
    assert n.lines.at[n.lines.index[0], "s_nom"] == 138.0  # RATEA 138 in the file


def test_features_rated_line_keeps_its_ratea(features):
    ln = features.network.lines
    assert ln["s_nom"].tolist() == [100.0]
    assert "s_nom_source" not in ln.columns
    assert _rating_rows(features.report) == {}


@pytest.mark.parametrize(
    "load_mva, kv, want",
    [
        (275.65, 138.0, 2757.0),  # 10 × load wins over the 250 MVA floor
        (10.0, 138.0, 250.0),  # 100 MVA < the ≤150 kV floor
        (10.0, 0.4, 100.0),  # <1 kV floor is 1 MVA
        (0.01, 0.4, 1.0),
        (0.01, 1.0, 50.0),  # 1 kV is in the ≤36 kV class
        (1.0, 20.0, 50.0),  # ≤36 kV floor
        (1.0, 36.0, 50.0),
        (1.0, 36.1, 250.0),
        (1.0, 150.0, 250.0),
        (1.0, 220.0, 1000.0),  # ≤300 kV floor
        (1.0, 300.0, 1000.0),
        (1.0, 380.0, 3000.0),  # >300 kV floor
        (0.21, 0.0, 3.0),  # BASKV 0: lowest class, still 10 × load (2.1 → 3)
        (0.21, math.nan, 3.0),  # BASKV missing: the same
    ],
)
def test_assumed_bound_rule(load_mva, kv, want):
    from gridspine.ingest.ratings import assumed_rating_mva

    assert assumed_rating_mva(load_mva, kv) == want


def test_assumed_bound_takes_the_higher_end_voltage():
    from gridspine.ingest.ratings import assumed_rating_mva

    assert assumed_rating_mva(1.0, 20.0, 380.0) == 3000.0
    assert assumed_rating_mva(1.0, 0.0, 220.0) == 1000.0


def test_an_out_of_service_load_is_not_in_the_bound(tmp_path):
    # 2000 MW switched off (STATUS 0): the bound stays 10 × SBASE 100 = 1000 MVA
    res = _case(
        tmp_path,
        BUS=_BUSES,
        LOAD=["2,'1',0,1,1,2000.0,0.0"],
        GENERATOR=[_SWING],
        BRANCH=["1,2,'1',0.01,0.1,0.0,0.0"],
    )
    assert bool(res.network.loads["active"].iloc[0]) is False
    assert res.network.lines.at["Line A - B 1", "s_nom"] == 1000.0


def test_an_in_service_load_sets_the_bound(tmp_path):
    res = _case(
        tmp_path,
        BUS=_BUSES,
        LOAD=["2,'1',1,1,1,300.0,400.0"],
        GENERATOR=[_SWING],
        BRANCH=["1,2,'1',0.01,0.1,0.0,0.0"],
    )
    # |S| = 500 MVA, 10 × 500 = 5000 MVA
    assert res.network.lines.at["Line A - B 1", "s_nom"] == 5000.0
