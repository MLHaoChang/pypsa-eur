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

from gridspine.ingest.import_result import ImportFormatError  # noqa: E402
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
    EDGE,
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


def test_bare_date_is_midnight():
    body = "Datetime,A\n01/01/2015,1\n01/01/2015 01:00,2\n01/01/2015 02:00:00,3\n"
    assert series(body)["A"].index[0] == pd.Timestamp("2015-01-01 00:00")
    assert series(body)["A"].tolist() == [1, 2, 3]


def test_locale_decides_day_and_month_order():
    body = "Datetime,A\n01/02/2015 00:00,1\n01/02/2015 01:00,2\n"
    feb1 = pd.date_range("2015-02-01", periods=2, freq="h")
    jan2 = pd.date_range("2015-01-02", periods=2, freq="h")
    assert series(body, snaps=feb1)["A"].tolist() == [1, 2]
    assert series(body, snaps=jan2, locale=1033)["A"].tolist() == [1, 2]
    iso = "Datetime,A\n2015-01-01 00:00,1\n2015-01-01 01:00,2\n2015-01-01 02:00,3\n"
    assert series(iso, locale=None)["A"].tolist() == [1, 2, 3]


def test_without_a_locale_the_whole_file_decides_the_order():
    """GK A T1: per file, never per cell; no day above 12 means ambiguous."""
    ambiguous = "Datetime,A\n01/02/2015 00:00,1\n01/02/2015 01:00,2\n"
    assert series(ambiguous, locale=None)["A"] == (
        "day/month order unknown: the Data File has no Locale and no day is above 12"
    )
    assert "Data File Locale 1031" in series(ambiguous, locale=1031)["A"]
    # '13/01' shows day first, so '01/02' in the same file is 1 February
    day_first = "Datetime,A\n13/01/2015 00:00,1\n01/02/2015 00:00,2\n"
    snaps = pd.DatetimeIndex(["2015-01-13", "2015-02-01"])
    got = read_series(text(day_first), ["A"], snaps, 60, None)["A"]
    assert isinstance(got, str)  # two dates, no single step: refused, not inferred
    assert got == "the snapshots have no single step; none inferred"
    days = pd.date_range("2015-01-13", periods=2, freq="D")
    d = "Datetime,A\n13/01/2015,1\n14/01/2015,2\n"
    m = "Datetime,A\n01/13/2015,1\n01/14/2015,2\n"
    assert read_series(text(d), ["A"], days, 1440, None)["A"].tolist() == [1, 2]
    assert read_series(text(m), ["A"], days, 1440, None)["A"].tolist() == [1, 2]
    both = "Datetime,A\n13/01/2015,1\n01/14/2015,2\n"
    assert read_series(text(both), ["A"], days, 1440, None)["A"] == (
        "day/month order unknown: the Data File has no Locale and the dates fit "
        "neither order"
    )


def test_year_month_day_period_layout():
    body = "Year,Month,Day,Period,A\n" + "".join(
        f"2015,1,1,{p},{p * 10}\n" for p in range(1, 4)
    )
    assert series(body)["A"].tolist() == [10, 20, 30]
    half = pd.date_range("2015-01-01", periods=3, freq="30min")
    assert series(body, snaps=half, step=30)["A"].tolist() == [10, 20, 30]
    bad = "Year,Month,Day,Period,A\n2015,1,1,25,1\n"
    assert "period 25 outside 1..24" in series(bad)["A"]
    # GK A N2: an infinite key is refused, never cast to int
    inf = "Year,Month,Day,Period,A\ninf,1,1,1,1\n"
    assert series(inf)["A"] == "Year 'inf' is not a whole number"
    inf = "Year,Month,Day,Period,A\n2015,1,1,-inf,1\n"
    assert series(inf)["A"] == "Period '-inf' is not a whole number"


MONTHLY = "NAME,M1,M2,M3,M4,M5,M6,M7,M8,M9,M10,M11,M12\n"
JAN_FEB = pd.date_range("2015-01-31 22:00", periods=4, freq="h")


def test_monthly_layout_gives_each_snapshot_its_months_value():
    """Slice 3b: 31 Jan 22:00 and 23:00 are January, 1 Feb 00:00 is February."""
    body = MONTHLY + "a,1,2,3,4,5,6,7,8,9,10,11,12\nB,0,0,0,0,0,0,0,0,0,0,0,0\n"
    got = series(body, names=("A", "B"), snaps=JAN_FEB)
    assert got["A"].tolist() == [1, 1, 2, 2]  # case-insensitive name match
    assert got["B"].tolist() == [0, 0, 0, 0]


@pytest.mark.parametrize(
    "rows, reason",
    [
        (
            "A,1,2,3,4,5,6,7,8,9,10,11,12\nA,1,2,3,4,5,6,7,8,9,10,11,12\n",
            "row 'A' appears 2 times",
        ),
        ("B,1,2,3,4,5,6,7,8,9,10,11,12\n", "no row 'A'"),
        ("A,1,nan,3,4,5,6,7,8,9,10,11,12\n", "value 'nan' at M2 is not a number"),
        ("A,1,2,inf,4,5,6,7,8,9,10,11,12\n", "value 'inf' at M3 is not a number"),
        ("A,1,2,3,4,5,6,7,8,9,10,11,\n", "value '' at M12 is not a number"),
        ("A,1,2\n", "value '' at M3 is not a number"),
    ],
)
def test_monthly_layout_refusals(rows, reason):
    """GK A C2: non-finite cells, duplicate and missing rows leave the name unread."""
    assert series(MONTHLY + rows, snaps=JAN_FEB)["A"] == reason


def _mdp(days, periods=24, value=lambda m, d, p: m * 10000 + d * 100 + p):
    body = "Month,Day,Period,A\n"
    for m, d in days:
        body += "".join(
            f"{m},{d},{p},{value(m, d, p)}\n" for p in range(1, periods + 1)
        )
    return body


def test_month_day_period_repeats_in_every_horizon_year():
    """Slice 3b, GK A C1: the year-free layout over a New Year horizon."""
    snaps = pd.date_range("2015-12-31 22:00", periods=4, freq="h")
    got = series(_mdp([(1, 1), (12, 31)]), snaps=snaps)["A"]
    assert got.tolist() == [123123, 123124, 10101, 10102]


def test_month_day_period_leap_day():
    """29 Feb rows are used in a leap year only; a leap year without them is a gap."""
    leap = pd.date_range("2016-02-29 23:00", periods=2, freq="h")
    days = [(2, 28), (2, 29), (3, 1)]
    assert series(_mdp(days), snaps=leap)["A"].tolist() == [22924, 30101]
    plain = pd.date_range("2015-02-28 23:00", periods=2, freq="h")
    assert series(_mdp(days), snaps=plain)["A"].tolist() == [22824, 30101]
    got = series(_mdp([(2, 28), (3, 1)]), snaps=leap)["A"]
    assert got == "file has no value for 1 of 2 snapshots, first 2016-02-29 23:00:00"


@pytest.mark.parametrize(
    "body, snaps, step, reason",
    [
        (
            _mdp([(1, 1)], periods=48),
            HOURS[:3],
            60,
            "file step 30 minutes (48 periods a day) is finer than the 60-minute "
            "snapshots; not resampled",
        ),
        (
            _mdp([(1, 1)], periods=24),
            pd.date_range("2015-01-01", periods=3, freq="30min"),
            30,
            "file step 60 minutes (24 periods a day) is coarser than the 30-minute "
            "snapshots; not resampled",
        ),
        (
            _mdp([(1, 1)]) + "1,2,1,5\n",
            HOURS[:3],
            60,
            "month 1 day 2 has 1 periods, other days 24; periods per day differ",
        ),
        (_mdp([(2, 30)]), HOURS[:3], 60, "month 2 day 30 is not a date"),
        (
            "Month,Day,Period,A\n1,1,inf,5\n",
            HOURS[:3],
            60,
            "Period 'inf' is not a whole number",
        ),
        (
            "Month,Day,Period,A\n1,-inf,1,5\n",
            HOURS[:3],
            60,
            "Day '-inf' is not a whole number",
        ),
        (
            "Month,Day,Period,A\n1,1,x,5\n",
            HOURS[:3],
            60,
            "Period 'x' is not a whole number",
        ),
    ],
)
def test_month_day_period_refusals(body, snaps, step, reason):
    """GK A C1: file step from periods per day, a finer and a coarser one refused."""
    assert series(body, snaps=snaps, step=step)["A"] == reason


def test_monthly_layout_partly_covered_snapshot_and_row_cap(monkeypatch):
    days = pd.date_range("2015-01-31 12:00", periods=2, freq="D")
    got = series(MONTHLY + "A,1,2,3,4,5,6,7,8,9,10,11,12\n", snaps=days, step=1440)
    assert got["A"] == (
        "pattern 'M1' covers snapshot 2015-01-31 12:00:00 only partly; not rounded"
    )
    body = MONTHLY + "A,1,2,3,4,5,6,7,8,9,10,11,12\nB,1,2,3,4,5,6,7,8,9,10,11,12\n"
    monkeypatch.setattr("gridspine.ingest.plexos_csv.MAX_CSV_ROWS", 2)
    assert series(body, snaps=JAN_FEB)["A"].tolist() == [1, 1, 2, 2]
    monkeypatch.setattr("gridspine.ingest.plexos_csv.MAX_CSV_ROWS", 1)
    assert series(body, snaps=JAN_FEB)["A"] == "more than 1 rows; not read"


def test_month_day_period_empty_and_period_zero():
    assert series("Month,Day,Period,A\n")["A"] == (
        "file has no value for 3 of 3 snapshots, first 2015-01-01 00:00:00"
    )
    assert series("Month,Day,Period,A\n1,1,0,5\n")["A"] == "period 0 is below 1"


def test_month_day_period_with_matching_half_hours():
    snaps = pd.date_range("2015-01-01", periods=3, freq="30min")
    assert series(_mdp([(1, 1)], periods=48), snaps=snaps, step=30)["A"].tolist() == [
        10101,
        10102,
        10103,
    ]


def test_pattern_layout():
    """Slice 3b: each row's pattern sets its value; equal overlaps are fine."""
    body = "Pattern,A,B\nH1-2,1,7\nH2-24,2,7\n"
    got = series(body, names=("A", "B"))
    assert (
        got["A"] == "row 3 gives 2 at 2015-01-01 01:00:00, an earlier row another value"
    )
    assert got["B"].tolist() == [7, 7, 7]
    body = "Pattern,A\nH1,1\nH2-24,2\n"
    assert series(body)["A"].tolist() == [1, 2, 2]


@pytest.mark.parametrize(
    "body, reason",
    [
        (
            "Pattern,A\nH1,1\n",
            "no pattern covers 2 of 3 snapshots, first 2015-01-01 01:00:00",
        ),
        ("Pattern,A\nH1-24,inf\n", "value 'inf' at row 2 is not a number"),
        ("Pattern,A\nH1-24,nan\n", "value 'nan' at row 2 is not a number"),
        (
            "Pattern,A\nY1,1\n",
            "row 2: pattern symbol 'Y' not read (read are M, W, D, H)",
        ),
    ],
)
def test_pattern_layout_refusals(body, reason):
    """GK A C2: gaps, non-finite cells and unreadable patterns leave the name unread."""
    assert series(body)["A"] == reason


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


def test_row_cap(monkeypatch):
    from gridspine.ingest import plexos_csv

    monkeypatch.setattr(plexos_csv, "MAX_CSV_ROWS", 2)
    body = "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,2\n01/01/2015 02:00,3\n"
    assert series(body)["A"] == "more than 2 rows; not read"


def test_header_without_a_needed_column_is_not_parsed():
    calls = []

    def opener():
        calls.append(1)
        return io.BytesIO(b"Datetime,B\n01/01/2015 00:00,x\n")

    assert read_series(opener, ["A"], HOURS[:1], 60, 2057) == {"A": "no column 'A'"}
    assert len(calls) == 1  # header only


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
    assert ("Node 'A' (Load)", "data file: no column 'A'") in got
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
    assert not su.cyclic_state_of_charge
    assert any(
        a.asset == "G" and a.parameter == "cyclic state of charge"
        for a in conv.report.assumed
    )
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
            "storage 'S' is linked to 2 generators: cascades are not converted yet",
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


def test_a_shared_storage_is_listed_once_under_its_first_reason():
    """Mutant survivor (2b): a storage already listed is not listed again."""
    conv = run(
        hydro_model(
            objects=[("Storage", "T"), ("Generator", "G2")],
            members=[
                (("Generator", "G"), ("Storage", "T"), "Tail Storage"),
                (("Generator", "G2"), ("Storage", "S"), "Head Storage"),
            ],
        )
    )
    assert unmapped_reason(conv, "Storage 'S'") == [
        "tail storage (pumped or cascaded hydro) is not converted yet"
    ]


@pytest.mark.parametrize("other", ["F", "H"])  # before and after 'G' by name
def test_head_of_one_generator_and_tail_of_another_is_a_cascade(other):
    """GK A (31) #1713 ask 1: head and tail links both count."""
    conv = run(
        hydro_model(
            objects=[("Generator", other)],
            members=[(("Generator", other), ("Storage", "S"), "Tail Storage")],
        )
    )
    assert conv.network.storage_units.empty
    assert unmapped_reason(conv, "Generator 'G'") == [
        "storage 'S' is linked to 2 generators: cascades are not converted yet"
    ]
    assert len(unmapped_reason(conv, "Storage 'S'")) == 1


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


def _no_scenarios(m):
    """The Europe Model with its scenarios off: the 2b base rows and files."""
    m.memberships = [
        x
        for x in m.memberships
        if not (x.parent[0] == "Model" and x.child[0] == "Scenario")
    ]
    return m


def run_files(m, files):
    m = _no_scenarios(m)
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


@pytest.mark.parametrize("packed", [False, True])
def test_over_cap_csv_is_unmapped_with_a_warning(tmp_path, monkeypatch, packed):
    """GK A T5: a file over the per-CSV cap is reported, not refused."""
    from gridspine.ingest import plexos_import

    monkeypatch.setattr(plexos_import, "MAX_CSV_BYTES", 100)
    monkeypatch.setattr(plexos_import, "_TOO_BIG", "is larger than 100 B; not read")
    csv = tmp_path / "All Demand UTC 2015.csv"
    csv.write_bytes(DEMAND.read_bytes())
    if packed:
        path = tmp_path / "m.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.write(IRL, "World.xml")
            zf.write(csv, "csv_files/" + csv.name)
        got = _read_input([path])
        name = "csv_files/" + csv.name
    else:
        got = _read_input([IRL, csv])
        name = csv.name
    assert got.data_files[name] == "is larger than 100 B; not read"
    conv = run_files(_two_days(got.xml), got.data_files)
    why = f"data file {name!r} is larger than 100 B; not read"
    assert unmapped_reason(conv, "Node 'EU-IRL'") == [f"data file: {why}"]
    assert f"{why}: 4 time series not imported" in conv.report.warnings


def test_edge_fixture_ledger_with_an_unreferenced_csv():
    """GK A T6 on the second fixture: the given CSV lands once, as unmapped."""
    m = read_plexos_xml(EDGE)
    conv = convert(m, {}, None, {"extra.csv": text("Datetime,A\n")})
    check_ledger(m, conv)
    assert ("data file extra.csv", "no imported property reads this file") in reasons(
        conv
    )


# --------------------------------------------- boundaries (mutant survivors)


def test_csv_cap_fits_the_largest_real_data_file():
    from gridspine.ingest import plexos_csv

    # PLEXOS-World's wind file is 216,981,296 bytes (Dataverse file 3985042)
    assert 216_981_296 < plexos_csv.MAX_CSV_BYTES <= 512 * 2**20


def test_row_cap_is_inclusive(monkeypatch):
    from gridspine.ingest import plexos_csv

    monkeypatch.setattr(plexos_csv, "MAX_CSV_ROWS", 3)
    body = "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,2\n01/01/2015 02:00,3\n"
    assert series(body)["A"].tolist() == [1, 2, 3]


def test_exact_column_wins_over_a_case_variant():
    body = "Datetime,GEN-A,Gen-a\n2015-01-01 00:00,1,2\n"
    assert series(body, names=("Gen-a",), snaps=HOURS[:1])["Gen-a"].tolist() == [2]


@pytest.mark.parametrize(
    "snaps, step",
    [(pd.RangeIndex(3), 60), (HOURS[:3], None), (HOURS[:3], 0)],
)
def test_no_datetime_snapshots_or_no_step_reads_nothing(snaps, step):
    got = read_series(text("Datetime,A\n"), ["A"], snaps, step)
    assert got == {"A": "the network has no time horizon"}


def test_snapshot_step_must_equal_the_horizon_step():
    body = "Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,2\n01/01/2015 02:00,3\n"
    assert (
        series(body, step=30)["A"] == "the snapshots have no single step; none inferred"
    )


@pytest.mark.parametrize(
    "dates, reason",
    [
        ("12/01/2015,12/02/2015", "no day is above 12"),
        ("01/12/2015,02/12/2015", "no day is above 12"),
    ],
)
def test_twelve_is_not_above_twelve(dates, reason):
    body = "Datetime,A\n" + "".join(f"{d},1\n" for d in dates.split(","))
    got = read_series(text(body), ["A"], HOURS[:1], 60, None)["A"]
    assert isinstance(got, str) and reason in got


def test_fractional_locale_is_no_locale():
    body = "Datetime,A\n01/02/2015 00:00,1\n"
    assert (
        "the Data File has no Locale"
        in series(body, snaps=HOURS[:1], locale=2057.5)["A"]
    )


@pytest.mark.parametrize(
    "header",
    ["NAME,A,B", "Object,M1,M2,M3,M4,M5,M6,M7,M8,M9,M10,M11,M12"],
)
def test_monthly_header_needs_name_and_all_months(header):
    assert series(header + "\n")["A"].startswith("layout not read")


@pytest.mark.parametrize(
    "row, reason",
    [
        ("2015,1,1,1.5,1", "Period '1.5' is not a whole number"),
        ("2015,1,x,1,1", "Day 'x' is not a whole number"),
        ("2015,1,1,0,1", "period 0 outside 1..24 for this horizon"),
        ("2015,1,1,25,1", "period 25 outside 1..24 for this horizon"),
        ("2015,2,30,1,1", "a Year/Month/Day is not a date"),
    ],
)
def test_period_layout_refusals(row, reason):
    got = series("Year,Month,Day,Period,A\n" + row + "\n", snaps=HOURS[:1])["A"]
    assert got == reason


@pytest.mark.parametrize(
    "rows, reason",
    [
        (["2015,1,1,1,1", "2015,1,1,0,1"], "period 0 outside 1..24 for this horizon"),
        (
            ["2015,1,1,24,1", "2015,1,1,25,1"],
            "period 25 outside 1..24 for this horizon",
        ),
    ],
)
def test_period_refusal_names_the_bad_period_not_a_valid_edge(rows, reason):
    body = "Year,Month,Day,Period,A\n" + "\n".join(rows) + "\n"
    assert series(body, snaps=HOURS[:1])["A"] == reason


def test_names_may_be_a_one_shot_iterator_with_repeats():
    """GK A (31) #1713 ask 2: names are read once, deduplicated."""
    got = series(
        "Datetime,A,B\n01/01/2015 00:00,1,2\n", names=iter("ABA"), snaps=HOURS[:1]
    )
    assert sorted(got) == ["A", "B"]
    assert (got["A"].iloc[0], got["B"].iloc[0]) == (1.0, 2.0)


def test_irregular_snapshots_are_refused_even_when_the_first_step_fits():
    snaps = HOURS[[0, 1, 3]]
    got = series("Datetime,A\n01/01/2015 00:00,1\n", snaps=snaps)["A"]
    assert got == "the snapshots have no single step; none inferred"


def test_last_period_of_the_day():
    body = "Year,Month,Day,Period,A\n2015,1,1,24,5\n"
    snaps = pd.DatetimeIndex(["2015-01-01 23:00"])
    assert series(body, snaps=snaps)["A"].tolist() == [5]


# ------------------------------------ plexos_import survivors (input, mapping)


def _zip_of(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for arc, data in members.items():
            zf.writestr(arc, data)
    return path


def test_csv_exactly_at_the_cap_is_read(tmp_path, monkeypatch):
    from gridspine.ingest import plexos_import

    body = b"Datetime,A\n"
    monkeypatch.setattr(plexos_import, "MAX_CSV_BYTES", len(body))
    csv = tmp_path / "a.csv"
    csv.write_bytes(body)
    with _read_input([IRL, csv]).data_files["a.csv"]() as fh:
        assert fh.read() == body
    path = _zip_of(tmp_path / "m.zip", {"m.xml": b"<a/>", "csv/a.csv": body})
    assert _read_input([path]).data_files["csv/a.csv"]().read() == body


def test_member_cap_is_checked_again_while_reading(tmp_path, monkeypatch):
    from gridspine.ingest import plexos_import

    path = _zip_of(tmp_path / "m.zip", {"m.xml": b"<a/>", "x.csv": b"1" * 10})
    opener = _read_input([path]).data_files["x.csv"]
    monkeypatch.setattr(plexos_import, "MAX_MEMBER_BYTES", 10)
    assert opener().read() == b"1" * 10
    monkeypatch.setattr(plexos_import, "MAX_MEMBER_BYTES", 9)
    with pytest.raises(
        ImportFormatError, match="m.zip refused: 'x.csv' unpacks larger than it states"
    ):
        opener()


def test_loose_csv_refusals(tmp_path):
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.csv").write_bytes(b"1")
    with pytest.raises(ImportFormatError, match="two data files are named x.csv"):
        _read_input([IRL, tmp_path / "a" / "x.csv", tmp_path / "b" / "x.csv"])
    with pytest.raises(ImportFormatError, match="gone.csv cannot be read"):
        _read_input([IRL, tmp_path / "gone.csv"])
    path = _zip_of(tmp_path / "m.zip", {"m.xml": b"<a/>", "x.csv": b"2"})
    with pytest.raises(
        ImportFormatError, match="x.csv is both in m.zip and given separately"
    ):
        _read_input([path, tmp_path / "a" / "x.csv"])


def test_row_level_data_file_text_is_the_path():
    text_row = {"texts": [("Data File", "csv_files\\load.csv")]}
    m = model(rows=[("Node", "A", "Load", 9, "MW", text_row)], files=())
    iso = "Datetime,A\n" + "".join(f"{t:%Y-%m-%d %H:%M},7\n" for t in HOURS)
    conv = run(m, {"load.csv": iso.encode()})  # no Data File, so no Locale
    assert conv.network.loads_t.p_set["A"].tolist() == [7.0] * 48
    assert conv.ledger.dest[("t_data", "2")] == "network"
    # the 9 MW is the file's placeholder, not a static load (GK A Q3)
    assert sum(conv.load_mw.values()) == 0


@pytest.mark.parametrize(
    "texts",
    [
        None,
        [],
        [("Data File", "a.csv"), ("Data File", "load.csv")],
        [("Data File", " ")],
    ],
)
def test_data_file_without_one_file_name(texts):
    m = model(rows=[("Node", "A", "Load", 0, "MW", filed())])
    m.rows = [
        dataclasses.replace(r, texts=texts) if r.property == "Filename" else r
        for r in m.rows
        if texts is not None or r.property != "Filename"
    ]
    conv = run(m, {"load.csv": hourly({"A": [1]})})
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == [
        "data file: Data File 'F' gives no file name"
    ]
    assert unmapped_reason(conv, "Data File 'F'") == [
        "no time series was read from this file"
    ]
    assert conv.ledger.dest[("t_attribute_data", "Data File|F|Locale")] == "unmapped"


def test_data_file_attributes_land_by_whether_the_file_was_read():
    rows = [("Node", "A", "Load", 0, "MW", filed())]
    other = [("Data File", "F", "Decimal Places", 2.0)]
    conv = run(model(rows=rows, attrs=other), {"load.csv": hourly({"A": [1]})})
    dest = conv.ledger.dest
    assert dest[("t_attribute_data", "Data File|F|Locale")] == "network"
    assert dest[("t_attribute_data", "Data File|F|Decimal Places")] == "unmapped"
    assert unmapped_reason(conv, "Data File 'F'") == ["attribute not imported yet"]
    conv = run(model(rows=rows, attrs=other), {"other.csv": hourly({"A": [1]})})
    assert conv.ledger.dest[("t_attribute_data", "Data File|F|Locale")] == "unmapped"
    assert sorted(unmapped_reason(conv, "Data File 'F'")) == [
        "attribute not imported yet",
        "no time series was read from this file",
    ]


def test_two_static_rows_for_one_property_are_both_refused():
    rows = [("Node", "A", "Load", 5, "MW"), ("Node", "A", "Load", 6, "MW")]
    conv = run(model(rows=rows))
    assert conv.network.loads.empty
    assert unmapped_reason(conv, "Node 'A'") == ["2 base values for 'Load'; none used"]
    two = [
        ("Node", "A", "Load", 0, "MW", filed()),
        ("Node", "A", "Load", 0, "MW", filed()),
    ]
    conv = run(model(rows=two), {"load.csv": hourly({"A": [1]})})
    assert unmapped_reason(conv, "Node 'A'") == ["2 base values for 'Load'; none used"]


def test_filed_load_placeholder_is_not_tallied():
    m = model(rows=[("Node", "A", "Load", 9, "MW", filed())])
    conv = run(m, {"load.csv": hourly({"A": [1]})})
    assert conv.network.loads_t.p_set["A"].sum() == 48
    assert sum(conv.load_mw.values()) == 0


def _fuel_price(*price):
    objects = [("Generator", "G"), ("Fuel", "Gas")]
    rows = [
        ("Generator", "G", "Units", 1, "-"),
        ("Generator", "G", "Max Capacity", 50, "MW"),
        ("Generator", "G", "Heat Rate", 10, "GJ/MWh"),
        *price,
    ]
    members = [
        (("Generator", "G"), ("Node", "A"), "Nodes"),
        (("Generator", "G"), ("Fuel", "Gas"), "Fuels"),
    ]
    return run(model(objects, rows, members), {"load.csv": hourly({"Gas": [3]})})


def test_fuel_price_unit_and_absence():
    conv = _fuel_price(("Fuel", "Gas", "Price", 0, "$/MWh", filed()))
    assert unmapped_reason(conv, "Fuel 'Gas'") == [
        "unit '$/MWh' for 'Price' is not converted (expects $/GJ)"
    ]
    assert conv.network.generators_t.marginal_cost.empty
    conv = _fuel_price(("Fuel", "Gas", "Price", 4, "$/MWh"))
    assert unmapped_reason(conv, "Fuel 'Gas'") == [
        "unit '$/MWh' for 'Price' is not converted (expects $/GJ)"
    ]
    conv = _fuel_price(("Fuel", "Gas", "Price", 4, "$/GJ"))
    assert conv.network.generators.loc["G", "marginal_cost"] == 40
    assert conv.sidecars[FUEL_PRICES_SIDECAR]["fuels"][0]["source"] == "static Price"
    conv = _fuel_price()
    assert not unmapped_reason(conv, "Fuel 'Gas'")


def _rated(rf):
    rows = [
        ("Generator", "G", "Units", 1, "-"),
        ("Generator", "G", "Max Capacity", 50, "MW"),
        rf,
    ]
    members = [(("Generator", "G"), ("Node", "A"), "Nodes")]
    return run(
        model([("Generator", "G")], rows, members),
        {"load.csv": hourly({"G": [0, 100, 120]})},
    )


def test_rating_factor_unit_and_first_bad_cell():
    conv = _rated(("Generator", "G", "Rating Factor", 50, "-"))
    assert conv.network.generators.loc["G", "p_max_pu"] == 1.0
    assert unmapped_reason(conv, "Generator 'G'") == [
        "unit '-' for 'Rating Factor' is not converted (expects %)"
    ]
    conv = _rated(("Generator", "G", "Rating Factor", 0, "%", filed()))
    assert unmapped_reason(conv, "Generator 'G'") == [
        "Rating Factor 120 % at 2015-01-01 02:00:00 outside [0, 100] %"
    ]


def _hydro(store=None, gen=None, **kw):
    """hydro_model() with some of its rows replaced: {property: (unit, value)}."""
    m = hydro_model(**kw)
    for obj, change in (
        (("Storage", "S"), store or {}),
        (("Generator", "G"), gen or {}),
    ):
        m.rows = [
            dataclasses.replace(
                r, unit=change[r.property][0], value=change[r.property][1]
            )
            if r.child == obj and r.property in change
            else r
            for r in m.rows
            if not (r.child == obj and change.get(r.property, 0) is None)
        ]
    return m


@pytest.mark.parametrize("gwh, mwh", [(0.0, 0.0), (2.0, 2000.0)])
def test_initial_volume_bounds_are_inclusive(gwh, mwh):
    conv = run(_hydro({"Initial Volume": ("GWh", gwh)}))
    assert conv.network.storage_units.loc["G", "state_of_charge_initial"] == mwh
    assert not unmapped_reason(conv, "Storage 'S'")


@pytest.mark.parametrize(
    "store, gen, reason",
    [
        ({"Max Volume": ("GWh", 0.0)}, {}, "Max Volume unlimited or not above 0"),
        ({"Max Volume": ("GWh", 1e29)}, {}, "Max Volume unlimited or not above 0"),
        ({}, {"Max Capacity": ("MW", 0.0)}, "no usable Max Capacity (MW, above 0)"),
        ({}, {"Max Capacity": ("kW", 100.0)}, "no usable Max Capacity (MW, above 0)"),
        ({}, {"Max Capacity": None}, "no usable Max Capacity (MW, above 0)"),
    ],
)
def test_hydro_capacity_and_volume_refused(store, gen, reason):
    conv = run(_hydro(store, gen))
    assert conv.network.storage_units.empty
    assert reason in unmapped_reason(conv, "Storage 'S'")
    assert reason in unmapped_reason(conv, "Generator 'G'")


def test_hydro_min_volume_zero_used_otherwise_unmapped():
    rows = [("Storage", "S", "Min Volume", 0, "GWh")]
    conv = run(hydro_model(store_rows=rows))
    assert not unmapped_reason(conv, "Storage 'S'")
    rows = [("Storage", "S", "Min Volume", 0.1, "GWh")]
    conv = run(hydro_model(store_rows=rows))
    assert unmapped_reason(conv, "Storage 'S'") == [
        "minimum volume is not imported yet"
    ]


def test_hydro_inflow_in_another_unit_unmapped():
    rows = [("Storage", "S", "Natural Inflow", 7, "GWh")]
    conv = run(hydro_model(store_rows=rows))
    assert conv.network.storage_units.loc["G", "inflow"] == 0
    assert unmapped_reason(conv, "Storage 'S'") == [
        "unit 'GWh' for 'Natural Inflow' is not converted (expects MW)"
    ]


def test_hydro_p_nom_is_capacity_times_units_and_carrier_falls_back():
    m = _hydro(gen={"Units": ("-", 2.0)})
    m.objects[("Generator", "G")] = dataclasses.replace(
        m.objects[("Generator", "G")], category=None
    )
    su = run(m).network.storage_units.loc["G"]
    assert (su.p_nom, su.max_hours, su.carrier) == (200.0, 10.0, "hydro")


def test_storage_as_waterway_parent_is_a_cascade():
    conv = run(
        hydro_model(
            objects=[("Waterway", "W")],
            members=[(("Storage", "S"), ("Waterway", "W"), "Waterways")],
        )
    )
    assert conv.network.storage_units.empty
    reason = "storage on a waterway: cascades are not converted yet"
    assert unmapped_reason(conv, "Storage 'S'") == [reason]
    assert unmapped_reason(conv, "Generator 'G'") == [reason]


def test_hydro_generator_without_node_or_units_lists_its_storage():
    m = hydro_model()
    m.memberships = [x for x in m.memberships if x.collection[2] != "Nodes"]
    conv = run(m)
    assert unmapped_reason(conv, "Storage 'S'") == ["generator needs exactly one node"]
    assert unmapped_reason(conv, "Generator 'G'") == [
        "needs exactly one node (no node)"
    ]
    conv = run(_hydro(gen={"Units": ("-", 0.0)}))
    assert unmapped_reason(conv, "Storage 'S'") == ["generator not built"]
    assert unmapped_reason(conv, "Generator 'G'") == ["0 units: not built"]
    conv = run(_hydro({"Units": ("-", 0.0)}))
    assert unmapped_reason(conv, "Generator 'G'") == ["storage not built"]
    assert sorted(unmapped_reason(conv, "Storage 'S'")) == [
        "0 units: not built",
        "storage not built",
    ]


def test_cascade_lists_objects_without_rows():
    m = model(
        [("Generator", "G"), ("Storage", "S"), ("Storage", "T")],
        [],
        [
            (("Generator", "G"), ("Node", "A"), "Nodes"),
            (("Generator", "G"), ("Storage", "S"), "Head Storage"),
            (("Generator", "G"), ("Storage", "T"), "Tail Storage"),
        ],
    )
    conv = run(m)
    reason = "tail storage (pumped or cascaded hydro) is not converted yet"
    for item in ("Generator 'G'", "Storage 'S'", "Storage 'T'"):
        assert unmapped_reason(conv, item) == [reason]


def test_leftover_rows_say_why_by_kind():
    rows = [
        ("Generator", "G", "Pump Load", 10, "MW"),
        ("Generator", "G", "Aux Base", 1, "MW"),
        ("Generator", "G", "Aux Incr", 0, "%", filed()),
    ]
    conv = run(hydro_model(gen_rows=rows), {"load.csv": hourly({"G": [1]})})
    assert sorted(r for r in reasons(conv) if r[0].startswith("Generator")) == [
        ("Generator 'G' (Aux Base)", "property not imported yet"),
        ("Generator 'G' (Aux Incr)", "time series for 'Aux Incr' is not imported yet"),
        ("Generator 'G' (Pump Load)", "pumping is not imported yet"),
    ]


def test_convert_without_options_or_files():
    m = hydro_model()
    conv = convert(m)
    check_ledger(m, conv)
    assert conv.network.storage_units.loc["G", "p_nom"] == 100


# ------------------------------------------------------------ review fixes


@pytest.mark.parametrize("cell", ["inf", "-INF", "Infinity"])
def test_infinite_cells_are_unreadable(cell):
    body = (
        f"Datetime,A\n01/01/2015 00:00,1\n01/01/2015 01:00,{cell}\n01/01/2015 02:00,3\n"
    )
    assert series(body)["A"] == f"value {cell!r} at 2015-01-01 01:00:00 is not a number"


def test_nan_locale_is_no_locale():
    body = "Datetime,A\n01/02/2015 00:00,1\n"
    got = series(body, snaps=HOURS[:1], locale=float("nan"))["A"]
    assert "the Data File has no Locale" in got


def test_storage_units_from_the_file_default():
    m = hydro_model()
    m.rows = [
        r for r in m.rows if not (r.child == ("Storage", "S") and r.property == "Units")
    ]
    m.defaults = {(("System", "Storage", "Storages"), "Units"): 1.0}
    conv = run(m)
    assert conv.network.storage_units.loc["G", "p_nom"] == 100.0
    assert any(a.asset == "S" and a.parameter == "units" for a in conv.report.assumed)
    m.defaults = {}
    conv = run(m)
    assert conv.network.storage_units.empty
    assert any("unit count unknown" in r for r in unmapped_reason(conv, "Storage 'S'"))


def test_non_energy_hydro_model_is_not_converted():
    m = hydro_model()
    m.config = {"Hydro Model": "Volume"}
    conv = run(m)
    assert conv.network.storage_units.empty and conv.network.generators.empty
    reason = (
        "hydro model 'Volume': only the energy model "
        "(t_config Hydro Model = Energy) is converted"
    )
    assert reason in unmapped_reason(conv, "Generator 'G'")
    assert reason in unmapped_reason(conv, "Storage 'S'")
