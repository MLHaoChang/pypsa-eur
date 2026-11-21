"""
The Excel input workbook (IMPORTS-1): round trip, untrusted hidden rows, limits.

GK B plan conditions E1–E8 (.scratch/excel-input-workbook/issues/01-reader-writer.md).
"""

from __future__ import annotations

import datetime
import json
import math
import pathlib
import random
import warnings

import openpyxl
import pandas as pd
import pypsa
import pytest

from gridspine.ingest import excel_layout as L
from gridspine.ingest.excel_export import write_workbook
from gridspine.ingest.excel_import import read_excel

warnings.filterwarnings("ignore", category=DeprecationWarning)

C = L.Column
#: A small catalogue in the app's shape (services/asset_schema/fields.py): plain names,
#: display units and factors, keyed by model name.
CATALOGUE = {
    "Carrier": [
        C("name", "Name", kind="text"),
        C("co2_emissions", "CO2 emissions", "t/MWh"),
    ],
    "Bus": [
        C("name", "Name", kind="text"),
        C("v_nom", "Nominal voltage", "kV"),
        C("carrier", "Carrier", kind="carrier"),
        C("unit", "Unit", kind="text"),
        C("x", "Longitude", "°E"),
        C("y", "Latitude", "°N"),
    ],
    "Generator": [
        C("name", "Name", kind="text"),
        C("bus", "Bus", kind="bus"),
        C("carrier", "Carrier", kind="carrier"),
        C("p_nom", "Capacity", "MW"),
        C("p_nom_extendable", "Extendable", kind="bool"),
        C("p_nom_max", "Maximum capacity", "MW"),
        C("capital_cost", "Annualised investment", "EUR/kW/yr", 1e-3),
        C("marginal_cost", "Variable cost", "EUR/MWh"),
        C("efficiency", "Efficiency", "%", 100.0),
        C("build_year", "Build year", "year", kind="int"),
        C("control", "Control", kind="enum", options=("PQ", "PV", "Slack")),
    ],
    "Load": [
        C("name", "Name", kind="text"),
        C("bus", "Bus", kind="bus"),
        C("p_set", "Demand", "MW"),
    ],
    "Line": [
        C("name", "Name", kind="text"),
        C("bus0", "From", kind="bus"),
        C("bus1", "To", kind="bus"),
        C("s_nom", "Capacity", "MVA"),
        C("x", "Reactance", "Ω"),
        C("r", "Resistance", "Ω"),
    ],
    "StorageUnit": [
        C("name", "Name", kind="text"),
        C("bus", "Bus", kind="bus"),
        C("p_nom", "Power", "MW"),
        C("max_hours", "Hours of storage", "h"),
    ],
    "Store": [
        C("name", "Name", kind="text"),
        C("bus", "Bus", kind="bus"),
        C("e_nom", "Energy", "MWh"),
    ],
    "Link": [
        C("name", "Name", kind="text"),
        C("bus0", "Input", kind="bus"),
        C("bus1", "Output", kind="bus"),
        C("p_nom", "Capacity", "MW"),
        C("efficiency", "Efficiency", "%", 100.0),
    ],
}
CUSTOM = {"Generator": ["eh_flag", "capex_part_eur_per_kw"], "Bus": ["eh_sk_mva"]}
SOLVER = {
    "discount_rate": 0.07,
    "multi_investment_periods": True,
    "investment_periods": [2030, 2040],
}


def _network(multi: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.name = "Fixture"
    steps = pd.date_range("2030-01-01", periods=3, freq="h")
    if multi:
        n.set_snapshots(
            pd.MultiIndex.from_product(
                [[2030, 2040], steps], names=["period", "timestep"]
            )
        )
        n.investment_period_weightings.loc[:, "objective"] = [1.0, 0.5]
        n.investment_period_weightings.loc[:, "years"] = [10.0, 10.0]
    else:
        n.set_snapshots(steps)
    n.snapshot_weightings.loc[:, "objective"] = 2.0
    n.snapshot_weightings.loc[:, "stores"] = 3.0
    k = len(n.snapshots)
    n.add("Carrier", ["AC", "wind", "H2"], co2_emissions=[0.0, 0.0, 0.0])
    n.add(
        "Bus",
        ["a", "b"],
        v_nom=[110.0, 110.0],
        carrier="AC",
        x=[8.5, 9.25],
        y=[50.1, 50.2],
    )
    n.add("Bus", "=h2 site", unit="t", carrier="H2")
    n.buses["eh_sk_mva"] = [250.0, None, None]
    n.add("Line", "l1", bus0="a", bus1="b", x=0.1, r=0.01, s_nom=100.0)
    n.add("Transformer", "t1", bus0="a", bus1="b", x=0.05, s_nom=40.0)
    n.add(
        "Generator",
        "wind 1",
        bus="a",
        carrier="wind",
        p_nom=50.0,
        p_nom_extendable=True,
        capital_cost=123456.789,
        marginal_cost=0.1,
        efficiency=0.37,
        build_year=2030,
        p_max_pu=[0.1 * (i + 1) for i in range(k)],
    )
    n.add(
        "Generator",
        "electrolyser out",
        bus="=h2 site",
        p_nom=5.0,
        p_nom_max=float("inf"),
    )
    n.generators["eh_flag"] = [True, False]
    n.generators["capex_part_eur_per_kw"] = [900.5, None]
    n.add("Load", "d", bus="b", p_set=[10.0 + i for i in range(k)])
    n.add(
        "StorageUnit", "bat", bus="a", p_nom=10.0, max_hours=4.0, efficiency_store=0.95
    )
    n.add("Store", "tank", bus="=h2 site", e_nom=30.0)
    n.add("Link", "elec", bus0="b", bus1="=h2 site", p_nom=20.0, efficiency=0.0189)
    n.add("ShuntImpedance", "sh", bus="a", g=0.01)
    n.add(
        "GlobalConstraint",
        "co2",
        sense="<=",
        constant=1e6,
        carrier_attribute="co2_emissions",
    )
    return n


def _user_ts(n: pypsa.Network) -> dict:
    """One uploaded profile that is the sheet's series, one with its own time steps, provenance."""
    s = n.loads_t.p_set["d"]
    idx = (
        [[int(p), t.isoformat()] for p, t in n.snapshots]
        if isinstance(n.snapshots, pd.MultiIndex)
        else [t.isoformat() for t in n.snapshots]
    )
    on_sheet = {"index": idx, "values": s.tolist()}
    if not isinstance(n.snapshots, pd.MultiIndex):
        on_sheet["index_name"] = "snapshot"
    return {
        "loads": {"p_set": {"d": on_sheet}},
        "generators": {
            "p_max_pu": {
                "other": {
                    "index": ["2030-06-01T00:00:00", "2030-06-01T01:00:00"],
                    "values": [0.5, None],
                }
            }
        },
        "$meta": {
            "loads": {"p_set": {"d": {"provenance": "upload", "source": "d.csv"}}}
        },
    }


def _write(n, tmp_path, *, template=False, sidecars=None, name="w.xlsx"):
    data = write_workbook(
        n,
        columns=CATALOGUE,
        sidecars=sidecars,
        template=template,
        custom_columns=CUSTOM,
    )
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _read(path):
    return read_excel(path, {"custom_columns": CUSTOM, "columns": CATALOGUE})


def _um(res) -> list[tuple[str, str]]:
    return [(u.source_item, u.reason) for u in res.report.unmapped]


def _am(res) -> list[tuple]:
    return [(a.asset, a.parameter, a.value, a.why) for a in res.report.assumed]


def _assert_same(
    a: pypsa.Network, b: pypsa.Network, *, weight_dtype: bool = True
) -> None:
    """Bit for bit: every float compares with ==, no tolerance (GK B ask 1, X6)."""
    pd.testing.assert_index_equal(a.snapshots, b.snapshots)
    pd.testing.assert_frame_equal(
        a.snapshot_weightings,
        b.snapshot_weightings,
        check_freq=False,
        check_dtype=weight_dtype,
        check_exact=True,
    )
    pd.testing.assert_frame_equal(
        a.investment_period_weightings,
        b.investment_period_weightings,
        check_dtype=False,
        check_index_type=False,
        check_exact=True,
    )
    for _, _, ln in L.SHEETS:
        x, y = getattr(a, ln), getattr(b, ln)
        assert list(x.index) == list(y.index), ln
        pd.testing.assert_frame_equal(
            x, y.reindex(columns=x.columns), check_dtype=False, check_exact=True, obj=ln
        )
        for attr, frame in getattr(a, f"{ln}_t").items():
            if frame.empty:
                continue
            pd.testing.assert_frame_equal(
                frame,
                getattr(b, f"{ln}_t")[attr],
                check_freq=False,
                check_dtype=False,
                check_exact=True,
                obj=f"{ln}_t.{attr}",
            )


# ── round trip (the contract) ────────────────────────────────────────────────


@pytest.mark.parametrize("multi", [False, True])
def test_export_then_import_gives_the_same_network_and_sidecars(tmp_path, multi):
    n = _network(multi)
    sidecars = {
        "user_ts.json": _user_ts(n),
        "solver_config.json": SOLVER
        if multi
        else {**SOLVER, "multi_investment_periods": False, "investment_periods": []},
    }
    res = _read(_write(n, tmp_path, sidecars=sidecars))
    assert _um(res) == []
    assert res.report.warnings == []
    _assert_same(n, res.network)
    assert res.sidecars == sidecars
    # Hand-checked counts, in plain sheet names.
    assert res.report.counts == {
        "Carriers": 3,
        "Nodes": 3,
        "Lines": 1,
        "Transformers": 1,
        "Generators": 2,
        "Storage": 1,
        "Stores": 1,
        "Loads": 1,
        "Links": 1,
        "Shunts": 1,
        "System limits": 1,
        "Time series": 2,
    }


def test_a_money_per_kw_value_reads_back_as_the_same_float(tmp_path):
    n = _network(False)
    path = _write(n, tmp_path)
    ws = openpyxl.load_workbook(path)["Generators"]
    col = [c.value for c in ws[L.ROW_KEY]].index("capital_cost") + 1
    assert ws.cell(L.ROW_UNIT, col).value == "EUR/kW/yr"
    assert ws.cell(L.FIRST_DATA_ROW, col).value == 123.456789
    assert _read(path).network.generators.at["wind 1", "capital_cost"] == 123456.789


def test_a_custom_column_shown_per_kw_reads_back_per_mw(tmp_path):
    """An investment part (a custom column) the catalogue shows in EUR/kW is stored per MW again (S2)."""
    n = _network(False)
    n.generators["capex_part_eur_per_kw"] = 250_000.0
    # A text or TRUE/FALSE cell in a scaled custom column is kept as the cell holds it.
    n.generators["part_note"] = "n/a"
    n.generators["part_on"] = True
    cat = {
        **CATALOGUE,
        "Generator": [
            *CATALOGUE["Generator"],
            C("capex_part_eur_per_kw", "Capex part", "EUR/kW", 1e-3),
            C("part_note", "Part note", "EUR/kW", 1e-3),
            C("part_on", "Part on", "EUR/kW", 1e-3),
        ],
    }
    custom = {**CUSTOM, "Generator": [*CUSTOM["Generator"], "part_note", "part_on"]}
    data = write_workbook(n, columns=cat, custom_columns=custom)
    path = tmp_path / "w.xlsx"
    path.write_bytes(data)
    ws = openpyxl.load_workbook(path)["Generators"]
    col = [c.value for c in ws[L.ROW_KEY]].index("capex_part_eur_per_kw") + 1
    assert ws.cell(L.FIRST_DATA_ROW, col).value == 250.0
    res = read_excel(path, {"custom_columns": custom, "columns": cat})
    gens = res.network.generators
    assert gens["capex_part_eur_per_kw"].tolist() == [250_000.0] * len(n.generators)
    assert gens["part_note"].tolist() == ["n/a"] * len(n.generators)
    assert [v is True for v in gens["part_on"]] == [True] * len(n.generators)


def test_a_size_on_a_tonne_node_is_shown_and_read_in_tonnes_per_hour(tmp_path):
    """E4: the size unit is the asset's node unit, not the catalogue's MW; the value is unchanged."""
    n = _network(True)
    path = _write(n, tmp_path)
    ws = openpyxl.load_workbook(path)["Generators"]
    keys = [c.value for c in ws[L.ROW_KEY]]
    unit_col = keys.index("~size_unit") + 1
    rows = {
        ws.cell(r, 1).value: ws.cell(r, unit_col).value
        for r in range(L.FIRST_DATA_ROW, ws.max_row + 1)
    }
    assert rows == {"wind 1": "MW", "electrolyser out": "t/h"}
    res = _read(path)
    assert res.network.generators.at["electrolyser out", "p_nom"] == 5.0
    assert res.report.warnings == []


def test_a_size_unit_that_disagrees_with_its_node_is_a_warning(tmp_path):
    path = _write(_network(False), tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Generators"]
    unit_col = [c.value for c in ws[L.ROW_KEY]].index("~size_unit") + 1
    ws.cell(L.FIRST_DATA_ROW, unit_col).value = "t/h"  # wind 1 sits on an energy node
    wb.save(path)
    assert any("wind 1" in w and "MW" in w for w in _read(path).report.warnings)


def test_snapshots_keep_their_times_without_a_timezone_shift(tmp_path):
    """E5."""
    n = _network(False)
    n.set_snapshots(
        pd.DatetimeIndex(
            ["2030-03-31T00:30:00", "2030-03-31T01:30:00", "2030-03-31T02:30:00"]
        )
    )
    res = _read(_write(n, tmp_path))
    assert list(res.network.snapshots) == list(n.snapshots)


def test_text_and_number_snapshots_round_trip(tmp_path):
    n = pypsa.Network()
    n.add("Bus", "a")
    assert list(_read(_write(n, tmp_path)).network.snapshots) == ["now"]
    n.set_snapshots(range(4))
    assert list(_read(_write(n, tmp_path, name="b.xlsx")).network.snapshots) == [
        0,
        1,
        2,
        3,
    ]


# ── the template ─────────────────────────────────────────────────────────────


def test_the_template_shows_plain_names_and_units_and_hides_the_model_names(tmp_path):
    path = _write(_network(False), tmp_path, template=True)
    wb = openpyxl.load_workbook(path)
    assert wb.sheetnames[0] == "About"
    assert wb["Lists"].sheet_state == "hidden"
    ws = wb["Generators"]
    assert [c.value for c in ws[L.ROW_LABEL]][:4] == [
        "Name",
        "Bus",
        "Carrier",
        "Capacity",
    ]
    assert [c.value for c in ws[L.ROW_UNIT]][3] == "MW"
    assert ws.row_dimensions[L.ROW_KEY].hidden and ws.row_dimensions[L.ROW_SCALE].hidden
    assert ws.max_row == L.ROW_SCALE  # no rows
    labels = {
        c.value for row in wb["Generators"].iter_rows(max_row=L.ROW_UNIT) for c in row
    }
    assert not labels & {"p_nom", "capital_cost", "bus"}  # RAW-NAMES-1
    kinds = {dv.formula1 for dv in ws.data_validations.dataValidation}
    assert '"TRUE,FALSE"' in kinds and '"PQ,PV,Slack"' in kinds
    assert any(f.startswith("'Lists'!$A$2") for f in kinds)
    about = [c.value for row in wb["About"].iter_rows() for c in row if c.value]
    for item in L.NOT_CARRIED:
        assert f"• {item}" in about


def test_a_template_filled_in_imports(tmp_path):
    path = _write(_network(False), tmp_path, template=True)
    wb = openpyxl.load_workbook(path)
    wb["Nodes"].append(["n1", 20])
    wb["Generators"].append(["g1", "n1", None, 7.5, "TRUE", None, 50])
    for j, v in enumerate(["2031-01-01T00:00:00", 1, 1, 1], start=1):
        wb["Time series"].cell(L.TS_FIRST_DATA_ROW, j).value = v
    wb.save(path)
    res = _read(path)
    assert _um(res) == []
    n = res.network
    assert (
        n.generators.at["g1", "p_nom"] == 7.5
        and n.generators.at["g1", "p_nom_extendable"]
    )
    assert n.generators.at["g1", "capital_cost"] == 50000.0
    assert ("g1", "Variable cost", 0.0, "left empty: the model default") in _am(res)
    assert list(n.snapshots) == [pd.Timestamp("2031-01-01")]


# ── untrusted hidden rows (E1) ───────────────────────────────────────────────


def _tamper(path, sheet, row, key, value):
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet]
    keys = (
        [c.value for c in ws[L.ROW_KEY]]
        if sheet != L.TIME_SERIES
        else [c.value for c in ws[L.TS_ROW_ASSET]]
    )
    ws.cell(row, keys.index(key) + 1).value = value
    wb.save(path)


@pytest.mark.parametrize(
    "raw", ["p_nom_opt", "__class__", "not_a_column", "eh_unlisted"]
)
def test_a_hidden_model_name_not_on_the_allow_list_is_reported_and_never_set(
    tmp_path, raw
):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_KEY, "marginal_cost", raw)
    res = _read(path)
    assert (
        "Generators · Variable cost",
        f"its hidden model name “{raw}” is not one Generators reads; the column was not read",
    ) in _um(res)
    assert raw not in res.network.generators.columns or raw == "p_nom_opt"
    assert res.network.generators.at["wind 1", "marginal_cost"] == 0.0
    if raw == "p_nom_opt":
        assert (res.network.generators["p_nom_opt"] == 0.0).all()


def test_a_broken_workbook_s_report_uses_no_straight_quotes_or_link_marks(tmp_path):
    """Issue 07 (UX 27 on S3): the report quotes in “…” and separates with "·"; "›" is for links only."""
    import re

    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_KEY, "marginal_cost", "p_nom_opt")
    _tamper(path, "Generators", L.ROW_SCALE, "capital_cost", 2.0)
    _tamper(path, "Generators", L.FIRST_DATA_ROW, "p_nom", "lots")
    res = _read(path)
    rows = _um(res)
    assert len(rows) >= 3
    for item, reason in rows:
        for text in (item, reason):
            assert "›" not in text, text
            assert not re.search(r"(^|\s)'\S", text), text
    assert (
        "Generators · Variable cost",
        "its hidden model name “p_nom_opt” is not one Generators reads; the column was not read",
    ) in rows


@pytest.mark.parametrize(
    "scale", [1.0, 0.0, -1e-3, float("nan"), float("inf"), "x", True, None]
)
def test_a_hidden_factor_that_does_not_match_its_unit_is_reported(tmp_path, scale):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_SCALE, "capital_cost", scale)
    res = _read(path)
    assert (
        "Generators · Annualised investment",
        "the hidden unit factor (row 4) does not match the unit “EUR/kW/yr”",
    ) in _um(res)
    assert res.network.generators.at["wind 1", "capital_cost"] == 0.0


def test_a_unit_changed_without_its_factor_is_reported(tmp_path):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_UNIT, "capital_cost", "EUR/MW/yr")
    assert any(
        item == "Generators · Annualised investment" for item, _ in _um(_read(path))
    )


def test_tampered_time_series_rows_are_reported(tmp_path):
    n = _network(False)
    path = _write(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb[L.TIME_SERIES]
    assets = [c.value for c in ws[L.TS_ROW_ASSET]]
    load_col = assets.index("d") + 1
    gen_col = assets.index("wind 1") + 1
    ws.cell(L.TS_ROW_ATTR, load_col).value = "p_nom"  # not a series
    ws.cell(L.TS_ROW_CLASS, gen_col).value = "Network"  # not an asset type
    wb.save(path)
    res = _read(path)
    reasons = dict(_um(res))
    assert (
        reasons["Time series · d Demand"]
        == "“p_nom” is not a parameter that can change over time"
    )
    assert reasons["Time series · wind 1 P max pu"] == "“Network” is not an asset type"
    assert res.network.loads_t.p_set.empty


def test_a_series_for_an_asset_that_does_not_exist_is_reported(tmp_path):
    path = _write(_network(False), tmp_path)
    _tamper(path, L.TIME_SERIES, L.TS_ROW_ASSET, "d", "ghost")
    assert ("Time series · ghost · p_set", "no Loads row is named “ghost”") in _um(
        _read(path)
    )


def test_an_unreadable_upload_mark_keeps_the_series_and_warns(tmp_path):
    n = _network(False)
    path = _write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)})
    wb = openpyxl.load_workbook(path)
    ws = wb[L.TIME_SERIES]
    col = [c.value for c in ws[L.TS_ROW_ASSET]].index("d") + 1
    ws.cell(L.TS_ROW_USER, col).value = "{not json"
    wb.save(path)
    res = _read(path)
    assert "loads" not in res.sidecars["user_ts.json"]
    assert any("upload mark" in w for w in res.report.warnings)
    assert res.network.loads_t.p_set["d"].tolist() == [10.0, 11.0, 12.0]


# ── values (E3) ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("p_nom", "nan", "not a number (NaN)"),
        ("p_nom", "inf", "infinity is not allowed here"),
        ("p_nom", True, "a TRUE/FALSE value in a number column"),
        ("p_nom", "lots", "“lots” is not a number"),
        ("p_nom_extendable", "maybe", "“maybe” is not TRUE or FALSE"),
        ("p_nom_extendable", 1, "“1” is not TRUE or FALSE"),
        ("build_year", 2030.5, "2030.5 is not a whole number"),
        ("bus", False, "a TRUE/FALSE value in a text column"),
    ],
)
def test_a_bad_value_is_a_report_row_and_the_default_is_kept(
    tmp_path, key, value, reason
):
    path = _write(_network(False), tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Generators"]
    col = [c.value for c in ws[L.ROW_KEY]].index(key) + 1
    label = ws.cell(L.ROW_LABEL, col).value
    ws.cell(L.FIRST_DATA_ROW, col).value = value
    wb.save(path)
    res = _read(path)
    assert (
        f"Generators · wind 1 · {label}",
        f"{reason}; the model default is kept",
    ) in _um(res)
    assert "wind 1" in res.network.generators.index


def test_infinity_is_read_where_the_engine_accepts_it(tmp_path):
    res = _read(_write(_network(False), tmp_path))
    assert math.isinf(res.network.generators.at["electrolyser out", "p_nom_max"])


def test_a_formula_without_a_saved_value_is_never_evaluated(tmp_path):
    """E2: openpyxl writes no cached value, as a file never opened in Excel."""
    path = _write(_network(False), tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Generators"]
    col = [c.value for c in ws[L.ROW_KEY]].index("p_nom") + 1
    ws.cell(L.FIRST_DATA_ROW, col).value = "=2*21"
    wb.save(path)
    res = _read(path)
    assert any(
        item == "Generators · wind 1 · Capacity" and "formula" in why
        for item, why in _um(res)
    )
    assert res.network.generators.at["wind 1", "p_nom"] == 0.0


def test_rows_without_a_name_twice_named_or_on_an_unknown_sheet_are_reported(tmp_path):
    path = _write(_network(False), tmp_path)
    wb = openpyxl.load_workbook(path)
    wb["Loads"].append([None, "a", 3.0])
    wb["Loads"].append(["d", "a", 4.0])
    wb.create_sheet("Notes").append(["anything"])
    wb.save(path)
    unmapped = _um(_read(path))
    assert ("Loads row 6", "the row has values but no name") in unmapped
    assert ("Loads row 7 (d)", "a second row with the same name") in unmapped
    assert (
        "Notes",
        "not a sheet this workbook uses; its rows were not read",
    ) in unmapped


# ── injection (E7) ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", L.INERT_TEXT_CASES)
def test_text_is_never_written_as_a_formula(text):
    wb = openpyxl.Workbook()
    cell = L.put(wb.active, 1, 1, text)
    assert cell.data_type == "s"
    assert cell.value == L.safe_text(text)


def test_names_that_look_like_formulas_round_trip_as_text(tmp_path):
    n = _network(False)
    path = _write(n, tmp_path)
    assert (
        openpyxl.load_workbook(path)["Nodes"].cell(L.FIRST_DATA_ROW + 2, 1).data_type
        == "s"
    )
    assert "=h2 site" in _read(path).network.buses.index


# ── read safety (E2) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "limit,low",
    [
        ("sheets", 3),
        ("rows", 6),
        ("columns", 4),
        ("cells", 50),
        ("zip_members", 5),
        ("uncompressed_bytes", 1000),
    ],
)
def test_each_limit_refuses_with_its_name(tmp_path, monkeypatch, limit, low):
    path = _write(_network(False), tmp_path)
    monkeypatch.setitem(L.LIMITS, limit, low)
    with pytest.raises(L.ImportLimitError) as exc:
        _read(path)
    assert exc.value.limit == limit


def test_garbage_and_damaged_files_are_refused_as_format_errors(tmp_path):
    good = _write(_network(False), tmp_path).read_bytes()
    rng = random.Random(7)
    blobs = [b"", b"not a workbook", b"PK\x03\x04" + b"\x00" * 64]
    blobs += [good[: len(good) * k // 10] for k in range(1, 10)]
    for _ in range(25):
        b = bytearray(good)
        for _ in range(20):
            b[rng.randrange(len(b))] = rng.randrange(256)
        blobs.append(bytes(b))
    for i, blob in enumerate(blobs):
        p = tmp_path / f"f{i}.xlsx"
        p.write_bytes(blob)
        try:
            _read(p)
        except L.ImportFormatError:
            pass


def test_only_one_workbook_at_a_time(tmp_path):
    p = _write(_network(False), tmp_path)
    with pytest.raises(L.ImportFormatError):
        read_excel([p, p])


# ── the contract ─────────────────────────────────────────────────────────────


def test_the_report_is_json_ready_and_an_infinite_default_reads_as_no_limit(tmp_path):
    path = _write(_network(False), tmp_path, template=True)
    wb = openpyxl.load_workbook(path)
    wb["Nodes"].append(["n1", 20])
    wb["Generators"].append(["g1", "n1", None, 7.5])
    wb.save(path)
    res = _read(path)
    assert res.report.source_format == "excel"
    assert (
        "g1",
        "Maximum capacity",
        None,
        "left empty: the model default (no limit)",
    ) in _am(res)
    json.dumps(res.report.to_dict(), allow_nan=False)
    json.dumps(res.sidecars, allow_nan=False)


def test_a_workbook_that_is_not_the_input_workbook_is_refused(tmp_path):
    from gridspine.ingest.import_result import ImportFormatError

    wb = openpyxl.Workbook()
    wb.active.title = "notes"
    path = tmp_path / "other.xlsx"
    wb.save(path)
    with pytest.raises(ImportFormatError, match="none of the sheets"):
        _read(path)


# ── older layouts (S1b, E6) ──────────────────────────────────────────────────


def _book(tmp_path, sheets: dict, name="legacy.xlsx"):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in sheets.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


def _native(n, tmp_path):
    """As today's /export/excel writes it: a text that reads as a formula gets a leading quote."""
    path = tmp_path / "native.xlsx"
    n.export_to_excel(str(path))
    wb = openpyxl.load_workbook(path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith(
                    L.FORMULA_LEADS
                ):
                    cell.value, cell.data_type = "'" + cell.value, "s"
    wb.save(path)
    return path


def _at_16_digits(n: pypsa.Network) -> pypsa.Network:
    """
    The network as PyPSA's own Excel export holds it: openpyxl writes floats with
    "%.16g", so 0.1 + 0.2 is saved as 0.3. Today's /import/excel reads the same
    digits, so this layout cannot be bit for bit; E6 asks for parity with it.
    """

    def cut(frame: pd.DataFrame) -> None:
        for col in frame.columns:
            if frame[col].dtype.kind == "f":
                frame[col] = [float(f"{x:.16g}") for x in frame[col]]

    m = n.copy()
    for _, _, ln in L.SHEETS:
        cut(getattr(m, ln))
        for frame in getattr(m, f"{ln}_t").values():
            cut(frame)
    cut(m.snapshot_weightings)
    return m


def test_pypsa_s_own_excel_export_reads_as_before(tmp_path):
    n = _network(False)
    res = _read(_native(n, tmp_path))
    # PyPSA's own reader gives whole-number weightings an int dtype, as today's route does.
    _assert_same(_at_16_digits(n), res.network, weight_dtype=False)
    assert res.sidecars == {}
    assert _um(res) == []
    assert res.report.counts["Nodes"] == len(n.buses)


def test_a_pypsa_export_column_off_the_allow_list_is_reported_not_set(tmp_path):
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110.0)
    n.buses["cmd"] = "x"
    res = read_excel(_native(n, tmp_path))
    assert "cmd" not in res.network.buses.columns
    assert (
        "buses · column cmd",
        "not an input of this kind of asset; the column was not read",
    ) in _um(res)


def test_a_legacy_sheet_row_the_model_refuses_is_reported_not_dropped(tmp_path):
    """The old /import/excel swallowed these rows (`except Exception: pass`)."""
    path = _book(
        tmp_path,
        {
            "buses": [["name", "v_nom"], ["B1", 110.0]],
            "generators": [
                ["name", "bus", "p_nom"],
                ["G1", "B1", 5.0],
                ["G1", "B1", 6.0],
                [None, "B1", 7.0],
                ["'=cmd", "B1", 1.0],
            ],
            "notes": [["x"]],
        },
    )
    res = read_excel(path)
    assert sorted(res.network.generators.index) == ["=cmd", "G1"]
    assert res.network.generators.at["G1", "p_nom"] == 5.0
    reasons = dict(_um(res))
    assert reasons["generators row 3 (G1)"] == "a second row with the same name"
    assert reasons["generators row 4"] == "the row has values but no name"
    assert reasons["notes"] == "not a component sheet; its rows were not read"
    assert res.report.counts == {"Nodes": 1, "Generators": 2}


def test_a_legacy_column_off_the_allow_list_is_reported_not_set(tmp_path):
    """E1 holds for raw sheets too: a header is a model input or on the allow-list."""
    path = _book(
        tmp_path,
        {
            "buses": [
                ["name", "v_nom", "__class__", "eh_flag"],
                ["B1", 110.0, "x", True],
            ],
        },
    )
    res = read_excel(path, {"custom_columns": {"Bus": ["eh_flag"]}})
    assert "__class__" not in res.network.buses.columns
    assert bool(res.network.buses.at["B1", "eh_flag"]) is True
    assert _um(res) == [
        (
            "buses · column __class__",
            "not an input of this kind of asset; the column was not read",
        )
    ]


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (
            "inf",
            "p_nom: infinity is not allowed here",
        ),  # an .xlsx cannot hold a float inf
        (True, "p_nom: a TRUE/FALSE value in a number column"),
        ("lots", "p_nom: “lots” is not a number"),
    ],
)
def test_a_legacy_cell_the_model_cannot_take_is_reported(tmp_path, value, reason):
    """E3 on raw sheets: the row is reported with the reason, never half-added."""
    path = _book(
        tmp_path,
        {
            "buses": [["name", "v_nom"], ["B1", 110.0]],
            "generators": [["name", "bus", "p_nom"], ["G1", "B1", value]],
        },
    )
    res = read_excel(path)
    assert res.network.generators.empty
    assert _um(res) == [("generators · G1", reason)]


def test_a_legacy_formula_with_no_saved_value_is_reported(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "buses"
    ws.append(["name", "v_nom"])
    ws.append(["B1", "=1+1"])
    path = tmp_path / "f.xlsx"
    wb.save(path)
    res = read_excel(path)
    assert "B1" in res.network.buses.index
    assert (
        "buses · cell B2",
        "a formula with no saved value; open and save the file in Excel, "
        "or type the value",
    ) in _um(res)


_BUS = [["name", "v_nom"], ["B1", 110.0]]
_OFF_LIST = "not an input of this kind of asset; the column was not read"


def test_a_legacy_output_column_is_reported_not_set(tmp_path):
    """E1: a result attribute such as p_nom_opt is not an input, so it is not read."""
    path = _book(
        tmp_path,
        {
            "buses": _BUS,
            "generators": [["name", "bus", "p_nom_opt"], ["G1", "B1", 9.0]],
        },
    )
    res = read_excel(path)
    assert ("generators · column p_nom_opt", _OFF_LIST) in _um(res)
    assert res.network.generators.at["G1", "p_nom_opt"] == 0.0


def test_a_network_sheet_pypsa_cannot_read_is_a_format_error(tmp_path):
    from gridspine.ingest.import_result import ImportFormatError

    path = _book(
        tmp_path,
        {"network": [["name"], ["n"]], "buses": [["name", "v_nom"], ["B1", "high"]]},
    )
    with pytest.raises(ImportFormatError, match="PyPSA could not read it"):
        read_excel(path)


def test_a_formula_with_no_saved_value_in_a_pypsa_export_is_reported(tmp_path):
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110.0)
    path = _native(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["buses"]
    col = [c.value for c in ws[1]].index("v_nom") + 1
    cell = ws.cell(row=2, column=col)
    cell.value = "=1+1"
    wb.save(path)
    res = read_excel(path)
    assert any(
        src == f"buses · cell {cell.coordinate}"
        and "formula with no saved value" in why
        for src, why in _um(res)
    )


def test_a_pypsa_export_counts_only_the_kinds_it_holds(tmp_path):
    n = pypsa.Network()
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Load", "L1", bus="B1", p_set=1.0)
    res = read_excel(_native(n, tmp_path))
    assert res.report.counts == {"Nodes": 1, "Loads": 1}


def test_an_empty_legacy_sheet_is_skipped(tmp_path):
    res = read_excel(_book(tmp_path, {"buses": _BUS, "loads": []}))
    assert list(res.network.buses.index) == ["B1"]
    assert res.network.loads.empty


def test_a_legacy_name_column_need_not_come_first(tmp_path):
    res = read_excel(_book(tmp_path, {"buses": [["v_nom", "name"], [110.0, "B1"]]}))
    assert res.network.buses.at["B1", "v_nom"] == 110.0
    assert _um(res) == []


def test_a_legacy_blank_header_or_blank_row_is_skipped_silently(tmp_path):
    path = _book(
        tmp_path,
        {
            "buses": [
                ["name", None, "v_nom"],
                ["B1", None, 110.0],
                [None, None, None],
                ["B2", None, 220.0],
            ]
        },
    )
    res = read_excel(path)
    assert _um(res) == []
    assert res.network.buses["v_nom"].to_dict() == {"B1": 110.0, "B2": 220.0}


def test_a_legacy_blank_name_is_reported_and_a_number_name_is_kept(tmp_path):
    path = _book(tmp_path, {"buses": [["name", "v_nom"], ["  ", 110.0], [7.0, 220.0]]})
    res = read_excel(path)
    assert ("buses row 2", "the row has values but no name") in _um(res)
    assert "7" in res.network.buses.index


@pytest.mark.parametrize("marker", [L.ABOUT, L.HORIZON])
def test_one_input_workbook_sheet_alone_reads_as_raw_sheets(tmp_path, marker):
    res = read_excel(_book(tmp_path, {marker: [["x"]], "buses": _BUS}))
    assert list(res.network.buses.index) == ["B1"]
    assert (marker, "not a component sheet; its rows were not read") in _um(res)


def test_a_legacy_row_names_its_first_bad_cell(tmp_path):
    path = _book(
        tmp_path,
        {
            "buses": _BUS,
            "generators": [
                ["name", "bus", "p_nom", "marginal_cost"],
                ["G1", "B1", "lots", "bad"],
            ],
        },
    )
    res = read_excel(path)
    assert _um(res) == [("generators · G1", "p_nom: “lots” is not a number")]


def _native_edit(tmp_path, n, sheet, header, row_name, value):
    """`_native(n)` with one cell set: the `header` column of the row named `row_name`."""
    path = _native(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb[sheet]
    heads = [c.value for c in ws[1]]
    if header not in heads:
        ws.cell(row=1, column=len(heads) + 1, value=header)
        heads.append(header)
    col = heads.index(header) + 1
    for r in range(2, ws.max_row + 1):
        if str(ws.cell(row=r, column=1).value) == row_name:
            ws.cell(row=r, column=col, value=value)
    wb.save(path)
    return path


def _two_gens() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Bus", "B2", v_nom=220.0)
    n.add("Generator", "G1", bus="B1", p_nom=5.0, p_max_pu=[0.5, 0.6])
    n.add("Generator", "G2", bus="B1", p_nom=6.0, p_max_pu=[0.7, 0.8])
    return n


@pytest.mark.parametrize(
    ("sheet", "header", "row", "value", "reason"),
    [
        ("buses", "v_nom", "B2", True, "v_nom: a TRUE/FALSE value in a number column"),
        ("buses", "v_nom", "B2", "-inf", "v_nom: infinity is not allowed here"),
        ("buses", "v_nom", "B2", "nan", "v_nom: not a number (NaN)"),
        ("generators", "p_nom", "G2", "inf", "p_nom: infinity is not allowed here"),
        (
            "generators",
            "p_nom_max",
            "G2",
            "-inf",
            "p_nom_max: only inf is allowed here",
        ),
    ],
)
def test_a_cell_pypsa_s_export_cannot_take_is_reported_and_its_asset_not_read(
    tmp_path, sheet, header, row, value, reason
):
    """E3 on PyPSA's own layout: checked on the cell as written, before PyPSA coerces it."""
    res = read_excel(_native_edit(tmp_path, _two_gens(), sheet, header, row, value))
    assert _um(res) == [(f"{sheet} · {row}", reason)]
    assert row not in getattr(res.network, sheet).index
    assert len(getattr(res.network, sheet)) == 1


def test_an_allowed_infinity_in_pypsa_s_export_is_read(tmp_path):
    path = _native_edit(tmp_path, _two_gens(), "generators", "p_nom_max", "G2", "inf")
    res = read_excel(path)
    assert _um(res) == []
    assert math.isinf(res.network.generators.at["G2", "p_nom_max"])


def test_a_model_output_column_in_pypsa_s_export_is_reported_and_reset(tmp_path):
    """E1: an attribute PyPSA knows but that is not an input is not read."""
    path = _native_edit(tmp_path, _two_gens(), "generators", "p_nom_opt", "G1", 99.0)
    res = read_excel(path)
    assert _um(res) == [("generators · column p_nom_opt", _OFF_LIST)]
    assert res.network.generators.at["G1", "p_nom_opt"] == 0.0


def test_an_output_time_series_sheet_in_pypsa_s_export_is_not_read(tmp_path):
    n = _two_gens()
    path = _native(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    src = wb["generators-p_max_pu"]
    out = wb.create_sheet("generators-p")
    for row in src.iter_rows(values_only=True):
        out.append(row)
    wb.save(path)
    res = read_excel(path)
    assert (
        "generators-p",
        "not an input time series of this kind of asset; the sheet was not read",
    ) in _um(res)
    assert res.network.generators_t.p.empty


def test_a_bad_cell_in_a_pypsa_export_time_series_drops_that_series(tmp_path):
    n = _two_gens()
    path = _native(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["generators-p_max_pu"]
    col = [c.value for c in ws[1]].index("G2") + 1
    ws.cell(row=3, column=col, value="nan")
    wb.save(path)
    res = read_excel(path)
    assert _um(res) == [("generators-p_max_pu · G2", "p_max_pu: not a number (NaN)")]
    assert list(res.network.generators_t.p_max_pu.columns) == ["G1"]


def _native_wb(tmp_path, edit):
    """`_native(_two_gens())` with `edit(workbook)` applied, then read."""
    path = _native(_two_gens(), tmp_path)
    wb = openpyxl.load_workbook(path)
    edit(wb)
    wb.save(path)
    return read_excel(path)


_NO_HEADER = "a column with no header; the column was not read"
_NOT_TS = "not an input time series of this kind of asset; the sheet was not read"


def test_a_header_less_column_in_pypsa_s_export_is_reported_and_dropped(tmp_path):
    def edit(wb):
        ws = wb["buses"]
        ws.insert_cols(2)
        ws["B3"] = 7

    res = _native_wb(tmp_path, edit)
    assert ("buses · column 2", _NO_HEADER) in _um(res)
    assert not any(c.startswith("Unnamed") for c in res.network.buses.columns)
    assert not any("None" in src for src, _ in _um(res))


def test_a_header_less_series_column_in_pypsa_s_export_is_not_read(tmp_path):
    def edit(wb):
        ws = wb["generators-p_max_pu"]
        ws.insert_cols(2)
        ws["B2"] = "x"

    res = _native_wb(tmp_path, edit)
    assert not any("None" in src for src, _ in _um(res))
    assert sorted(res.network.generators_t.p_max_pu.columns) == ["G1", "G2"]


@pytest.mark.parametrize("name", [True, datetime.datetime(2030, 1, 1)])
def test_a_name_pypsa_turns_to_text_still_gets_its_cells_checked(tmp_path, name):
    """Bug found by mutants: a date or TRUE name skipped E3 on the row."""

    def edit(wb):
        wb["buses"].append([name, True])

    res = _native_wb(tmp_path, edit)
    assert [why for _, why in _um(res)] == [
        "v_nom: a TRUE/FALSE value in a number column"
    ]
    assert len(res.network.buses) == 2


def test_a_short_row_in_pypsa_s_export_reads(tmp_path):
    path = _native_edit(tmp_path, _two_gens(), "generators", "marginal_cost", "G1", 3.0)
    res = read_excel(path)
    assert _um(res) == []
    assert res.network.generators.at["G1", "marginal_cost"] == 3.0


def test_a_row_with_two_bad_cells_in_pypsa_s_export_is_reported_once(tmp_path):
    def edit(wb):
        ws = wb["generators"]
        heads = [c.value for c in ws[1]]
        for h in ("bus", "p_nom"):
            ws.cell(row=3, column=heads.index(h) + 1, value=True)

    res = _native_wb(tmp_path, edit)
    assert [src for src, _ in _um(res)] == ["generators · G2"]


@pytest.mark.parametrize("attr", ["foo", "p_nom"])
def test_a_series_sheet_that_is_not_a_varying_input_is_not_read(tmp_path, attr):
    def edit(wb):
        out = wb.create_sheet(f"generators-{attr}")
        for row in wb["generators-p_max_pu"].iter_rows(values_only=True):
            out.append(row)

    res = _native_wb(tmp_path, edit)
    assert (f"generators-{attr}", _NOT_TS) in _um(res)


def test_a_short_or_blank_series_cell_in_pypsa_s_export_is_not_a_bad_cell(tmp_path):
    def edit(wb):
        ws = wb["generators-p_max_pu"]
        ws["B2"] = None
        ws["D2"] = None  # widens the sheet past row 3

    res = _native_wb(tmp_path, edit)
    assert _um(res) == []
    assert sorted(res.network.generators_t.p_max_pu.columns) == ["G1", "G2"]


def test_a_series_with_two_bad_cells_is_reported_once(tmp_path):
    def edit(wb):
        ws = wb["generators-p_max_pu"]
        col = [c.value for c in ws[1]].index("G1") + 1
        ws.cell(row=2, column=col, value="x")
        ws.cell(row=3, column=col, value="x")

    res = _native_wb(tmp_path, edit)
    assert [src for src, _ in _um(res)] == ["generators-p_max_pu · G1"]


def test_a_nameless_row_in_pypsa_s_export_is_reported_not_read_as_nan(tmp_path):
    def edit(wb):
        wb["buses"].append([None, True])

    res = _native_wb(tmp_path, edit)
    assert sorted(res.network.buses.index) == ["B1", "B2"]
    assert _um(res) == [("buses row 4", "the row has values but no name")]


def test_a_blank_cell_mid_row_in_pypsa_s_export_keeps_the_default(tmp_path):
    def edit(wb):
        ws = wb["generators"]
        heads = [c.value for c in ws[1]]
        ws.cell(row=1, column=len(heads) + 1, value="marginal_cost")
        ws.cell(row=2, column=len(heads) + 1, value=3.0)
        ws.cell(row=2, column=heads.index("p_nom") + 1).value = None

    res = _native_wb(tmp_path, edit)
    assert _um(res) == []
    assert res.network.generators.at["G1", "p_nom"] == 0.0


def test_a_series_row_shorter_than_its_header_reads(tmp_path):
    def edit(wb):
        ws = wb["generators-p_max_pu"]
        ws["D1"], ws["D2"] = "G3", 0.5

    res = _native_wb(tmp_path, edit)
    assert {"G1", "G2"} <= set(res.network.generators_t.p_max_pu.columns)


def test_a_legacy_true_false_name_is_reported(tmp_path):
    res = read_excel(_book(tmp_path, {"buses": [["name", "v_nom"], [True, 110.0]]}))
    assert _um(res) == [("buses row 2", "name: a TRUE/FALSE value in a text column")]
    assert res.network.buses.empty


def test_unescape_undoes_only_the_export_s_quote():
    from gridspine.ingest.excel_import import unescape_formula

    assert unescape_formula("'=cmd") == "=cmd"
    assert unescape_formula("'-5") == "'-5"
    assert unescape_formula("'abc") == "'abc"
    assert unescape_formula("=x") == "=x"
    assert unescape_formula(3.0) == 3.0


def test_the_registry_finds_this_reader():
    from gridspine.ingest.import_result import DECLARED

    spec = next(s for s in DECLARED if s.format_id == "excel")
    assert spec.is_available()
    assert spec.load_reader() is read_excel


# ── edges the mutation run found untested (E8) ───────────────────────────────


def _edit(path, fn):
    wb = openpyxl.load_workbook(path)
    fn(wb)
    wb.save(path)
    return _read(path)


def _col(ws, key, row=L.ROW_KEY):
    return [c.value for c in ws[row]].index(key) + 1


def test_a_limit_equal_to_the_file_is_not_passed(tmp_path, monkeypatch):
    import zipfile

    path = _write(_network(False), tmp_path)
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
    monkeypatch.setitem(L.LIMITS, "zip_members", len(infos))
    monkeypatch.setitem(L.LIMITS, "uncompressed_bytes", sum(i.file_size for i in infos))
    assert _um(_read(path)) == []


def test_numbers_and_dates_in_a_text_column_read_as_text(tmp_path):
    import datetime

    def fn(wb):
        ws = wb["Nodes"]
        ws.cell(L.FIRST_DATA_ROW, 1).value = 7.0
        ws.cell(L.FIRST_DATA_ROW + 1, 1).value = datetime.datetime(2030, 1, 2)

    res = _edit(_write(_network(False), tmp_path), fn)
    assert {"7", "2030-01-02T00:00:00"} <= set(res.network.buses.index)


def test_a_date_in_a_number_column_is_reported(tmp_path):
    import datetime

    def fn(wb):
        ws = wb["Generators"]
        ws.cell(L.FIRST_DATA_ROW, _col(ws, "p_nom")).value = datetime.datetime(
            2030, 1, 2
        )

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        "Generators · wind 1 · Capacity",
        "“2030-01-02 00:00:00” is not a number; the model default is kept",
    ) in _um(res)


def test_a_formula_with_a_saved_value_reads_the_value(tmp_path):
    """E2: what Excel saved is read; the formula itself is never evaluated."""
    import re
    import zipfile

    path = _write(_network(False), tmp_path)
    ws = openpyxl.load_workbook(path)["Generators"]
    ref = f"{openpyxl.utils.get_column_letter(_col(ws, 'p_nom'))}{L.FIRST_DATA_ROW}"
    with zipfile.ZipFile(path) as zf:
        parts = {i.filename: zf.read(i.filename) for i in zf.infolist()}
    # openpyxl writes sheet N of the workbook as xl/worksheets/sheetN.xml.
    sheet = f"xl/worksheets/sheet{openpyxl.load_workbook(path).sheetnames.index('Generators') + 1}.xml"
    xml = parts[sheet].decode()
    xml, k = re.subn(
        rf'<c r="{ref}"([^>]*?)(?:/>|>.*?</c>)',
        rf'<c r="{ref}"><f>2*21</f><v>42</v></c>',
        xml,
    )
    assert k == 1
    parts[sheet] = xml.encode()
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in parts.items():
            zf.writestr(name, data)
    res = _read(path)
    assert _um(res) == []
    assert res.network.generators.at["wind 1", "p_nom"] == 42.0


def test_a_number_in_the_hidden_model_name_row_is_reported(tmp_path):
    def fn(wb):
        ws = wb["Generators"]
        ws.cell(L.ROW_KEY, _col(ws, "marginal_cost")).value = 5

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        "Generators · Variable cost",
        "its hidden model name “5” is not one Generators reads; the column was not read",
    ) in _um(res)


def test_a_sheet_without_a_name_column_is_reported(tmp_path):
    def fn(wb):
        wb["Loads"].cell(L.ROW_KEY, 1).value = "p_set"

    res = _edit(_write(_network(False), tmp_path), fn)
    assert ("Loads", "no Name column; the sheet's rows were not read") in _um(res)
    assert res.network.loads.empty


def test_an_empty_row_between_assets_is_skipped_quietly(tmp_path):
    def fn(wb):
        ws = wb["Loads"]
        ws.insert_rows(L.FIRST_DATA_ROW)

    res = _edit(_write(_network(False), tmp_path), fn)
    assert _um(res) == []
    assert list(res.network.loads.index) == ["d"]


def test_a_size_unit_column_on_a_sheet_without_a_size_is_reported(tmp_path):
    def fn(wb):
        ws = wb["Lines"]
        c = ws.max_column + 1
        for r, v in (
            (L.ROW_LABEL, "Size unit"),
            (L.ROW_KEY, "~size_unit"),
            (L.ROW_SCALE, 1.0),
            (L.FIRST_DATA_ROW, "t/h"),
        ):
            ws.cell(r, c).value = v

    res = _edit(_write(_network(False), tmp_path), fn)
    assert ("Lines · Size unit", "its hidden model name “~size_unit” is not one Lines reads; the column was not read") in _um(res)


def test_a_missing_weighting_column_keeps_the_default(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.delete_cols(_col(ws, "stores", L.TS_ROW_ATTR))

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        "every time step",
        "Storage weighting",
        1.0,
        "no column: the model default",
    ) in _am(res)
    assert (res.network.snapshot_weightings["stores"] == 1.0).all()
    assert (res.network.snapshot_weightings["objective"] == 2.0).all()


def test_an_empty_time_series_cell_takes_the_fixed_value_and_says_so(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.cell(L.TS_FIRST_DATA_ROW + 1, _col(ws, "d", L.TS_ROW_ASSET)).value = None

    res = _edit(_write(_network(False), tmp_path), fn)
    assert ("d", "p_set", 0.0, "1 empty time step(s): the asset's fixed value") in _am(
        res
    )
    assert res.network.loads_t.p_set["d"].tolist() == [10.0, 0.0, 12.0]


def test_an_empty_time_series_sheet_is_reported(tmp_path):
    def fn(wb):
        del wb[L.TIME_SERIES]
        wb.create_sheet(L.TIME_SERIES)

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (L.TIME_SERIES, "no Time column; one time step is assumed") in _um(res)


@pytest.mark.parametrize("mark", [5, True, "[1, 2]", "x" * 50])
def test_an_upload_mark_that_is_not_a_small_json_object_warns(
    tmp_path, monkeypatch, mark
):
    monkeypatch.setattr("gridspine.ingest.excel_import._MAX_JSON_CELL", 40)
    n = _network(False)

    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.cell(L.TS_ROW_USER, _col(ws, "d", L.TS_ROW_ASSET)).value = mark

    res = _edit(_write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)}), fn)
    assert "loads" not in res.sidecars["user_ts.json"]
    assert any("upload mark" in w for w in res.report.warnings)


def test_an_upload_mark_at_the_size_cap_is_read(tmp_path, monkeypatch):
    mark = '{"index_name": "snapshot"}'
    monkeypatch.setattr("gridspine.ingest.excel_import._MAX_JSON_CELL", len(mark))
    n = _network(False)
    res = _read(_write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)}))
    assert (
        res.sidecars["user_ts.json"]["loads"]["p_set"]["d"]["index_name"] == "snapshot"
    )


@pytest.mark.parametrize("index", ["[broken", '["2030"'])
def test_an_uploaded_profile_with_an_unreadable_time_step_is_reported(tmp_path, index):
    n = _network(True)

    def fn(wb):
        wb[L.UPLOADED].cell(L.UP_FIRST_DATA_ROW, 1).value = index

    res = _edit(_write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)}), fn)
    assert any(item == "Uploaded profiles · other · p_max_pu" for item, _ in _um(res))
    assert "generators" not in res.sidecars["user_ts.json"]


def test_a_date_typed_as_an_excel_date_reads_and_a_number_is_refused(tmp_path):
    import datetime

    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.cell(
            L.TS_FIRST_DATA_ROW, _col(ws, "snapshot", L.TS_ROW_ATTR)
        ).value = datetime.datetime(2030, 1, 1)
        ws.cell(L.TS_FIRST_DATA_ROW + 2, _col(ws, "snapshot", L.TS_ROW_ATTR)).value = 5

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        f"{L.TIME_SERIES} row {L.TS_FIRST_DATA_ROW + 2}",
        "time step: “5” is not a date as YYYY-MM-DD or YYYY-MM-DD hh:mm[:ss], "
        "without a time zone",
    ) in _um(res)
    assert list(res.network.snapshots) == [
        pd.Timestamp("2030-01-01 00:00"),
        pd.Timestamp("2030-01-01 01:00"),
    ]


def test_read_excel_takes_a_plain_string_path_and_no_options(tmp_path):
    path = _write(_network(False), tmp_path)
    res = read_excel(str(path))
    assert res.report.source_files == ["w.xlsx"]
    # Without the allow-list, the custom columns are reported, never set.
    assert any("is not one Generators reads; the column was not read" in why for _, why in _um(res))
    assert "eh_flag" not in res.network.generators.columns


# ── review findings (mp-code-review) ─────────────────────────────────────────


def test_a_unit_and_its_factor_changed_together_cannot_rescale_a_value(tmp_path):
    """E1: the shown unit must be the catalogue's, so the pair cannot be swapped."""

    def fn(wb):
        ws = wb["Generators"]
        c = _col(ws, "capital_cost")
        ws.cell(L.ROW_UNIT, c).value = "EUR/MW/yr"
        ws.cell(L.ROW_SCALE, c).value = 1.0

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        "Generators · Annualised investment",
        "the unit “EUR/MW/yr” is not this parameter's unit “EUR/kW/yr”",
    ) in _um(res)
    assert res.network.generators.at["wind 1", "capital_cost"] == 0.0


def test_a_store_on_a_tonne_node_counts_its_size_in_tonnes(tmp_path):
    """E4: a stock on a tonne node is in t, a flow in t/h."""
    path = _write(_network(False), tmp_path)
    ws = openpyxl.load_workbook(path)["Stores"]
    assert ws.cell(L.FIRST_DATA_ROW, _col(ws, "~size_unit")).value == "t"
    assert _read(path).report.warnings == []


def test_only_the_engine_s_own_infinity_is_accepted(tmp_path):
    def fn(wb):
        ws = wb["Generators"]
        ws.cell(L.FIRST_DATA_ROW + 1, _col(ws, "p_nom_max")).value = "-inf"

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        "Generators · electrolyser out · Maximum capacity",
        "only inf is allowed here; the model default is kept",
    ) in _um(res)


def test_a_formula_without_a_saved_value_anywhere_is_reported(tmp_path):
    def fn(wb):
        wb[L.HORIZON]["B2"] = "=7/100"

    res = _edit(
        _write(_network(False), tmp_path, sidecars={"solver_config.json": SOLVER}), fn
    )
    assert (
        f"{L.HORIZON} · cell B2",
        "a formula with no saved value; open and save the file in Excel, or type the value",
    ) in _um(res)
    assert "discount_rate" not in res.sidecars["solver_config.json"]


def test_upload_provenance_that_is_not_json_ready_is_dropped_with_a_warning(tmp_path):
    n = _network(False)

    def fn(wb):
        wb[L.LISTS]["D2"] = '{"loads": NaN}'

    res = _edit(_write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)}), fn)
    assert "$meta" not in res.sidecars["user_ts.json"]
    assert any("provenance" in w for w in res.report.warnings)
    json.dumps(res.sidecars, allow_nan=False)


def test_the_template_carries_the_accepted_custom_columns_hidden(tmp_path):
    ws = openpyxl.load_workbook(_write(_network(False), tmp_path, template=True))[
        "Generators"
    ]
    for key in CUSTOM["Generator"]:
        c = _col(ws, key)
        assert ws.column_dimensions[openpyxl.utils.get_column_letter(c)].hidden


def test_a_custom_column_off_the_allow_list_is_exported_and_reported_on_import(
    tmp_path,
):
    n = _network(False)
    n.loads["site_note"] = ["north yard"]
    res = _read(_write(n, tmp_path))
    assert ("Loads · Site note", "its hidden model name “site_note” is not one Loads reads; the column was not read") in _um(res)
    assert "site_note" not in res.network.loads.columns


# ── E8: one test per guard a targeted mutant showed unkilled ─────────────────


def test_a_second_column_for_the_same_parameter_is_reported_not_overwritten(tmp_path):
    def fn(wb):
        ws = wb["Generators"]
        src, dst = _col(ws, "p_nom"), _col(ws, "marginal_cost")
        for row in (L.ROW_UNIT, L.ROW_KEY, L.ROW_SCALE):
            ws.cell(row, dst).value = ws.cell(row, src).value

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(why == "a second column for the same parameter" for _, why in _um(res))
    assert res.network.generators.at["wind 1", "p_nom"] == 50.0


def test_a_formula_without_a_saved_value_in_a_time_series_names_its_column(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.cell(L.TS_FIRST_DATA_ROW, _col(ws, "d", row=L.TS_ROW_ASSET)).value = "=1+1"

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(
        item.startswith(f"{L.TIME_SERIES} · d")
        and why.startswith(
            f"row {L.TS_FIRST_DATA_ROW} is a formula with no saved value"
        )
        for item, why in _um(res)
    )


def test_a_valid_zip_with_a_damaged_sheet_is_a_format_error(tmp_path):
    import zipfile

    from gridspine.ingest.import_result import ImportFormatError

    src = _write(_network(False), tmp_path)
    bad = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "xl/worksheets/sheet2.xml":
                data = data[: len(data) // 2]
            zout.writestr(info, data)
    with pytest.raises(ImportFormatError, match="not a readable Excel workbook"):
        _read(bad)


def test_upload_provenance_longer_than_a_cell_holds_is_dropped_with_a_warning(tmp_path):
    """
    Openpyxl cuts a cell at 32,767 characters, so the JSON no longer parses.

    The reader's own 1,000,000-character cap is therefore defence in depth (E8: equivalent).
    """
    n = _network(False)

    def fn(wb):
        wb[L.LISTS]["D2"] = '{"x": "' + "a" * 1_000_001 + '"}'

    res = _edit(_write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)}), fn)
    assert "$meta" not in res.sidecars["user_ts.json"]
    assert any("provenance" in w for w in res.report.warnings)


def test_an_uploaded_time_step_that_is_not_json_ready_is_reported(tmp_path):
    n = _network(False)

    def fn(wb):
        wb[L.UPLOADED].cell(L.UP_FIRST_DATA_ROW, 1).value = "[NaN, 1]"

    res = _edit(_write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)}), fn)
    assert (
        f"{L.UPLOADED} · other · p_max_pu",
        "uploaded profile: “[NaN, 1]” is not a time step",
    ) in _um(res)
    assert "generators" not in res.sidecars["user_ts.json"]


def test_an_unknown_snapshot_kind_is_reported(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        r = next(c.row for c in ws["D"] if c.value == "snapshot_kind")
        ws.cell(r, 2).value = "bogus"

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(why.startswith("“bogus” is not one of") for _, why in _um(res))


def test_an_infinite_value_is_written_as_text_and_refused_where_the_engine_does(
    tmp_path,
):
    n = _network(False)
    n.generators.loc["wind 1", "p_nom"] = float("inf")
    path = _write(n, tmp_path)
    ws = openpyxl.load_workbook(path)["Generators"]
    assert ws.cell(L.FIRST_DATA_ROW, _col(ws, "p_nom")).value == "inf"
    assert any(
        item == "Generators · wind 1 · Capacity"
        and why.startswith("infinity is not allowed here")
        for item, why in _um(_read(path))
    )


def test_an_upload_with_a_gap_is_still_the_sheet_s_own_series(tmp_path):
    """NaN in the series and null in user_ts.json are the same value."""
    n = _network(False)
    n.loads_t.p_set.iloc[1, 0] = float("nan")
    ts = _user_ts(n)
    ts["loads"]["p_set"]["d"]["values"][1] = None
    ws = openpyxl.load_workbook(_write(n, tmp_path, sidecars={"user_ts.json": ts}))[
        L.UPLOADED
    ]
    assert "d" not in [c.value for c in ws[L.UP_ROW_ASSET]]


# ── GK B review of df4497ad ──────────────────────────────────────────────────


def _patch_xml(path, member, fn):
    import zipfile

    with zipfile.ZipFile(path) as zin:
        items = [(i, zin.read(i)) for i in zin.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in items:
            zout.writestr(info, fn(data) if info.filename == member else data)
    return path


def test_a_tiny_file_declaring_a_huge_sheet_is_refused_at_once(tmp_path):
    import re
    import time

    path = _patch_xml(
        _write(_network(False), tmp_path),
        "xl/worksheets/sheet1.xml",
        lambda d: re.sub(
            rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1:XFD1048576"/>', d
        ),
    )
    assert path.stat().st_size < 100_000
    t = time.monotonic()
    with pytest.raises(L.ImportLimitError) as e:
        _read(path)
    assert e.value.limit == "cells"
    assert time.monotonic() - t < 10


def test_a_declared_size_smaller_than_the_sheet_still_reads_every_row(tmp_path):
    import re

    n = _network(False)
    ws_index = [
        s for s in openpyxl.load_workbook(_write(n, tmp_path)).sheetnames
    ].index("Generators")
    path = _patch_xml(
        _write(n, tmp_path, name="small.xlsx"),
        f"xl/worksheets/sheet{ws_index + 1}.xml",
        lambda d: re.sub(rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1:A1"/>', d),
    )
    res = _read(path)
    assert list(res.network.generators.index) == list(n.generators.index)


def test_a_factor_only_close_to_the_unit_s_is_reported(tmp_path):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_SCALE, "capital_cost", 0.001 * (1 + 5e-13))
    res = _read(path)
    assert any(
        item == "Generators · Annualised investment" and "hidden unit factor" in why
        for item, why in _um(res)
    )
    assert res.network.generators.at["wind 1", "capital_cost"] == 0.0


def test_a_second_time_series_column_for_one_profile_is_reported(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        src = _col(ws, "d", row=L.TS_ROW_ASSET)
        dst = ws.max_column + 1
        for r in range(1, ws.max_row + 1):
            ws.cell(r, dst).value = ws.cell(r, src).value
        ws.cell(L.TS_FIRST_DATA_ROW, dst).value = 999.0

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(why == "a second column for the same profile" for _, why in _um(res))
    assert 999.0 not in res.network.loads_t.p_set["d"].tolist()


def test_a_second_weighting_column_is_reported(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        src = _col(ws, "objective", row=L.TS_ROW_ATTR)
        dst = ws.max_column + 1
        for r in range(1, ws.max_row + 1):
            ws.cell(r, dst).value = ws.cell(r, src).value
        ws.cell(L.TS_FIRST_DATA_ROW, dst).value = 7.0

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(
        why == "a second column for the same time step data" for _, why in _um(res)
    )
    assert res.network.snapshot_weightings["objective"].iloc[0] == 2.0


def _horizon_row(ws, key):
    return next(c.row for c in ws["D"] if c.value == key)


def test_an_unknown_setting_row_is_reported(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        ws.cell(_horizon_row(ws, "discount_rate"), 4).value = "discount_rte"

    res = _edit(
        _write(_network(False), tmp_path, sidecars={"solver_config.json": SOLVER}), fn
    )
    assert any(
        why == "“discount_rte” is not a setting this workbook uses"
        for _, why in _um(res)
    )
    assert "discount_rate" not in res.sidecars["solver_config.json"]


def test_a_discount_rate_in_another_unit_is_reported_not_read(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        ws.cell(_horizon_row(ws, "discount_rate"), 3).value = "fraction"

    res = _edit(
        _write(_network(False), tmp_path, sidecars={"solver_config.json": SOLVER}), fn
    )
    assert any(why.startswith("the unit must be “%”") for _, why in _um(res)), _um(res)
    assert "discount_rate" not in res.sidecars.get("solver_config.json", {})


def test_a_second_row_for_a_setting_is_reported_and_the_first_kept(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        r = _horizon_row(ws, "snapshot_kind")
        ws.cell(r, 4).value = "discount_rate"
        ws.cell(r, 2).value = 99.0
        ws.cell(r, 3).value = "%"

    res = _edit(
        _write(_network(False), tmp_path, sidecars={"solver_config.json": SOLVER}), fn
    )
    assert any(
        why == "a second row for the setting “Discount rate”" for _, why in _um(res)
    )
    assert (
        res.sidecars["solver_config.json"]["discount_rate"] == SOLVER["discount_rate"]
    )


def test_a_repeated_period_row_is_reported_and_the_first_kept(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        last = ws.max_row
        ws.cell(last + 1, 1).value = ws.cell(last, 1).value
        ws.cell(last + 1, 2).value = 9.0
        ws.cell(last + 1, 3).value = 9.0

    n = _network(True)
    res = _edit(_write(n, tmp_path), fn)
    assert any("a second row for period 2040" in why for _, why in _um(res))
    assert res.network.investment_period_weightings.at[2040, "objective"] == 0.5


def test_the_same_time_step_twice_is_refused(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        c = _col(ws, "snapshot", row=L.TS_ROW_ATTR)
        ws.cell(L.TS_FIRST_DATA_ROW + 1, c).value = ws.cell(
            L.TS_FIRST_DATA_ROW, c
        ).value

    res = _edit(_write(_network(False), tmp_path), fn)
    assert (
        L.TIME_SERIES,
        "the same time step appears twice; the time series were not read",
    ) in _um(res)
    assert res.network.loads_t.p_set.empty


def test_upload_provenance_past_the_reader_s_cap_is_dropped_with_a_warning(tmp_path):
    """A crafted file can hold a cell longer than Excel or openpyxl ever writes."""
    import zipfile

    n = _network(False)
    path = _write(n, tmp_path, sidecars={"user_ts.json": _user_ts(n)})
    head = b'<t>{"loads": {"p_set": {"d": {"provenance"'
    with zipfile.ZipFile(path) as zf:
        member = next(m for m in zf.namelist() if head in zf.read(m))

    def fn(d):
        i = d.index(head) + 3
        j = d.index(b"</t>", i)
        return d[:i] + b'{"x": "' + b"a" * 1_100_000 + b'"}' + d[j:]

    _patch_xml(path, member, fn)
    res = _read(path)
    assert "$meta" not in res.sidecars["user_ts.json"]
    assert any("provenance" in w for w in res.report.warnings)


@pytest.mark.parametrize(
    "text", ["now", "today", "2030-01-01T00:00:00+02:00", "1/2/2030"]
)
def test_a_time_step_that_is_not_a_plain_iso_date_is_refused(tmp_path, text):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.cell(
            L.TS_FIRST_DATA_ROW, _col(ws, "snapshot", row=L.TS_ROW_ATTR)
        ).value = text

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(f"“{text}” is not a date as YYYY-MM-DD" in why for _, why in _um(res))


def test_a_number_with_underscores_is_refused(tmp_path):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.FIRST_DATA_ROW, "p_nom", "1_000")
    assert ("“1_000” is not a number") in " ".join(why for _, why in _um(_read(path)))


def test_a_negative_weighting_is_refused_and_1_used(tmp_path):
    def fn(wb):
        ws = wb[L.TIME_SERIES]
        ws.cell(
            L.TS_FIRST_DATA_ROW, _col(ws, "objective", row=L.TS_ROW_ATTR)
        ).value = -2.0

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(why.startswith("a weighting cannot be negative") for _, why in _um(res))
    assert res.network.snapshot_weightings["objective"].iloc[0] == 1.0


def test_a_repeated_period_with_a_negative_weighting_is_reported(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        ws.cell(ws.max_row, 3).value = -10.0

    res = _edit(_write(_network(True), tmp_path), fn)
    assert any(
        why == "investment period weighting: a weighting cannot be negative"
        for _, why in _um(res)
    )


@pytest.mark.parametrize("text", ["#N/A", "#REF!", "#DIV/0!"])
def test_an_error_code_text_is_written_as_text(tmp_path, text):
    n = _network(False)
    n.carriers.loc["wind", "nice_name"] = text
    path = _write(n, tmp_path)
    ws = openpyxl.load_workbook(path)["Carriers"]
    cell = ws.cell(L.FIRST_DATA_ROW + 1, _col(ws, "nice_name"))
    assert (cell.value, cell.data_type) == (text, "s")
    assert _read(path).network.carriers.at["wind", "nice_name"] == text


# ── GK B ask 1, option A (X1-X6): an unedited round trip is bit for bit ──────

#: Values %.16g or a decimal shift would change; the two collision pairs show as
#: one float at their scale (1e-3, 100), so only the Exact values sheet tells them apart.
HARD = [
    0.1 + 0.2,
    0.187 / 0.4,
    1.2345678901234567e130,
    -9.876543210987654e-130,
    0.001918771917354847,
    0.0019187719173548471,
    190446.44222042646,
    190446.44222042648,
]


def _hard_network(v: float) -> pypsa.Network:
    n = _network(False)
    n.generators.loc[
        "wind 1", ["p_nom", "capital_cost", "efficiency", "marginal_cost"]
    ] = v
    n.generators_t.efficiency = pd.DataFrame(
        {"wind 1": [v, 0.5, 0.25]}, index=n.snapshots
    )
    n.loads_t.p_set.iloc[0, 0] = v
    n.snapshot_weightings.iloc[0, 0] = abs(v)
    return n


@pytest.mark.parametrize("v", HARD, ids=repr)
def test_an_unedited_round_trip_is_exact_at_every_site(tmp_path, v):
    n = _hard_network(v)
    solver = {
        "discount_rate": v,
        "multi_investment_periods": False,
        "investment_periods": [],
    }
    res = _read(_write(n, tmp_path, sidecars={"solver_config.json": solver}))
    out = res.network
    g = out.generators.loc["wind 1"]
    assert g["p_nom"] == v  # unscaled static
    assert g["marginal_cost"] == v  # unscaled static
    assert g["capital_cost"] == v  # x 1e-3
    assert g["efficiency"] == v  # x 100
    assert (
        out.generators_t.efficiency.at[n.snapshots[0], "wind 1"] == v
    )  # scaled series
    assert out.loads_t.p_set.iat[0, 0] == v  # unscaled series
    assert out.snapshot_weightings.iat[0, 0] == abs(v)  # weighting
    assert res.sidecars["solver_config.json"]["discount_rate"] == v  # x 100, Horizon
    assert not [u for u in _um(res) if u[0].startswith(L.EXACT)]


def test_a_collision_pair_is_told_apart_only_by_the_exact_sheet(tmp_path):
    a, b = 0.001918771917354847, 0.0019187719173548471
    assert L.rescale(a, 1e-3) == L.rescale(b, 1e-3)
    n = _network(False)
    n.generators.loc["wind 1", "capital_cost"] = b
    path = _write(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    assert L.EXACT in wb.sheetnames and wb[L.EXACT].sheet_state == "hidden"
    assert _read(path).network.generators.at["wind 1", "capital_cost"] == b
    del wb[L.EXACT]  # a user may delete it: a clean fallback to the visible value
    wb.save(path)
    res = _read(path)
    assert res.network.generators.at["wind 1", "capital_cost"] == L.rescale(
        L.rescale(b, 1e-3), 1e3
    )
    assert _um(res) == []


def _exact_rows(path):
    ws = openpyxl.load_workbook(path)[L.EXACT]
    return ws, [[c.value for c in row] for row in ws.iter_rows(min_row=2)]


def _collision(tmp_path, name="w.xlsx"):
    n = _network(False)
    n.generators.loc["wind 1", "capital_cost"] = 0.0019187719173548471
    return _write(n, tmp_path, name=name)


def test_an_edited_cell_ignores_its_stale_exact_value(tmp_path):
    path = _collision(tmp_path)
    _tamper(path, "Generators", L.FIRST_DATA_ROW, "capital_cost", 2.5)
    res = _read(path)
    assert res.network.generators.at["wind 1", "capital_cost"] == 2500.0
    assert _um(res) == []


def test_a_moved_row_keeps_its_exact_value_by_name(tmp_path):
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb["Generators"]
    a, b = L.FIRST_DATA_ROW, L.FIRST_DATA_ROW + 1
    for c in range(1, ws.max_column + 1):  # swap the two rows: a sort in Excel
        x, y = ws.cell(a, c), ws.cell(b, c)
        x.value, y.value = y.value, x.value
        for cell in (x, y):
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"
    wb.save(path)
    res = _read(path)
    assert res.network.generators.at["wind 1", "capital_cost"] == 0.0019187719173548471
    assert _um(res) == []


@pytest.mark.parametrize(
    ("name", "reported"),
    [
        ("electrolyser out", False),  # that cell differs: a stale copy, ignored
        ("ghost", True),  # no such cell: an orphan, reported
    ],
)
def test_an_exact_value_keyed_to_another_asset_is_not_applied(tmp_path, name, reported):
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb[L.EXACT].cell(2, 2).value = name
    wb.save(path)
    res = _read(path)
    v = res.network.generators.at["wind 1", "capital_cost"]
    assert v == L.rescale(L.rescale(0.0019187719173548471, 1e-3), 1e3)
    assert res.network.generators.at["electrolyser out", "capital_cost"] == 0.0
    orphan = ("Exact values row 2", "no cell matches this exact value; ignored")
    assert (orphan in _um(res)) is reported


@pytest.mark.parametrize(
    ("col", "value", "reason"),
    [
        (6, "5.0", "the exact value does not show as its cell; ignored"),
        (6, "0.0019187719173548472", "not a readable exact value; ignored"),
        (6, "nan", "not a readable exact value; ignored"),
        (6, "inf", "not a readable exact value; ignored"),
        (6, True, "not a readable exact value; ignored"),
        (6, 0.0019187719173548471, "not a readable exact value; ignored"),
        (5, "=1+1", "a formula in the exact values; ignored"),
    ],
)
def test_a_tampered_exact_value_is_reported_and_moves_nothing(
    tmp_path, col, value, reason
):
    """X3: a bad entry falls back to the visible value, so it moves a value by at most 2 ulp."""
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb[L.EXACT].cell(2, col).value = value
    wb.save(path)
    res = _read(path)
    v = res.network.generators.at["wind 1", "capital_cost"]
    assert v in (0.001918771917354847, 0.0019187719173548471)
    assert ("Exact values row 2", reason) in _um(res)


def test_a_tampered_exact_value_can_pick_only_a_float_that_shows_alike(tmp_path):
    """The other collision partner shows as the same cell, so it is accepted: 1 ulp."""
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb[L.EXACT].cell(2, 6).value = repr(0.001918771917354847)
    wb.save(path)
    v = _read(path).network.generators.at["wind 1", "capital_cost"]
    assert v == 0.001918771917354847
    assert abs(v - 0.0019187719173548471) == math.ulp(v)


def test_a_second_exact_value_for_one_cell_drops_both(tmp_path):
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb[L.EXACT]
    for c in range(1, len(L.EXACT_COLUMNS) + 1):
        ws.cell(3, c).value = ws.cell(2, c).value
    wb.save(path)
    res = _read(path)
    assert res.network.generators.at["wind 1", "capital_cost"] == L.rescale(
        L.rescale(0.0019187719173548471, 1e-3), 1e3
    )
    assert (
        "Exact values row 3",
        "a second exact value for the same cell; both ignored",
    ) in _um(res)


def test_a_visible_sheet_with_the_reserved_name_is_not_read(tmp_path):
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    wb[L.EXACT].sheet_state = "visible"
    wb.save(path)
    res = _read(path)
    assert any(item == L.EXACT and "reserved name" in why for item, why in _um(res))
    assert res.network.generators.at["wind 1", "capital_cost"] == L.rescale(
        L.rescale(0.0019187719173548471, 1e-3), 1e3
    )


def test_the_exact_sheet_counts_toward_the_limits(tmp_path, monkeypatch):
    path = _collision(tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb[L.EXACT]
    for r in range(3, 40):
        ws.cell(r, 1).value = "x"
    wb.save(path)
    rows = max(
        openpyxl.load_workbook(path)[s].max_row for s in wb.sheetnames if s != L.EXACT
    )
    monkeypatch.setitem(L.LIMITS, "rows", max(rows, 38))
    with pytest.raises(L.ImportLimitError) as e:
        _read(path)
    assert e.value.limit == "rows"


def test_the_float_formatter_is_restored_when_the_save_fails(tmp_path, monkeypatch):
    from openpyxl.cell import _writer

    original = _writer.safe_string
    seen = []

    def boom(self, *a, **k):
        seen.append(_writer.safe_string(0.1 + 0.2))
        raise OSError("disk full")

    monkeypatch.setattr(openpyxl.Workbook, "save", boom)
    with pytest.raises(OSError):
        _write(_network(False), tmp_path)
    assert seen == ["0.30000000000000004"]  # swapped during the save
    assert _writer.safe_string is original
    assert _writer.safe_string(0.1 + 0.2) == "0.3"


def test_the_formula_pass_stops_at_the_cell_limit(tmp_path, monkeypatch):
    """The cap holds in the first pass too: it refuses before reading the rest of the sheet."""
    import re
    import zipfile

    small = b'<dimension ref="A1:A1"/>'
    path = _write(_network(False), tmp_path)
    assert openpyxl.load_workbook(path).sheetnames[0] == L.ABOUT
    with zipfile.ZipFile(path) as zf:
        members = [m for m in zf.namelist() if m.startswith("xl/worksheets/sheet")]
    for m in members:  # every declared size passes, so only the passes count cells
        _patch_xml(path, m, lambda d: re.sub(rb'<dimension ref="[^"]+"/>', small, d))
    _patch_xml(  # the first sheet read is damaged past the limit
        path,
        "xl/worksheets/sheet1.xml",
        lambda d: d[: int(d.index(b"</sheetData>") * 0.8)],
    )
    monkeypatch.setitem(L.LIMITS, "cells", 10)
    with pytest.raises(L.ImportLimitError) as e:
        _read(path)
    assert e.value.limit == "cells"


# ── ticket 04: the exact-value bound and the workbook-wide cap ────────────────


def _nudge(x: float, k: int) -> float:
    for _ in range(abs(k)):
        x = math.nextafter(x, math.inf if k > 0 else -math.inf)
    return x


def _exact_entry(tmp_path, key, shown, stored, name="e.xlsx"):
    """A collision workbook whose `key` cell for wind 1 shows `shown`, with one exact entry."""
    path = _collision(tmp_path, name=name)
    _tamper(path, "Generators", L.FIRST_DATA_ROW, key, shown)
    wb = openpyxl.load_workbook(path)
    ws = wb[L.EXACT]
    ws.cell(2, 3).value = key
    ws.cell(2, 5).value = repr(shown)
    ws.cell(2, 6).value = repr(stored)
    wb.save(path)
    return _read(path)


@pytest.mark.parametrize("shown", [5e-324, 0.0, -0.0], ids=repr)
@pytest.mark.parametrize("k", [2, -2, 3, -3])
def test_an_exact_value_is_kept_only_within_two_ulps_of_its_cell(tmp_path, shown, k):
    """T1 at a non-unit scale (capital cost, x 1e-3): every float here shows alike."""
    visible = L.rescale(shown, 1e3)
    stored = _nudge(visible, k)
    assert L.rescale(stored, 1e-3) == shown  # only the bound can tell them apart
    res = _exact_entry(tmp_path, "capital_cost", shown, stored)
    v = res.network.generators.at["wind 1", "capital_cost"]
    if abs(k) <= 2:
        assert v == stored and not [u for u in _um(res) if u[0].startswith(L.EXACT)]
    else:
        assert v == visible
        assert (
            "Exact values row 2",
            "the exact value is too far from its cell; ignored",
        ) in _um(res)


def test_an_exact_value_for_an_unscaled_cell_is_reported(tmp_path):
    """T1 at scale 1: an unscaled cell never needs an entry, so one is ignored."""
    stored = _nudge(5.0, 2)
    res = _exact_entry(tmp_path, "marginal_cost", 5.0, stored)
    assert res.network.generators.at["wind 1", "marginal_cost"] == 5.0
    assert (
        "Exact values row 2",
        "the exact value does not show as its cell; ignored",
    ) in _um(res)


def test_a_tampered_exact_value_on_a_zero_cell_is_reported(tmp_path):
    res = _exact_entry(tmp_path, "capital_cost", 0.0, 1.0)
    assert res.network.generators.at["wind 1", "capital_cost"] == 0.0
    assert (
        "Exact values row 2",
        "the exact value does not show as its cell; ignored",
    ) in _um(res)


def test_a_stored_subnormal_shown_as_zero_round_trips_through_its_exact_value(
    tmp_path,
):
    """T2: the writer does emit an entry whose shown value is 0.0, so the reader keeps it."""
    n = _network(False)
    n.generators.loc["wind 1", "capital_cost"] = 5e-324
    path = _write(n, tmp_path)
    _, rows = _exact_rows(path)
    assert rows == [["Generators", "wind 1", "capital_cost", None, "0.0", "5e-324"]]
    res = _read(path)
    assert res.network.generators.at["wind 1", "capital_cost"] == 5e-324
    assert _um(res) == []


def _declared(path) -> int:
    wb = openpyxl.load_workbook(path, read_only=True)
    return sum((ws.max_row or 0) * (ws.max_column or 0) for ws in wb.worksheets)


def test_the_workbook_cell_cap_counts_every_sheet_s_declared_size(
    tmp_path, monkeypatch
):
    """T4: the boundary value passes, one less is refused before any cell is read."""
    path = _write(_network(False), tmp_path)
    total = _declared(path)
    monkeypatch.setitem(L.LIMITS, "workbook_cells", total)
    _read(path)
    monkeypatch.setitem(L.LIMITS, "workbook_cells", total - 1)
    from gridspine.ingest import excel_import

    scanned = []
    real = excel_import._capped
    monkeypatch.setattr(
        excel_import, "_capped", lambda rows: scanned.append(1) or real(rows)
    )
    with pytest.raises(L.ImportLimitError) as e:
        _read(path)
    assert e.value.limit == "workbook_cells"
    assert (e.value.value, scanned) == (total, []), "refused by the scanned count"


def test_the_workbook_cell_cap_holds_when_the_declared_sizes_lie_low(
    tmp_path, monkeypatch
):
    import re

    path = _write(_network(False), tmp_path)
    for i in range(len(openpyxl.load_workbook(path).sheetnames)):
        path = _patch_xml(
            path,
            f"xl/worksheets/sheet{i + 1}.xml",
            lambda d: re.sub(
                rb'<dimension ref="[^"]+"/>', b'<dimension ref="A1:A1"/>', d
            ),
        )
    sheets = len(openpyxl.load_workbook(path).sheetnames)
    assert _declared(path) == sheets
    monkeypatch.setitem(L.LIMITS, "workbook_cells", 50)  # above the declared sum
    with pytest.raises(L.ImportLimitError) as e:
        _read(path)
    assert e.value.limit == "workbook_cells"


def test_many_sparse_sheets_are_refused_at_once(tmp_path):
    """T4: the file from the #1646 review (25 sparse sheets: 12 s and 459 MB before)."""
    import time

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for i in range(25):
        ws = wb.create_sheet(f"s{i}")
        ws["A1"] = "x"
        ws["XFD800"] = "x"  # 13.1M cells declared: under the per-sheet cap
    path = tmp_path / "sparse.xlsx"
    wb.save(path)
    t = time.monotonic()
    with pytest.raises(L.ImportLimitError) as e:
        _read(path)
    assert e.value.limit == "workbook_cells"
    assert time.monotonic() - t < 10


def _golden_cases():
    golden = pathlib.Path(__file__).with_name("excel_golden.json")
    return sorted(json.loads(golden.read_text()))


@pytest.mark.parametrize("case", _golden_cases())
def test_a_valid_workbook_reads_byte_for_byte_as_the_golden(tmp_path, case):
    """T5: every frame, dtype, index and sidecar as the reader on the golden's commit read it."""
    from tests.gridspine import excel_golden

    golden = json.loads(excel_golden.GOLDEN.read_text())[case]
    n, sidecars = excel_golden.cases()[case]
    got = excel_golden.snapshot(excel_golden.read_back(n, sidecars, tmp_path))
    assert got["snapshots"] == golden["snapshots"]
    assert got["sidecars"] == golden["sidecars"]
    assert got["sidecar_floats"] == golden["sidecar_floats"]
    assert got["frames"].keys() == golden["frames"].keys()
    for name, frame in golden["frames"].items():
        assert got["frames"][name] == frame, name


# ── GK B 67 on #1732: what PyPSA's own layout reads outside the component sheets ──

_NOT_INPUT = "not an input of this kind of asset; the column was not read"
_NOT_SETTING = "not a network setting this import reads; the column was not read"
_NOT_READ_SHEET = "not a sheet this import reads; its rows were not read"


def _set_header(ws, header, value):
    """Set `header`'s cell in row 2, adding the column when it is not there."""
    heads = [c.value for c in ws[1]]
    if header not in heads:
        ws.cell(row=1, column=len(heads) + 1, value=header)
        heads.append(header)
    ws.cell(row=2, column=heads.index(header) + 1, value=value)


def test_the_network_sheet_sets_only_the_settings_an_export_writes(tmp_path):
    def edit(wb):
        ws = wb["network"]
        for header, value in [
            ("evil_attr", "x"),
            ("objective", 123.0),
            ("objective_constant", 4.0),
            ("model", "m"),
            ("optimize", 1),
            ("srid", "not a number"),
        ]:
            _set_header(ws, header, value)
        ws.cell(row=1, column=ws.max_column + 1, value="name")  # a second `name`
        ws.cell(row=2, column=ws.max_column, value="second")
        ws.cell(row=2, column=ws.max_column + 1, value="no header")

    res = _native_wb(tmp_path, edit)
    n, um = res.network, _um(res)
    for header in ("evil_attr", "objective", "objective_constant", "model", "optimize"):
        assert (f"network · column {header}", _NOT_SETTING) in um
    assert ("network · column name", _NOT_SETTING) in um
    assert any(src == "network · srid" for src, _ in um)
    assert any(src.startswith("network · column ") and r == _NO_HEADER for src, r in um)
    assert not hasattr(n, "evil_attr")
    assert n._objective is None and n._objective_constant is None and n._model is None
    assert not isinstance(n.optimize, int)
    assert n.srid == 4326
    assert n.name != "second"
    assert not any(k.startswith(("Unnamed", "name.")) for k in vars(n))


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("pypsa_version", "not.a.version!"),
        ("_multi_invest", 7),
        ("name", "NA"),
        ("srid", 0),
    ],
)
def test_a_network_setting_the_model_cannot_take_is_reported_and_not_read(
    tmp_path, header, value
):
    res = _native_wb(tmp_path, lambda wb: _set_header(wb["network"], header, value))
    assert any(src == f"network · {header}" for src, _ in _um(res))
    assert getattr(res.network, header, None) != value


def test_sheets_pypsa_would_load_unchecked_are_removed_and_reported(tmp_path):
    def edit(wb):
        ws = wb.create_sheet("line_types")
        ws.append(["name", "zzz", "r_per_length"])
        ws.append(["evil", 1, 0.1])
        ws = wb["meta"] if "meta" in wb.sheetnames else wb.create_sheet("meta")
        ws.append(["evil", '{"injected": true}'])
        ws = wb.create_sheet("shapes")
        ws.append(["name", "geometry"])
        ws.append(["s1", "POINT (0 0)"])
        wb.create_sheet("sub_networks").append(["name"])  # empty: removed silently
        wb.copy_worksheet(wb["crs"]).title = "crs_copy"  # a crs, but not named so

    res = _native_wb(tmp_path, edit)
    n, um = res.network, _um(res)
    for sheet in ("line_types", "meta", "shapes", "crs_copy"):
        assert (sheet, _NOT_READ_SHEET) in um
    assert "sub_networks" not in {src for src, _ in um}
    assert "zzz" not in n.line_types.columns and "evil" not in n.line_types.index
    assert n.meta == {}
    assert n.shapes.empty


def test_a_tampered_crs_sheet_is_removed_and_reported(tmp_path):
    def edit(wb):
        ws = wb["crs"]
        ws.cell(row=2, column=2, value="not a coordinate system")

    res = _native_wb(tmp_path, edit)
    assert ("crs", _NOT_READ_SHEET) in _um(res)
    assert res.network.crs.to_epsg() == 4326


def test_pypsa_s_own_crs_sheet_is_read_without_a_report(tmp_path):
    res = read_excel(_native(_two_gens(), tmp_path))
    assert _um(res) == []


def _pad_crs(ws, length):
    ws["B2"] = ws["B2"].value.ljust(length)


@pytest.mark.parametrize(
    "edit",
    [lambda ws: ws.delete_rows(2, 10), lambda ws: _pad_crs(ws, 20_000)],
    ids=["empty", "longest"],
)
def test_a_crs_sheet_pypsa_reads_is_read_without_a_report(tmp_path, edit):
    res = _native_wb(tmp_path, lambda wb: edit(wb["crs"]))
    assert _um(res) == []


@pytest.mark.parametrize(
    "edit",
    [
        lambda ws: ws.cell(row=2, column=1, value="evil"),
        lambda ws: ws.append(["_crs", ws["B2"].value]),
        lambda ws: ws.delete_cols(2),
        lambda ws: ws.cell(row=2, column=2, value=4326),
        lambda ws: _pad_crs(ws, 20_001),
    ],
    ids=["key", "two-rows", "no-value", "number", "too-long"],
)
def test_a_crs_sheet_pypsa_does_not_write_is_removed_and_reported(tmp_path, edit):
    res = _native_wb(tmp_path, lambda wb: edit(wb["crs"]))
    assert ("crs", _NOT_READ_SHEET) in _um(res)
    assert res.network.crs.to_epsg() == 4326


@pytest.mark.parametrize(
    ("column", "value", "reason"),
    [
        ("objective", "inf", "infinity is not allowed here"),
        ("generators", "nan", "not a number (NaN)"),
        ("stores", "NA", "“NA” is not a number"),
        ("objective", -1, "a weighting cannot be negative"),
    ],
)
def test_a_snapshot_weighting_is_checked_as_written(tmp_path, column, value, reason):
    def edit(wb):
        ws = wb["snapshots"]
        col = [c.value for c in ws[1]].index(column) + 1
        ws.cell(row=3, column=col, value=value)

    res = _native_wb(tmp_path, edit)
    hits = [r for src, r in _um(res) if src == "snapshots row 3"]
    assert len(hits) == 1 and hits[0].startswith(f"{column}: {reason}")
    assert hits[0].endswith("the weighting 1 is used")
    assert res.network.snapshot_weightings[column].tolist() == [1.0, 1.0]


@pytest.mark.parametrize("value", [None, "NA"])
def test_a_snapshot_with_no_name_refuses_the_file(tmp_path, value):
    def edit(wb):
        ws = wb["snapshots"]
        col = [c.value for c in ws[1]].index("snapshot") + 1
        ws.cell(row=3, column=col).value = value

    with pytest.raises(L.ImportFormatError, match="row 3 has no name"):
        _native_wb(tmp_path, edit)


def test_a_repeated_snapshot_refuses_the_file(tmp_path):
    def edit(wb):
        ws = wb["snapshots"]
        col = [c.value for c in ws[1]].index("snapshot") + 1
        ws.cell(row=3, column=col, value=ws.cell(row=2, column=col).value)

    with pytest.raises(L.ImportFormatError, match="row 3 repeats an earlier row"):
        _native_wb(tmp_path, edit)


def test_a_snapshots_sheet_with_no_level_header_is_keyed_by_its_first_column(
    tmp_path,
):
    def edit(wb):
        ws = wb["snapshots"]
        col = [c.value for c in ws[1]].index("snapshot") + 1
        ws.cell(row=1, column=col, value="x")

    res = _native_wb(tmp_path, edit)
    assert _um(res) == []
    assert len(res.network.snapshots) == 2


def _multi_period() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(
        pd.MultiIndex.from_product(
            [[2030, 2040], pd.date_range("2030-01-01", periods=2, freq="h")]
        )
    )
    n.investment_periods = [2030, 2040]
    n.add("Bus", "B1", v_nom=110.0)
    n.add("Generator", "G1", bus="B1", p_nom=5.0, p_max_pu=[0.1, 0.2, 0.3, 0.4])
    return n


def _period_edit(tmp_path, edit):
    path = _native(_multi_period(), tmp_path)
    wb = openpyxl.load_workbook(path)
    edit(wb["investment_periods"])
    wb.save(path)
    return read_excel(path)


def test_an_investment_period_weighting_is_checked_as_written(tmp_path):
    def edit(ws):
        ws["B3"] = "inf"
        ws["C3"] = "N/A"
        ws["D1"], ws["D2"] = "evil", 9

    res = _period_edit(tmp_path, edit)
    um = _um(res)
    rows = [r for src, r in um if src == "investment_periods row 3"]
    assert len(rows) == 2 and all(r.endswith("the weighting 1 is used") for r in rows)
    assert ("investment_periods · column evil", _NOT_INPUT) in um
    w = res.network.investment_period_weightings
    assert w.loc[2040].tolist() == [1.0, 1.0] and "evil" not in w.columns


def test_pypsa_s_own_investment_periods_are_read_without_a_report(tmp_path):
    res = read_excel(_native(_multi_period(), tmp_path))
    assert _um(res) == []
    assert res.network.investment_period_weightings.shape == (2, 2)


@pytest.mark.parametrize(
    ("value", "match"), [("2040x", "row 3 is not a year"), (2030, "row 3 repeats")]
)
def test_a_bad_investment_period_refuses_the_file(tmp_path, value, match):
    def edit(ws):
        ws["A3"] = value

    with pytest.raises(L.ImportFormatError, match=match):
        _period_edit(tmp_path, edit)


def test_a_series_sheet_off_the_snapshots_is_not_read(tmp_path):
    def edit(wb):
        wb["generators-p_max_pu"]["A3"] = 99
        wb["generators-p_max_pu"]["B2"] = "x"  # not checked: the sheet is not read

    res = _native_wb(tmp_path, edit)
    assert (
        "generators-p_max_pu",
        "its first column is not the time steps of the snapshots sheet; "
        "the sheet was not read",
    ) in _um(res)
    assert res.network.generators_t.p_max_pu.empty
    assert not any(src.startswith("generators-p_max_pu ·") for src, _ in _um(res))


def test_an_empty_column_without_a_header_is_dropped_without_a_report(tmp_path):
    def edit(wb):
        ws = wb["network"]
        col = ws.max_column + 2  # the column before it stays empty
        ws.cell(row=1, column=col, value="evil")
        ws.cell(row=2, column=col, value=1)

    res = _native_wb(tmp_path, edit)
    assert _um(res) == [("network · column evil", _NOT_SETTING)]


def test_a_column_that_is_not_read_is_not_checked(tmp_path):
    def edit(wb):
        ws = wb["generators"]
        ws.cell(row=1, column=ws.max_column + 1, value="evil")
        ws.cell(row=2, column=ws.max_column, value="NA")

    res = _native_wb(tmp_path, edit)
    assert _um(res) == [("generators · column evil", _NOT_INPUT)]
    assert list(res.network.generators.index) == ["G1", "G2"]


def test_text_pandas_reads_as_empty_is_reported_not_turned_into_the_default(tmp_path):
    def edit(wb):
        ws = wb["buses"]
        heads = [c.value for c in ws[1]]
        if "carrier" not in heads:
            ws.cell(row=1, column=len(heads) + 1, value="carrier")
            heads.append("carrier")
        ws.cell(row=2, column=heads.index("carrier") + 1, value="N/A")

    res = _native_wb(tmp_path, edit)
    assert (
        "buses · B1",
        "carrier: the text “N/A” reads as empty; leave the cell empty instead",
    ) in _um(res)
    assert "B1" not in res.network.buses.index


def test_an_asset_named_with_text_pandas_reads_as_empty_is_reported(tmp_path):
    def edit(wb):
        wb["generators"]["A3"] = "NA"

    res = _native_wb(tmp_path, edit)
    assert ("generators row 3", "the row has values but no name") in _um(res)
    assert "nan" not in res.network.generators.index
    assert list(res.network.generators.index) == ["G1"]


# ── GK B 67 ask 4: nothing points at an asset that was not read ────────────────


def test_assets_on_a_removed_bus_are_reported_and_removed(tmp_path):
    n = _two_gens()
    n.add("Line", "l1", bus0="B2", bus1="B1", x=0.1, s_nom=50.0)
    n.add("Load", "D2", bus="B2", p_set=1.0)
    res = read_excel(_native_edit(tmp_path, n, "buses", "v_nom", "B1", "nan"))
    um, net = _um(res), res.network
    for item, col in [
        ("generators · G1", "bus"),
        ("generators · G2", "bus"),
        ("lines · l1", "bus1"),
    ]:
        assert (
            item,
            f"{col}: “B1” was not read, so this asset was not read either",
        ) in um
    assert net.generators.empty and net.lines.empty
    assert list(net.loads.index) == ["D2"] and list(net.buses.index) == ["B2"]
    assert net.generators_t.p_max_pu.empty
    assert res.report.counts.get("Generators", 0) == 0


def test_an_asset_on_two_removed_buses_is_reported_for_the_first(tmp_path):
    n = _two_gens()
    n.add("Line", "l1", bus0="B1", bus1="B2", x=0.1, s_nom=50.0)
    path = _native(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    for r in (2, 3):
        wb["buses"].cell(row=r, column=2, value="nan")  # B1's and B2's v_nom
    wb.save(path)
    um = [item for item in _um(read_excel(path)) if item[0] == "lines · l1"]
    assert um == [
        ("lines · l1", "bus0: “B1” was not read, so this asset was not read either")
    ]


def test_a_removed_carrier_takes_its_buses_and_their_assets(tmp_path):
    n = _two_gens()
    n.add("Carrier", "DC", co2_emissions=0.0)
    n.buses.loc["B1", "carrier"] = "DC"
    res = read_excel(
        _native_edit(tmp_path, n, "carriers", "co2_emissions", "DC", "nan")
    )
    um = _um(res)
    assert (
        "buses · B1",
        "carrier: “DC” was not read, so this asset was not read either",
    ) in um
    assert (
        "generators · G1",
        "bus: “B1” was not read, so this asset was not read either",
    ) in um
    assert list(res.network.buses.index) == ["B2"] and res.network.generators.empty


def test_a_removed_carrier_is_matched_on_the_carrier_column_only(tmp_path):
    n = _two_gens()  # both generators have control "PQ" and no carrier
    n.add("Carrier", "PQ", co2_emissions=0.0)
    res = read_excel(
        _native_edit(tmp_path, n, "carriers", "co2_emissions", "PQ", "nan")
    )
    assert _um(res) == [("carriers · PQ", "co2_emissions: not a number (NaN)")]
    assert list(res.network.generators.index) == ["G1", "G2"]


def test_the_raw_sheets_layout_drops_what_refers_to_a_skipped_row(tmp_path):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in {
        "buses": [["name", "v_nom"], ["B1", "x"], ["B2", 110.0]],
        "generators": [["name", "bus", "p_nom"], ["G1", "B1", 5.0], ["G2", "B2", 1.0]],
        "lines": [["name", "bus0", "bus1", "x"], ["l1", "B2", "B1", 0.1]],
    }.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    path = tmp_path / "raw.xlsx"
    wb.save(path)
    res = read_excel(path)
    um = _um(res)
    assert (
        "generators · G1",
        "bus: “B1” was not read, so this asset was not read either",
    ) in um
    assert (
        "lines · l1",
        "bus1: “B1” was not read, so this asset was not read either",
    ) in um
    assert list(res.network.generators.index) == ["G2"] and res.network.lines.empty
    assert res.report.counts["Generators"] == 1 and res.report.counts["Lines"] == 0


# ── GK B 68 on #1732 @69dccb3e ─────────────────────────────────────────────────


def test_an_empty_snapshot_weighting_is_reported_and_set_to_one(tmp_path):
    def edit(wb):
        ws = wb["snapshots"]
        col = [c.value for c in ws[1]].index("objective") + 1
        ws.cell(row=3, column=col).value = None

    res = _native_wb(tmp_path, edit)
    assert (
        "snapshots row 3",
        "objective: the cell is empty; the weighting 1 is used",
    ) in _um(res)
    assert res.network.snapshot_weightings["objective"].tolist() == [1.0, 1.0]


def test_an_empty_period_weighting_is_reported_and_set_to_one(tmp_path):
    def edit(ws):
        ws["C3"] = None

    res = _period_edit(tmp_path, edit)
    assert (
        "investment_periods row 3",
        "years: the cell is empty; the weighting 1 is used",
    ) in _um(res)
    assert res.network.investment_period_weightings["years"].tolist() == [1.0, 1.0]


def test_an_empty_row_inside_the_snapshots_refuses_the_file(tmp_path):
    def edit(wb):
        wb["snapshots"].insert_rows(3)

    with pytest.raises(L.ImportFormatError, match="row 3 has no name"):
        _native_wb(tmp_path, edit)


def test_empty_rows_after_the_last_snapshot_are_not_a_time_step(tmp_path):
    def edit(wb):
        for sheet in ("snapshots", "generators-p_max_pu"):
            ws = wb[sheet]
            ws.cell(row=ws.max_row + 2, column=1).value = None  # styled, empty
            ws.cell(row=ws.max_row, column=1).number_format = "0.00"

    res = _native_wb(tmp_path, edit)
    assert _um(res) == []
    assert len(res.network.snapshots) == 2


def test_an_empty_row_inside_a_series_sheet_drops_the_sheet(tmp_path):
    def edit(wb):
        wb["generators-p_max_pu"].insert_rows(3)

    res = _native_wb(tmp_path, edit)
    assert (
        "generators-p_max_pu",
        "its first column is not the time steps of the snapshots sheet; "
        "the sheet was not read",
    ) in _um(res)
    assert res.network.generators_t.p_max_pu.empty
    assert not res.network.generators_t.p_max_pu.isna().any().any()


def test_a_line_or_transformer_of_an_unknown_type_is_reported_and_not_read(tmp_path):
    n = _two_gens()
    n.add("Line", "l1", bus0="B1", bus1="B2", type="no such type", s_nom=1.0)
    n.add("Line", "l2", bus0="B1", bus1="B2", type="149-AL1/24-ST1A 110.0")
    n.add("Transformer", "t1", bus0="B1", bus1="B2", type="mine")
    path = _native(n, tmp_path)
    wb = openpyxl.load_workbook(path)
    ws = wb.create_sheet("transformer_types")  # the file's own type: never read
    ws.append(["name", "s_nom", "v_nom_0", "v_nom_1"])
    ws.append(["mine", 40.0, 220.0, 110.0])
    wb.save(path)
    res = read_excel(path)
    um = _um(res)
    assert (
        "lines · l1",
        "type: “no such type” is not a standard line type; the asset was not read",
    ) in um
    assert (
        "transformers · t1",
        "type: “mine” is not a standard transformer type; the asset was not read",
    ) in um
    assert ("transformer_types", _NOT_READ_SHEET) in um
    assert list(res.network.lines.index) == ["l2"] and res.network.transformers.empty
    res.network.consistency_check()


def test_the_raw_sheets_layout_reports_an_unknown_line_type(tmp_path):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet, rows in {
        "buses": [["name", "v_nom"], ["B1", 110.0], ["B2", 110.0]],
        "lines": [["name", "bus0", "bus1", "type"], ["l1", "B1", "B2", "nope"]],
    }.items():
        ws = wb.create_sheet(sheet)
        for row in rows:
            ws.append(row)
    path = tmp_path / "raw.xlsx"
    wb.save(path)
    res = read_excel(path)
    assert (
        "lines · l1",
        "type: “nope” is not a standard line type; the asset was not read",
    ) in _um(res)
    assert res.network.lines.empty and res.report.counts["Lines"] == 0


@pytest.mark.parametrize("value", [" ", "\t"])
def test_a_blank_text_snapshot_name_refuses_the_file(tmp_path, value):
    def edit(wb):
        ws = wb["snapshots"]
        col = [c.value for c in ws[1]].index("snapshot") + 1
        ws.cell(row=3, column=col).value = value

    with pytest.raises(L.ImportFormatError, match="row 3 has no name"):
        _native_wb(tmp_path, edit)


def test_a_blank_text_series_index_drops_the_sheet(tmp_path):
    def edit(wb):
        wb["generators-p_max_pu"]["A3"] = " "

    res = _native_wb(tmp_path, edit)
    assert (
        "generators-p_max_pu",
        "its first column is not the time steps of the snapshots sheet; "
        "the sheet was not read",
    ) in _um(res)
    assert res.network.generators_t.p_max_pu.empty


# ── Pass 4 found: a blank network setting, and an asset named only with spaces ──


@pytest.mark.parametrize("header", ["srid", "pypsa_version", "_multi_invest"])
def test_a_blank_network_setting_keeps_its_default(tmp_path, header):
    def edit(wb):
        ws = wb["network"]
        col = [c.value for c in ws[1]].index(header) + 1
        ws.cell(row=2, column=col).value = None

    res = _native_wb(tmp_path, edit)
    assert _um(res) == []
    assert res.network.srid == 4326
    assert res.network._multi_invest == 0


@pytest.mark.parametrize("name", [" ", "\t"])
def test_an_asset_named_only_with_spaces_is_reported_not_read(tmp_path, name):
    def edit(wb):
        wb["generators"]["A3"] = name

    res = _native_wb(tmp_path, edit)
    assert ("generators row 3", "the row has values but no name") in _um(res)
    assert list(res.network.generators.index) == ["G1"]
