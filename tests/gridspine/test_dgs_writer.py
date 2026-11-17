"""
PowerFactory DGS writer (GC-05): the file parses back, with this test's own
reader, to the same elements and per-unit impedances as the pandapower net.
"""

import math

import pandapower as pp
import pytest

from gridspine.handoff.dgs_writer import (
    ATTRIBUTE_SOURCES,
    ATTRIBUTES,
    HARMONIC_CLASS,
    NO_SPECTRUM,
    WITH_SPECTRUM,
    write_dgs,
)
from gridspine.readback.results import dgs_name_map
from gridspine.schema.contracts import ContractError
from gridspine.static.harmonics import DEFAULT_SPECTRA

SBASE = 1.0  # pandapower's own system base for this net (sn_mva)


def read_dgs(path) -> dict:
    """
    ``{class: [ {attr: value} ]}`` — vectors (``name:SIZEROW`` then
    ``name:k``) come back as lists; numbers as int/float by their type.
    """
    out, head = {}, None
    for line in open(path, encoding="utf-8").read().splitlines():
        cells = line.split(";")
        if line.startswith("$$"):
            head = [(c.split("(")[0], c.split("(")[1].rstrip(")")) for c in cells[1:]]
            out.setdefault(cells[0][2:], [])
            cls = cells[0][2:]
            continue
        row = {}
        for (name, kind), v in zip(head, cells):
            val = (
                int(v)
                if kind == "i" or name == "ID"
                else float(v)
                if kind == "r"
                else (int(v) if v else None)
                if kind == "p"
                else v
            )
            if ":" in name:
                base, k = name.split(":")
                if k == "SIZEROW":
                    row[base] = []
                else:
                    row[base].append(val)
            else:
                row[name] = val
        out[cls].append(row)
    return out


def fixture_net(tap_pos=2):
    net = pp.create_empty_network(sn_mva=SBASE, f_hz=50.0, name="fixture")
    b = {
        n: pp.create_bus(net, vn_kv=kv, name=n)
        for n, kv in [
            ("grid", 132.0),
            ("mv;1", 33.0),
            ("mv_2", 33.0),
            ("lv", 0.69),
            ("x" * 50, 33.0),
            ("x" * 50 + "b", 33.0),
        ]
    }
    pp.create_ext_grid(
        net,
        b["grid"],
        vm_pu=1.01,
        s_sc_max_mva=4000.0,
        s_sc_min_mva=1500.0,
        rx_max=0.1,
        rx_min=0.15,
        name="grid",
    )
    pp.create_transformer_from_parameters(
        net,
        b["grid"],
        b["mv;1"],
        sn_mva=80.0,
        vn_hv_kv=132.0,
        vn_lv_kv=33.0,
        vkr_percent=0.4,
        vk_percent=12.0,
        pfe_kw=0.0,
        i0_percent=0.0,
        shift_degree=150.0,
        tap_side="hv",
        tap_neutral=0,
        tap_min=-8,
        tap_max=8,
        tap_step_percent=1.25,
        tap_pos=tap_pos,
        tap_changer_type="Ratio",
        name="T1",
    )
    pp.create_transformer_from_parameters(
        net,
        b["mv_2"],
        b["lv"],
        sn_mva=5.0,
        vn_hv_kv=33.0,
        vn_lv_kv=0.69,
        vkr_percent=0.8,
        vk_percent=7.0,
        pfe_kw=0.0,
        i0_percent=0.0,
        name="T2",
    )
    pp.create_line_from_parameters(
        net,
        b["mv;1"],
        b["mv_2"],
        length_km=3.2,
        r_ohm_per_km=0.06,
        x_ohm_per_km=0.11,
        c_nf_per_km=300.0,
        max_i_ka=0.8,
        type="cs",
        parallel=2,
        name="cable",
    )
    pp.create_line_from_parameters(
        net,
        b["mv;1"],
        b["x" * 50],
        length_km=7.0,
        r_ohm_per_km=0.12,
        x_ohm_per_km=0.35,
        c_nf_per_km=10.0,
        max_i_ka=0.5,
        name="ohl",
    )
    pp.create_line_from_parameters(
        net,
        b["x" * 50],
        b["x" * 50 + "b"],
        length_km=1.0,
        r_ohm_per_km=0.12,
        x_ohm_per_km=0.35,
        c_nf_per_km=10.0,
        max_i_ka=0.5,
        name="ohl2",
    )
    pp.create_load(net, b["lv"], p_mw=3.0, q_mvar=1.0, name="drive")
    pp.create_load(net, b["mv_2"], p_mw=8.0, q_mvar=2.0, name="heater")
    pp.create_sgen(net, b["mv_2"], p_mw=4.0, q_mvar=0.0, sn_mva=5.0, name="pv")
    pp.create_sgen(net, b["mv_2"], p_mw=0.0, q_mvar=0.0, sn_mva=6.0, name="bess")
    pp.create_gen(
        net, b["x" * 50 + "b"], p_mw=5.0, vm_pu=1.0, sn_mva=7.5, name="genset"
    )
    pp.create_shunt(
        net, b["mv;1"], q_mvar=-2.0, p_mw=0.0, step=1, max_step=3, name="cap"
    )
    pp.create_shunt(net, b["mv_2"], q_mvar=1.5, p_mw=0.0, name="reactor")
    return net


HARMONICS = {
    "drive": {
        "spectrum": DEFAULT_SPECTRA["6-pulse"],
        "source": "demo default (6-pulse)",
    },
    "pv": {"spectrum": "4: 1, 5: 2, 10: 0.5, 11: 1.5", "source": "vendor"},
}


@pytest.fixture
def written(tmp_path):
    net = fixture_net()
    path = tmp_path / "fixture.dgs"
    exp = write_dgs(
        net, path, title="fixture", harmonics=HARMONICS, converters=["bess"]
    )
    return net, read_dgs(path), exp


def _terms(dgs):
    return {r["ID"]: r for r in dgs["ElmTerm"]}


def _ends(dgs, elm_id):
    """The terminals of element ``elm_id``, by cubicle side."""
    return {
        c["obj_bus"]: c["fold_id"] for c in dgs["StaCubic"] if c["obj_id"] == elm_id
    }


def test_every_element_is_written_once(written):
    net, dgs, _ = written
    counts = {
        "ElmTerm": len(net.bus),
        "ElmLne": len(net.line),
        "TypLne": len(net.line),
        "ElmTr2": len(net.trafo),
        "TypTr2": len(net.trafo),
        "ElmLod": len(net.load),
        "ElmGenstat": len(net.sgen),
        "ElmSym": len(net.gen),
        "ElmShnt": len(net.shunt),
        "ElmXnet": len(net.ext_grid),
        "TypHmccur": 2,
    }
    assert {k: len(dgs.get(k, [])) for k in counts} == counts
    ids = [r["ID"] for rows in dgs.values() for r in rows]
    assert len(ids) == len(set(ids))
    two_ended = len(net.line) + len(net.trafo)
    assert len(dgs["StaCubic"]) == 2 * two_ended + len(net.load) + len(net.sgen) + len(
        net.gen
    ) + len(net.shunt) + len(net.ext_grid)


def test_names_are_dgs_safe_unique_and_mapped_back(written):
    net, dgs, exp = written
    names = [r["loc_name"] for r in dgs["ElmTerm"]]
    assert len(set(names)) == len(names)
    assert all(len(n) <= 40 and ";" not in n for n in names)
    bus_map, branch_map = dgs_name_map(exp.names)
    assert set(bus_map.values()) == set(net.bus["name"])
    assert bus_map["mv_1"] == "mv;1"
    long_names = sorted(n for n in names if n.startswith("xxx"))
    assert long_names == ["x" * 40, "x" * 38 + "~2"]
    assert branch_map["T1"] == ("grid", "mv;1", "1")
    assert branch_map["cable"] == ("mv;1", "mv_2", "1")


def test_line_per_unit_values_match_pandapower(written):
    net, dgs, _ = written
    pp.runpp(net)
    ppc_br = net._ppc["branch"]
    terms = _terms(dgs)
    types = {r["ID"]: r for r in dgs["TypLne"]}
    for k, elm in enumerate(dgs["ElmLne"]):
        typ = types[elm["typ_id"]]
        kv = terms[_ends(dgs, elm["ID"])[0]]["uknom"]
        zb = kv**2 / SBASE
        n = elm["nlnum"]
        r = typ["rline"] * elm["dline"] / n / zb
        x = typ["xline"] * elm["dline"] / n / zb
        b = typ["bline"] * 1e-6 * elm["dline"] * n * zb
        assert abs(r - ppc_br[k, 2].real) < 1e-6
        assert abs(x - ppc_br[k, 3].real) < 1e-6
        assert abs(b - ppc_br[k, 4].real) < 1e-6
    assert [r["inAir"] for r in dgs["TypLne"]] == [0, 1, 1]  # the cable is not overhead


def test_transformer_per_unit_values_and_tap_match_pandapower(written):
    net, dgs, _ = written
    pp.runpp(net)
    ppc_br = net._ppc["branch"]
    terms = _terms(dgs)
    types = {r["ID"]: r for r in dgs["TypTr2"]}
    for k, elm in enumerate(dgs["ElmTr2"]):
        typ = types[elm["typ_id"]]
        ends = _ends(dgs, elm["ID"])
        kv_hv, kv_lv = terms[ends[0]]["uknom"], terms[ends[1]]["uknom"]
        conv = (SBASE / typ["strn"]) * (typ["utrn_l"] / kv_lv) ** 2 / elm["ntnum"]
        r = typ["pcutr"] / (typ["strn"] * 1000.0) * conv
        z = typ["uktr"] / 100.0 * conv
        x = math.sqrt(z * z - r * r)
        tap = 1 + (elm["nntap"] - typ["nntap0"]) * typ["dutap"] / 100.0
        ratio = (typ["utrn_h"] * tap / kv_hv) / (typ["utrn_l"] / kv_lv)
        row = ppc_br[len(net.line) + k]
        assert abs(r - row[2].real) < 1e-6
        assert abs(x - row[3].real) < 1e-6
        assert abs(ratio - row[8].real) < 1e-6
        assert abs(typ["nt2ag"] * 30.0 - row[9].real) < 1e-6
    assert dgs["ElmTr2"][0]["nntap"] == 2 and dgs["TypTr2"][0]["ntpmx"] == 8


def test_injections_shunts_and_both_short_circuit_cases(written):
    net, dgs, _ = written
    assert [(r["plini"], r["qlini"]) for r in dgs["ElmLod"]] == [(3.0, 1.0), (8.0, 2.0)]
    assert [(r["sgn"], r["pgini"]) for r in dgs["ElmGenstat"]] == [
        (5.0, 4.0),
        (6.0, 0.0),
    ]
    assert dgs["ElmSym"][0]["pgini"] == 5.0 and dgs["TypSym"][0]["sgn"] == 7.5
    cap, reactor = dgs["ElmShnt"]
    assert (cap["shtype"], cap["qtotn"], cap["ncapx"], cap["ncapa"]) == (2, 6.0, 3, 1)
    assert (reactor["shtype"], reactor["qtotn"]) == (1, 1.5)
    x = dgs["ElmXnet"][0]
    assert (x["snss"], x["snssmin"], x["rntxn"], x["rntxnmin"], x["usetp"]) == (
        4000.0,
        1500.0,
        0.1,
        0.15,
        1.01,
    )


def test_each_source_carries_its_units_spectrum_and_a_missing_one_is_listed(written):
    _, dgs, exp = written
    hm = {r["ID"]: r for r in dgs["TypHmccur"]}
    drive = next(r for r in dgs["ElmLod"] if r["loc_name"] == "drive")
    heater = next(r for r in dgs["ElmLod"] if r["loc_name"] == "heater")
    pv = next(r for r in dgs["ElmGenstat"] if r["loc_name"] == "pv")
    bess = next(r for r in dgs["ElmGenstat"] if r["loc_name"] == "bess")
    six = DEFAULT_SPECTRA["6-pulse"]
    assert hm[drive["phmc"]]["norder"] == sorted(six)
    assert hm[drive["phmc"]]["ampl"] == [six[h] for h in sorted(six)]
    assert hm[drive["phmc"]]["alpha"] == [1.4, 1.4, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    assert (hm[pv["phmc"]]["norder"], hm[pv["phmc"]]["ampl"]) == (
        [4, 5, 10, 11],
        [1.0, 2.0, 0.5, 1.5],
    )
    assert hm[pv["phmc"]]["alpha"] == [
        1.0,
        1.4,
        1.4,
        2.0,
    ]  # the band edges of IEC 61000-3-6
    assert heater["phmc"] is None and bess["phmc"] is None
    hs = exp.harmonic_sources.set_index("pp_name")
    assert (
        hs.at["drive", "status"] == WITH_SPECTRUM
        and hs.at["pv", "spectrum_source"] == "vendor"
    )
    assert hs.at["bess", "status"] == NO_SPECTRUM
    assert "heater" not in hs.index  # not a converter: nothing to list


def test_a_spectrum_for_an_unknown_element_or_an_unbounded_grid_is_refused(tmp_path):
    with pytest.raises(ContractError, match="no load or static generator"):
        write_dgs(
            fixture_net(), tmp_path / "a.dgs", harmonics={"nope": {"spectrum": "5: 1"}}
        )
    for col in ("s_sc_min_mva", "rx_min"):
        net = fixture_net()
        net.ext_grid[col] = float("nan")
        with pytest.raises(ContractError, match=col):
            write_dgs(net, tmp_path / "b.dgs")


def test_a_converter_the_net_has_no_injection_for_is_still_listed(tmp_path):
    exp = write_dgs(fixture_net(), tmp_path / "c.dgs", converters=["genset", "ghost"])
    hs = exp.harmonic_sources.set_index("pp_name")
    assert hs.loc[["genset", "ghost"], "status"].tolist() == [NO_SPECTRUM, NO_SPECTRUM]


def test_an_unnamed_bus_gets_a_name(tmp_path):
    net = fixture_net()
    net.bus.loc[0, "name"] = None
    write_dgs(net, tmp_path / "d.dgs")
    assert read_dgs(tmp_path / "d.dgs")["ElmTerm"][0]["loc_name"] == "bus_0"


GOLDEN_HEADERS = [
    "$$General;ID(a:40);Descr(a:40);Val(a:40)",
    "$$ElmNet;ID(a:40);loc_name(a:40);frnom(r)",
    "$$ElmTerm;ID(a:40);loc_name(a:40);fold_id(p);uknom(r);outserv(i)",
    "$$TypLne;ID(a:40);loc_name(a:40);uline(r);sline(r);rline(r);xline(r);bline(r);inAir(i);frnom(r)",
    "$$ElmLne;ID(a:40);loc_name(a:40);fold_id(p);typ_id(p);dline(r);nlnum(i);outserv(i)",
    "$$TypTr2;ID(a:40);loc_name(a:40);strn(r);utrn_h(r);utrn_l(r);uktr(r);pcutr(r);pfe(r);curmg(r);nt2ag(r);"
    "dutap(r);nntap0(i);ntpmn(i);ntpmx(i);tap_side(i);frnom(r)",
    "$$ElmTr2;ID(a:40);loc_name(a:40);fold_id(p);typ_id(p);nntap(i);ntnum(i);outserv(i)",
    "$$ElmLod;ID(a:40);loc_name(a:40);fold_id(p);plini(r);qlini(r);scale0(r);phmc(p);outserv(i)",
    "$$ElmGenstat;ID(a:40);loc_name(a:40);fold_id(p);sgn(r);pgini(r);qgini(r);scale0(r);phmc(p);outserv(i)",
    "$$TypSym;ID(a:40);loc_name(a:40);sgn(r);ugn(r)",
    "$$ElmSym;ID(a:40);loc_name(a:40);fold_id(p);typ_id(p);pgini(r);usetp(r);scale0(r);outserv(i)",
    "$$ElmShnt;ID(a:40);loc_name(a:40);fold_id(p);shtype(i);ushnm(r);qtotn(r);ncapx(i);ncapa(i);outserv(i)",
    "$$ElmXnet;ID(a:40);loc_name(a:40);fold_id(p);usetp(r);snss(r);snssmin(r);rntxn(r);rntxnmin(r);bustp(a:2);"
    "outserv(i)",
    "$$StaCubic;ID(a:40);loc_name(a:40);fold_id(p);obj_id(p);obj_bus(i)",
    "$$TypHmccur;ID(a:40);loc_name(a:40);norder:SIZEROW(i);norder:0(i);norder:1(i);norder:2(i);norder:3(i);"
    "ampl:SIZEROW(i);ampl:0(r);ampl:1(r);ampl:2(r);ampl:3(r);alpha:SIZEROW(i);alpha:0(r);alpha:1(r);alpha:2(r);"
    "alpha:3(r)",
]


def test_the_file_header_and_every_section_header_are_golden(tmp_path):
    path = tmp_path / "g.dgs"
    write_dgs(fixture_net(), path, title="fixture", harmonics={"pv": HARMONICS["pv"]})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[:3] == [
        "$$General;ID(a:40);Descr(a:40);Val(a:40)",
        "1;Version;5.0",
        "2;Title;fixture",
    ]
    assert [ln for ln in lines if ln.startswith("$$")] == GOLDEN_HEADERS
    assert path.read_bytes().endswith(b"\n") and b"\r" not in path.read_bytes()


def test_every_class_names_where_its_attributes_come_from():
    assert set(ATTRIBUTE_SOURCES) == {*ATTRIBUTES, HARMONIC_CLASS}
    assert all(src.startswith("DGS 5.x") for src in ATTRIBUTE_SOURCES.values())


TABLE_CLASS = {
    "bus": "ElmTerm",
    "line": "ElmLne",
    "trafo": "ElmTr2",
    "load": "ElmLod",
    "sgen": "ElmGenstat",
    "gen": "ElmSym",
    "shunt": "ElmShnt",
    "ext_grid": "ElmXnet",
}


def test_every_element_maps_to_exactly_one_dgs_object_both_ways(written):
    net, dgs, exp = written
    names = exp.names
    for table, cls in TABLE_CLASS.items():
        rows = names[names["pp_table"] == table]
        assert sorted(rows["pp_index"]) == sorted(
            getattr(net, table).index
        )  # every pp element, once
        assert sorted(rows["dgs_name"]) == sorted(
            r["loc_name"] for r in dgs[cls]
        )  # every DGS object, once
        assert (rows["dgs_class"] == cls).all()
    terms = {r["ID"]: r["loc_name"] for r in dgs["ElmTerm"]}
    bus_map, _ = dgs_name_map(names)
    for table, (a, b), cls in (
        ("line", ("from_bus", "to_bus"), "ElmLne"),
        ("trafo", ("hv_bus", "lv_bus"), "ElmTr2"),
    ):
        by_name = {r["loc_name"]: r["ID"] for r in dgs[cls]}
        for r in names[names["pp_table"] == table].itertuples():
            ends = _ends(dgs, by_name[r.dgs_name])
            el = getattr(net, table).loc[r.pp_index]
            assert bus_map[terms[ends[0]]] == net.bus.at[el[a], "name"]
            assert bus_map[terms[ends[1]]] == net.bus.at[el[b], "name"]
