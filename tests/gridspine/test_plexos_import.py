"""
PLEXOS importer slice 2a: ``gridspine.ingest.plexos_import``.

The plan and GK A's conditions (P1-P9 slice 1, Q1-Q11 slice 2) are in
``.scratch/project-io/plexos-mapping.md``. Edge cases build a
:class:`PlexosModel` directly; the two fixture files check the whole path.
"""

from __future__ import annotations

import json
import math
import stat
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

pypsa = pytest.importorskip("pypsa")

from gridspine.ingest import import_result  # noqa: E402
from gridspine.ingest.import_result import ImportFormatError  # noqa: E402
from gridspine.ingest.plexos_import import (  # noqa: E402
    AREAS_SIDECAR,
    FUEL_PRICES_SIDECAR,
    Ledger,
    _Converter,
    convert,
    excel_date,
    read_plexos,
)
from gridspine.ingest.plexos_xml import (  # noqa: E402
    Attribute,
    Membership,
    PlexosModel,
    PlexosObject,
    PropertyRow,
    Tag,
    read_plexos_xml,
)

FIXTURES = Path(__file__).parent / "fixtures" / "plexos"
IRL = FIXTURES / "world2015_irl.xml"
EDGE = FIXTURES / "edge_cases.xml"

_COLL = {
    "Node": "Nodes",
    "Generator": "Generators",
    "Line": "Lines",
    "Battery": "Batteries",
    "Fuel": "Fuels",
    "Region": "Regions",
    "Zone": "Zones",
    "System": "Systems",
}


def build(objects, rows=(), memberships=(), attributes=()):
    """
    A PlexosModel from short tuples.
    rows: (cls, name, property, value, unit[, dict of PropertyRow fields]).
    memberships: (parent, child, collection), each side a (cls, name) pair.
    attributes: (cls, name, attribute, value[, unit]).
    """
    objs = {
        (c, n): PlexosObject(str(i), c, n, cat, None)
        for i, (c, n, *rest) in enumerate(objects)
        for cat in [rest[0] if rest else None]
    }
    mems = [
        Membership(f"m{i}", (p[0], c[0], coll), p, c)
        for i, (p, c, coll) in enumerate(memberships)
    ]
    prows = []
    for i, (cls, name, prop, value, unit, *extra) in enumerate(rows):
        parent = ("System", "System")
        child = (cls, name)
        coll = ("System", cls, _COLL.get(cls, cls + "s"))
        kw = dict(extra[0]) if extra else {}
        if "parent" in kw:
            parent = kw.pop("parent")
            coll = (parent[0], cls, kw.pop("collection"))
        prows.append(
            PropertyRow(
                data_id=str(i + 1),
                membership_id="x",
                collection=coll,
                parent=parent,
                child=child,
                property=prop,
                unit=unit,
                raw_value=None if value is None else str(value),
                value=value,
                **kw,
            )
        )
    attrs = [
        Attribute((c, n), a, (rest[1] if len(rest) > 1 else "-"), str(rest[0]), rest[0])
        for (c, n, a, *rest) in attributes
    ]
    return PlexosModel("8.300", "test", {}, objs, mems, prows, attrs, [], [], {})


def bus_gen(gen_rows=(), extra_objects=(), extra_members=(), node_rows=()):
    """One 220 kV node 'A' and one generator 'G' on it."""
    objects = [("Node", "A"), ("Generator", "G"), *extra_objects]
    rows = [
        ("Node", "A", "Voltage", 220, "kV"),
        *node_rows,
        ("Generator", "G", "Units", 1, "-"),
        *gen_rows,
    ]
    members = [(("Generator", "G"), ("Node", "A"), "Nodes"), *extra_members]
    return build(objects, rows, members)


def reasons(conv):
    return [(u.source_item, u.reason) for u in conv.report.unmapped]


def unmapped_reason(conv, needle):
    return [r for item, r in reasons(conv) if needle in item]


def check_ledger(model, conv):
    """GK A Q9: every row exactly once (duplicates raise in Ledger.put)."""
    rows = {("t_data", r.data_id) for r in model.rows}
    attrs = {
        ("t_attribute_data", f"{a.object[0]}|{a.object[1]}|{a.name}")
        for a in model.attributes
    }
    assert set(conv.ledger.dest) == rows | attrs
    totals = conv.ledger.totals()
    assert sum(totals.get("t_data", {}).values()) == len(model.rows)
    assert sum(totals.get("t_attribute_data", {}).values()) == len(model.attributes)


# ----------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def irl():
    model = read_plexos_xml(IRL)
    return model, convert(model, {})


def test_irl_accounting_and_counts(irl):
    model, conv = irl
    check_ledger(model, conv)
    assert conv.ledger.totals() == {
        "t_data": {"network": 282, "sidecar": 9, "unmapped": 243, "assumed": 0},
        "t_attribute_data": {"network": 8, "sidecar": 0, "unmapped": 171, "assumed": 0},
    }
    assert conv.report.counts["grid nodes"] == 3
    assert conv.report.counts["generators"] == 59
    assert conv.report.counts["links"] == 1


def test_irl_hand_checked_values(irl):
    """Values checked by hand against world2015_irl.xml."""
    _, conv = irl
    n = conv.network
    g = n.generators.loc["IRL_Coa_Moneypointpowe13298"]
    assert g.p_nom == pytest.approx(916.0)
    assert g.bus == "EU-IRL" and g.carrier == "Europe_Coa"
    assert g.marginal_cost == pytest.approx(24.15)
    assert n.generators.loc["IRL_Gas_Huntstown13284"].marginal_cost == pytest.approx(
        9.4 * 6.7
    )
    link = n.links.loc["GBR-IRL"]
    assert (link.p_nom, link.p_min_pu) == (800.0, -1.0)
    assert n.carriers.loc["Europe_Gas", "co2_emissions"] == pytest.approx(53.8 * 0.0036)
    assert n.buses.loc["EU-IRL", ["x", "y"]].tolist() == pytest.approx(
        [-8.24389, 53.41291]
    )
    assert "Max Flow 0 MW: line has no capacity" in unmapped_reason(
        conv, "Line 'FRA-IRL'"
    )
    assert any("not selected" in r for _, r in reasons(conv))


def test_edge_fixture_accounting():
    model = read_plexos_xml(EDGE)
    conv = convert(model, {})
    check_ledger(model, conv)
    assert unmapped_reason(conv, "Line 'L1'") == [
        "needs a node voltage to convert reactance"
    ]
    assert (
        "Constraint 'Cap Gas'",
        "generic constraint, no PyPSA equivalent yet",
    ) in reasons(conv)
    assert len(
        [u for u in conv.report.unmapped if u.source_item.startswith("t_")]
    ) == len(model.broken)


def test_ledger_refuses_a_row_twice():
    led = Ledger()
    led.put("t_data", "1", "network")
    with pytest.raises(AssertionError, match="counted twice"):
        led.put("t_data", "1", "unmapped")


def test_read_plexos_result_is_json_ready(tmp_path):
    res = read_plexos([IRL], {})
    json.dumps(res.report.to_dict(), allow_nan=False)
    assert set(res.sidecars) == {AREAS_SIDECAR, FUEL_PRICES_SIDECAR}
    areas = res.sidecars[AREAS_SIDECAR]["areas"]
    region = next(a for a in areas if a["kind"] == "region")
    assert region["voll"] > 0 and sorted(region["buses"]) == [
        "EU-FRA",
        "EU-GBR",
        "EU-IRL",
    ]


# -------------------------------------------------------------------- input


def _zip(tmp_path, members, name="m.zip"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, data in members:
            if isinstance(arc, zipfile.ZipInfo):
                zf.writestr(arc, data)
            else:
                zf.writestr(arc, data)
    return path


def test_zip_reads_like_the_xml(tmp_path):
    xml = IRL.read_bytes()
    path = _zip(tmp_path, [("model/World.XML", xml), ("model/csv_files/a.csv", b"x")])
    res = read_plexos([path], {})
    assert res.report.counts == read_plexos([IRL], {}).report.counts
    assert res.report.source_files == ["m.zip"]
    assert (
        "data file model/csv_files/a.csv",
        "data-file time series are read in a later slice",
    ) in [(u.source_item, u.reason) for u in res.report.unmapped]


def _link_info(name):
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize(
    "members, match",
    [
        ([("/etc/m.xml", b"<a/>")], "absolute path"),
        ([("C:\\m.xml", b"<a/>")], "absolute path"),
        ([("ok.xml", b"<a/>"), ("csv/../../x.csv", b"1")], r"leaves the archive"),
        ([("ok.xml", b"<a/>"), ("csv\\..\\..\\x.csv", b"1")], r"leaves the archive"),
        (
            [("ok.xml", b"<a/>"), (_link_info("link.csv"), b"/etc/passwd")],
            "symbolic link",
        ),
        ([("a.xml", b"<a/>"), ("b.xml", b"<a/>")], "exactly one PLEXOS .xml"),
        ([("a.csv", b"1")], "exactly one PLEXOS .xml"),
        ([("bomb.xml", b"0" * (3 * 2**20))], "compressed more than"),
    ],
)
def test_zip_refusals(tmp_path, members, match):
    with pytest.raises(ImportFormatError, match=match):
        read_plexos([_zip(tmp_path, members)], {})


def test_zip_refuses_oversized_and_too_many(tmp_path, monkeypatch):
    import gridspine.ingest.plexos_import as mod

    path = _zip(tmp_path, [("m.xml", b"<a/>" * 100), ("x.csv", b"1")])
    monkeypatch.setattr(mod, "MAX_MEMBER_BYTES", 50)
    with pytest.raises(ImportFormatError, match="larger than"):
        read_plexos([path], {})
    # a data file is never read in this slice: only the stated size stops it
    big_csv = _zip(tmp_path, [("m.xml", b"<a/>"), ("x.csv", b"1" * 60)], name="c.zip")
    with pytest.raises(ImportFormatError, match=r"'x.csv' is unsafe \(larger than"):
        read_plexos([big_csv], {})
    monkeypatch.setattr(mod, "MAX_MEMBER_BYTES", 2**20)
    monkeypatch.setattr(mod, "MAX_TOTAL_BYTES", 300)
    with pytest.raises(ImportFormatError, match="unpacked"):
        read_plexos([path], {})
    monkeypatch.setattr(mod, "MAX_ZIP_MEMBERS", 1)
    with pytest.raises(ImportFormatError, match="holds 2 files"):
        read_plexos([path], {})


def test_zip_refuses_encrypted_and_garbage(tmp_path):
    path = _zip(tmp_path, [("m.xml", b"<a/>")])
    raw = bytearray(path.read_bytes())
    # set the "encrypted" flag bit in the central directory entry
    cd = raw.rfind(b"PK\x01\x02")
    raw[cd + 8] |= 0x1
    path.write_bytes(bytes(raw))
    with pytest.raises(ImportFormatError, match="encrypted"):
        read_plexos([path], {})
    bad = tmp_path / "x.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(ImportFormatError, match="not a readable zip"):
        read_plexos([bad], {})


@pytest.mark.parametrize(
    "names, match",
    [(["a.xlsx"], "not a.xlsx"), (["a.csv"], "got 0"), (["a.xml", "b.zip"], "got 2")],
)
def test_input_shape_refused(tmp_path, names, match):
    paths = []
    for n in names:
        (tmp_path / n).write_bytes(b"x")
        paths.append(tmp_path / n)
    with pytest.raises(ImportFormatError, match=match):
        read_plexos(paths, {})


def test_hostile_xml_is_an_import_error(tmp_path):
    path = tmp_path / "evil.xml"
    path.write_bytes(b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>')
    with pytest.raises(ImportFormatError, match="evil.xml"):
        read_plexos([path], {})


def test_registry_entry():
    spec = import_result.get("plexos")
    assert spec.extensions == (".xml", ".zip")
    assert spec.is_available()
    assert spec.load_reader() is read_plexos
    assert import_result.for_extension(".zip")[0].format_id == "plexos"


# ------------------------------------------------------------ model choice


def _models():
    return build(
        [
            ("Model", "B"),
            ("Model", "A"),
            ("Scenario", "On"),
            ("Scenario", "Off"),
            ("Node", "N"),
            ("Generator", "G"),
        ],
        [
            ("Node", "N", "Load", 5, "MW", {"tags": [Tag("Scenario", "On", None)]}),
            ("Node", "N", "Load", 7, "MW", {"tags": [Tag("Scenario", "Off", None)]}),
        ],
        [(("Model", "B"), ("Scenario", "On"), "Scenarios")],
    )


def test_model_default_is_first_by_name_and_listed():
    conv = convert(_models(), {})
    a = next(a for a in conv.report.assumed if a.parameter == "Model")
    assert a.value == "A" and "Other Models: B" in a.why
    assert ("Node 'N' (Load)", "scenario 'On' not selected") in reasons(conv)


def test_model_option_switches_scenarios_on():
    conv = convert(_models(), {"model": "B"})
    assert not [a for a in conv.report.assumed if a.parameter == "Model"]
    assert sorted(unmapped_reason(conv, "Node 'N'")) == [
        "scenario 'Off' not selected",
        "scenario 'On' is on in the chosen Model; scenario rows are applied in a later slice",
    ]
    with pytest.raises(ImportFormatError, match="has: A, B"):
        convert(_models(), {"model": "C"})


# ------------------------------------------------------------- horizon, Q8


@pytest.mark.parametrize(
    "serial, expected",
    [
        (43831, datetime(2020, 1, 1)),
        (43831.5, datetime(2020, 1, 1, 12)),
        (61, datetime(1900, 3, 1)),
        (59, datetime(1900, 2, 28)),
        (1, datetime(1900, 1, 1)),
    ],
)
def test_excel_dates(serial, expected):
    assert excel_date(serial) == expected


@pytest.mark.parametrize("serial", [60, 0, -3, float("nan"), None])
def test_excel_dates_refused(serial):
    assert excel_date(serial) is None


def _horizon(**attrs):
    return build(
        [("Model", "M"), ("Horizon", "H")],
        memberships=[(("Model", "M"), ("Horizon", "H"), "Horizon")],
        attributes=[("Horizon", "H", k.replace("_", " "), v) for k, v in attrs.items()],
    )


def test_horizon_leap_year_hourly():
    model = _horizon(Chrono_Date_From=43831, Chrono_Step_Count=366)
    conv = convert(model, {})
    n = conv.network
    assert len(n.snapshots) == 8784 and n.snapshots[-1] == datetime(2020, 12, 31, 23)
    assert (n.snapshot_weightings.objective == 1.0).all()
    check_ledger(model, conv)


def test_horizon_two_hour_step_weighs_two():
    model = _horizon(Chrono_Date_From=43831, Chrono_Step_Count=2, Periods_per_Day=12)
    conv = convert(model, {})
    n = conv.network
    assert len(n.snapshots) == 24
    assert n.snapshots[1] - n.snapshots[0] == datetime(2020, 1, 1, 2) - datetime(
        2020, 1, 1
    )
    assert (
        (n.snapshot_weightings.loc[:, ["objective", "stores", "generators"]] == 2.0)
        .all()
        .all()
    )


def test_horizon_without_length_is_one_year_and_assumed():
    conv = convert(_horizon(Date_From=43466), {})
    assert len(conv.network.snapshots) == 8760
    assert any(a.parameter == "snapshots" for a in conv.report.assumed)


def test_horizon_bad_periods_is_unmapped():
    model = _horizon(Date_From=43466, Periods_per_Day=7)
    conv = convert(model, {})
    assert len(conv.network.snapshots) == 1
    assert unmapped_reason(conv, "Horizon 'H'") == [
        "horizon start or periods per day unreadable"
    ]
    check_ledger(model, conv)


# --------------------------------------------------------------- nodes, lines


def test_node_without_voltage_is_assumed_and_reactance_line_unmapped():
    model = build(
        [("Node", "A"), ("Node", "B"), ("Line", "L")],
        [
            ("Node", "A", "Voltage", 220, "kV"),
            ("Line", "L", "Max Flow", 100, "MW"),
            ("Line", "L", "Reactance", 0.01, "pu"),
        ],
        [
            (("Line", "L"), ("Node", "A"), "Node From"),
            (("Line", "L"), ("Node", "B"), "Node To"),
        ],
    )
    conv = convert(model, {})
    assert [a.asset for a in conv.report.assumed if a.parameter == "voltage"] == ["B"]
    assert unmapped_reason(conv, "Line 'L'") == [
        "needs a node voltage to convert reactance"
    ]
    assert conv.network.lines.empty


def _ac(extra_rows=(), extra_objects=()):
    return build(
        [("Node", "A"), ("Node", "B"), ("Line", "L"), *extra_objects],
        [
            ("Node", "A", "Voltage", 220, "kV"),
            ("Node", "B", "Voltage", 220, "kV"),
            ("Line", "L", "Max Flow", 300, "MW"),
            ("Line", "L", "Min Flow", -300, "MW"),
            ("Line", "L", "Reactance", 0.01, "pu"),
            ("Line", "L", "Resistance", 0.002, "pu"),
            *extra_rows,
        ],
        [
            (("Line", "L"), ("Node", "A"), "Node From"),
            (("Line", "L"), ("Node", "B"), "Node To"),
        ],
    )


def test_reactance_line_on_default_base_hand_checked():
    """0.01 pu on 220 kV / 100 MVA: Z = 484 Ω, x = 4.84 Ω, r = 0.968 Ω."""
    model = _ac()
    conv = convert(model, {})
    line = conv.network.lines.loc["L"]
    assert (line.x, line.r, line.s_nom) == (
        pytest.approx(4.84),
        pytest.approx(0.968),
        300,
    )
    assert any(
        a.parameter == "MVA base" and a.value == 100.0 for a in conv.report.assumed
    )
    check_ledger(model, conv)


def test_reactance_line_uses_the_files_base():
    model = _ac([("Transmission", "T", "MVA Base", 50, "MVA")], [("Transmission", "T")])
    conv = convert(model, {})
    assert conv.network.lines.loc["L"].x == pytest.approx(9.68)
    assert not any(a.parameter == "MVA base" for a in conv.report.assumed)


@pytest.mark.parametrize(
    "mn, expected",
    [(-500, -1.0), (-250, -0.5), (100, 0.2), (None, -1.0)],
)
def test_link_min_flow_sign(mn, expected):
    rows = [("Line", "L", "Max Flow", 500, "MW")]
    if mn is not None:
        rows.append(("Line", "L", "Min Flow", mn, "MW"))
    model = build(
        [("Node", "A"), ("Node", "B"), ("Line", "L")],
        rows,
        [
            (("Line", "L"), ("Node", "A"), "Node From"),
            (("Line", "L"), ("Node", "B"), "Node To"),
        ],
    )
    conv = convert(model, {})
    assert conv.network.links.loc["L", "p_min_pu"] == pytest.approx(expected)
    check_ledger(model, conv)


@pytest.mark.parametrize(
    "rows, members, needle",
    [
        ([("Line", "L", "Max Flow", 0, "MW")], None, "no capacity"),
        (
            [("Line", "L", "Max Flow", 100, "MW"), ("Line", "L", "Units", 0, "-")],
            None,
            "0 units",
        ),
        (
            [("Line", "L", "Max Flow", 100, "MW")],
            [
                (("Line", "L"), ("Node", "A"), "Node From"),
                (("Line", "L"), ("Node", "A"), "Node To"),
            ],
            "same node",
        ),
        (
            [("Line", "L", "Max Flow", 100, "MW")],
            [(("Line", "L"), ("Node", "A"), "Node From")],
            "no node to",
        ),
    ],
)
def test_lines_unmapped(rows, members, needle):
    members = members or [
        (("Line", "L"), ("Node", "A"), "Node From"),
        (("Line", "L"), ("Node", "B"), "Node To"),
    ]
    model = build([("Node", "A"), ("Node", "B"), ("Line", "L")], rows, members)
    conv = convert(model, {})
    assert conv.network.links.empty and conv.network.lines.empty
    assert any(needle in r for r in unmapped_reason(conv, "Line 'L'"))
    check_ledger(model, conv)


def test_node_units_zero_kept_out_of_service():
    model = build([("Node", "A")], [("Node", "A", "Units", 0, "-")])
    conv = convert(model, {})
    assert "A" in conv.network.buses.index
    assert any(
        a.asset == "A" and a.parameter == "in service" for a in conv.report.assumed
    )


# ---------------------------------------------------------------- loads, Q3


def test_load_in_equals_mapped_plus_unmapped():
    model = build(
        [("Node", "A"), ("Node", "B"), ("Region", "R")],
        [
            ("Node", "A", "Load", 40, "MW"),
            ("Node", "B", "Load", 60, "MW"),
            ("Region", "R", "Load", 25, "MW"),
            ("Node", "A", "Load Participation Factor", 1, "-"),
            ("Region", "R", "VoLL", 10000, "$/MWh"),
        ],
        [
            (("Node", "A"), ("Region", "R"), "Region"),
            (("Node", "B"), ("Region", "R"), "Region"),
        ],
    )
    conv = convert(model, {})
    total_in = sum(r.value for r in model.rows if r.property == "Load")
    assert conv.load_mw["network"] + conv.load_mw["unmapped"] == total_in == 125
    assert conv.network.loads.p_set.sum() == 100
    assert any("region-level load" in r for r in unmapped_reason(conv, "Region 'R'"))
    # VoLL lands only in the areas sidecar, never on the network or settings
    assert conv.sidecars[AREAS_SIDECAR]["areas"][0]["voll"] == 10000
    assert set(conv.sidecars) == {AREAS_SIDECAR}
    check_ledger(model, conv)


# ----------------------------------------------------------- generators


def test_generator_units_zero_not_built():
    m0 = build(
        [("Node", "A"), ("Generator", "G")],
        [
            ("Generator", "G", "Units", 0, "-"),
            ("Generator", "G", "Max Capacity", 50, "MW"),
        ],
        [(("Generator", "G"), ("Node", "A"), "Nodes")],
    )
    conv = convert(m0, {})
    assert conv.network.generators.empty
    assert any("0 units" in r for r in unmapped_reason(conv, "Generator 'G'"))
    check_ledger(m0, conv)


@pytest.mark.parametrize(
    "default, built, needle",
    [
        (1.0, True, None),
        (0.0, False, "0 units (the file's default Units): not built"),
        (None, False, "unit count unknown"),
        ("absent", False, "unit count unknown"),
    ],
)
def test_missing_units_use_the_files_default(default, built, needle):
    model = build(
        [("Node", "A"), ("Generator", "G")],
        [("Generator", "G", "Max Capacity", 50, "MW")],
        [(("Generator", "G"), ("Node", "A"), "Nodes")],
    )
    if default != "absent":
        model.defaults[(("System", "Generator", "Generators"), "Units")] = default
    conv = convert(model, {})
    assert ("G" in conv.network.generators.index) is built
    if built:
        assert conv.network.generators.loc["G", "p_nom"] == 50
        assert any(
            a.asset == "G" and a.parameter == "units" and a.value == 1
            for a in conv.report.assumed
        )
    else:
        assert any(needle in r for r in unmapped_reason(conv, "Generator 'G'"))
    check_ledger(model, conv)


def test_irl_file_default_units_is_zero():
    assert (
        read_plexos_xml(IRL).defaults[(("System", "Generator", "Generators"), "Units")]
        == 0
    )


def test_generator_p_nom_is_capacity_times_units():
    model = bus_gen([("Generator", "G", "Max Capacity", 50, "MW")])
    model.rows[1].value = 3  # Units
    conv = convert(model, {})
    assert conv.network.generators.loc["G", "p_nom"] == 150


@pytest.mark.parametrize("cap", [0, -5])
def test_generator_nonpositive_capacity_unmapped(cap):
    conv = convert(bus_gen([("Generator", "G", "Max Capacity", cap, "MW")]), {})
    assert conv.network.generators.empty
    assert "Max Capacity must be above 0" in unmapped_reason(conv, "Generator 'G'")


def test_generator_on_two_nodes_unmapped():
    model = bus_gen(
        [("Generator", "G", "Max Capacity", 50, "MW")],
        [("Node", "B")],
        [(("Generator", "G"), ("Node", "B"), "Nodes")],
    )
    conv = convert(model, {})
    assert conv.network.generators.empty
    assert unmapped_reason(conv, "Generator 'G'") == [
        "needs exactly one node (2 node memberships)"
    ]
    check_ledger(model, conv)


def _fuelled(hr, unit="GJ/MWh", extra=(), fuels=("F",)):
    objects = [("Fuel", f) for f in fuels]
    rows = [
        ("Generator", "G", "Max Capacity", 100, "MW"),
        ("Generator", "G", "VO&M Charge", 2, "$/MWh"),
        *[("Fuel", f, "Price", 5, "$/GJ") for f in fuels],
        *extra,
    ]
    if hr is not None:
        rows.append(("Generator", "G", "Heat Rate", hr, unit))
    members = [(("Generator", "G"), ("Fuel", f), "Fuels") for f in fuels]
    return bus_gen(rows, objects, members)


def test_marginal_cost_and_assumed_row():
    conv = convert(_fuelled(10), {})
    assert conv.network.generators.loc["G", "marginal_cost"] == pytest.approx(52)
    a = next(a for a in conv.report.assumed if a.parameter == "marginal cost")
    assert a.value == 52 and "heat rate 10 GJ/MWh" in a.why
    assert conv.sidecars[FUEL_PRICES_SIDECAR]["fuels"] == [
        {"fuel": "F", "price": 5, "unit": "$/GJ", "source": "static Price"}
    ]


def test_heat_rate_btu_per_kwh_converted():
    conv = convert(_fuelled(9478.17, "BTU/kWh"), {})
    assert conv.network.generators.loc["G", "marginal_cost"] == pytest.approx(
        10 * 5 + 2, rel=1e-5
    )


@pytest.mark.parametrize(
    "model, needle",
    [
        (_fuelled(10, "kJ/kWh"), "unit 'kJ/kWh' for 'Heat Rate' is not converted"),
        (
            _fuelled(
                None,
                extra=[
                    ("Generator", "G", "Heat Rate Base", 3, "GJ/h"),
                    ("Generator", "G", "Heat Rate Incr", 8, "GJ/MWh"),
                ],
            ),
            "heat-rate curve",
        ),
        (_fuelled(10, fuels=("F", "H")), "2 fuels: multi-fuel"),
    ],
)
def test_heat_rate_forms_never_guess(model, needle):
    conv = convert(model, {})
    assert conv.network.generators.loc["G", "marginal_cost"] == 2  # VO&M only
    assert any(needle in r for r in unmapped_reason(conv, "Generator 'G'"))
    check_ledger(model, conv)


def test_unknown_capacity_unit_unmapped():
    conv = convert(bus_gen([("Generator", "G", "Max Capacity", 0.1, "GW")]), {})
    assert conv.network.generators.empty
    assert (
        "unit 'GW' for 'Max Capacity' is not converted (expects MW)"
        in unmapped_reason(conv, "Generator 'G'")
    )


@pytest.mark.parametrize(
    "msf, msl, expected, disagree",
    [
        (40, None, 0.4, False),
        (None, 30, 0.3, False),
        (40, 40, 0.4, False),
        (20, 50, 0.5, True),
    ],
)
def test_min_stable_larger_wins_as_suggestion(msf, msl, expected, disagree):
    rows = [("Generator", "G", "Max Capacity", 100, "MW")]
    if msf is not None:
        rows.append(("Generator", "G", "Min Stable Factor", msf, "%"))
    if msl is not None:
        rows.append(("Generator", "G", "Min Stable Level", msl, "MW"))
    conv = convert(bus_gen(rows), {})
    (s,) = conv.report.suggestions
    assert s.proposed == pytest.approx(expected) and s.target == "G"
    assert conv.network.generators.loc["G", "p_min_pu"] == 0
    assert (
        any(a.parameter == "minimum stable level" for a in conv.report.assumed)
        is disagree
    )


@pytest.mark.parametrize("value", [0, 120, -5])
def test_percent_out_of_range_unmapped(value):
    rows = [
        ("Generator", "G", "Max Capacity", 100, "MW"),
        ("Generator", "G", "Rating Factor", value, "%"),
    ]
    conv = convert(bus_gen(rows), {})
    assert conv.network.generators.loc["G", "p_max_pu"] == 1.0
    assert "Rating Factor outside (0, 100] %" in unmapped_reason(conv, "Generator 'G'")


def test_ramp_dates_outages():
    rows = [
        ("Generator", "G", "Max Capacity", 100, "MW"),
        ("Generator", "G", "Max Ramp Up", 0.5, "MW/min"),
        ("Generator", "G", "Commission Date", 36526, "-"),  # 2000-01-01
        ("Generator", "G", "Retirement Date", 47484, "-"),  # 2030-01-01
        ("Generator", "G", "Forced Outage Rate", 6, "%"),
        ("Generator", "G", "Mean Time to Repair", 48, "h"),
        ("Generator", "G", "Maintenance Rate", 5, "%"),
        ("Generator", "G", "Start Cost", 1000, "$"),
    ]
    model = bus_gen(rows)
    conv = convert(model, {})
    g = conv.network.generators.loc["G"]
    assert g.ramp_limit_up == pytest.approx(0.3)
    assert (g.build_year, g.lifetime) == (2000, 30)
    assert (g.outage_rate_value, g.outage_rate_basis, g.mttr_hours) == (
        pytest.approx(0.06),
        "FOR",
        48,
    )
    assert g.start_up_cost == 1000
    assert "planned maintenance has no network field yet" in unmapped_reason(
        conv, "Generator 'G'"
    )
    check_ledger(model, conv)


@pytest.mark.parametrize(
    "dates, needle",
    [
        ([("Retirement Date", 47484)], "without a Commission Date"),
        (
            [("Commission Date", 47484), ("Retirement Date", 36526)],
            "not after Commission",
        ),
        (
            [("Commission Date", 36526), ("Retirement Date", 36800)],
            "not after Commission",
        ),
        ([("Commission Date", 60)], "Commission Date unreadable"),
    ],
)
def test_lifetime_needs_both_dates_in_order(dates, needle):
    rows = [
        ("Generator", "G", "Max Capacity", 100, "MW"),
        *[("Generator", "G", p, v, "-") for p, v in dates],
    ]
    conv = convert(bus_gen(rows), {})
    assert math.isinf(conv.network.generators.loc["G", "lifetime"])
    assert any(needle in r for r in unmapped_reason(conv, "Generator 'G'"))


def test_carrier_from_category_without_fuel():
    model = build(
        [("Node", "A"), ("Generator", "W", "Wind")],
        [
            ("Generator", "W", "Units", 1, "-"),
            ("Generator", "W", "Max Capacity", 10, "MW"),
        ],
        [(("Generator", "W"), ("Node", "A"), "Nodes")],
    )
    assert convert(model, {}).network.generators.loc["W", "carrier"] == "Wind"


# ---------------------------------------------------------------- batteries


def _battery(*extra):
    return build(
        [("Node", "A"), ("Battery", "B")],
        [
            ("Battery", "B", "Units", 2, "-"),
            ("Battery", "B", "Max Power", 10, "MW"),
            ("Battery", "B", "Capacity", 40, "MWh"),
            *extra,
        ],
        [(("Battery", "B"), ("Node", "A"), "Node")],
    )


def test_battery_mapped():
    model = _battery(
        ("Battery", "B", "Charge Efficiency", 90, "%"),
        ("Battery", "B", "Discharge Efficiency", 95, "%"),
        ("Battery", "B", "Initial SoC", 50, "%"),
    )
    conv = convert(model, {})
    s = conv.network.storage_units.loc["B"]
    assert (s.p_nom, s.max_hours) == (20, 4)
    assert (s.efficiency_store, s.efficiency_dispatch) == (
        pytest.approx(0.9),
        pytest.approx(0.95),
    )
    assert s.state_of_charge_initial == pytest.approx(40)
    check_ledger(model, conv)


@pytest.mark.parametrize("eff", [0, 101, float("inf")])
def test_battery_efficiency_out_of_range(eff):
    model = _battery(("Battery", "B", "Charge Efficiency", eff, "%"))
    conv = convert(model, {})
    assert conv.network.storage_units.loc["B", "efficiency_store"] == 1.0
    assert "Charge Efficiency outside (0, 100] %" in unmapped_reason(
        conv, "Battery 'B'"
    )


# ------------------------------------------------------------- names, Q7


def test_name_clash_gets_a_fixed_suffix():
    conv = _Converter(build([]), {}, import_result.ImportReport("plexos", ["x"]))
    assert conv.unique("StorageUnit", "X", "Battery") == "X"
    assert conv.unique("StorageUnit", "X", "Hydro") == "X (Hydro)"
    assert conv.unique("StorageUnit", "X", "Hydro") == "X (Hydro 3)"
    assert [a.value for a in conv.r.assumed] == ["X (Hydro)", "X (Hydro 3)"]
    assert all("'X' is already used" in a.why for a in conv.r.assumed)


def test_row_with_an_on_and_an_off_scenario_is_on():
    model = _models()
    model.rows[1].tags = [Tag("Scenario", "Off", None), Tag("Scenario", "On", None)]
    conv = convert(model, {"model": "B"})
    assert all(
        "is on in the chosen Model" in r for r in unmapped_reason(conv, "Node 'N'")
    )


def test_load_tally_counts_every_readable_load_row():
    model = build(
        [("Node", "A"), ("Scenario", "S")],
        [
            ("Node", "A", "Load", 10, "MW"),
            ("Node", "A", "Load", 7, "GW", {"band": 2}),
            ("Node", "A", "Load", 3, "MW", {"tags": [Tag("Scenario", "S", None)]}),
        ],
    )
    conv = convert(model, {})
    assert conv.load_mw["network"] == 10 and conv.load_mw["unmapped"] == 10


def test_chrono_step_type_is_not_booked_as_read():
    model = _horizon(Chrono_Date_From=43831, Chrono_Step_Count=1, Chrono_Step_Type=2)
    conv = convert(model, {})
    assert (
        conv.ledger.dest[("t_attribute_data", "Horizon|H|Chrono Step Type")]
        == "unmapped"
    )
    assert any("Chrono Step Type is not read" in a.why for a in conv.report.assumed)


@pytest.mark.parametrize("default, built", [(1.0, True), (0.0, False), (None, False)])
def test_battery_units_use_the_files_default(default, built):
    model = _battery()
    model.rows.pop(0)  # no Units row
    if default is not None:
        model.defaults[(("System", "Battery", "Batteries"), "Units")] = default
    conv = convert(model, {})
    assert ("B" in conv.network.storage_units.index) is built
    check_ledger(model, conv)


def test_battery_efficiency_default_comes_from_the_file():
    model = _battery()
    model.defaults[(("System", "Battery", "Batteries"), "Charge Efficiency")] = 70.0
    conv = convert(model, {})
    s = conv.network.storage_units.loc["B"]
    assert (s.efficiency_store, s.efficiency_dispatch) == (pytest.approx(0.7), 1.0)
    params = {a.parameter for a in conv.report.assumed if a.asset == "B"}
    assert params == {"charge efficiency", "discharge efficiency"}
