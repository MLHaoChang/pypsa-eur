"""
PLEXOS importer slice 2b: data-file time series and head-storage units.

``gridspine.ingest.plexos_csv`` reads the CSVs; ``plexos_import`` maps node
Load, generator Rating Factor, fuel Price and storage Natural Inflow series,
and turns a generator with one head storage into a storage unit. GK A's
conditions (Q7, Q10 and the 2b plan) are in ``.scratch/project-io/plexos-mapping.md``.
"""

from __future__ import annotations

import dataclasses
import io
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

pypsa = pytest.importorskip("pypsa")

from gridspine.ingest.plexos_csv import (  # noqa: E402
    file_key,
    match_files,
    read_series,
)
from gridspine.ingest.plexos_import import (  # noqa: E402
    FUEL_PRICES_SIDECAR,
    _read_input,
    convert,
    read_plexos,
)
from gridspine.ingest.plexos_xml import Tag, read_plexos_xml  # noqa: E402
from tests.gridspine.test_plexos_import import (  # noqa: E402
    IRL,
    build,
    check_ledger,
    reasons,
    unmapped_reason,
)

CSV_DIR = Path(__file__).parent / "fixtures" / "plexos" / "csv_files"
DEMAND = CSV_DIR / "All Demand UTC 2015.csv"
WIND = CSV_DIR / "Renewables.ninja.wind.output.Full.csv"
CSP = CSV_DIR / "renewables.ninja.Csp.output.full.csv"
HOURS = pd.date_range("2015-01-01", periods=48, freq="h")


def column_sum(path: Path, name: str) -> float:
    """Independent of plexos_csv: plain text split, as a hand check would."""
    lines = path.read_text().splitlines()
    i = lines[0].split(",").index(name)
    return sum(float(line.split(",")[i]) for line in lines[1:])


def text(body: str):
    return lambda: io.BytesIO(body.encode())


def series(body, names=("A",), snaps=HOURS[:3], step=60, locale=2057):
    return read_series(text(body), names, snaps, step, locale)


# ------------------------------------------------------------- CSV layouts


def test_datetime_layout_on_the_real_demand_file():
    got = read_series(lambda: open(DEMAND, "rb"), ["EU-IRL"], HOURS, 60, 2057)
    s = got["EU-IRL"]
    assert list(s.index) == list(HOURS)
    # the file writes midnight as a bare date: '01/01/2015' then '01/01/2015 01:00'
    assert s.iloc[0] == 3118 and s.iloc[1] == 2902
    assert s.sum() == column_sum(DEMAND, "EU-IRL") == 153155


def test_locale_decides_day_and_month_order():
    body = "Datetime,A\n01/02/2015 00:00,1\n01/02/2015 01:00,2\n"
    gb = series(body, snaps=pd.date_range("2015-02-01", periods=2, freq="h"))
    us = series(
        body, snaps=pd.date_range("2015-01-02", periods=2, freq="h"), locale=1033
    )
    assert gb["A"].tolist() == [1, 2] and us["A"].tolist() == [1, 2]
    none = series(body, locale=None)["A"]
    assert "day/month order unknown (the Data File has no Locale)" in none
    iso = "Datetime,A\n2015-01-01 00:00,1\n2015-01-01 01:00,2\n2015-01-01 02:00,3\n"
    assert series(iso, locale=None)["A"].tolist() == [1, 2, 3]


def test_year_month_day_period_layout():
    body = "Year,Month,Day,Period,A\n" + "".join(
        f"2015,1,1,{p},{p * 10}\n" for p in range(1, 4)
    )
    assert series(body)["A"].tolist() == [10, 20, 30]
    half = pd.date_range("2015-01-01", periods=3, freq="30min")
    assert series(body, snaps=half, step=30)["A"].tolist() == [10, 20, 30]
    bad = "Year,Month,Day,Period,A\n2015,1,1,25,1\n"
    assert "period 25 outside 1..24" in series(bad)["A"]


def test_monthly_layout():
    body = "NAME,M1,M2,M3,M4,M5,M6,M7,M8,M9,M10,M11,M12\nA,1,2,3,4,5,6,7,8,9,10,11,12\n"
    snaps = pd.DatetimeIndex(["2015-01-31 23:00", "2015-02-01 00:00", "2015-12-01"])
    assert series(body, snaps=snaps)["A"].tolist() == [1, 2, 12]
    assert series(body, names=("B",))["B"] == "no row 'B'"
    twice = body + "a,1,1,1,1,1,1,1,1,1,1,1,1\n"
    assert series(twice)["A"].tolist() == [1, 1, 1]  # exact name wins
    dup = body + "A,1,1,1,1,1,1,1,1,1,1,1,1\n"
    assert series(dup)["A"] == "2 rows for 'A'"


@pytest.mark.parametrize(
    "body, reason",
    [
        (
            "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 00:00,1\n01/01/2015 01:00,2\n01/01/2015 02:00,3\n",
            "timestamp 2015-01-01 00:00:00 appears twice",
        ),
        (
            "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 02:00,3\n",
            "file has no value for 1 of 3 snapshots, first 2015-01-01 01:00:00",
        ),
        (
            "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 00:30,1\n01/01/2015 01:00,2\n01/01/2015 02:00,3\n",
            "finer than the 60-minute snapshots; not resampled",
        ),
        (
            "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,\n01/01/2015 02:00,3\n",
            "value '' at 2015-01-01 01:00:00 is not a number",
        ),
        (
            "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,NaN\n01/01/2015 02:00,3\n",
            "value 'NaN' at 2015-01-01 01:00:00 is not a number",
        ),
        (
            "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,abc\n01/01/2015 02:00,3\n",
            "value 'abc' at 2015-01-01 01:00:00 is not a number",
        ),
        ("Datetime,A\n32/01/2015 00:00,1\n", "timestamp '32/01/2015 00:00' unreadable"),
        ("Hour,A\n1,1\n", "layout not read (header starts Hour, A)"),
        ("Datetime,A,A\n01/01/2015 00:00,1,1\n", "column 'A' appears twice"),
        ("", "file is empty"),
    ],
)
def test_unusable_series_say_why_and_are_never_filled(body, reason):
    got = series(body)["A"]
    assert isinstance(got, str) and reason in got


def test_short_cell_rows_are_unreadable_not_zero():
    body = (
        "Datetime,A,B\n01/01/2015 00:00,1,1\n01/01/2015 01:00,2\n01/01/2015 02:00,3,3\n"
    )
    got = series(body, names=("A", "B"))
    assert got["A"].tolist() == [1, 2, 3]
    assert got["B"] == "value '' at 2015-01-01 01:00:00 is not a number"


def test_columns_match_by_name_then_case_insensitively():
    body = "Datetime,Gen-a,gen-B,GEN-b\n2015-01-01 00:00,1,2,3\n"
    snaps = HOURS[:1]
    got = series(body, names=("Gen-a", "gen-a", "gen-b", "x"), snaps=snaps)
    assert got["Gen-a"].tolist() == [1] and got["gen-a"].tolist() == [1]
    assert got["gen-b"] == "no column 'gen-b'"  # two case-insensitive matches
    assert got["x"] == "no column 'x'"


def test_no_horizon_reads_nothing():
    got = read_series(text("Datetime,A\n"), ["A"], pd.Index(["now"]), None)
    assert got == {"A": "the network has no time horizon"}


def test_file_names_match_after_backslash_and_case():
    given = {"m/csv_files/renewables.ninja.Csp.output.full.csv": text("")}
    path = "csv_files\\renewables.ninja.CSP.output.full.csv"
    assert file_key(path) == "renewables.ninja.csp.output.full.csv"
    assert match_files([path], given)[path] is given[next(iter(given))]
    two = {"a/x.csv": text(""), "b/X.CSV": text("")}
    assert match_files(["x.csv"], two)["x.csv"] == (
        "2 given files are named 'x.csv': a/x.csv, b/X.CSV"
    )
    assert match_files(["y.csv"], two)["y.csv"] == "data file 'y.csv' not supplied"


def test_csv_reader_loads_without_pypsa():
    code = (
        "import sys; sys.modules['pypsa'] = None\n"
        "import gridspine.ingest.plexos_csv as m; print(m.file_key('A\\\\B.CSV'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "b.csv"


# --------------------------------------------------------- mapping series


def horizon(days=2):
    """A Model with a chronological horizon from 1 Jan 2015, hourly."""
    objects = [("Model", "M"), ("Horizon", "H")]
    members = [(("Model", "M"), ("Horizon", "H"), "Horizon")]
    attrs = [
        ("Horizon", "H", "Chrono Date From", 42005.0),
        ("Horizon", "H", "Chrono Step Count", float(days)),
    ]
    return objects, members, attrs


def filed(name="F"):
    return {"tags": [Tag("Data File", name, None)]}


def data_file(name="F", path="csv_files\\load.csv", locale=2057):
    objects = [("Data File", name)]
    rows = [("Data File", name, "Filename", 0, "-", {"texts": [("Data File", path)]})]
    attrs = [("Data File", name, "Locale", float(locale))] if locale else []
    return objects, rows, attrs


def model(objects=(), rows=(), members=(), attrs=(), files=("load.csv",)):
    ho, hm, ha = horizon()
    fo, fr, fa = data_file(path="csv_files\\" + files[0]) if files else ([], [], [])
    return build(
        [*ho, *fo, ("Node", "A"), *objects],
        [("Node", "A", "Voltage", 220, "kV"), *fr, *rows],
        [*hm, *members],
        [*ha, *fa, *attrs],
    )


def hourly(cols: dict[str, list[float]], hours=48) -> bytes:
    lines = ["Datetime," + ",".join(cols)]
    for i, t in enumerate(pd.date_range("2015-01-01", periods=hours, freq="h")):
        vals = ",".join(f"{v[i % len(v)]:g}" for v in cols.values())
        lines.append(f"{t:%d/%m/%Y %H:%M},{vals}")
    return ("\n".join(lines) + "\n").encode()


def run(m, files: dict[str, bytes] | None = None):
    given = {k: (lambda b=b: io.BytesIO(b)) for k, b in (files or {}).items()}
    conv = convert(m, {}, None, given)
    check_ledger(m, conv)
    return conv


def test_node_load_series_and_the_static_row_it_replaces():
    m = model(
        rows=[
            ("Node", "A", "Load", 5, "MW"),
            ("Node", "A", "Load", 0, "MW", filed()),
        ]
    )
    conv = run(m, {"x/LOAD.csv": hourly({"a": [100, 200]})})
    p = conv.network.loads_t.p_set["A"]
    assert len(p) == 48 and p.tolist()[:3] == [100, 200, 100]
    assert unmapped_reason(conv, "Node 'A'") == ["the data-file row for 'Load' is used"]
    dest = conv.ledger.dest
    assert dest[("t_data", "2")] == "network"  # Filename row: the file was read
    assert dest[("t_attribute_data", "Data File|F|Locale")] == "network"
    assert conv.load_mw == dict.fromkeys(conv.load_mw, 0.0) | {"unmapped": 5.0}


def test_missing_file_is_unmapped_with_a_warning_never_the_placeholder():
    m = model(rows=[("Node", "A", "Load", 0, "MW", filed())])
    conv = run(m, {"other.csv": b"Datetime\n"})
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == [
        "data file: data file 'csv_files\\\\load.csv' not supplied"
    ]
    assert (
        "data file 'csv_files\\\\load.csv' not supplied: 1 time series not imported"
        in (conv.report.warnings)
    )
    assert ("data file other.csv", "no imported property reads this file") in reasons(
        conv
    )
    assert conv.ledger.dest[("t_data", "2")] == "unmapped"  # Filename row


def test_short_real_file_over_a_year_is_unmapped():
    m = model(rows=[("Node", "A", "Load", 0, "MW", filed())])
    m.attributes = [
        dataclasses.replace(a, value=365.0) if a.name == "Chrono Step Count" else a
        for a in m.attributes
    ]
    conv = run(m, {"load.csv": hourly({"A": [1]})})
    assert unmapped_reason(conv, "Node 'A'") == [
        "data file: file has no value for 8712 of 8760 snapshots, first 2015-01-03 00:00:00"
    ]


def test_rating_factor_series_in_percent_and_out_of_range():
    gens = [("Generator", "G"), ("Generator", "H")]
    rows = [
        (cls, name, prop, v, unit, *extra)
        for name in ("G", "H")
        for cls, _, prop, v, unit, *extra in [
            ("Generator", "", "Units", 1, "-"),
            ("Generator", "", "Max Capacity", 50, "MW"),
            ("Generator", "", "Rating Factor", 100, "%", filed()),
        ]
    ]
    members = [((("Generator", g)), ("Node", "A"), "Nodes") for g in ("G", "H")]
    conv = run(
        model(gens, rows, members),
        {"load.csv": hourly({"G": [0, 50, 100], "H": [40, 120]})},
    )
    p = conv.network.generators_t.p_max_pu
    assert p["G"].tolist()[:3] == [0.0, 0.5, 1.0]
    assert "H" not in p
    assert unmapped_reason(conv, "Generator 'H'") == [
        "Rating Factor 120 % at 2015-01-01 01:00:00 outside [0, 100] %"
    ]


def test_fuel_price_series_to_sidecar_and_marginal_cost():
    objects = [("Generator", "G"), ("Fuel", "Gas")]
    rows = [
        ("Generator", "G", "Units", 1, "-"),
        ("Generator", "G", "Max Capacity", 50, "MW"),
        ("Generator", "G", "Heat Rate", 10, "GJ/MWh"),
        ("Generator", "G", "VO&M Charge", 2, "$/MWh"),
        ("Fuel", "Gas", "Price", 0, "$/GJ", filed()),
    ]
    members = [
        (("Generator", "G"), ("Node", "A"), "Nodes"),
        (("Generator", "G"), ("Fuel", "Gas"), "Fuels"),
    ]
    conv = run(model(objects, rows, members), {"load.csv": hourly({"Gas": [3, 4]})})
    cost = conv.network.generators_t.marginal_cost["G"]
    assert cost.tolist()[:2] == [10 * 3 + 2, 10 * 4 + 2]
    fuel = conv.sidecars[FUEL_PRICES_SIDECAR]["fuels"][0]
    assert fuel["source"] == "data-file Price" and fuel["series"][
        "2015-01-01T01:00:00"
    ] == pytest.approx(4)
    assert conv.ledger.dest[("t_data", "7")] == "sidecar"  # the Price row
    assert any(a.asset == "G" and "price series" in a.why for a in conv.report.assumed)


def test_data_file_rows_not_read_never_use_the_placeholder():
    rows = [
        ("Generator", "G", "Units", 1, "-"),
        ("Generator", "G", "Max Capacity", 99, "MW", filed()),
        (
            "Node",
            "A",
            "Load",
            0,
            "MW",
            filed()
            | {"tags": [Tag("Data File", "F", None), Tag("Variable", "V", "×")]},
        ),
        (
            "Node",
            "A",
            "Voltage",
            0,
            "kV",
            {"tags": [Tag("Data File", "F", None), Tag("Data File", "G", None)]},
        ),
    ]
    conv = run(
        model(
            [("Generator", "G")], rows, [(("Generator", "G"), ("Node", "A"), "Nodes")]
        ),
        {"load.csv": hourly({"G": [1]})},
    )
    assert conv.network.generators.empty
    got = reasons(conv)
    assert (
        "Generator 'G' (Max Capacity)",
        "time series for 'Max Capacity' is not imported yet",
    ) in got
    assert ("Node 'A' (Load)", "scaled by Variable 'V': later slice") in got
    assert ("Node 'A' (Voltage)", "2 data files on one row; none used") in got


# --------------------------------------------------------------- head storage


def hydro_model(store_rows=(), gen_rows=(), objects=(), members=(), store="S"):
    gen = [
        ("Generator", "G", "Units", 1, "-"),
        ("Generator", "G", "Max Capacity", 100, "MW"),
        *gen_rows,
    ]
    st = [
        ("Storage", store, "Units", 1, "-"),
        ("Storage", store, "Max Volume", 2, "GWh"),
        ("Storage", store, "Initial Volume", 0.5, "GWh"),
        *store_rows,
    ]
    return model(
        [("Generator", "G", "Hydro"), ("Storage", store), *objects],
        [*gen, *st],
        [
            (("Generator", "G"), ("Node", "A"), "Nodes"),
            (("Generator", "G"), ("Storage", store), "Head Storage"),
            *members,
        ],
        files=("load.csv",),
    )


def test_head_storage_becomes_a_storage_unit():
    m = hydro_model(
        store_rows=[("Storage", "S", "Natural Inflow", 0, "MW", filed())],
        gen_rows=[("Generator", "G", "Rating Factor", 80, "%")],
    )
    conv = run(m, {"load.csv": hourly({"S": [10, 30]})})
    su = conv.network.storage_units.loc["G"]
    assert (su.bus, su.p_nom, su.carrier) == ("A", 100.0, "Hydro")
    assert su.max_hours == pytest.approx(2 * 1000 / 100)
    assert su.state_of_charge_initial == pytest.approx(500)
    assert (su.p_min_pu, su.p_max_pu, su.efficiency_dispatch) == (0.0, 0.8, 1.0)
    assert conv.network.storage_units_t.inflow["G"].sum() == 24 * (10 + 30)
    assert conv.network.generators.empty
    assert conv.report.counts["hydro reservoirs"] == 1
    assert conv.report.counts["batteries"] == 0


def test_static_inflow_in_mwh_storage():
    m = hydro_model(
        store_rows=[("Storage", "S", "Natural Inflow", 7, "MW")],
    )
    m.rows = [
        dataclasses.replace(r, unit="MWh", value=400.0)
        if r.property == "Max Volume"
        else r
        for r in m.rows
    ]
    conv = run(m)
    su = conv.network.storage_units.loc["G"]
    assert su.max_hours == pytest.approx(4.0) and su.inflow == 7.0
    assert unmapped_reason(conv, "Storage 'S'") == [
        "Initial Volume outside [0, Max Volume] or not GWh/MWh"
    ]  # 0.5 GWh read as 0.5 MWh would be wrong: unit is GWh, value 500 MWh > 400


@pytest.mark.parametrize(
    "change, reason",
    [
        ({"Max Volume": ("1000 m³", 5.0)}, "storage volume in '1000 m³'"),
        ({"Max Volume": ("GWh", 1e30)}, "Max Volume unlimited or not above 0"),
        ({"Units": ("-", 0.0)}, "0 units: not built"),
    ],
)
def test_storages_not_converted_drop_the_whole_unit(change, reason):
    m = hydro_model()
    m.rows = [
        dataclasses.replace(r, unit=change[r.property][0], value=change[r.property][1])
        if r.child == ("Storage", "S") and r.property in change
        else r
        for r in m.rows
    ]
    conv = run(m)
    n = conv.network
    assert n.storage_units.empty and n.generators.empty
    assert any(reason in r for r in unmapped_reason(conv, "Storage 'S'"))
    assert unmapped_reason(conv, "Generator 'G'")


@pytest.mark.parametrize(
    "objects, members, reason",
    [
        (
            [("Storage", "T")],
            [(("Generator", "G"), ("Storage", "T"), "Tail Storage")],
            "tail storage (pumped or cascaded hydro) is not converted yet",
        ),
        (
            [("Generator", "G2")],
            [(("Generator", "G2"), ("Storage", "S"), "Head Storage")],
            "storage 'S' feeds 2 generators: cascades are not converted yet",
        ),
        (
            [("Waterway", "W")],
            [(("Waterway", "W"), ("Storage", "S"), "Storage From")],
            "storage on a waterway: cascades are not converted yet",
        ),
    ],
)
def test_cascades_are_not_converted(objects, members, reason):
    conv = run(hydro_model(objects=objects, members=members))
    assert conv.network.storage_units.empty and conv.network.generators.empty
    assert reason in unmapped_reason(conv, "Generator 'G'")
    assert reason in unmapped_reason(conv, "Storage 'S'")


def test_storage_without_a_generator():
    conv = run(model([("Storage", "S")], [("Storage", "S", "Max Volume", 1, "GWh")]))
    assert unmapped_reason(conv, "Storage 'S'") == ["storage without a generator"]


def test_battery_and_hydro_generator_with_one_name_both_kept():
    """GK A Q7: a fixed suffix and an assumed row naming both, never overwritten."""
    m = hydro_model(
        objects=[("Battery", "G")],
        members=[(("Battery", "G"), ("Node", "A"), "Nodes")],
    )
    m2 = build(
        [],
        [
            ("Battery", "G", "Units", 1, "-"),
            ("Battery", "G", "Max Power", 10, "MW"),
            ("Battery", "G", "Capacity", 40, "MWh"),
        ],
    )
    offset = len(m.rows)
    m.rows += [
        dataclasses.replace(r, data_id=str(offset + int(r.data_id))) for r in m2.rows
    ]
    conv = run(m)
    su = conv.network.storage_units
    assert su.loc["G", "carrier"] == "battery" and su.loc["G", "p_nom"] == 10
    assert su.loc["G (Generator)", "carrier"] == "Hydro"
    assert any(
        a.asset == "G (Generator)" and "already used" in a.why
        for a in conv.report.assumed
    )


# ------------------------------------------------------------ fixture path


def _two_days(xml: bytes):
    m = read_plexos_xml(io.BytesIO(xml))
    m.attributes = [
        dataclasses.replace(a, value=2.0)
        if a.object == ("Horizon", "Full Year 2015 Hourly")
        and a.name == "Chrono Step Count"
        else a
        for a in m.attributes
    ]
    return m


def test_world_fixture_with_its_csvs_in_a_zip(tmp_path):
    path = tmp_path / "world.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(IRL, "World.xml")
        for f in (DEMAND, WIND, CSP):
            zf.write(f, f"csv_files/{f.name}")
    got = _read_input([path])
    m = _two_days(got.xml)
    conv = run_files(m, got.data_files)
    n = conv.network
    assert len(n.snapshots) == 48
    for node in ("EU-ESP", "EU-FRA", "EU-GBR", "EU-IRL"):
        assert n.loads_t.p_set[node].sum() == column_sum(DEMAND, node)
    wind = n.generators_t.p_max_pu["IRL_Win_Altagowlan13258"]
    assert wind.iloc[0] == pytest.approx(0.60)  # 60 % in the file's first row
    su = n.storage_units.loc["ESP_Csp_ANDASOL316855"]
    # hand check: Max Capacity 150 MW, Max Volume 1.125 GWh (world2015_irl.xml)
    assert (su.p_nom, su.max_hours) == (150.0, pytest.approx(1125 / 150))
    inflow = n.storage_units_t.inflow["ESP_Csp_ANDASOL316855"]
    assert inflow.sum() == column_sum(CSP, "ESP_Csp_ANDASOL316855_Storage")
    assert not conv.report.warnings


def run_files(m, files):
    conv = convert(m, {"model": "PLEXOS World 2015 Europe"}, None, files)
    check_ledger(m, conv)
    return conv


def test_world_fixture_loose_csvs_over_a_full_year(tmp_path):
    res = read_plexos([IRL, DEMAND, WIND, CSP], {"model": "PLEXOS World 2015 Europe"})
    assert res.network.loads_t.p_set.empty
    assert any(
        "file has no value for 8688 of 8736 snapshots" in u.reason
        for u in res.report.unmapped
        if "EU-IRL" in u.source_item
    )
