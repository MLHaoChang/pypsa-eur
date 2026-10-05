"""
The commercial root: `set_site_connection` (IC U1 follow-up, item b).

`PUT /simulation/solver_config` accepts `poc_link`, `export_link` and
`timezone`, but nothing helped the user set them: `attach_tariff`,
`define_participants` and the Investment tab all end in `no_commercial_config`.
`binding.check_site_connection` says whether two Links can be the meter (they
exist, are one-way, and point grid → site and site → grid), and
`set_site_connection` writes ONLY those three keys through the same route,
keeping the rest of a stored commercial config (tariff, contracts and the
value flows, which the route keeps itself).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services import chat_tools
from services.chat_tools_schema import TOOL_ROUTES, safety_tier_for
from services.commercial import binding
from tests.fixtures.investment_case.edge_15min import build_edge_15min

TARIFF = {"id": "t", "name": "t", "jurisdiction": "DE", "valid_from": "2030-01-01",
          "items": [{"id": "e", "kind": "energy", "unit": "per_kwh",
                     "periods": [{"name": "all", "rate": 0.10}]}]}


def _net(export=True, reverse_export=False):
    n = build_edge_15min()
    if export:
        b0, b1 = ("grid", "poc") if reverse_export else ("poc", "grid")
        n.add("Link", "export", bus0=b0, bus1=b1, p_nom=80.0, carrier="AC")
    return n


# ── the check (pure) ───────────────────────────────────────────────────────


def test_a_grid_to_site_poc_and_a_site_to_grid_export_pass():
    binding.check_site_connection(_net(), "import", "export")
    binding.check_site_connection(_net(export=False), "import", None)


@pytest.mark.parametrize("poc,export,code", [
    ("ghost", None, "site_connection_link_missing"),
    ("import", "ghost", "site_connection_link_missing"),
    ("pv", None, "site_connection_link_missing"),          # a Generator, not a Link
    ("import", "import", "site_connection_invalid"),        # one Link cannot be both
])
def test_missing_or_reused_links_are_refused(poc, export, code):
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(_net(), poc, export)
    assert (exc.value.status, exc.value.code) == (422, code)


def test_a_missing_link_lists_the_likely_candidates():
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(_net(), "ghost", None)
    assert "'import'" in exc.value.message


def test_an_export_link_from_the_grid_side_points_the_wrong_way():
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(_net(reverse_export=True), "import", "export")
    assert exc.value.code == "site_connection_wrong_direction"
    assert "export" in exc.value.message


def test_a_poc_into_the_grid_bus_points_the_wrong_way():
    n = _net()
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(n, "export", None)
    assert exc.value.code == "site_connection_wrong_direction"


def test_a_two_way_poc_is_refused():
    n = _net()
    n.links.loc["import", "p_min_pu"] = -1.0
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(n, "import", None)
    assert exc.value.code == "site_connection_invalid"
    assert "reverse flow" in exc.value.message


def test_candidates_rank_a_link_from_a_grid_bus_first():
    got = binding.site_connection_candidates(_net())
    assert got["poc_link"][0] == "import"
    assert got["export_link"][0] == "export"
    assert "poc_site" not in got["poc_link"]     # two-way: never a meter


# ── the chat tool ──────────────────────────────────────────────────────────


def test_the_tool_is_registered_as_a_write_on_the_solver_config_route():
    assert "set_site_connection" in chat_tools.DISPATCHERS
    assert safety_tier_for("set_site_connection") == "write"
    assert ("PUT", "/api/simulation/solver_config") in TOOL_ROUTES["set_site_connection"]


def test_it_sets_the_root_on_a_project_without_a_commercial_config(client, install_network,
                                                                   session_ctx):
    install_network(_net(), name="sc_new")
    out = chat_tools.DISPATCHERS["set_site_connection"](
        poc_link="import", export_link="export", timezone="Europe/Berlin")
    assert out["commercial"] == {"poc_link": "import", "export_link": "export",
                                 "timezone": "Europe/Berlin"}
    assert out["created"] is True
    from routers.simulation import get_solver_config

    stored = get_solver_config()["commercial"]
    assert (stored["poc_link"], stored["export_link"], stored["timezone"]) == \
        ("import", "export", "Europe/Berlin")


def test_it_keeps_the_rest_of_a_stored_commercial_config(client, install_network):
    install_network(_net(), name="sc_keep")
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "import_tariff": TARIFF, "site_party": "owner"}})
    out = chat_tools.DISPATCHERS["set_site_connection"](poc_link="import",
                                                        export_link="export")
    assert out["created"] is False
    from routers.simulation import get_solver_config

    stored = get_solver_config()["commercial"]
    assert stored["import_tariff"]["id"] == "t" and stored["site_party"] == "owner"
    assert stored["export_link"] == "export"
    # An omitted timezone keeps the stored one (none here).
    assert stored["timezone"] is None


def test_an_omitted_export_link_and_timezone_keep_the_stored_values(client, install_network):
    install_network(_net(), name="sc_omit")
    chat_tools.DISPATCHERS["set_site_connection"](poc_link="import", export_link="export",
                                                  timezone="UTC")
    out = chat_tools.DISPATCHERS["set_site_connection"](poc_link="import")
    assert out["commercial"] == {"poc_link": "import", "export_link": "export",
                                 "timezone": "UTC"}


def test_it_keeps_the_stored_value_flows(client, install_network):
    install_network(_net(), name="sc_vf")
    chat_tools.DISPATCHERS["set_site_connection"](poc_link="import")
    chat_tools.DISPATCHERS["define_participants"](template="single_owner")
    from routers.simulation import get_solver_config

    before = get_solver_config()["commercial"]["value_flows"]
    assert before is not None
    chat_tools.DISPATCHERS["set_site_connection"](poc_link="import", export_link="export")
    assert get_solver_config()["commercial"]["value_flows"] == before


@pytest.mark.parametrize("kwargs,kind", [
    ({"poc_link": "ghost"}, "site_connection_link_missing"),
    ({"poc_link": "import", "export_link": "export_rev"}, "site_connection_wrong_direction"),
    ({"poc_link": "import", "timezone": "Mars/Olympus"}, "site_connection_invalid"),
])
def test_refusals_carry_an_error_kind_and_write_nothing(client, install_network, kwargs, kind):
    n = _net()
    n.add("Link", "export_rev", bus0="grid", bus1="poc", p_nom=80.0, carrier="AC")
    install_network(n, name="sc_bad")
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["set_site_connection"](**kwargs)
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == kind
    from routers.simulation import get_solver_config

    assert get_solver_config()["commercial"] is None


def test_a_kept_export_link_that_became_two_way_is_refused(client, install_network):
    """An omitted export_link keeps the stored one, and the check runs on it."""
    install_network(_net(), name="sc_bind")
    exp_item = {"id": "x", "kind": "energy", "unit": "per_kwh", "measured_on": "export",
                "direction": "revenue", "periods": [{"name": "all", "rate": 0.01}]}
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "export_link": "export",
        "import_tariff": {**TARIFF, "items": [*TARIFF["items"], exp_item]}}})
    from services.pypsa_service import PyPSAService

    PyPSAService.get_network().links.loc["export", "p_min_pu"] = -1.0
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["set_site_connection"](poc_link="import")
    assert exc.value.detail["error_kind"] == "site_connection_invalid"
