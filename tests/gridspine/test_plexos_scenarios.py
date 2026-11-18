"""
PLEXOS importer slice 3a: scenarios of the chosen Model, and Variable profiles.

GK A's plan GO (S1-S6, C1-C6) is in ``.scratch/project-io/plexos-mapping.md``.
"""

from __future__ import annotations

import zipfile

import pytest

pypsa = pytest.importorskip("pypsa")

from gridspine.ingest.plexos_import import _read_input, convert  # noqa: E402
from gridspine.ingest.plexos_xml import Tag, read_plexos_xml  # noqa: E402
from tests.gridspine.test_plexos_import import (  # noqa: E402
    EDGE,
    IRL,
    check_ledger,
    reasons,
    unmapped_reason,
)
from tests.gridspine.test_plexos_timeseries import (  # noqa: E402
    CSV_DIR,
    DEMAND,
    _two_days,
    data_file,
    filed,
    hourly,
    model,
    run,
)

WIND_ADJ = CSV_DIR / "Renewables.ninja.wind.output.Full.adjusted.csv"
CSP_ADJ = CSV_DIR / "renewables.ninja.Csp.output.full.adjusted.csv"


def on(*names):
    return {"tags": [Tag("Scenario", n, None) for n in names]}


def scenarios(*names, objects=(), rows=(), members=(), attrs=(), **kw):
    """model() whose Model 'M' switches ``names`` on; 'Off' exists, not on."""
    objs = [("Scenario", n) for n in (*names, "Off")]
    mems = [(("Model", "M"), ("Scenario", n), "Scenarios") for n in names]
    return model([*objs, *objects], rows, [*mems, *members], attrs, **kw)


def load(conv):
    return conv.network.loads.p_set.to_dict()


# ------------------------------------------------------------- S1: override


def test_on_scenario_row_replaces_the_base_row():
    conv = run(
        scenarios(
            "On",
            rows=[
                ("Node", "A", "Load", 5, "MW"),
                ("Node", "A", "Load", 9, "MW", on("On")),
                ("Node", "A", "Load", 7, "MW", on("Off")),
            ],
        )
    )
    assert load(conv) == {"A": 9.0}
    assert sorted(unmapped_reason(conv, "Node 'A'")) == [
        "replaced by scenario 'On'",
        "scenario 'Off' not selected",
    ]


def test_dated_or_banded_scenario_row_never_replaces_the_base_row():
    """GK A C1: dated and band > 1 rows are slice 3b; the base row stays."""
    conv = run(
        scenarios(
            "On",
            rows=[
                ("Node", "A", "Load", 5, "MW"),
                ("Node", "A", "Load", 9, "MW", on("On") | {"date_from": "2015-01-01"}),
                ("Node", "A", "Load", 8, "MW", on("On") | {"band": 2}),
            ],
        )
    )
    assert load(conv) == {"A": 5.0}
    assert sorted(unmapped_reason(conv, "Node 'A'")) == [
        "band 2: only band 1 is used",
        "dated row: dates are applied in slice 3b",
    ]


# ------------------------------------------------------------- S2: ties


def test_two_on_scenarios_on_one_property_fail_closed_with_one_warning():
    """GK A C2: equal values still tie; one warning per scenario pair."""
    conv = run(
        scenarios(
            "X",
            "Y",
            objects=[("Node", "B")],
            rows=[
                ("Node", "B", "Voltage", 220, "kV"),
                *[
                    ("Node", n, "Load", v, "MW", on(s))
                    for n in ("A", "B")
                    for s, v in (("X", 4), ("Y", 4))
                ],
                ("Node", "A", "Load", 5, "MW"),
            ],
        )
    )
    assert conv.network.loads.empty
    why = "scenarios 'X' and 'Y' both set 'Load'; none used"
    assert unmapped_reason(conv, "Node 'A'") == [why]
    assert unmapped_reason(conv, "Node 'B'") == [why]
    tie = [w for w in conv.report.warnings if "'X' and 'Y'" in w]
    assert len(tie) == 1


def test_one_row_tagged_with_two_on_scenarios_is_not_a_tie():
    conv = run(
        scenarios("X", "Y", rows=[("Node", "A", "Load", 6, "MW", on("X", "Y", "Off"))])
    )
    assert load(conv) == {"A": 6.0}
    assert not conv.report.warnings


def test_overlapping_tag_sets_tie_and_name_each_scenario_once():
    conv = run(
        scenarios(
            "X",
            "Y",
            rows=[
                ("Node", "A", "Load", 4, "MW", on("X", "Y")),
                ("Node", "A", "Load", 6, "MW", on("X")),
            ],
        )
    )
    assert unmapped_reason(conv, "Node 'A'") == [
        "scenarios 'X' and 'Y' both set 'Load'; none used"
    ]


def test_two_rows_of_one_scenario_are_not_called_base_values():
    conv = run(
        scenarios(
            "On",
            rows=[
                ("Node", "A", "Load", 4, "MW", on("On")),
                ("Node", "A", "Load", 6, "MW", on("On")),
            ],
        )
    )
    assert unmapped_reason(conv, "Node 'A'") == [
        "2 values of scenario 'On' for 'Load'; none used"
    ]


# ------------------------------------------------- S3: file chosen by scenario


def _adjusted_file(given):
    m = scenarios(
        "Adj",
        rows=[
            ("Node", "A", "Load", 0, "MW", filed()),
            (
                "Data File",
                "F",
                "Filename",
                0,
                "-",
                {"texts": [("Data File", "csv_files\\adj.csv")]} | on("Adj"),
            ),
        ],
    )
    return run(m, given)


def test_scenario_filename_row_picks_the_adjusted_file():
    conv = _adjusted_file({"adj.csv": hourly({"A": [3, 4]})})
    assert conv.network.loads_t.p_set["A"].tolist()[:3] == [3, 4, 3]
    assert unmapped_reason(conv, "Data File 'F'") == ["replaced by scenario 'Adj'"]


def test_scenario_filename_missing_is_not_replaced_by_the_base_file():
    """Ruling A: a missing file is unmapped; the base file is never a fallback."""
    conv = _adjusted_file({"load.csv": hourly({"A": [3, 4]})})
    assert conv.network.loads_t.p_set.empty
    assert any(
        r.endswith("adj.csv' not supplied") for r in unmapped_reason(conv, "Node 'A'")
    )


# ------------------------------------------------------------- S4: variables


def var(name="V", action="×"):
    return Tag("Variable", name, action)


def scaled(
    *tags, profile=None, extra=(), files=None, objects=(), load_row=None, attrs=()
):
    """Node A's Load 10 MW (or ``load_row``) scaled by ``tags``."""
    names = sorted({t.name for t in tags})
    rows = [load_row or ("Node", "A", "Load", 10, "MW", {"tags": list(tags)})]
    for n in names:
        if profile is not None:
            rows.append(("Variable", n, "Profile", *profile))
    rows += list(extra)
    return run(
        model([*(("Variable", n) for n in names), *objects], rows, attrs=attrs),
        files,
    )


@pytest.mark.parametrize(
    "action, want", [("×", 20.0), ("÷", 5.0), ("+", 12.0), ("-", 8.0)]
)
def test_static_variable_actions(action, want):
    conv = scaled(var(action=action), profile=(2, "-"))
    assert load(conv) == {"A": want}
    # one variable on a row needs no assumed row about the order
    assert not [a for a in conv.report.assumed if a.parameter == "Load"]


def test_multiplying_by_zero_is_not_a_division_by_zero():
    assert load(scaled(var(), profile=(0, "-"))) == {"A": 0.0}


def test_overflow_after_the_variable_unmaps_the_row():
    conv = scaled(
        var(),
        profile=(1e300, "-"),
        load_row=("Node", "A", "Load", 1e300, "MW", {"tags": [var()]}),
    )
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == [
        "a value is not finite after its variables"
    ]


def test_static_only_property_times_a_static_variable_is_scaled():
    conv = run(
        model(
            [("Generator", "G"), ("Variable", "V")],
            [
                ("Generator", "G", "Units", 1, "-"),
                ("Generator", "G", "Max Capacity", 50, "MW", {"tags": [var()]}),
                ("Variable", "V", "Profile", 2, "-"),
            ],
            [(("Generator", "G"), ("Node", "A"), "Nodes")],
        )
    )
    assert conv.network.generators.loc["G", "p_nom"] == 100.0


def test_file_variable_scales_a_file_series_at_each_timestamp():
    vo, vr, va = data_file("VF", "csv_files\\var.csv")
    conv = scaled(
        Tag("Data File", "F", None),
        var(),
        profile=(0, "-", {"tags": [Tag("Data File", "VF", None)]}),
        load_row=(
            "Node",
            "A",
            "Load",
            0,
            "MW",
            {"tags": [Tag("Data File", "F", None), var()]},
        ),
        objects=vo,
        extra=vr,
        attrs=va,
        files={
            "load.csv": hourly({"A": [10, 20]}),
            "var.csv": hourly({"V": [1.5, 0.5]}),
        },
    )
    p = conv.network.loads_t.p_set["A"]
    assert (p.iloc[0], p.iloc[1]) == (15.0, 10.0)


def test_static_load_times_a_file_variable_becomes_a_series():
    vo, vr, va = data_file("VF", "csv_files\\var.csv")
    conv = scaled(
        var(),
        profile=(0, "-", {"tags": [Tag("Data File", "VF", None)]}),
        objects=vo,
        extra=vr,
        attrs=va,
        files={"var.csv": hourly({"V": [1, 2]})},
    )
    assert conv.network.loads_t.p_set["A"].tolist()[:2] == [10.0, 20.0]


def test_static_only_property_times_a_file_variable_is_unmapped():
    vo, vr, va = data_file("VF", "csv_files\\var.csv")
    conv = run(
        model(
            [("Generator", "G"), ("Variable", "V"), *vo],
            [
                ("Generator", "G", "Max Capacity", 50, "MW", {"tags": [var()]}),
                ("Variable", "V", "Profile", 0, "-", filed("VF")),
                *vr,
            ],
            [(("Generator", "G"), ("Node", "A"), "Nodes")],
            va,
        ),
        {"var.csv": hourly({"V": [1, 2]})},
    )
    assert "static property scaled by a time-varying variable" in unmapped_reason(
        conv, "Generator 'G'"
    )


@pytest.mark.parametrize(
    "cells, why",
    [
        ([1, 0, 1], "divided by variable 'V', which is 0"),
        (None, "scaled by variable 'V': data file: "),
    ],
)
def test_one_bad_variable_cell_unmaps_the_whole_row(cells, why):
    """GK A C3: ÷0 or an empty cell at one hour of 48; never a partial series."""
    vo, vr, va = data_file("VF", "csv_files\\var.csv")
    body = hourly({"V": cells}) if cells else _hole(hourly({"V": [1]}))
    conv = scaled(
        var(action="÷"),
        profile=(0, "-", {"tags": [Tag("Data File", "VF", None)]}),
        objects=vo,
        extra=vr,
        attrs=va,
        files={"var.csv": body},
    )
    assert conv.network.loads.empty and conv.network.loads_t.p_set.empty
    assert any(r.startswith(why) for r in unmapped_reason(conv, "Node 'A'"))


def _hole(body: bytes) -> bytes:
    lines = body.decode().splitlines()
    lines[5] = lines[5].split(",")[0] + ","
    return ("\n".join(lines) + "\n").encode()


def test_variable_of_a_row_whose_file_is_missing_is_not_counted_as_used():
    conv = scaled(
        var(),
        profile=(2, "-"),
        load_row=(
            "Node",
            "A",
            "Load",
            0,
            "MW",
            {"tags": [Tag("Data File", "F", None), var()]},
        ),
    )
    assert conv.network.loads_t.p_set.empty
    assert any(r.endswith("not supplied") for r in unmapped_reason(conv, "Node 'A'"))
    assert unmapped_reason(conv, "Variable 'V'") == ["no imported row uses it"]


def test_static_divide_by_zero_and_unknown_action():
    conv = scaled(var(action="÷"), profile=(0, "-"))
    assert unmapped_reason(conv, "Node 'A'") == ["divided by variable 'V', which is 0"]
    conv = scaled(var(action="^"), profile=(2, "-"))
    assert unmapped_reason(conv, "Node 'A'") == [
        "scaled by Variable 'V' with action '^': only × ÷ + - are read"
    ]
    assert unmapped_reason(conv, "Variable 'V'") == ["no imported row uses it"]


def test_sampled_variable_is_unmapped_and_the_file_default_is_plain():
    m = model(
        [("Variable", "V")],
        [
            ("Node", "A", "Load", 10, "MW", {"tags": [var()]}),
            ("Variable", "V", "Profile", 2, "-"),
            ("Variable", "V", "Sampling Method", 3, "-"),
        ],
    )
    m.defaults[(("System", "Variable", "Variables"), "Sampling Method")] = 1.0
    conv = run(m)
    why = "scaled by variable 'V': sampled variable (Sampling Method 3)"
    assert unmapped_reason(conv, "Node 'A'") == [why]
    assert unmapped_reason(conv, "Variable 'V'") == [
        "sampled variable (Sampling Method 3)"
    ]
    m.rows[-1].value = 1.0
    assert load(run(m)) == {"A": 20.0}


@pytest.mark.parametrize(
    "rows, why",
    [
        ([("Variable", "V", "Profile", 2, "-", on("Off"))], "no profile in this Model"),
        (
            [
                ("Variable", "V", "Profile", 2, "-"),
                ("Variable", "V", "Condition", 1, "-"),
            ],
            "variable property 'Condition' is not read",
        ),
    ],
)
def test_variable_without_a_plain_profile_unmaps_its_rows(rows, why):
    conv = run(
        scenarios(
            objects=[("Variable", "V")],
            rows=[("Node", "A", "Load", 10, "MW", {"tags": [var()]}), *rows],
        )
    )
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == [f"scaled by variable 'V': {why}"]


def test_several_variables_apply_in_tag_order_with_an_assumed_row():
    conv = run(
        model(
            [("Variable", "V"), ("Variable", "W")],
            [
                ("Node", "A", "Load", 10, "MW", {"tags": [var(), var("W", "+")]}),
                ("Variable", "V", "Profile", 2, "-"),
                ("Variable", "W", "Profile", 1, "-"),
            ],
        )
    )
    assert load(conv) == {"A": 21.0}  # (10 × 2) + 1, not (10 + 1) × 2
    a = next(a for a in conv.report.assumed if a.parameter == "Load")
    assert a.value == "× V then + W"


# ------------------------------------------------- S5: order of operations


def _rf(value, *tags, files=None):
    return run(
        model(
            [("Generator", "G"), ("Variable", "V"), ("Variable", "W")],
            [
                ("Generator", "G", "Units", 1, "-"),
                ("Generator", "G", "Max Capacity", 50, "MW"),
                ("Generator", "G", "Rating Factor", value, "%", {"tags": list(tags)}),
                ("Variable", "V", "Profile", 1.5, "-"),
                ("Variable", "W", "Profile", 10, "-"),
            ],
            [(("Generator", "G"), ("Node", "A"), "Nodes")],
        ),
        files,
    )


def test_variable_applies_before_the_percent_conversion():
    """GK A C4: (50 × 1.5) + 10 = 85 % → 0.85; the other order gives 10.75."""
    conv = _rf(50, var(), var("W", "+"))
    assert conv.network.generators.loc["G", "p_max_pu"] == pytest.approx(0.85)


def test_multiplied_rating_factor_above_100_is_unmapped_not_clipped():
    """GK A Q-a: World's 1.011 wind multiplier on a 100 % cell."""
    conv = _rf(
        0,
        Tag("Data File", "F", None),
        var(),
        files={"load.csv": hourly({"G": [40, 80]})},
    )
    assert "G" not in conv.network.generators_t.p_max_pu
    assert unmapped_reason(conv, "Generator 'G'") == [
        "Rating Factor 120 % at 2015-01-01 01:00:00 outside [0, 100] %"
    ]
    # the variable's rows are not counted as used by a row that was not imported
    assert unmapped_reason(conv, "Variable 'V'") == ["no imported row uses it"]


# ----------------------------------------------- the World fixture, S3 + S6


def _europe(tmp_path, files):
    path = tmp_path / "world.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.write(IRL, "World.xml")
        for f in files:
            zf.write(f, f"csv_files/{f.name}")
    got = _read_input([path])
    m = _two_days(got.xml)
    conv = convert(m, {"model": "PLEXOS World 2015 Europe"}, None, got.data_files)
    check_ledger(m, conv)
    return conv


def _cell(path, name, hour):
    lines = path.read_text().splitlines()
    return float(lines[hour + 1].split(",")[lines[0].split(",").index(name)])


def test_europe_model_reads_the_adjusted_wind_file_times_its_multiplier(tmp_path):
    conv = _europe(tmp_path, [DEMAND, WIND_ADJ, CSP_ADJ])
    p = conv.network.generators_t.p_max_pu["IRL_Win_Altagowlan13258"]
    for h in (0, 30):
        cell = _cell(WIND_ADJ, "IRL_Win_Altagowlan13258", h)
        assert p.iloc[h] == pytest.approx(cell * 1.011 / 100)
    # 'Exclude CSP' is on in this Model: the CSP unit is not built
    assert "ESP_Csp_ANDASOL316855" not in conv.network.storage_units.index
    assert not [w for w in conv.report.warnings if "both set" in w]


def test_europe_model_without_the_adjusted_files_never_uses_the_base_files(tmp_path):
    base = CSV_DIR / "Renewables.ninja.wind.output.Full.csv"
    conv = _europe(tmp_path, [DEMAND, base])
    assert "IRL_Win_Altagowlan13258" not in conv.network.generators_t.p_max_pu
    assert any(
        "Renewables.ninja.wind.output.Full.adjusted.csv" in r and "not supplied" in r
        for r in unmapped_reason(conv, "Generator 'IRL_Win_Altagowlan13258'")
    )


MODELS = sorted(o.name for o in read_plexos_xml(IRL).objects_of("Model"))


@pytest.mark.parametrize("name", MODELS)
def test_every_row_lands_once_for_each_world_model(name):
    """GK A C5: network + sidecar + unmapped + assumed = input, per Model."""
    m = read_plexos_xml(IRL)
    check_ledger(m, convert(m, {"model": name}))


def test_every_row_lands_once_on_the_edge_fixture():
    m = read_plexos_xml(EDGE)
    conv = convert(m, {})
    check_ledger(m, conv)
    assert any("not selected" in r for _, r in reasons(conv))
