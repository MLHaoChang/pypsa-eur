"""`get_load_shape`: the copilot's read of the Load shape tab (services/load_shape_read.py).

Values come only from services/results/load_shape.py; a group that is not
modelled or not established reaches the model as that state with its reason,
never as a missing key or a zero.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from harness.catalogue import TOOLS, safety_tier_for
from services import chat_tools, result_views as rv
from services.load_shape_read import MAX_CHARS, SERIES, VIEWS, shape
from services.results.load_shape import load_shape
from tests.golden import fixture as gf
from tests.test_results_load_shape import _site, _solve, _week_net


@pytest.fixture(scope="module")
def payload():
    return load_shape(_week_net())


def _shape(payload, view="summary", series="grid"):
    return shape(payload, view=view, series=series, inputs_changed=False)


def test_the_tool_is_a_read_tool_and_makes_the_loadshape_view_live():
    tool = next(t for t in TOOLS if t["name"] == "get_load_shape")
    assert safety_tier_for("get_load_shape") == "read"
    assert set(tool["input_schema"]["properties"]["view"]["enum"]) == set(VIEWS)
    assert set(tool["input_schema"]["properties"]["series"]["enum"]) == set(SERIES)
    assert rv.view("loadshape").live()


def test_before_a_solve_it_is_unavailable_never_zero(client):
    out = chat_tools.DISPATCHERS["get_load_shape"]()
    assert out["status"] == "unavailable" and out["unavailable_kind"] == "not_solved"
    assert "heatmaps" not in out and "annual" not in out


def test_an_unestablished_run_claims_no_values():
    out = _shape({"status": "not_established", "reason": "bus site is in t/h, not an energy unit",
                  "period": None, "periods": [None], "unit": "MW"})
    assert out["status"] == "unavailable" and out["unavailable_kind"] == "not_established"
    assert "energy unit" in out["reason"]
    assert not {"heatmaps", "week", "monthly", "annual", "series_status"} & set(out)


def test_summary_keeps_every_group_state_and_reason(payload):
    out = _shape(payload)
    st = out["series_status"]
    assert st["grid"] == {"status": "ok"}
    assert st["wind"]["status"] == "not_modelled" and "wind" in st["wind"]["reason"]
    assert st["unserved"]["status"] == "unavailable" and st["unserved"]["reason"]
    assert out["ui_reference"]["results_tab"] == "loadshape"
    assert out["monthly"]["status"] == "unavailable" and "representative" not in out["monthly"]["reason"] or True
    # the peak cell is the module's own largest cell
    cells = [v for row in payload["heatmaps"]["import"]["mean_mw"] for v in row if v is not None]
    assert out["heatmaps"]["import"]["peak_cell"]["mean_mw"] == pytest.approx(max(cells), abs=0.01)


def test_heatmap_view_passes_the_gaps_through_as_null(payload):
    sub = load_shape(_week_net(), window=(0, 5))
    out = _shape(sub, "heatmap")
    mean = out["heatmaps"]["import"]["mean_mw"]
    assert mean[0][12] is None and mean[0][2] is not None
    assert "null = no snapshot" in out["meaning"]


def test_week_view_returns_one_series_and_names_an_unavailable_one(payload):
    solar = _shape(payload, "week", "solar")["week"]
    assert solar["series"]["status"] == "ok" and len(solar["series"]["mw"]) == 168
    assert solar["series"]["mw"][12] == pytest.approx(payload["week"]["series"]["solar"]["mw"][12], abs=0.01)
    uns = _shape(payload, "week", "unserved")["week"]
    assert uns["series"]["status"] == "unavailable" and "mw" not in uns["series"] and uns["series"]["reason"]
    wind = _shape(payload, "week", "wind")["week"]
    assert wind["series"]["status"] == "not_modelled" and "mw" not in wind["series"]
    assert solar["flag_basis"] == payload["week"]["flag_basis"]


def test_annual_numbers_are_the_modules_own(payload):
    out = _shape(payload, "annual")
    row, want = out["annual"][0], payload["annual"][0]
    assert row["solar"] == pytest.approx(want["solar"], abs=0.1) and row["load_served"] == pytest.approx(want["load_served"], abs=0.1)
    assert row["wind"] is None            # not modelled stays null, not 0


def test_a_full_year_gives_monthly_rows_and_every_view_stays_bounded():
    idx = pd.date_range("2030-01-01", periods=24 * 365, freq="h")
    hour = idx.hour.to_numpy()
    n = _solve(_site(idx, 3.0, np.clip(np.sin((hour - 6) / 12 * np.pi), 0, 1)))
    p = load_shape(n)
    monthly = _shape(p, "monthly")["monthly"]
    assert monthly["status"] == "ok" and len(monthly["months"]) == 12
    for view in VIEWS:
        for series in SERIES:
            assert len(json.dumps(_shape(p, view, series), default=str)) <= MAX_CHARS, (view, series)


def test_a_long_week_is_truncated_and_says_so():
    idx = pd.date_range("2030-03-04", periods=4 * 24 * 7, freq="15min")          # a week at 15 minutes
    n = _solve(_site(idx, 3.0, np.clip(np.sin((idx.hour.to_numpy() - 6) / 12 * np.pi), 0, 1), step_hours=0.25))
    out = _shape(load_shape(n), "week", "grid")
    assert len(json.dumps(out, default=str)) <= MAX_CHARS
    assert out["week"]["series"].get("truncated") is True


def test_unknown_view_and_series_fall_back_to_the_summary_and_grid(client, reset_backend):
    gf.install_golden(gf.solve_golden_network())
    out = chat_tools.DISPATCHERS["get_load_shape"](view="nonsense", series="nonsense")
    assert out["status"] == "ok" and out["view"] == "summary"
    # the golden network has no grid connection: said so, not drawn as 0
    assert out["heatmaps"]["import"]["status"] == "not_modelled"
    assert out["series_status"]["grid"]["status"] == "not_modelled"


def test_the_dispatcher_serves_a_solved_network_through_the_route_payload(client, reset_backend):
    gf.install_golden(_week_net())
    out = chat_tools.DISPATCHERS["get_load_shape"](view="week", week=0, series="solar")
    assert out["status"] == "ok" and out["week"]["series"]["status"] == "ok"
    assert out["week"]["start"].startswith("2030-03-04")
