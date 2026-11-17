"""
The Excel input workbook (IMPORTS-1): round trip, untrusted hidden rows, limits.

GK B plan conditions E1–E8 (.scratch/excel-input-workbook/issues/01-reader-writer.md).
"""

from __future__ import annotations

import json
import math
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
    pd.testing.assert_index_equal(a.snapshots, b.snapshots)
    pd.testing.assert_frame_equal(
        a.snapshot_weightings,
        b.snapshot_weightings,
        check_freq=False,
        check_dtype=weight_dtype,
    )
    pd.testing.assert_frame_equal(
        a.investment_period_weightings,
        b.investment_period_weightings,
        check_dtype=False,
        check_index_type=False,
    )
    for _, _, ln in L.SHEETS:
        x, y = getattr(a, ln), getattr(b, ln)
        assert list(x.index) == list(y.index), ln
        pd.testing.assert_frame_equal(
            x, y.reindex(columns=x.columns), check_dtype=False, obj=ln
        )
        for attr, frame in getattr(a, f"{ln}_t").items():
            if frame.empty:
                continue
            pd.testing.assert_frame_equal(
                frame,
                getattr(b, f"{ln}_t")[attr],
                check_freq=False,
                check_dtype=False,
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
        "Generators › Variable cost",
        f"'{raw}' is not a parameter of Generators",
    ) in _um(res)
    assert raw not in res.network.generators.columns or raw == "p_nom_opt"
    assert res.network.generators.at["wind 1", "marginal_cost"] == 0.0
    if raw == "p_nom_opt":
        assert (res.network.generators["p_nom_opt"] == 0.0).all()


@pytest.mark.parametrize(
    "scale", [1.0, 0.0, -1e-3, float("nan"), float("inf"), "x", True, None]
)
def test_a_hidden_factor_that_does_not_match_its_unit_is_reported(tmp_path, scale):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_SCALE, "capital_cost", scale)
    res = _read(path)
    assert (
        "Generators › Annualised investment",
        "the hidden unit factor (row 4) does not match the unit 'EUR/kW/yr'",
    ) in _um(res)
    assert res.network.generators.at["wind 1", "capital_cost"] == 0.0


def test_a_unit_changed_without_its_factor_is_reported(tmp_path):
    path = _write(_network(False), tmp_path)
    _tamper(path, "Generators", L.ROW_UNIT, "capital_cost", "EUR/MW/yr")
    assert any(
        item == "Generators › Annualised investment" for item, _ in _um(_read(path))
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
        reasons["Time series › d Demand"]
        == "'p_nom' is not a parameter that can change over time"
    )
    assert reasons["Time series › wind 1 P max pu"] == "'Network' is not an asset type"
    assert res.network.loads_t.p_set.empty


def test_a_series_for_an_asset_that_does_not_exist_is_reported(tmp_path):
    path = _write(_network(False), tmp_path)
    _tamper(path, L.TIME_SERIES, L.TS_ROW_ASSET, "d", "ghost")
    assert ("Time series › ghost › p_set", "no Loads row is named 'ghost'") in _um(
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
        ("p_nom", "lots", "'lots' is not a number"),
        ("p_nom_extendable", "maybe", "'maybe' is not TRUE or FALSE"),
        ("p_nom_extendable", 1, "'1' is not TRUE or FALSE"),
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
        f"Generators › wind 1 › {label}",
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
        item == "Generators › wind 1 › Capacity" and "formula" in why
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
    wb.active.title = "buses"
    path = tmp_path / "other.xlsx"
    wb.save(path)
    with pytest.raises(ImportFormatError, match="not the app's input workbook"):
        _read(path)


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
        "Generators › wind 1 › Capacity",
        "'2030-01-02 00:00:00' is not a number; the model default is kept",
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
        "Generators › Variable cost",
        "'5' is not a parameter of Generators",
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
    assert ("Lines › Size unit", "'~size_unit' is not a parameter of Lines") in _um(res)


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
    assert any(item == "Uploaded profiles › other › p_max_pu" for item, _ in _um(res))
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
        "time step: '5' is not a date",
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
    assert any("is not a parameter of Generators" in why for _, why in _um(res))
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
        "Generators › Annualised investment",
        "the unit 'EUR/MW/yr' is not this parameter's unit 'EUR/kW/yr'",
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
        "Generators › electrolyser out › Maximum capacity",
        "only inf is allowed here; the model default is kept",
    ) in _um(res)


def test_a_formula_without_a_saved_value_anywhere_is_reported(tmp_path):
    def fn(wb):
        wb[L.HORIZON]["B2"] = "=7/100"

    res = _edit(
        _write(_network(False), tmp_path, sidecars={"solver_config.json": SOLVER}), fn
    )
    assert (
        f"{L.HORIZON} › cell B2",
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
    assert ("Loads › Site note", "'site_note' is not a parameter of Loads") in _um(res)
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
        item.startswith(f"{L.TIME_SERIES} › d")
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


def test_upload_provenance_past_the_size_cap_is_dropped_with_a_warning(tmp_path):
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
        f"{L.UPLOADED} › other › p_max_pu",
        "uploaded profile: '[NaN, 1]' is not a time step",
    ) in _um(res)
    assert "generators" not in res.sidecars["user_ts.json"]


def test_an_unknown_snapshot_kind_is_reported(tmp_path):
    def fn(wb):
        ws = wb[L.HORIZON]
        r = next(c.row for c in ws["D"] if c.value == "snapshot_kind")
        ws.cell(r, 2).value = "bogus"

    res = _edit(_write(_network(False), tmp_path), fn)
    assert any(why.startswith("'bogus' is not one of") for _, why in _um(res))


def test_an_infinite_value_is_written_as_text_and_refused_where_the_engine_does(
    tmp_path,
):
    n = _network(False)
    n.generators.loc["wind 1", "p_nom"] = float("inf")
    path = _write(n, tmp_path)
    ws = openpyxl.load_workbook(path)["Generators"]
    assert ws.cell(L.FIRST_DATA_ROW, _col(ws, "p_nom")).value == "inf"
    assert any(
        item == "Generators › wind 1 › Capacity"
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
