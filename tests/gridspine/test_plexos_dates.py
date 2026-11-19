"""
PLEXOS importer slice 3b: dated and timesliced rows.

GK A's plan GO (P4, D1-D5, L1) is in ``.scratch/project-io/plexos-mapping.md``.
The horizon is 1-2 January 2015, hourly, unless a test says otherwise.
"""

from __future__ import annotations

import pytest

pypsa = pytest.importorskip("pypsa")

from gridspine.ingest.plexos_import import _windowed  # noqa: E402
from gridspine.ingest.plexos_xml import Tag, read_plexos_xml  # noqa: E402
from tests.gridspine.test_plexos_import import IRL, reasons, unmapped_reason  # noqa: E402
from tests.gridspine.test_plexos_timeseries import filed, model, run  # noqa: E402

MIDNIGHT = "a midnight Date To covers that whole day"


def dated(date_from=None, date_to=None, **kw):
    return {"date_from": date_from, "date_to": date_to, **kw}


def sliced(name, **kw):
    return {"tags": [Tag("Timeslice", name, None)], **kw}


def include(name, pattern, **kw):
    return (
        "Timeslice",
        name,
        "Include",
        -1,
        "-",
        {"texts": [("Timeslice", pattern)], **kw},
    )


def three_hourly(objects=(), rows=(), **kw):
    """Model with 8 periods a day (3 h step) over two days."""
    attrs = [("Horizon", "H", "Periods per Day", 8.0)]
    return model(objects, rows, attrs=attrs, files=(), **kw)


def p_set(conv, name="A"):
    return conv.network.loads_t.p_set[name]


def assumed(conv):
    return [(a.asset, a.parameter, a.value) for a in conv.report.assumed]


# ------------------------------------------------------------- dated rows


def test_date_from_starts_a_step_at_its_snapshot():
    """GK A D1: Date From is inclusive at its timestamp."""
    conv = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00")),
            ],
            files=(),
        )
    )
    p = p_set(conv)
    assert p["2015-01-01 23:00"] == 10 and p["2015-01-02 00:00"] == 20
    assert p.sum() == 24 * 10 + 24 * 20
    assert ("dated rows", "Date To", MIDNIGHT) in assumed(conv)
    assert set(conv.ledger.dest.values()) <= {"network", "sidecar", "unmapped"}
    assert conv.ledger.dest[("t_data", "2")] == "network"  # undated row
    assert conv.ledger.dest[("t_data", "3")] == "network"  # dated row


def test_midnight_date_to_covers_that_whole_day():
    """GK A D2: a midnight Date To includes its day's last snapshot, not the next."""
    conv = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated(date_to="2015-01-01T00:00:00")),
            ],
            files=(),
        )
    )
    p = p_set(conv)
    assert p["2015-01-01 23:00"] == 20 and p["2015-01-02 00:00"] == 10
    assert ("dated rows", "Date To", MIDNIGHT) in assumed(conv)


def test_date_to_with_a_time_includes_the_snapshot_starting_at_it():
    """GK A D2: up to and including the snapshot that starts at it."""
    conv = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated(date_to="2015-01-01T05:00:00")),
            ],
            files=(),
        )
    )
    p = p_set(conv)
    assert p["2015-01-01 05:00"] == 20 and p["2015-01-01 06:00"] == 10


@pytest.mark.parametrize(
    "when, reason",
    [
        (
            dated("2015-01-01T04:00:00"),
            "Date From 2015-01-01 04:00:00 falls inside snapshot 2015-01-01 03:00:00; "
            "not rounded",
        ),
        (
            dated(date_to="2015-01-01T04:00:00"),
            "Date To 2015-01-01 04:00:00 falls inside snapshot 2015-01-01 03:00:00; "
            "not rounded",
        ),
        (dated("soon"), "Date From 'soon' not read"),
        (dated("01/02/2015"), "Date From '01/02/2015' not read"),
        (
            dated("2015-01-02T00:00:00+01:00"),
            "Date From '2015-01-02T00:00:00+01:00' has a UTC offset; the snapshots "
            "have none",
        ),
        (
            dated(date_to="2015-01-02T00:00:00Z"),
            "Date To '2015-01-02T00:00:00Z' has a UTC offset; the snapshots have none",
        ),
        (dated(date_to="soon"), "Date To 'soon' not read"),
    ],
)
def test_date_inside_a_snapshot_is_refused(when, reason):
    """GK A D1: at a 3 h step, a date inside a snapshot leaves the group unmapped."""
    conv = run(
        three_hourly(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", when),
            ]
        )
    )
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == [reason]


def test_three_hour_step_on_boundaries():
    conv = run(
        three_hourly(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-01T03:00:00")),
            ]
        )
    )
    assert p_set(conv).tolist()[:3] == [10, 20, 20]


def test_overlap_with_equal_values_is_fine_different_values_fail_closed():
    """GK A D3."""
    same = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-01T12:00:00")),
                ("Node", "A", "Load", 20, "MW", dated(date_to="2015-01-02T00:00:00")),
            ],
            files=(),
        )
    )
    assert same.network.loads.p_set.to_dict() == {"A": 20.0}  # one value: static
    differ = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-01T12:00:00")),
                ("Node", "A", "Load", 30, "MW", dated(date_to="2015-01-01T12:00:00")),
            ],
            files=(),
        )
    )
    assert differ.network.loads.empty
    assert set(unmapped_reason(differ, "Node 'A'")) == {
        "rows of 'Load' overlap at 2015-01-01 12:00:00 with different values; none used"
    }


def test_dated_and_timesliced_rows_overlapping_with_different_values_fail_closed():
    """GK A D3: dated × timesliced."""
    conv = run(
        model(
            objects=[("Timeslice", "Night")],
            rows=[
                include("Night", "H1-6"),
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00")),
                ("Node", "A", "Load", 30, "MW", sliced("Night")),
            ],
            files=(),
        )
    )
    assert conv.network.loads.empty
    assert (
        "rows of 'Load' overlap at 2015-01-02 00:00:00 with different values; none used"
        in (unmapped_reason(conv, "Node 'A'"))
    )
    assert unmapped_reason(conv, "Timeslice 'Night'") == ["no imported row uses it"]


def test_band_two_rows_are_never_merged_into_band_one():
    """GK A D4."""
    conv = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00", band=2)),
            ],
            files=(),
        )
    )
    assert conv.network.loads.p_set.to_dict() == {"A": 10.0}
    assert unmapped_reason(conv, "Node 'A'") == ["band 2: only band 1 is used"]


def test_gap_without_an_undated_row_is_unmapped():
    conv = run(
        model(
            rows=[("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00"))],
            files=(),
        )
    )
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == [
        "no value for 24 of 48 snapshots, first 2015-01-01 00:00:00"
    ]


# ------------------------------------------------------------- static properties


def voltage(*rows):
    return run(model(rows=[(("Node", "A", "Voltage", *r)) for r in rows], files=()))


def test_static_property_constant_over_the_horizon():
    conv = voltage((110, "kV", dated("2014-01-01T00:00:00")))
    assert conv.network.buses.v_nom.to_dict() == {"A": 110.0}
    assert ("Node 'A'", "Voltage", 110.0) in assumed(conv)


def test_static_property_dated_rows_all_outside_the_horizon_use_the_undated_row():
    conv = voltage((110, "kV", dated("2030-01-01T00:00:00")))
    # the model() helper's own undated Voltage row is 220 kV
    assert conv.network.buses.v_nom.to_dict() == {"A": 220.0}
    assert unmapped_reason(conv, "Node 'A'") == [
        "dated or timesliced row covers no snapshot"
    ]
    assert (
        "Node 'A': the dated or timesliced rows of 'Voltage' all fall outside the "
        "horizon; the undated value is used"
    ) in conv.report.warnings


def test_static_property_changing_within_the_horizon_is_unmapped():
    conv = voltage((110, "kV", dated("2015-01-02T00:00:00")))
    assert set(unmapped_reason(conv, "Node 'A'")) == {
        "'Voltage' changes within the horizon (at 2015-01-02 00:00:00); it is static "
        "in PyPSA"
    }


def test_constant_check_is_exact():
    """GK A D5: no tolerance."""
    conv = voltage((220.000001, "kV", dated("2015-01-02T00:00:00")))
    # neither 220 nor 220.000001 is used: the node keeps no voltage (2a rule)
    assert ("A", "voltage", None) in assumed(conv)
    assert not [a for a in assumed(conv) if a[:2] == ("Node 'A'", "Voltage")]
    assert set(unmapped_reason(conv, "Node 'A'")) == {
        "'Voltage' changes within the horizon (at 2015-01-02 00:00:00); it is static "
        "in PyPSA"
    }


def test_static_all_outside_without_an_undated_row_is_a_gap():
    """GK A D5: not the warning path."""
    conv = run(
        model(
            objects=[("Node", "B")],
            rows=[("Node", "B", "Load", 20, "MW", dated("2030-01-01T00:00:00"))],
            files=(),
        )
    )
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'B'") == [
        "no value for 48 of 48 snapshots, first 2015-01-01 00:00:00"
    ]
    assert not [w for w in conv.report.warnings if "outside the horizon" in w]


# ------------------------------------------------------------- timeslices


def test_timesliced_row_from_its_include_pattern():
    conv = run(
        model(
            objects=[("Timeslice", "Day")],
            rows=[
                include("Day", "H9-20"),
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", sliced("Day")),
            ],
            files=(),
        )
    )
    p = p_set(conv)
    assert p["2015-01-01 07:00"] == 10 and p["2015-01-01 08:00"] == 20
    assert p["2015-01-01 19:00"] == 20 and p["2015-01-01 20:00"] == 10
    assert conv.ledger.dest[("t_data", "2")] == "network"  # the Include row
    assert any(a[0] == "timeslices" for a in assumed(conv))
    assert not [a for a in assumed(conv) if a[0] == "dated rows"]


def test_timeslice_name_is_the_pattern_only_without_a_pattern_row():
    """GK A P4: the name as pattern gets an assumed row naming that reading."""
    conv = run(
        model(
            objects=[("Timeslice", "H1-12")],
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", sliced("H1-12")),
            ],
            files=(),
        )
    )
    assert p_set(conv).tolist()[11:13] == [20, 10]
    assert ("Timeslice 'H1-12'", "pattern", "H1-12") in assumed(conv)


@pytest.mark.parametrize(
    "objects, rows, reason",
    [
        (
            [("Timeslice", "Peak")],
            [],
            "timeslice 'Peak': pattern 'Peak': pattern item 'Peak' not read",
        ),
        (
            [("Timeslice", "T")],
            [include("T", "H1"), include("T", "H2")],
            "timeslice 'T': 2 pattern rows; none used",
        ),
        (
            [("Timeslice", "T")],
            [include("T", "H1", date_from="2015-01-01")],
            "timeslice 'T': its pattern row is dated, tagged or banded; not read",
        ),
        (
            [("Timeslice", "T"), ("Scenario", "S")],
            [include("T", "H1", tags=[Tag("Scenario", "S", None)])],
            "timeslice 'T': its pattern row is dated, tagged or banded; not read",
        ),
        (
            [("Timeslice", "T")],
            [include("T", "H1", date_to="2015-01-01")],
            "timeslice 'T': its pattern row is dated, tagged or banded; not read",
        ),
        (
            [("Timeslice", "T")],
            [include("T", "H1", band=2)],
            "timeslice 'T': its pattern row is dated, tagged or banded; not read",
        ),
        (
            [("Timeslice", "T")],
            [include("T", "  ")],
            "timeslice 'T': its pattern row gives no single pattern",
        ),
        (
            [("Timeslice", "T")],
            [include("T", "H8-9")],
            "timeslice 'T': pattern 'H8-9': pattern 'H8-9' covers snapshot "
            "2015-01-01 06:00:00 only partly; not rounded",
        ),
    ],
)
def test_unusable_timeslice_leaves_the_group_unmapped(objects, rows, reason):
    """GK A P4; the 3 h horizon makes H8-9 partial (GK A P1)."""
    name = objects[0][1]
    conv = run(
        three_hourly(
            objects=objects,
            rows=[
                *rows,
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", sliced(name)),
            ],
        )
    )
    assert conv.network.loads.empty
    assert set(unmapped_reason(conv, "Node 'A'")) == {reason}
    if not rows:  # a Timeslice without rows is still listed, with why
        assert (f"Timeslice '{name}'", reason.split(": ", 1)[1]) in reasons(conv)


# ------------------------------------------------------------- Q3, scenarios


def test_dated_data_file_row_leaves_the_group_unmapped():
    """GK A Q3: no overlay onto a file series, either way round."""
    why = (
        "dated or timesliced rows with a data-file or variable row for 'Load': not read"
    )
    dated_file = run(
        model(
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 0, "MW", dated("2015-01-02T00:00:00") | filed()),
            ]
        )
    )
    assert dated_file.network.loads.empty
    assert set(unmapped_reason(dated_file, "Node 'A'")) == {why}
    file_under = run(
        model(
            rows=[
                ("Node", "A", "Load", 0, "MW", filed()),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00")),
            ]
        )
    )
    assert file_under.network.loads.empty
    assert set(unmapped_reason(file_under, "Node 'A'")) == {why}


def test_variable_scaled_dated_row_leaves_the_group_unmapped():
    conv = run(
        model(
            objects=[("Variable", "V")],
            rows=[
                ("Variable", "V", "Profile", 2, "-"),
                ("Node", "A", "Load", 10, "MW"),
                (
                    "Node",
                    "A",
                    "Load",
                    20,
                    "MW",
                    dated("2015-01-02T00:00:00", tags=[Tag("Variable", "V", "×")]),
                ),
            ],
            files=(),
        )
    )
    assert conv.network.loads.empty


def test_world_fixture_has_no_dated_or_timesliced_rows():
    """GK A L1: the World ledgers are unchanged by slice 3b because of this."""
    m = read_plexos_xml(IRL)
    assert not [r for r in m.rows if _windowed(r)]
    assert not m.objects_of("Timeslice")


def test_unused_timeslice_rows_are_unmapped():
    conv = run(model(objects=[("Timeslice", "T")], rows=[include("T", "H1")], files=()))
    assert ("Timeslice 'T' (Include)", "no imported row uses it") in reasons(conv)


def test_two_timeslices_on_one_row_fail_closed():
    conv = run(
        model(
            objects=[("Timeslice", "H1"), ("Timeslice", "H2")],
            rows=[
                ("Node", "A", "Load", 10, "MW"),
                (
                    "Node",
                    "A",
                    "Load",
                    20,
                    "MW",
                    {
                        "tags": [
                            Tag("Timeslice", "H1", None),
                            Tag("Timeslice", "H2", None),
                        ]
                    },
                ),
            ],
            files=(),
        )
    )
    assert set(unmapped_reason(conv, "Node 'A'")) == {
        "2 timeslices on one row; none used"
    }


def test_other_timeslice_properties_are_unmapped_when_the_pattern_is_used():
    conv = run(
        model(
            objects=[("Timeslice", "Day")],
            rows=[
                include("Day", "H9-20"),
                ("Timeslice", "Day", "Weight", 1, "-"),
                ("Node", "A", "Load", 10, "MW"),
                ("Node", "A", "Load", 20, "MW", sliced("Day")),
            ],
            files=(),
        )
    )
    assert conv.ledger.dest[("t_data", "2")] == "network"
    assert ("Timeslice 'Day' (Weight)", "Timeslice property 'Weight' not read") in (
        reasons(conv)
    )


def test_midnight_date_to_ending_inside_a_snapshot_is_refused():
    """GK A D1/D2: a 3 h horizon from 01:00, so midnight falls inside a snapshot."""
    m = three_hourly(
        rows=[
            ("Node", "A", "Load", 10, "MW"),
            ("Node", "A", "Load", 20, "MW", dated(date_to="2015-01-01T00:00:00")),
        ]
    )
    for a in m.attributes:
        if a.name == "Chrono Date From":
            object.__setattr__(a, "value", 42005 + 1 / 24)
    conv = run(m)
    assert set(unmapped_reason(conv, "Node 'A'")) == {
        "end of Date To 2015-01-02 00:00:00 falls inside snapshot "
        "2015-01-01 22:00:00; not rounded"
    }


def test_no_horizon_leaves_dated_rows_and_timeslices_unmapped():
    from tests.gridspine.test_plexos_import import build
    from tests.gridspine.test_plexos_timeseries import run as run_any

    m = build(
        [("Node", "A"), ("Timeslice", "H1")],
        [
            ("Node", "A", "Load", 10, "MW"),
            ("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00")),
            ("Node", "A", "Load", 30, "MW", sliced("H1")),
        ],
    )
    conv = run_any(m)
    assert set(unmapped_reason(conv, "Node 'A'")) == {"the network has no time horizon"}
    assert ("Timeslice 'H1'", "the network has no time horizon") in reasons(conv)


def test_undated_variable_scaled_row_in_a_dated_group_is_unmapped():
    """GK A Q3 / N4: no overlay onto a variable-scaled undated row."""
    conv = run(
        model(
            objects=[("Variable", "V")],
            rows=[
                ("Variable", "V", "Profile", 2, "-"),
                ("Node", "A", "Load", 10, "MW", {"tags": [Tag("Variable", "V", "×")]}),
                ("Node", "A", "Load", 20, "MW", dated("2015-01-02T00:00:00")),
            ],
            files=(),
        )
    )
    assert conv.network.loads.empty
    assert set(unmapped_reason(conv, "Node 'A'")) == {
        "dated or timesliced rows with a data-file or variable row for 'Load': not read"
    }
    assert unmapped_reason(conv, "Variable 'V'") == ["no imported row uses it"]
