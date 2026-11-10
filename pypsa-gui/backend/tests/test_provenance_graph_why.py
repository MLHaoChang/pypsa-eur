"""
provenance-graph 03, "why this number": the assistant's read tool over `why(node)`.

The acceptance example (a PV cost priced from the pack through its DC/AC ratio), the route and the tool, and
that the tool reads only (COPILOT-1).
"""

from __future__ import annotations

import copy
import json

import pypsa
import pytest

from harness import catalogue
from services import chat_tools
from services.asset_schema import provenance as P
from services.provenance_graph import money
from services.provenance_graph.explain import explain
from tests.test_typed_cost_units_stale import (
    _clear_caches,  # noqa: F401 - the autouse fixture
    _fc,
    _pv,
    _strip_scaled_by,
)

RATIO = "input:Generator/g/dc_ac_ratio"
URL = "/api/network/asset_schema/{}/provenance/{}/why/{}"


def _snapshot(n) -> str:
    return json.dumps(
        {
            "meta": n.meta,
            "generators": n.generators.to_dict(),
            "links": n.links.to_dict(),
        },
        sort_keys=True,
        default=str,
    )


# ── the acceptance example ──────────────────────────────────────────────────


def test_why_a_pv_cost_walks_to_the_pack_price_and_the_ratio_with_their_stamps(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    out = explain(n, "Generator", "g", "overnight_cost")
    assert out["state"] == "fresh" and out["node"] == money.derived_id(
        "Generator", "g", "overnight_cost"
    )
    cost, ratio = out["steps"]
    assert cost["value"] == pytest.approx(n.generators.at["g", "overnight_cost"])
    assert cost["unit"] == "EUR/MW"
    stamp = P.get(n, "Generator", "g")["overnight_cost"]
    assert cost["stamp"]["status"] == "default"
    assert cost["stamp"]["source"] == stamp["source"]
    assert cost["stamp"]["source_year"] == stamp["source_year"]
    assert cost["stamp"]["currency_year"] == stamp["currency_year"]
    # The pack price it was scaled from: the value ÷ the ratio pricing recorded.
    assert cost["pack_price"]["value"] == pytest.approx(cost["value"] / 1.2)
    assert cost["pack_price"]["source"] == stamp["source"]
    assert ratio["node"] == RATIO and ratio["value"] == pytest.approx(1.2)
    assert ratio["via"]["edge"] == "priced_through"
    assert ratio["via"]["recorded"]["column"] == "dc_ac_ratio"
    assert ratio["via"]["recorded"]["value"] == pytest.approx(1.2)
    assert (ratio["state"], ratio["broken"]) == ("fresh", None)


def test_why_says_when_the_ratio_has_changed_since_pricing(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.35
    out = explain(n, "Generator", "g", "overnight_cost")
    assert out["state"] == "stale"
    cost, ratio = out["steps"]
    assert cost["state"] == "stale"
    # The pack price is still worked back through the ratio it was priced with, not today's.
    assert cost["pack_price"]["value"] == pytest.approx(cost["value"] / 1.2)
    assert ratio["value"] == pytest.approx(1.35)
    assert ratio["state"] == "stale"
    assert ratio["broken"] == {
        "field": "dc_ac_ratio",
        "recorded": pytest.approx(1.2),
        "current_field": "dc_ac_ratio",
        "current": pytest.approx(1.35),
        "reason": "scale input changed",
    }


def test_the_economics_annualised_cost_walks_both_money_fields_to_the_ratio(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    out = explain(n, "Generator", "g", "capital_cost")
    assert [(s["node"], s["via"] and s["via"]["edge"], s["state"]) for s in out["steps"]] == [
        (money.derived_id("Generator", "g", "capital_cost"), None, "stale"),
        (money.derived_id("Generator", "g", "overnight_cost"), "derived_from", "stale"),
        (RATIO, "priced_through", "stale"),
        (money.derived_id("Generator", "g", "fom_cost"), "derived_from", "stale"),
    ]
    assert out["steps"][0]["unit"] == "EUR/MW/yr"
    assert out["steps"][0]["stamp"] is None


def test_a_link_cost_walks_to_its_output_efficiency(monkeypatch):
    n = _fc()
    n.links.at["fc", "efficiency"] = 0.4
    out = explain(n, "Link", "fc", "overnight_cost")
    assert out["steps"][1]["node"] == "input:Link/fc/efficiency"
    assert out["steps"][1]["broken"]["current"] == pytest.approx(0.4)
    assert out["state"] == "stale"


def test_an_unrecorded_scale_reads_stale_with_its_reason_and_no_pack_price(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    _strip_scaled_by(n, "Generator", "g")
    out = explain(n, "Generator", "g", "overnight_cost")
    cost, scale = out["steps"]
    assert "pack_price" not in cost
    assert scale["via"]["recorded"] == {"unrecorded": money.NOT_RECORDED}
    assert scale["state"] == "stale" and scale["broken"]["reason"] == money.NOT_RECORDED


def test_a_field_nothing_priced_reads_uncompared_never_fresh(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    out = explain(n, "Generator", "g", "lifetime")
    assert out["state"] == "uncompared"
    (step,) = out["steps"]
    assert step["kind"] == "input" and step["stamp"]["status"] == "default"
    # A field never recorded at all: no stamp, still uncompared.
    out = explain(n, "Generator", "g", "p_nom")
    assert out["state"] == "uncompared" and out["steps"][0]["stamp"] is None


def test_a_typed_over_cost_is_its_own_input(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    P.mark_customised(n, "Generator", "g", ["overnight_cost"])
    out = explain(n, "Generator", "g", "overnight_cost")
    assert out["node"] == money.input_id("Generator", "g", "overnight_cost")
    assert [s["state"] for s in out["steps"]] == ["uncompared"]
    assert out["steps"][0]["stamp"]["status"] == "customised"


# ── reads only (COPILOT-1) ──────────────────────────────────────────────────


def test_explain_changes_nothing_on_the_network(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    before = _snapshot(n)
    meta = copy.deepcopy(n.meta)
    for field in ("overnight_cost", "fom_cost", "capital_cost", "lifetime", "p_nom"):
        explain(n, "Generator", "g", field)
    assert _snapshot(n) == before and n.meta == meta


# ── the route and the tool ──────────────────────────────────────────────────


def test_the_tool_is_registered_read_only_on_its_get_route():
    names = {t["name"] for t in catalogue.TOOLS}
    assert "why_this_number" in names and "why_this_number" in chat_tools.DISPATCHERS
    assert catalogue.safety_tier_for("why_this_number") == "read"
    assert catalogue.TOOL_ROUTES["why_this_number"] == [
        ("GET", "/api/network/asset_schema/{component}/provenance/{name}/why/{field}")
    ]
    assert not catalogue.tool_edits_network("why_this_number")
    assert "why_this_number" not in chat_tools.UNDO_CAPTURED_TOOLS


def test_the_route_and_the_tool_answer_the_same_and_write_nothing(
    tmp_path, monkeypatch, client, install_network
):
    n = install_network(_pv(tmp_path, monkeypatch))
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    before = _snapshot(n)
    via_tool = chat_tools.DISPATCHERS["why_this_number"](
        component="Generator", name="g", field="overnight_cost"
    )
    r = client.get(URL.format("Generator", "g", "overnight_cost"))
    assert r.status_code == 200, r.text
    assert r.json() == json.loads(json.dumps(via_tool))
    assert r.json()["state"] == "stale"
    assert r.json()["steps"][1]["broken"]["current"] == pytest.approx(1.3)
    assert _snapshot(n) == before


@pytest.mark.parametrize(
    "component, name, field, code",
    [
        ("Nope", "g", "overnight_cost", 400),
        ("Generator", "missing", "overnight_cost", 404),
        ("Generator", "g", "no_such_field", 404),
    ],
)
def test_the_route_refuses_an_unknown_target(
    install_network, client, component, name, field, code
):
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b")
    install_network(n)
    assert client.get(URL.format(component, name, field)).status_code == code
