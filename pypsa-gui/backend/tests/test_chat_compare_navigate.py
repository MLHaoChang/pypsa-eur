"""Unit tests for compare_scenarios + enriched ui_open_panel."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from services import chat_tools


def test_ui_open_panel_navigate_fields():
    ev = chat_tools.ui_open_panel(
        panel_id="Results",
        results_tab="dispatch",
        bottom_tab="Generators",
        compare_rail=True,
        compare_a="base",
        compare_b="alt",
        compare_tab="economics",
    )
    assert ev["_ui_event"] is True
    assert ev["kind"] == "navigate"
    assert ev["results_tab"] == "dispatch"
    assert ev["bottom_tab"] == "Generators"
    assert ev["compare_rail"] is True
    assert ev["compare_a"] == "base"
    assert ev["compare_b"] == "alt"
    assert ev["compare_tab"] == "economics"


def test_compare_scenarios_headlines_and_delta(monkeypatch):
    def fake_summary(name: str):
        if name == "A":
            return {
                "project": "A",
                "has_solve": True,
                "is_multi_period": False,
                "periods": [],
                "capacity": {
                    "capacity_mw_by_carrier": {"gas": {"total": 100.0, "by_period": {}}},
                    "capex_meur_by_carrier": {"gas": {"total": 10.0, "by_period": {}}},
                    "new_capex_meur_by_carrier": {},
                },
                "dispatch": {
                    "dispatch_gwh_by_carrier": {"gas": {"total": 50.0, "by_period": {}}},
                    "opex_meur": {"total": 5.0, "by_period": {}},
                    "total_load_gwh": {"total": 60.0, "by_period": {}},
                },
            }
        return {
            "project": "B",
            "has_solve": True,
            "is_multi_period": False,
            "periods": [],
            "capacity": {
                "capacity_mw_by_carrier": {"gas": {"total": 120.0, "by_period": {}}},
                "capex_meur_by_carrier": {"gas": {"total": 12.0, "by_period": {}}},
                "new_capex_meur_by_carrier": {},
            },
            "dispatch": {
                "dispatch_gwh_by_carrier": {"gas": {"total": 55.0, "by_period": {}}},
                "opex_meur": {"total": 6.0, "by_period": {}},
                "total_load_gwh": {"total": 60.0, "by_period": {}},
            },
        }

    monkeypatch.setattr(chat_tools, "get_project_results_summary", fake_summary)
    out = chat_tools.compare_scenarios("A", "B", focus="capacity", open_compare_rail=True)
    assert out["a"]["capacity_mw_total"] == 100.0
    assert out["b"]["capacity_mw_total"] == 120.0
    assert out["delta_b_minus_a"]["capacity_mw_total"] == 20.0
    assert out["delta_b_minus_a"]["opex_meur"] == 1.0
    assert out["_ui_event"] is True
    assert out["kind"] == "navigate"
    assert out["compare_a"] == "A"
    assert out["compare_b"] == "B"
    assert out["compare_tab"] == "capacity"
    assert out["results_tab"] == "capex"
    assert "focus_section" in out
    assert out["focus_section"]["a"]["capacity_mw_by_carrier"]["gas"]["total"] == 100.0


def test_compare_scenarios_in_dispatchers():
    assert "compare_scenarios" in chat_tools.DISPATCHERS
    assert chat_tools.DISPATCHERS["compare_scenarios"] is chat_tools.compare_scenarios
