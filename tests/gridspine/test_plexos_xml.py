"""
PLEXOS input XML reader (``ingest/plexos_xml.py``), PLEXOS import slice 1.

Fixtures (licences in ``fixtures/plexos/README.md``):

- ``world2015_irl.xml``: node EU-IRL cut from PLEXOS-World 2015 (CC0), a real
  file saved by PLEXOS 8.300. Its expected counts come from the independent
  cutter ``make_fixture.py`` and from a raw tag count, never from the reader.
- ``edge_cases.xml``: hand-written. Each broken row in it is listed below by
  hand, with the guard that must catch it.
"""

import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from lxml import etree

from gridspine.ingest.plexos_xml import REQUIRED_TABLES, PlexosRefused, read_plexos_xml

HERE = Path(__file__).parent / "fixtures" / "plexos"
WORLD = HERE / "world2015_irl.xml"
EDGE = HERE / "edge_cases.xml"
NS = "{http://tempuri.org/MasterDataSet.xsd}"

# printed by make_fixture.py when it cut the fixture (2026-11-17)
WORLD_IRL_COUNTS = {
    "t_config": 18,
    "t_action": 7,
    "t_class": 82,
    "t_collection": 23,
    "t_unit": 85,
    "t_category": 21,
    "t_property": 27,
    "t_object": 125,
    "t_membership": 386,
    "t_data": 534,
    "t_tag": 256,
    "t_text": 8,
    "t_memo_data": 110,
    "t_attribute_data": 179,
    "t_attribute": 20,
}
WORLD_IRL_CLASSES = {
    "Battery": 5,
    "Data File": 3,
    "Emission": 1,
    "Fuel": 7,
    "Generator": 63,
    "Horizon": 4,
    "Line": 2,
    "Model": 11,
    "Node": 3,
    "Region": 1,
    "Scenario": 20,
    "System": 1,
    "Variable": 1,
    "Zone": 3,
}

# Every broken row of edge_cases.xml, by hand: (table, row id) -> reason fragment.
EDGE_BROKEN = {
    ("t_config", "Units"): "a second 'Units' setting",
    ("t_config", "row 4"): "setting without a name",
    ("t_unit", "8"): "without value",
    ("t_action", "7"): "without action_symbol",
    ("t_category", "2"): "without name",
    ("t_attribute", "2"): "without a name",  # no name
    ("t_attribute", "3"): "of an unknown class",  # class 99
    ("t_collection", "14"): "unknown parent or child class",  # parent class 99
    ("t_collection", "15"): "unknown parent or child class",  # child class 99
    ("t_collection", "16"): "or no name",
    ("t_property", "12"): "unknown collection",
    ("t_property", "13"): "without a name",
    ("t_object", "4"): "duplicate object_id",
    ("t_object", "13"): "unknown class id 99",
    ("t_object", "15"): "a second Node named 'X'",
    ("t_membership", "1"): "duplicate membership_id",
    ("t_membership", "18"): "parent or child object does not exist",  # child 999
    ("t_membership", "23"): "parent or child object does not exist",  # parent 999
    (
        "t_membership",
        "24",
    ): "objects are Generator/Fuel but the collection is Generator/Node",  # child only
    ("t_membership", "19"): "unknown collection id 99",
    (
        "t_membership",
        "20",
    ): "objects are Fuel/Node but the collection is Generator/Node",
    ("t_data", "1"): "duplicate data_id",
    ("t_data", "18"): "membership 999 does not exist",
    ("t_data", "19"): "property 999 does not exist",
    ("t_data", "20"): "belongs to another collection",
    ("t_data", "22"): "membership 18 does not exist",  # a row on a broken membership
    ("t_band", "999"): "data row 999 does not exist",
    ("t_band", "2"): "not a whole number",
    ("t_band", "3 (row 3)"): "a second t_band entry",
    ("t_band", "1"): "band 0 is below 1",
    ("t_band", "6"): "None is not a whole number",  # no band_id at all
    ("t_date_from", "9 (row 1)"): "a second t_date_from entry",
    ("t_memo_data", "9 (row 1)"): "a second t_memo_data entry",
    ("t_memo_data", "row 2"): "data row None does not exist",  # no data_id
    ("t_text", "12"): "unknown class id 99",
    ("t_tag", "5 (row 5)"): "tag object 999 does not exist",
    ("t_tag", "999"): "data row 999 does not exist",
    ("t_tag", "6 (row 7)"): "unknown action id 77",
    ("t_attribute_data", "999/1 (row 1)"): "object or attribute does not exist",
    ("t_attribute_data", "3/1 (row 2)"): "attribute of Node set on a Generator",
    ("t_attribute_data", "2/999 (row 3)"): "object or attribute does not exist",
}


@pytest.fixture(scope="module")
def edge():
    return read_plexos_xml(str(EDGE))


@pytest.fixture(scope="module")
def world():
    return read_plexos_xml(str(WORLD))


def raw_counts(path):
    root = etree.parse(
        str(path), etree.XMLParser(resolve_entities=False, no_network=True)
    ).getroot()
    return Counter(el.tag.replace(NS, "") for el in root if isinstance(el.tag, str))


def row(model, data_id):
    (r,) = [r for r in model.rows if r.data_id == data_id]
    return r


def xml(body: str, header: str = "") -> bytes:
    return (
        header + f'<MasterDataSet xmlns="{NS[1:-1]}">{body}</MasterDataSet>'
    ).encode()


MINIMAL = "".join(f"<{t}><x>1</x></{t}>" for t in REQUIRED_TABLES)


# P1: hostile XML is refused, never expanded


def test_billion_laughs_is_refused_as_a_dtd(tmp_path):
    lol = (
        '<!DOCTYPE MasterDataSet [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
        '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;"><!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">]>'
    )
    p = tmp_path / "lol.xml"
    p.write_bytes(
        xml("<t_class><class_id>1</class_id><name>&d;</name></t_class>", header=lol)
    )
    with pytest.raises(PlexosRefused) as exc:
        read_plexos_xml(str(p))
    assert exc.value.kind == "dtd"


def test_external_entity_is_refused_as_a_dtd(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    ext = f'<!DOCTYPE MasterDataSet [<!ENTITY s SYSTEM "file://{secret}">]>'
    p = tmp_path / "xxe.xml"
    p.write_bytes(
        xml("<t_class><class_id>1</class_id><name>&s;</name></t_class>", header=ext)
    )
    with pytest.raises(PlexosRefused) as exc:
        read_plexos_xml(str(p))
    assert exc.value.kind == "dtd"


def test_a_bare_doctype_is_refused(tmp_path):
    p = tmp_path / "bare.xml"
    p.write_bytes(xml(MINIMAL, header="<!DOCTYPE MasterDataSet>"))
    with pytest.raises(PlexosRefused) as exc:
        read_plexos_xml(str(p))
    assert exc.value.kind == "dtd"


def test_external_dtd_reference_is_refused(tmp_path):
    p = tmp_path / "sys.xml"
    p.write_bytes(
        xml(
            MINIMAL,
            header='<!DOCTYPE MasterDataSet SYSTEM "http://example.invalid/x.dtd">',
        )
    )
    with pytest.raises(PlexosRefused) as exc:
        read_plexos_xml(str(p))
    assert exc.value.kind == "dtd"


@pytest.mark.parametrize(
    "content, kind",
    [
        (b"", "not_xml"),
        (b"<MasterDataSet><t_class>", "not_xml"),
        (b"PK\x03\x04 zip bytes", "not_xml"),
        (b"<Workbook><t_class/></Workbook>", "not_plexos"),
    ],
)
def test_a_file_that_is_not_a_plexos_xml_is_refused_by_kind(tmp_path, content, kind):
    p = tmp_path / "f.xml"
    p.write_bytes(content)
    with pytest.raises(PlexosRefused) as exc:
        read_plexos_xml(str(p))
    assert exc.value.kind == kind


# P3: a missing required table is a typed refusal naming the table


@pytest.mark.parametrize("missing", REQUIRED_TABLES)
def test_a_missing_required_table_is_refused_by_name(tmp_path, missing):
    body = "".join(f"<{t}><x>1</x></{t}>" for t in REQUIRED_TABLES if t != missing)
    p = tmp_path / "f.xml"
    p.write_bytes(xml(body))
    with pytest.raises(PlexosRefused, match=missing) as exc:
        read_plexos_xml(str(p))
    assert exc.value.kind == "missing_table"


# P2: names resolve within their class


def test_the_same_name_in_two_classes_is_two_objects(edge):
    node, gen = edge.objects[("Node", "X")], edge.objects[("Generator", "X")]
    assert (node.object_id, gen.object_id) == ("2", "3")
    assert row(edge, "1").child == ("Generator", "X")
    assert row(edge, "8").child == ("Node", "X")
    links = {(m.collection, m.parent, m.child) for m in edge.memberships}
    assert (("Generator", "Node", "Nodes"), ("Generator", "X"), ("Node", "X")) in links


def test_collections_are_keyed_by_parent_child_and_name(edge):
    line_ends = {m.collection for m in edge.memberships if m.parent == ("Line", "L1")}
    assert line_ends == {("Line", "Node", "Node From"), ("Line", "Node", "Node To")}


# P3 + P4: every row is resolved or broken, and the broken ones say why


def test_each_broken_edge_row_lands_in_the_broken_bucket_with_its_reason(edge):
    got = {(b.table, b.row_id): b.reason for b in edge.broken}
    assert set(got) == set(EDGE_BROKEN)
    for key, fragment in EDGE_BROKEN.items():
        assert fragment in got[key], (key, got[key])


def test_edge_rows_in_equal_resolved_plus_broken_against_a_raw_count(edge):
    raw = raw_counts(EDGE)
    assert {t: a["in"] for t, a in edge.accounting.items()} == dict(raw)
    broken = Counter(t for t, _ in EDGE_BROKEN)
    for table, a in edge.accounting.items():
        assert a["broken"] == broken.get(table, 0), table
        assert a["in"] == a["resolved"] + a["broken"] + a["not_read"], table
    assert edge.accounting["t_report"] == {
        "in": 1,
        "resolved": 0,
        "broken": 0,
        "not_read": 1,
    }
    assert any("t_report (1 rows)" in w for w in edge.warnings)


def test_objects_of_lists_one_class(edge):
    assert sorted(o.name for o in edge.objects_of("Generator")) == ["G2", "G3", "X"]
    assert [o.name for o in edge.objects_of("Reserve")] == ["Spin Up"]
    assert [o.name for o in edge.objects_of("Transformer")] == ["T1"]


def test_a_dangling_unit_or_category_is_named_in_warnings(edge):
    assert edge.objects[("Generator", "G2")].category == "Wind"
    assert edge.objects[("Generator", "G3")].category is None
    # exactly these: no warning for G2's known category, for objects without
    # one, or for property "Notes", which states no unit at all
    assert edge.warnings == [
        "Property 'VO&M Charge' names unit id 55, which the file does not define; its unit is unknown.",
        "Generator 'G3' names category id 77, which the file does not define; it has no category.",
        "Table t_report (1 rows) holds settings this reader does not interpret.",
    ]
    assert edge.config["Units"] == "Metric"  # the first of two is kept


def test_the_first_of_two_duplicate_ids_is_kept(edge):
    assert ("Generator", "G2") in edge.objects and (
        "Node",
        "Duplicate id",
    ) not in edge.objects
    assert row(edge, "1").raw_value == "100"


def test_world_rows_all_resolve_and_match_the_cutter_counts(world):
    assert dict(raw_counts(WORLD)) == WORLD_IRL_COUNTS
    assert world.broken == []
    for table, n in WORLD_IRL_COUNTS.items():
        a = world.accounting[table]
        assert (a["in"], a["resolved"], a["not_read"]) == (n, n, 0), table
    assert Counter(o.cls for o in world.objects.values()) == WORLD_IRL_CLASSES


def test_world_hand_checked_generator(world):
    gen = ("Generator", "IRL_Bio_WestOffalyPow13313")
    props = {r.property: r for r in world.rows if r.child == gen}
    assert (props["Max Capacity"].value, props["Max Capacity"].unit) == (137.0, "MW")
    assert props["Commission Date"].scenarios == ["Include LT Commission Date"]
    fuels = [
        m.child
        for m in world.memberships
        if m.parent == gen and m.collection[2] == "Fuels"
    ]
    assert len(fuels) == 1 and fuels[0][0] == "Fuel"


# P5: unreadable values stay unreadable


@pytest.mark.parametrize("data_id, raw", [("14", "abc"), ("15", "NaN"), ("16", "INF")])
def test_an_unreadable_value_is_kept_with_a_reason_never_zero(edge, data_id, raw):
    r = row(edge, data_id)
    assert r.value is None and r.raw_value == raw
    assert r.unreadable and raw in r.unreadable


@pytest.mark.parametrize("data_id", ["23", "24"])  # whitespace only; no <value> at all
def test_an_empty_value_is_unreadable_as_no_value(edge, data_id):
    r = row(edge, data_id)
    assert (r.value, r.unreadable) == (None, "no value")


def test_a_readable_value_has_no_reason(edge):
    r = row(edge, "2")
    assert (r.value, r.unreadable) == (9.5, None)


# P6: a data row keeps every qualifier


def test_a_row_keeps_scenario_data_file_and_variable_tags_together(edge):
    tags = {(t.cls, t.name, t.action) for t in row(edge, "5").tags}
    assert tags == {
        ("Data File", "Wind Profile", None),
        ("Scenario", "Low Wind", None),
        ("Variable", "Wind Scaler", "×"),
    }


def test_a_row_keeps_two_scenario_tags(edge):
    assert sorted(row(edge, "7").scenarios) == ["High Gas", "Low Wind"]
    assert row(edge, "6").scenarios == []  # its only tag is broken (action 77)


def test_band_dates_text_and_memo_are_kept(edge):
    assert (row(edge, "3").band, row(edge, "2").band, row(edge, "4").band) == (
        2,
        1,
        1,
    )  # 4: band 1 stated
    r9 = row(edge, "9")
    assert (r9.date_from, r9.date_to, r9.memo) == (
        "2030-01-01T00:00:00",
        "2030-12-31T00:00:00",
        "from TSO plan",
    )
    assert row(edge, "12").texts == [("Data File", "csv_files\\wind.csv")]
    assert row(edge, "13").texts == [("Data File", "")]  # a text row without a value


def test_attributes_resolve_to_their_object(edge):
    assert [(a.object, a.name, a.value, a.unit) for a in edge.attributes] == [
        (("Node", "X"), "Latitude", 53.3, "°")
    ]


# P7: units come from the file


def test_units_are_read_from_the_file(edge, tmp_path):
    assert (row(edge, "1").property, row(edge, "1").unit) == ("Max Capacity", "MW")
    assert (row(edge, "2").property, row(edge, "2").unit) == ("Heat Rate", "GJ/MWh")
    p = tmp_path / "btu.xml"
    p.write_bytes(
        EDGE.read_bytes().replace(b"<value>GJ/MWh</value>", b"<value>MMBtu/MWh</value>")
    )
    assert row(read_plexos_xml(str(p)), "2").unit == "MMBtu/MWh"


def test_a_file_without_a_unit_table_reads_with_unknown_units(tmp_path):
    body = etree.parse(str(EDGE)).getroot()
    for el in body.findall(f"{NS}t_unit"):
        body.remove(el)
    p = tmp_path / "nounit.xml"
    p.write_bytes(etree.tostring(body))
    model = read_plexos_xml(str(p))
    assert row(model, "1").unit is None
    assert (
        model.warnings[0] == "The file has no t_unit table; property units are unknown."
    )
    assert not any(
        "names unit id" in w for w in model.warnings
    )  # one warning, not one per property
    assert not any("t_unit" in w for w in read_plexos_xml(str(EDGE)).warnings)


# P8: version


def _with_version(tmp_path, version):
    body = EDGE.read_bytes()
    old = b"<t_config><element>Version</element><value>9.200</value></t_config>"
    assert old in body
    new = b"" if version is None else old.replace(b"9.200", version.encode())
    p = tmp_path / "v.xml"
    p.write_bytes(body.replace(old, new))
    return read_plexos_xml(str(p))


@pytest.mark.parametrize(
    "version, fragment",
    [
        (None, "does not state"),
        ("6.400", "outside"),
        ("11.000", "outside"),
        ("abc", "not a number"),
        ("inf", "not a number"),
        ("1e400", "not a number"),
        ("nan", "not a number"),
    ],
)
def test_an_unsupported_or_missing_version_warns_but_reads(tmp_path, version, fragment):
    model = _with_version(tmp_path, version)
    assert any(fragment in w for w in model.warnings)
    assert len(model.rows) == 18


@pytest.mark.parametrize("version", ["7.000", "9.200", "10.900"])
def test_a_supported_version_does_not_warn(tmp_path, version):
    assert not any(
        "version" in w.lower() for w in _with_version(tmp_path, version).warnings
    )


def test_world_reports_its_version(world):
    assert (world.version, world.saved_with) == (
        "8.300",
        "PLEXOS 8.300 R01 x64 Edition",
    )


# P9: engine cage A


def test_the_reader_imports_without_pypsa():
    code = "import sys; sys.modules['pypsa'] = None; import gridspine.ingest.plexos_xml; print('ok')"
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[2],
    )
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
