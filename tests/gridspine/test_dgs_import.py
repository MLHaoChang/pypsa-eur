"""
PowerFactory DGS import, step 2 (IMPORTS-1): the parsed file as a pypsa network.

Two independent oracles (GK A2 D7):

- ``HAND``: a hand-written DGS file with every mapped class, each value
  checked against a hand calculation written next to the assertion;
- the GC-05 writer's fixture, round-tripped through ``write_dgs``: per-unit
  branch values against pandapower's own ``net._ppc`` within 1e-6, and the
  AC load flow against pandapower's within 1e-6.

Fixture licence: ``HAND`` was written for this test from the public DGS 5.x
grammar and generic engineering values; it copies no third-party file.
"""

import json
import logging
import math

import pandapower as pp
import pytest

from gridspine.handoff.dgs_writer import write_dgs
from gridspine.ingest import import_result
from gridspine.ingest.dgs_import import read_dgs
from gridspine.ingest.import_result import ImportFormatError
from tests.gridspine.test_dgs_writer import HARMONICS, fixture_net

logging.getLogger("pypsa").setLevel(logging.ERROR)

HEAD = {
    "General": "Descr(a:40);Val(a:40)",
    "ElmNet": "loc_name(a:40);frnom(r)",
    "ElmTerm": "loc_name(a:40);fold_id(p);uknom(r);outserv(i)",
    "ElmCoup": "loc_name(a:40);fold_id(p);on_off(i)",
    "TypLne": "loc_name(a:40);uline(r);sline(r);rline(r);xline(r);bline(r);gline(r)",
    "ElmLne": "loc_name(a:40);fold_id(p);typ_id(p);dline(r);nlnum(i);outserv(i)",
    "TypTr2": "loc_name(a:40);strn(r);utrn_h(r);utrn_l(r);uktr(r);pcutr(r);pfe(r);"
    "curmg(r);nt2ag(r);dutap(r);nntap0(i);tap_side(i)",
    "ElmTr2": "loc_name(a:40);fold_id(p);typ_id(p);nntap(i);ntnum(i);outserv(i)",
    "TypTr3": "loc_name(a:40);strn3_h(r);strn3_m(r);strn3_l(r);utrn3_h(r);utrn3_m(r);"
    "utrn3_l(r);uktr3_h(r);uktr3_m(r);uktr3_l(r)",
    "ElmTr3": "loc_name(a:40);fold_id(p);typ_id(p);outserv(i)",
    "TypHmccur": "loc_name(a:40);norder:SIZEROW(i);norder:0(i);norder:1(i);norder:2(i);"
    "ampl:SIZEROW(i);ampl:0(r);ampl:1(r);ampl:2(r)",
    "ElmLod": "loc_name(a:40);fold_id(p);plini(r);qlini(r);scale0(r);phmc(p);outserv(i)",
    "ElmGenstat": "loc_name(a:40);fold_id(p);sgn(r);cosn(r);pgini(r);qgini(r);outserv(i)",
    "TypSym": "loc_name(a:40);sgn(r);cosn(r)",
    "ElmSym": "loc_name(a:40);fold_id(p);typ_id(p);pgini(r);usetp(r);ip_ctrl(i);outserv(i)",
    "ElmXnet": "loc_name(a:40);fold_id(p);usetp(r);snss(r);snssmin(r);rntxn(r);"
    "rntxnmin(r);bustp(a:2);outserv(i)",
    "ElmShnt": "loc_name(a:40);fold_id(p);shtype(i);ushnm(r);qtotn(r);ncapx(i);"
    "ncapa(i);outserv(i)",
    "ElmRelay": "loc_name(a:40)",
    "StaCubic": "loc_name(a:40);fold_id(p);obj_id(p);obj_bus(i)",
    "StaSwitch": "loc_name(a:40);fold_id(p);on_off(i)",
}
ROWS = {
    "General": ["1;Version;5.0"],
    "ElmNet": ["2;Grid;50.0"],
    "ElmTerm": [
        "10;HV;2;33.0;0",
        "11;HV-B;2;33.0;0",
        "12;LV;2;11.0;0",
        "13;Spare;2;33.0;0",
        "14;EHV;2;132.0;0",
    ],
    "ElmCoup": ["20;C1;2;1", "21;C2;2;0"],
    "TypLne": [
        "30;L1 type;33.0;0.5;0.1;0.4;3.0;0.1",
        "36;unused type;33.0;0.5;0.1;0.4;0;0",
    ],
    "ElmLne": ["31;L1;2;30;10.0;2;0"],
    "TypTr2": ["32;T type;10.0;33.0;11.0;10.0;50.0;10.0;0.5;11.0;2.5;0;0"],
    "ElmTr2": ["33;T2W;2;32;-2;1;0"],
    "TypTr3": ["34;T3 type;100.0;50.0;25.0;132.0;33.0;11.0;10.0;8.0;12.0"],
    "ElmTr3": ["35;T3W;2;34;0"],
    "TypHmccur": [
        "50;drive;3;5;7;11;3;20.0;0.0;9.0",
        "51;spare;3;5;7;11;3;1.0;1.0;1.0",
    ],
    "ElmLod": ["40;Load;2;3.0;1.0;2.0;50;0"],
    "ElmGenstat": ["41;PV;2;5.0;0.9;3.0;1.0;0"],
    "TypSym": ["42;Sym type;10.0;0.8"],
    "ElmSym": ["43;Gen;2;42;4.0;1.02;0;0"],
    "ElmXnet": ["44;Grid;2;1.0;1000.0;500.0;0.1;0.2;SL;0"],
    "ElmShnt": [
        "45;Cap;2;2;11.0;6.0;3;2;0",
        "46;Reac;2;1;33.0;2.0;1;1;0",
        "47;Filt;2;0;11.0;1.0;1;1;0",
    ],
    "ElmRelay": ["60;Relay"],
    "StaCubic": [
        "100;c;11;20;0",
        "101;c;10;20;1",  # C1: HV-B - HV, closed
        "102;c;10;21;0",
        "103;c;13;21;1",  # C2: HV - Spare, open
        "104;c;11;31;0",
        "105;c;13;31;1",  # L1: HV-B - Spare
        "106;c;10;33;0",
        "107;c;12;33;1",  # T2W: HV - LV
        "108;c;14;35;0",
        "109;c;10;35;1",
        "110;c;12;35;2",  # T3W
        "111;c;12;40;0",
        "112;c;12;41;0",
        "113;c;13;43;0",
        "114;c;14;44;0",
        "115;c;12;45;0",
        "116;c;10;46;0",
        "117;c;12;47;0",
    ],
    "StaSwitch": ["90;S-PV;112;0"],
}


def dgs_text(rows=ROWS, head=HEAD):
    out = ["* hand-written DGS fixture"]
    for cls, lines in rows.items():
        out.append(f"$${cls};ID(a:40);{head[cls]}")
        out.extend(lines)
    return "\n".join(out) + "\n"


def read_text(tmp_path, text, name="hand.dgs"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return read_dgs([path], {})


@pytest.fixture
def hand(tmp_path):
    return read_text(tmp_path, dgs_text())


def test_hand_counts_unmapped_and_report_are_complete(hand):
    rep = hand.report
    assert rep.source_format == "dgs" and rep.source_files == ["hand.dgs"]
    # 5 terminals, HV-B joined to HV: 4 buses. Every class once except shunts
    # (Cap, Reac; Filt is unmapped). One spectrum (Load's).
    assert rep.counts == {
        "Couplers": 2,
        "Switches": 1,
        "Buses": 4,
        "Lines": 1,
        "Transformers": 1,
        "Three-winding transformers": 1,
        "Loads": 1,
        "Harmonic spectra": 1,
        "Static generators": 1,
        "Synchronous machines": 1,
        "External grids": 1,
        "Shunts": 2,
        "Loads given a harmonic spectrum": 1,
        "Slack generators given short-circuit data": 1,
    }
    # the preview names what the DGS extras filled (DGS-EXTRAS-1, UX 25)
    assert hand.sidecars["metadata.json"]["import"]["filled"] == {
        "harmonic_spectrum": {"Load": ["Load"], "Generator": []},
        "short_circuit": ["Grid"],
    }
    assert sorted((u.source_item, u.reason) for u in rep.unmapped) == [
        ("ElmRelay 'Relay' (line 46)", "PowerFactory class ElmRelay is not imported"),
        ("ElmShnt 'Filt' (line 44)", "shunt type 0 (a filter) is not imported"),
        ("TypHmccur 'spare' (line 30)", "a type no imported element uses"),
        ("TypLne 'unused type' (line 17)", "a type no imported element uses"),
    ]
    assert ("HV", "Terminals joined", "HV-B") in {
        (a.asset, a.parameter, a.value) for a in rep.assumed
    }
    # JSON-ready, as the contract requires
    json.dumps(rep.to_dict(), allow_nan=False)
    json.dumps(hand.sidecars["metadata.json"], allow_nan=False)


def test_hand_buses_and_coupler_topology(hand):
    n = hand.network
    assert sorted(n.buses.index) == ["EHV", "HV", "LV", "Spare", "T3W star"]
    assert n.buses.at["HV", "v_nom"] == 33.0 and n.buses.at["EHV", "v_nom"] == 132.0
    names = {
        (r["dgs_name"], r["name"])
        for r in hand.sidecars["metadata.json"]["import"]["names"]
        if r["dgs_class"] == "ElmTerm"
    }
    assert ("HV-B", "HV") in names and ("HV", "HV") in names  # merged keeps both
    assert hand.sidecars["metadata.json"]["import"]["dgs_names"] == {
        "HV": ["HV", "HV-B"],
        "LV": ["LV"],
        "Spare": ["Spare"],
        "EHV": ["EHV"],
    }
    sw = hand.sidecars["metadata.json"]["import"]["switches"]
    assert sw == [
        {"dgs_name": "C1", "kind": "coupler", "closed": True},
        {"dgs_name": "C2", "kind": "coupler", "closed": False},
        {"dgs_name": "S-PV", "kind": "switch", "closed": False},
    ]


def test_hand_line(hand):
    ln = hand.network.lines.loc["L1"]
    assert (ln.bus0, ln.bus1) == ("HV", "Spare")  # HV-B merged into HV
    # r = 0.1 * 10 / 2, x = 0.4 * 10 / 2, b = 3e-6 * 10 * 2, g = 0.1e-6 * 10 * 2
    assert ln.r == pytest.approx(0.5) and ln.x == pytest.approx(2.0)
    assert ln.b == pytest.approx(6e-5) and ln.g == pytest.approx(2e-6)
    assert ln.s_nom == pytest.approx(math.sqrt(3) * 33.0 * 0.5 * 2)  # 57.157 MVA


def test_hand_two_winding_transformer(hand):
    t = hand.network.transformers.loc["T2W"]
    # z = 0.10, r = 50 / (1000 * 10) = 0.005, x = sqrt(0.01 - 0.000025)
    assert t.r == pytest.approx(0.005) and t.x == pytest.approx(0.09987492177719089)
    # g = 10 / 10000 = 0.001, |y| = 0.005, b = -sqrt(0.005**2 - 0.001**2)
    assert t.g == pytest.approx(0.001) and t.b == pytest.approx(-0.004898979485566356)
    # HV tap: 1 + 2.5 / 100 * (-2 - 0) = 0.95; clock 11 = 330 degrees
    assert t.tap_ratio == pytest.approx(0.95) and t.phase_shift == pytest.approx(330.0)
    assert (t.s_nom, t.tap_side, t.tap_position) == (10.0, 0, -2)


def test_hand_three_winding_star_with_unequal_ratings(hand):
    tr = hand.network.transformers
    # Pairs on min(S_i, S_j): hm 10% on 50, ml 8% on 25, lh 12% on 25 MVA.
    # On the HV rating (100 MVA): 0.20, 0.32, 0.48. Legs: h = (0.20 + 0.48 -
    # 0.32) / 2 = 0.18, m = (0.20 + 0.32 - 0.48) / 2 = 0.02, l = (0.32 + 0.48
    # - 0.20) / 2 = 0.30; on each leg's own rating: 0.18, 0.01, 0.075.
    for leg, bus, s_nom, x in (
        ("T3W HV", "EHV", 100.0, 0.18),
        ("T3W MV", "HV", 50.0, 0.01),
        ("T3W LV", "LV", 25.0, 0.075),
    ):
        row = tr.loc[leg]
        assert (row.bus0, row.bus1, row.s_nom) == ("T3W star", bus, s_nom)
        assert row.x == pytest.approx(x) and row.r == pytest.approx(0.0)
        assert row.tap_ratio == pytest.approx(1.0)


def test_hand_injections_shunts_and_grid(hand):
    n = hand.network
    ld = n.loads.loc["Load"]
    assert (ld.bus, ld.p_set, ld.q_set) == ("LV", 6.0, 2.0)  # scale0 2
    assert n.loads.at["Load", "harmonic_spectrum"] == "5:20, 11:9"  # 7th is padding
    g = n.generators
    assert g.at["PV", "p_nom"] == pytest.approx(4.5)  # 5 MVA * 0.9
    assert (g.at["PV", "control"], bool(g.at["PV", "active"])) == (
        "PQ",
        False,
    )  # open switch
    assert g.at["Gen", "p_nom"] == pytest.approx(8.0) and g.at["Gen", "control"] == "PV"
    assert n.buses.at["Spare", "v_mag_pu_set"] == pytest.approx(1.02)
    assert g.at["Grid", "control"] == "Slack" and math.isinf(g.at["Grid", "p_nom"])
    assert [
        g.at["Grid", c] for c in ("sk_max_mva", "sk_min_mva", "rx_max", "rx_min")
    ] == [
        1000.0,
        500.0,
        0.1,
        0.2,
    ]
    sh = n.shunt_impedances
    assert sh.at["Cap", "b"] == pytest.approx(6.0 * 2 / 3 / 11.0**2)  # 4 Mvar at 11 kV
    assert sh.at["Reac", "b"] == pytest.approx(-2.0 / 33.0**2)
    assert "Filt" not in sh.index


def test_hand_network_solves_and_survives_netcdf(hand, tmp_path):
    import pypsa

    n = hand.network
    n.lpf()
    n.pf(use_seed=True)
    assert n.buses_t.v_mag_pu.notna().all().all()
    path = tmp_path / "network.nc"
    n.export_to_netcdf(path)
    back = pypsa.Network(path)
    assert back.loads.at["Load", "harmonic_spectrum"] == "5:20, 11:9"
    assert back.generators.at["Grid", "sk_max_mva"] == 1000.0


# --- refusals (D8): each guard over a hand list of fields -------------------


@pytest.mark.parametrize(
    "cls, row, words",
    [
        ("ElmLne", "31;L1;2;30;-1.0;2;0", "length (km) -1 must be above zero"),
        ("ElmLne", "31;L1;2;30;0.0;2;0", "length (km) 0 must be above zero"),
        ("ElmLne", "31;L1;2;30;10.0;0;0", "parallel lines 0 must be at least 1"),
        ("TypLne", "30;L1 type;33.0;-0.5;0.1;0.4;3.0;0.1", "rated current (kA) -0.5"),
        ("TypLne", "30;L1 type;0;0.5;0.1;0.4;3.0;0.1", "rated voltage (kV) 0"),
        ("TypLne", "30;L1 type;33.0;0.5;;0.4;3.0;0.1", "resistance per km is missing"),
        ("TypLne", "30;L1 type;33.0;0.5;-0.1;0.4;3.0;0.1", "must not be negative"),
        ("ElmTr2", "33;T2W;2;32;-2;0;0", "parallel units 0 must be at least 1"),
        (
            "TypTr2",
            "32;T type;-10.0;33.0;11.0;10.0;50.0;10.0;0.5;11.0;2.5;0;0",
            "rating (MVA) -10",
        ),
        (
            "TypTr2",
            "32;T type;10.0;33.0;11.0;0.1;50.0;10.0;0.5;11.0;2.5;0;0",
            "above z",
        ),
        (
            "TypTr2",
            "32;T type;10.0;33.0;11.0;10.0;50.0;100.0;0.5;11.0;2.5;0;0",
            "above |y|",
        ),
        (
            "TypTr3",
            "34;T3 type;100.0;0;25.0;132.0;33.0;11.0;10.0;8.0;12.0",
            "MV rating (MVA) 0",
        ),
        ("ElmGenstat", "41;PV;2;5.0;1.2;3.0;1.0;0", "power factor 1.2 is above 1"),
        ("ElmGenstat", "41;PV;2;-5.0;0.9;3.0;1.0;0", "rating (MVA) -5"),
        ("ElmShnt", "45;Cap;2;2;11.0;6.0;3;4;0", "steps in service"),
        ("ElmShnt", "45;Cap;2;2;11.0;6.0;0;0;0", "steps 0 must be at least 1"),
        ("ElmXnet", "44;Grid;2;1.0;-1000.0;500.0;0.1;0.2;SL;0", "sk max mva -1000"),
        ("ElmTerm", "14;EHV;2;0;0", "nominal voltage (kV) 0"),
        ("TypHmccur", "50;drive;3;1;7;11;3;20.0;0.0;9.0", "order 1"),
        ("TypHmccur", "50;drive;3;5;7;11;3;-20.0;0.0;9.0", "amplitude -20"),
        ("TypLne", "30;L1 type;33.0;0.5;-0.1;0.4;3.0;0.1", "must not be negative"),
        ("TypLne", "30;L1 type;33.0;0.5;0.1;0;3.0;0.1", "x must be above zero"),
        ("TypLne", "30;L1 type;33.0;0.5;0.1;0.4;-3.0;0.1", "must not be negative"),
        ("TypLne", "30;L1 type;33.0;0.5;0.1;0.4;3.0;-0.1", "must not be negative"),
        (
            "TypTr2",
            "32;T type;10.0;33.0;11.0;10.0;-10.0;10.0;0.5;11.0;2.5;0;0",
            "copper losses give r -0.001",
        ),
        (
            "TypTr2",
            "32;T type;10.0;33.0;11.0;10.0;50.0;-1.0;0.5;11.0;2.5;0;0",
            "no-load losses give g -0.0001",
        ),
        ("ElmShnt", "45;Cap;2;2;11.0;-6.0;3;2;0", "must be 0..rated"),
        ("ElmShnt", "45;Cap;2;2;11.0;6.0;3;-1;0", "must be 0..rated"),
        (
            "ElmXnet",
            "44;Grid;2;1.0;1000.0;500.0;-0.1;0.2;SL;0",
            "rx_max -0.1 is negative",
        ),
    ],
)
def test_each_guard_refuses_naming_the_item(tmp_path, cls, row, words):
    rows = {k: list(v) for k, v in ROWS.items()}
    oid = row.split(";")[0]
    rows[cls] = [row if r.split(";")[0] == oid else r for r in rows[cls]]
    with pytest.raises(ImportFormatError) as e:
        read_text(tmp_path, dgs_text(rows))
    msg = str(e.value)
    assert msg.startswith("hand.dgs: ") and words in msg
    # the item is named: the element itself, or the element using the type
    user = {"TypLne": "ElmLne 'L1'", "TypTr2": "ElmTr2 'T2W'", "TypTr3": "ElmTr3 'T3W'"}
    assert user.get(cls, f"{cls} '") in msg


def test_file_level_refusals(tmp_path):
    good = tmp_path / "a.dgs"
    good.write_text(dgs_text())
    with pytest.raises(ImportFormatError, match="one .dgs file, got 2"):
        read_dgs([good, good], {})
    other = tmp_path / "a.txt"
    other.write_text(dgs_text())
    with pytest.raises(ImportFormatError, match="a.txt is not a .dgs file"):
        read_dgs([other], {})
    with pytest.raises(ImportFormatError, match="version '6.0' found"):
        read_text(tmp_path, dgs_text().replace("1;Version;5.0", "1;Version;6.0"))


def test_the_registry_reads_dgs(tmp_path):
    path = tmp_path / "net.dgs"
    path.write_text(dgs_text())
    res = import_result.read("dgs", [path], {})
    assert res.report.counts["Lines"] == 1


# --- the GC-05 writer round trip, against pandapower -------------------------


def _round_trip(tmp_path, net):
    path = tmp_path / "rt.dgs"
    write_dgs(net, path, title="rt", harmonics=HARMONICS, converters=["bess"])
    return read_dgs([path], {})


def _ppc_branch(net):
    pp.runpp(net)
    br = net._ppc["branch"]
    look = net._pd2ppc_lookups["branch"]
    return br, look


@pytest.mark.parametrize(
    "variant", ["as is", "LV-side tap", "rated off the bus voltage"]
)
def test_round_trip_matches_pandapower_per_unit_and_load_flow(tmp_path, variant):
    net = fixture_net()
    if variant == "LV-side tap":
        net.trafo.at[0, "tap_side"] = "lv"
    if variant == "rated off the bus voltage":
        net.trafo.at[1, "vn_hv_kv"] = 34.5  # on a 33 kV bus
        net.trafo.at[1, "vn_lv_kv"] = 0.7  # on a 0.69 kV bus
    res = _round_trip(tmp_path, net)
    n = res.network
    br, look = _ppc_branch(net)
    sbase = net.sn_mva
    a, _ = look["line"]
    for k, ln in enumerate(net.line.itertuples()):
        row = n.lines.loc[ln.name]
        zb = n.buses.at[row.bus0, "v_nom"] ** 2 / sbase
        assert row.r / zb == pytest.approx(br[a + k, 2].real, abs=1e-6)
        assert row.x / zb == pytest.approx(br[a + k, 3].real, abs=1e-6)
        assert row.b * zb == pytest.approx(br[a + k, 4].real, abs=1e-6)
    a, _ = look["trafo"]
    for k, tr in enumerate(net.trafo.itertuples()):
        row = n.transformers.loc[tr.name]
        assert row.r * sbase / row.s_nom == pytest.approx(br[a + k, 2].real, abs=1e-6)
        assert row.x * sbase / row.s_nom == pytest.approx(br[a + k, 3].real, abs=1e-6)
        assert row.tap_ratio == pytest.approx(br[a + k, 8].real, abs=1e-6)
        assert row.phase_shift == pytest.approx(br[a + k, 9].real, abs=1e-6)
    if variant == "rated off the bus voltage":
        assert "Off-nominal ratio" in {a.parameter for a in res.report.assumed}
    # the AC load flow agrees with pandapower's
    n.lpf()
    n.pf(use_seed=True)
    vm = n.buses_t.v_mag_pu.iloc[0]
    va = n.buses_t.v_ang.iloc[0].map(math.degrees)
    names = res.sidecars["metadata.json"]["import"]["names"]
    bus_name = {r["dgs_id"]: r["name"] for r in names if r["dgs_class"] == "ElmTerm"}
    for (i, b), dgs_bus in zip(net.bus.iterrows(), sorted(bus_name, key=int)):
        nb = bus_name[dgs_bus]
        assert vm[nb] == pytest.approx(net.res_bus.at[i, "vm_pu"], abs=1e-6), nb
        assert va[nb] == pytest.approx(net.res_bus.at[i, "va_degree"], abs=1e-6), nb


def test_round_trip_counts_and_spectra(tmp_path):
    from gridspine.static import harmonics as hm

    net = fixture_net()
    res = _round_trip(tmp_path, net)
    assert res.report.counts == {
        "Buses": 6,
        "Lines": 3,
        "Transformers": 2,
        "Loads": 2,
        "Harmonic spectra": 2,
        "Static generators": 2,
        "Synchronous machines": 1,
        "External grids": 1,
        "Shunts": 2,
        "Loads given a harmonic spectrum": 1,
        "Generators given a harmonic spectrum": 1,
        "Slack generators given short-circuit data": 1,
    }
    assert res.sidecars["metadata.json"]["import"]["filled"] == {
        "harmonic_spectrum": {"Load": ["drive"], "Generator": ["pv"]},
        "short_circuit": ["grid"],
    }
    assert res.report.unmapped == []
    n = res.network
    for table, comp in (("load", n.loads), ("sgen", n.generators)):
        for name in net[table]["name"]:
            got = comp.at[name, "harmonic_spectrum"] if name in HARMONICS else ""
            want = HARMONICS.get(name)
            if want is None:
                assert got == ""
                continue
            spec = hm.parse_spectrum(want["spectrum"])
            assert hm.parse_spectrum(got) == pytest.approx(spec)
    # the slack carries both short-circuit cases
    g = n.generators.loc["grid"]
    assert (g.sk_max_mva, g.sk_min_mva, g.rx_max, g.rx_min) == (
        4000.0,
        1500.0,
        0.1,
        0.15,
    )


# --- pypsa behaviour the mapping relies on (D5, D6) ---------------------------


def test_an_infinite_slack_rating_does_not_change_the_load_flow(hand):
    n = hand.network
    n.lpf()
    n.pf(use_seed=True)
    before = n.buses_t.v_mag_pu.iloc[0].copy()
    n.generators.at["Grid", "p_nom"] = 1e6
    n.lpf()
    n.pf(use_seed=True)
    assert n.buses_t.v_mag_pu.iloc[0].to_dict() == pytest.approx(
        before.to_dict(), abs=1e-12
    )


def test_an_out_of_service_item_is_ignored_by_the_load_flow(tmp_path):
    rows = {k: list(v) for k, v in ROWS.items()}
    rows["ElmLod"] = ["40;Load;2;3.0;1.0;2.0;50;1"]  # out of service
    off = read_text(tmp_path, dgs_text(rows)).network
    rows["ElmLod"] = ["40;Load;2;0.0;0.0;2.0;50;0"]  # in service, no power
    zero = read_text(tmp_path, dgs_text(rows), "zero.dgs").network
    assert not bool(off.loads.at["Load", "active"])
    for n in (off, zero):
        n.lpf()
        n.pf(use_seed=True)
    assert off.buses_t.v_mag_pu.iloc[0].to_dict() == pytest.approx(
        zero.buses_t.v_mag_pu.iloc[0].to_dict(), abs=1e-9
    )


def test_a_bus_cut_off_from_every_source_is_warned(tmp_path):
    rows = {k: list(v) for k, v in ROWS.items()}
    rows["ElmLne"] = ["31;L1;2;30;10.0;2;1"]  # Spare loses its line; Gen still feeds it
    rows["ElmSym"] = ["43;Gen;2;42;4.0;1.02;0;1"]  # and its machine
    res = read_text(tmp_path, dgs_text(rows))
    assert any("cannot solve them: Spare" in w for w in res.report.warnings)


# --- details the mutant run left untested --------------------------------------


def _with(**changes):
    rows = {k: list(v) for k, v in ROWS.items()}
    for cls, new in changes.items():
        rows[cls] = new
    return rows


def test_imported_spectra_fill_only_the_fields_the_screen_reads(tmp_path):
    from gridspine.static import harmonics as hm

    n = _round_trip(tmp_path, fixture_net()).network
    # harmonic_source stays empty everywhere: the screen accepts only its
    # topologies there (DGS-EXTRAS-1)
    assert set(n.loads.harmonic_source) == {""}
    assert set(n.generators.harmonic_source) == {""}
    # a unit without a spectrum gets an empty string, not a placeholder
    assert n.loads.at["heater", "harmonic_spectrum"] == ""
    assert n.generators.at["bess", "harmonic_spectrum"] == ""
    # the sgen's rating comes from sgn; a load's file row has none
    assert n.generators.at["pv", "rated_mva"] == 5.0
    assert math.isnan(n.loads.at["drive", "rated_mva"])
    # the screen takes the imported spectrum as the unit's own
    g = n.generators.loc["pv"]
    spec, source = hm.unit_spectrum(g.harmonic_source, g.harmonic_spectrum)
    assert source == "vendor"
    assert spec == pytest.approx(hm.parse_spectrum(HARMONICS["pv"]["spectrum"]))


def test_a_load_with_a_spectrum_is_warned_for_its_missing_rating(hand):
    n = hand.network
    assert n.loads.at["Load", "harmonic_source"] == ""
    assert math.isnan(n.loads.at["Load", "rated_mva"])
    assert (
        "Load 'Load' has a harmonic spectrum but the file gives no rated apparent "
        "power: enter it for the harmonic screen"
    ) in hand.report.warnings
    # the PV row names no spectrum, so it gets no rating warning
    assert not any("'PV'" in w for w in hand.report.warnings)


def test_an_unnamed_item_is_reported_by_its_id(tmp_path):
    res = read_text(tmp_path, dgs_text(_with(ElmRelay=["60;"])))
    assert (
        "ElmRelay 'ID 60' (line 46)",
        "PowerFactory class ElmRelay is not imported",
    ) in {(u.source_item, u.reason) for u in res.report.unmapped}


def test_an_empty_scaling_means_one(tmp_path):
    res = read_text(tmp_path, dgs_text(_with(ElmLod=["40;Load;2;3.0;1.0;;50;0"])))
    ld = res.network.loads.loc["Load"]
    assert (ld.p_set, ld.q_set) == (3.0, 1.0)


def test_a_repeated_name_gets_a_suffix_and_a_warning(tmp_path):
    rows = _with(
        ElmLod=[*ROWS["ElmLod"], "48;Load;2;1.0;0.0;1.0;;0"],
        StaCubic=[*ROWS["StaCubic"], "118;c;12;48;0"],
    )
    res = read_text(tmp_path, dgs_text(rows))
    assert list(res.network.loads.index) == ["Load", "Load (2)"]
    assert res.network.loads.at["Load (2)", "p_set"] == 1.0
    assert (
        "Two Load items are named 'Load'; the second is 'Load (2)'"
        in res.report.warnings
    )


def test_a_path_given_as_text_is_read(tmp_path):
    path = tmp_path / "hand.dgs"
    path.write_text(dgs_text(), encoding="utf-8")
    assert read_dgs([str(path)], {}).report.counts["Buses"] == 4


def test_a_zero_power_factor_is_refused(tmp_path):
    with pytest.raises(
        ImportFormatError, match="rated power factor 0 must be above zero"
    ):
        read_text(tmp_path, dgs_text(_with(ElmGenstat=["41;PV;2;5.0;0;3.0;1.0;0"])))


PHASE_WARNING = (
    "Some transformers shift the phase angle (vector group). The AC load "
    "flow converges from a linear-flow start, not from a flat start."
)


def test_a_phase_shifting_transformer_is_warned(hand, tmp_path):
    assert hand.network.transformers.at["T2W", "phase_shift"] == 330.0  # nt2ag 11
    assert PHASE_WARNING in hand.report.warnings
    yy0 = "32;T type;10.0;33.0;11.0;10.0;50.0;10.0;0.5;0;2.5;0;0"
    res = read_text(tmp_path, dgs_text(_with(TypTr2=[yy0])))
    assert res.network.transformers.at["T2W", "phase_shift"] == 0.0
    assert PHASE_WARNING not in res.report.warnings


def test_three_winding_copper_losses_split_into_the_legs(tmp_path):
    head = dict(HEAD, TypTr3=HEAD["TypTr3"] + ";pcut3_h(r);pcut3_m(r);pcut3_l(r)")
    rows = _with(
        TypTr3=["34;T3 type;100.0;50.0;25.0;132.0;33.0;11.0;10.0;8.0;12.0;300;50;100"]
    )
    tr = read_text(tmp_path, dgs_text(rows, head)).network.transformers
    # Pair r on its own (smaller) rating: hm 300 kW / 50 MVA = 0.006, ml 50 kW /
    # 25 MVA = 0.002, lh 100 kW / 25 MVA = 0.004. On 100 MVA: 0.012, 0.008,
    # 0.016. Legs: h = (0.012 + 0.016 - 0.008) / 2 = 0.010, m = (0.012 + 0.008 -
    # 0.016) / 2 = 0.002, l = (0.008 + 0.016 - 0.012) / 2 = 0.006; on each leg's
    # own rating: 0.010, 0.001, 0.0015.
    # x per pair on 100 MVA: sqrt(z^2 - r^2) on its own rating, times 2 or 4.
    xhm = 2 * math.sqrt(0.10**2 - 0.006**2)
    xml = 4 * math.sqrt(0.08**2 - 0.002**2)
    xlh = 4 * math.sqrt(0.12**2 - 0.004**2)
    for leg, r, x in (
        ("T3W HV", 0.010, (xhm + xlh - xml) / 2),
        ("T3W MV", 0.001, (xhm + xml - xlh) / 2 * 0.5),
        ("T3W LV", 0.0015, (xml + xlh - xhm) / 2 * 0.25),
    ):
        assert tr.at[leg, "r"] == pytest.approx(r)
        assert tr.at[leg, "x"] == pytest.approx(x)


def test_a_three_winding_leg_rated_off_its_bus_voltage(tmp_path):
    # LV winding rated 10 kV on the 11 kV LV bus: ratio (132/132)/(10/11) = 1.1
    rows = _with(TypTr3=["34;T3 type;100.0;50.0;25.0;132.0;33.0;10.0;10.0;8.0;12.0"])
    res = read_text(tmp_path, dgs_text(rows))
    tr = res.network.transformers
    assert tr.at["T3W LV", "tap_ratio"] == pytest.approx(1.1)
    assert tr.at["T3W LV", "x"] == pytest.approx(0.075 * (10.0 / 11.0) ** 2)
    assert tr.at["T3W HV", "tap_ratio"] == pytest.approx(1.0)
    off = {(a.asset, a.parameter): a.value for a in res.report.assumed}
    assert off[("T3W LV", "Off-nominal ratio")] == pytest.approx(1.1)
    assert ("T3W MV", "Off-nominal ratio") not in off
    assert off[("T3W star", "Star point")] == 132.0


def test_the_star_point_is_reported_at_the_bus_voltage(tmp_path):
    # HV winding rated 130 kV on the 132 kV bus: the star sits at 132 kV
    rows = _with(TypTr3=["34;T3 type;100.0;50.0;25.0;130.0;33.0;11.0;10.0;8.0;12.0"])
    res = read_text(tmp_path, dgs_text(rows))
    assert res.network.buses.at["T3W star", "v_nom"] == 132.0
    off = {(a.asset, a.parameter): a.value for a in res.report.assumed}
    assert off[("T3W star", "Star point")] == 132.0
    # (130/132)/(33/33) on the MV leg
    assert off[("T3W MV", "Off-nominal ratio")] == pytest.approx(130.0 / 132.0)


# --- every guard's boundary, and every skip, reached (mutant survivors) ---------


@pytest.mark.parametrize(
    "cls,row,component,name,attr,want",
    [
        # zero r, b and g are allowed on a line; only x must be above zero
        ("TypLne", "30;L1 type;33.0;0.5;0;0.4;0;0", "Line", "L1", "r", 0.0),
        ("TypLne", "30;L1 type;33.0;0.5;0;0.4;0;0", "Line", "L1", "b", 0.0),
        ("TypLne", "30;L1 type;33.0;0.5;0;0.4;0;0", "Line", "L1", "g", 0.0),
        # no copper losses: r 0; losses giving r exactly z: x 0
        (
            "TypTr2",
            "32;T type;10.0;33.0;11.0;10.0;0;10.0;0.5;11.0;2.5;0;0",
            "Transformer",
            "T2W",
            "r",
            0.0,
        ),
        (
            "TypTr2",
            "32;T type;10.0;33.0;11.0;10.0;1000.0;10.0;0.5;11.0;2.5;0;0",
            "Transformer",
            "T2W",
            "x",
            0.0,
        ),
        # the 2nd harmonic is an order the screen takes
        (
            "TypHmccur",
            "50;drive;3;2;7;11;3;20.0;0.0;9.0",
            "Load",
            "Load",
            "harmonic_spectrum",
            "2:20, 11:9",
        ),
        ("ElmGenstat", "41;PV;2;5.0;1.0;3.0;1.0;0", "Generator", "PV", "p_nom", 5.0),
        # no reactive power, or no step in service: b 0
        ("ElmShnt", "45;Cap;2;2;11.0;0;3;2;0", "ShuntImpedance", "Cap", "b", 0.0),
        ("ElmShnt", "45;Cap;2;2;11.0;6.0;3;0;0", "ShuntImpedance", "Cap", "b", 0.0),
        (
            "ElmXnet",
            "44;Grid;2;1.0;1000.0;500.0;0;0.2;SL;0",
            "Generator",
            "Grid",
            "rx_max",
            0.0,
        ),
        # two-winding parallel units multiply the rating
        ("ElmTr2", "33;T2W;2;32;-2;2;0", "Transformer", "T2W", "s_nom", 20.0),
        # an unnamed element is named by its class and ID
        ("ElmLne", "31;;2;30;10.0;2;0", "Line", "line 31", "length", 10.0),
    ],
)
def test_each_guard_boundary_is_accepted(
    tmp_path, cls, row, component, name, attr, want
):
    oid = row.split(";")[0]
    rows = _with(**{cls: [row if r.split(";")[0] == oid else r for r in ROWS[cls]]})
    n = read_text(tmp_path, dgs_text(rows)).network
    got = n.components[component].static.at[name, attr]
    assert got == (pytest.approx(want) if isinstance(want, float) else want)


def test_an_empty_short_circuit_field_is_left_out(tmp_path):
    rows = _with(ElmXnet=["44;Grid;2;1.0;1000.0;;0.1;0.2;SL;0"])
    g = read_text(tmp_path, dgs_text(rows)).network.generators.loc["Grid"]
    assert g.sk_max_mva == 1000.0 and "sk_min_mva" not in g.index


def test_a_second_external_grid_keeps_the_first_ones_strength(tmp_path):
    rows = _with(
        ElmXnet=[*ROWS["ElmXnet"], "48;Grid2;2;1.0;2000.0;800.0;0.3;0.4;SL;0"],
        StaCubic=[*ROWS["StaCubic"], "118;c;10;48;0"],
    )
    g = read_text(tmp_path, dgs_text(rows)).network.generators
    assert g.loc["Grid", ["sk_max_mva", "sk_min_mva", "rx_max", "rx_min"]].tolist() == [
        1000.0,
        500.0,
        0.1,
        0.2,
    ]
    assert g.at["Grid2", "sk_max_mva"] == 2000.0


def test_a_tap_ratio_of_zero_is_refused(tmp_path):
    rows = _with(
        TypTr2=["32;T type;10.0;33.0;11.0;10.0;50.0;10.0;0.5;11.0;50.0;0;0"],
        ElmTr2=["33;T2W;2;32;-2;1;0"],  # 1 + 50 % * (-2) = 0
    )
    with pytest.raises(ImportFormatError, match="tap position -2 gives ratio 0"):
        read_text(tmp_path, dgs_text(rows))


@pytest.mark.parametrize(
    "typ,assumed",
    [
        ("32;T type;10.0;33.0;11.0;10.0;50.0;10.0;0.5;11.0;2.5;0;0", None),
        ("32;T type;10.0;34.0;11.0;10.0;50.0;10.0;0.5;11.0;2.5;0;0", 34.0 / 33.0),
        ("32;T type;10.0;33.0;10.0;10.0;50.0;10.0;0.5;11.0;2.5;0;0", 11.0 / 10.0),
    ],
)
def test_a_two_winding_off_nominal_ratio_is_recorded(tmp_path, typ, assumed):
    res = read_text(tmp_path, dgs_text(_with(TypTr2=[typ])))
    got = [
        a.value
        for a in res.report.assumed
        if (a.asset, a.parameter) == ("T2W", "Off-nominal ratio")
    ]
    assert got == ([] if assumed is None else [pytest.approx(assumed)])


T3_PCU_HEAD = dict(HEAD, TypTr3=HEAD["TypTr3"] + ";pcut3_h(r);pcut3_m(r);pcut3_l(r)")


@pytest.mark.parametrize("hm,ok", [(-1.0, False), (6000.0, False), (5000.0, True)])
def test_three_winding_copper_losses_are_guarded(tmp_path, hm, ok):
    # hm pair: 10 % on 50 MVA, so z = 0.1 and r = kW / 50 000; 5000 kW is r = z
    t3 = f"34;T3 type;100.0;50.0;25.0;132.0;33.0;11.0;10.0;8.0;12.0;{hm};0;0"
    text = dgs_text(_with(TypTr3=[t3]), T3_PCU_HEAD)
    if ok:
        assert len(read_text(tmp_path, text).network.transformers) == 4
    else:
        with pytest.raises(ImportFormatError, match="copper losses hm give r above z"):
            read_text(tmp_path, text)


def test_a_three_winding_leg_off_its_bus_voltage_scales_r_too(tmp_path):
    t3 = "34;T3 type;100.0;50.0;25.0;132.0;33.0;10.0;10.0;8.0;12.0;300;50;100"
    tr = read_text(tmp_path, dgs_text(_with(TypTr3=[t3]), T3_PCU_HEAD)).network
    # the LV leg's r (0.0015 on its own rating, see the copper-loss test)
    # moves from 10 kV to the 11 kV bus
    assert tr.transformers.at["T3W LV", "r"] == pytest.approx(0.0015 * (10 / 11) ** 2)


def test_scaling_applies_to_generators_and_machines(tmp_path):
    head = dict(
        HEAD,
        ElmGenstat=HEAD["ElmGenstat"] + ";scale0(r)",
        ElmSym=HEAD["ElmSym"] + ";scale0(r)",
    )
    rows = _with(
        ElmGenstat=["41;PV;2;5.0;0.9;3.0;1.0;0;2.0"],
        ElmSym=["43;Gen;2;42;4.0;1.02;0;0;0.5"],
    )
    g = read_text(tmp_path, dgs_text(rows, head)).network.generators
    assert (g.at["PV", "p_set"], g.at["PV", "q_set"]) == (6.0, 2.0)
    assert g.at["Gen", "p_set"] == 2.0


def test_a_unit_without_a_spectrum_gets_no_harmonic_columns(hand):
    cols = hand.network.generators.columns
    assert not {"harmonic_spectrum", "harmonic_source", "rated_mva"} & set(cols)


def _unmapped(res):
    return sorted(
        (u.source_item.split(" (line")[0], u.reason) for u in res.report.unmapped
    )


def test_an_element_with_a_loose_end_is_reported_and_left_out(tmp_path):
    cubic = [
        r
        for r in ROWS["StaCubic"]
        if r.split(";")[0] not in ("105", "107", "110", "111")
    ]
    cubic.append("111;c;2;40;0")  # the load's cubicle sits in the grid, not a terminal
    res = read_text(tmp_path, dgs_text(_with(StaCubic=cubic)))
    got = _unmapped(res)
    for item, side in (
        ("ElmLne 'L1'", 1),
        ("ElmTr2 'T2W'", 1),
        ("ElmTr3 'T3W'", 2),
        ("ElmLod 'Load'", 0),
    ):
        assert (item, f"side {side} is not connected to a terminal") in got
    n = res.network
    assert n.lines.empty and n.loads.empty and n.transformers.empty


def test_a_cubicle_missing_either_end_is_reported(tmp_path):
    cubic = [*ROWS["StaCubic"], "118;c;;40;1", "119;c;12;;0"]
    got = _unmapped(read_text(tmp_path, dgs_text(_with(StaCubic=cubic))))
    reason = "a cubicle without its element or terminal"
    assert got.count(("StaCubic 'c'", reason)) == 2


def test_a_coupler_missing_either_side_is_reported(tmp_path):
    rows = _with(
        ElmCoup=[*ROWS["ElmCoup"], "22;C3;2;1", "23;C4;2;1"],
        StaCubic=[*ROWS["StaCubic"], "118;c;10;22;0", "119;c;10;23;1"],
    )
    res = read_text(tmp_path, dgs_text(rows))
    got = _unmapped(res)
    for c in ("C3", "C4"):
        assert (f"ElmCoup '{c}'", "a coupler not between two terminals") in got
    assert res.report.counts["Couplers"] == 2


def test_an_element_pointing_at_the_wrong_type_is_reported(tmp_path):
    rows = _with(
        ElmLne=["31;L1;2;32;10.0;2;0"],  # a transformer type
        ElmSym=["43;Gen;2;32;4.0;1.02;0;0"],
        ElmLod=["40;Load;2;3.0;1.0;2.0;42;0"],  # a machine type as spectrum
    )
    res = read_text(tmp_path, dgs_text(rows))
    got = _unmapped(res)
    assert ("ElmLne 'L1'", "no TypLne type") in got
    assert ("ElmSym 'Gen'", "no TypSym type") in got
    assert (
        "ElmLod 'Load'",
        "its harmonic source is not a harmonic current type",
    ) in got
    assert "Gen" not in res.network.generators.index


def test_a_line_type_class_absent_from_the_file_is_reported(tmp_path):
    rows = _with(ElmLne=["31;L1;2;;10.0;2;0"])
    del rows["TypLne"]
    got = _unmapped(read_text(tmp_path, dgs_text(rows)))
    assert ("ElmLne 'L1'", "no TypLne type") in got


def test_a_closed_switch_keeps_its_element_in_service(tmp_path):
    rows = _with(StaSwitch=[*ROWS["StaSwitch"], "91;S-Gen;113;1"])
    g = read_text(tmp_path, dgs_text(rows)).network.generators
    assert bool(g.at["Gen", "active"]) and not bool(g.at["PV", "active"])


def test_a_type_pointer_into_another_class_with_its_own_class_absent(tmp_path):
    # the line points at the transformer type while no TypLne table exists
    rows = _with(ElmLne=["31;L1;2;32;10.0;2;0"])
    del rows["TypLne"]
    got = _unmapped(read_text(tmp_path, dgs_text(rows)))
    assert ("ElmLne 'L1'", "no TypLne type") in got
