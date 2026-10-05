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


# ── round 1 review: B3 (a tag beats the name heuristic) ────────────────────


def _microgrid(tag_import="grid_import", tag_export="grid_export"):
    """A site bus whose NAME contains "grid": mainland → microgrid_ac."""
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(range(3))
    for b in ("mainland", "microgrid_ac"):
        n.add("Bus", b, carrier="AC")
    n.add("Link", "tie_in", bus0="mainland", bus1="microgrid_ac", p_nom=5.0)
    n.add("Link", "tie_out", bus0="microgrid_ac", bus1="mainland", p_nom=5.0)
    n.links["eh_role"] = ""
    if tag_import:
        n.links.loc["tie_in", "eh_role"] = tag_import
    if tag_export:
        n.links.loc["tie_out", "eh_role"] = tag_export
    return n


def test_a_grid_import_tag_beats_a_site_bus_named_like_the_grid():
    binding.check_site_connection(_microgrid(), "tie_in", "tie_out")
    assert binding.site_connection_candidates(_microgrid())["poc_link"][0] == "tie_in"


def test_an_untagged_link_into_a_grid_named_bus_is_still_refused_by_the_name_check():
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(_microgrid(tag_import="", tag_export=""), "tie_in", None)
    assert exc.value.code == "site_connection_wrong_direction"


def test_a_grid_export_tag_refusal_names_the_tag():
    n = _net()
    n.links["eh_role"] = ""
    n.links.loc["export", "eh_role"] = "grid_export"
    n.links.loc["export", ["bus0", "bus1"]] = ["grid", "poc"]   # even pointing grid → site
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(n, "export", None)
    assert exc.value.code == "site_connection_wrong_direction"
    assert "eh_role" in exc.value.message and "grid_export" in exc.value.message
    assert "into the grid" not in exc.value.message


# ── round 1 review: B1 (the form's PUT is checked too) ─────────────────────


def test_the_solver_config_route_refuses_meter_links_the_wrong_way_round(client,
                                                                          install_network):
    install_network(_net(), name="sc_route")
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": {"poc_link": "export", "export_link": "import"}})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "site_connection_wrong_direction"
    assert detail["error_kind"] == "site_connection_wrong_direction"
    assert "export" in detail["message"]
    from routers.simulation import get_solver_config

    assert get_solver_config()["commercial"] is None


def test_the_route_accepts_the_right_way_round_and_keeps_the_binding_codes(client,
                                                                           install_network):
    install_network(_net(), name="sc_route_ok")
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": {"poc_link": "import", "export_link": "export"}})
    assert r.status_code == 200, r.text
    # A missing Link is still the binding's own refusal (its existing code).
    r = client.put("/api/simulation/solver_config", json={"commercial": {"poc_link": "pv"}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "commercial_binding_invalid"


def test_the_route_does_not_recheck_unchanged_meter_links(client, install_network,
                                                          monkeypatch):
    install_network(_net(), name="sc_route_same")
    body = {"commercial": {"poc_link": "import", "export_link": "export"}}
    assert client.put("/api/simulation/solver_config", json=body).status_code == 200
    calls = []
    monkeypatch.setattr(binding, "check_site_connection",
                        lambda *a, **k: calls.append(a))
    body["commercial"]["site_party"] = "owner"
    assert client.put("/api/simulation/solver_config", json=body).status_code == 200
    assert calls == []


# ── round 1 review: non-binding 5 ──────────────────────────────────────────


def test_an_explicit_null_clears_the_export_link_and_the_timezone(client, install_network):
    install_network(_net(), name="sc_clear")
    chat_tools.DISPATCHERS["set_site_connection"](poc_link="import", export_link="export",
                                                  timezone="UTC")
    out = chat_tools.DISPATCHERS["set_site_connection"](poc_link="import", export_link=None,
                                                        timezone=None)
    assert out["commercial"] == {"poc_link": "import", "export_link": None, "timezone": None}


def test_the_schema_says_null_clears():
    from services.chat_tools_schema import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "set_site_connection")
    props = tool["input_schema"]["properties"]
    assert props["export_link"]["type"] == ["string", "null"]
    assert props["timezone"]["type"] == ["string", "null"]
    assert "null" in tool["description"]


def test_a_kept_config_refused_without_a_kind_of_its_own_is_site_connection_invalid(
        client, install_network):
    """The rewrap is reachable: a kept FCA agreement on an unsaved network is
    the binding's `fca_needs_saved_project`, which has no chat kind."""
    import dataclasses

    from routers.simulation import _state

    install_network(_net())          # unbound: no project directory
    fca = {"kind": "fca", "import_cap_mw": 50.0, "curtailment_hours_per_year": 100,
           "available_from": "2030-01-01"}
    _state["solver_config"] = dataclasses.replace(
        _state["solver_config"], commercial={"poc_link": "import", "connection": fca})
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["set_site_connection"](poc_link="import")
    assert exc.value.detail["error_kind"] == "site_connection_invalid"
    assert exc.value.detail["code"] == "fca_needs_saved_project"


def test_a_link_tagged_for_the_other_side_is_never_a_candidate_for_this_one():
    n = _net()
    n.links["eh_role"] = ""
    n.links.loc["export", "eh_role"] = "grid_import"      # mis-tagged, named grid-side
    got = binding.site_connection_candidates(n)
    assert "export" not in got["export_link"]
    n.links.loc["import", "eh_role"] = "grid_export"
    assert "import" not in binding.site_connection_candidates(n)["poc_link"]


def test_during_a_solve_the_route_refuses_in_flight_before_reading_the_links(
        client, install_network, monkeypatch):
    import routers.simulation as S

    install_network(_net(), name="sc_route_busy")
    monkeypatch.setattr(S, "_solver_in_flight_ctx", lambda ctx: True)
    monkeypatch.setattr(binding, "check_site_connection",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read")))
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": {"poc_link": "export", "export_link": "import"}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_in_flight"


# ── round 2 review: re-check only the side that changed ───────────────────


def _microgrid_untagged_with_spare_export():
    n = _microgrid(tag_import="", tag_export="")
    n.add("Link", "tie_out2", bus0="microgrid_ac", bus1="mainland", p_nom=5.0)
    return n


def _store_root(commercial):
    import dataclasses

    from routers.simulation import _state

    _state["solver_config"] = dataclasses.replace(_state["solver_config"],
                                                  commercial=commercial)


def test_adding_an_export_link_does_not_recheck_an_unchanged_poc(client, install_network):
    """A config saved before the route check: an untagged PoC into a bus
    named like the grid. Adding the export Link must not refuse the PoC."""
    install_network(_microgrid_untagged_with_spare_export(), name="sc_side_exp")
    _store_root({"poc_link": "tie_in"})
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": {"poc_link": "tie_in", "export_link": "tie_out"}})
    assert r.status_code == 200, r.text
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": {"poc_link": "tie_in", "export_link": "tie_out2"}})
    assert r.status_code == 200, r.text


def test_changing_the_poc_to_that_link_is_still_refused(client, install_network):
    install_network(_microgrid_untagged_with_spare_export(), name="sc_side_poc")
    _store_root({"poc_link": "tie_out", "export_link": None})
    r = client.put("/api/simulation/solver_config", json={"commercial": {"poc_link": "tie_in"}})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "site_connection_wrong_direction"


def test_the_pair_check_runs_when_either_side_changes(client, install_network):
    install_network(_microgrid_untagged_with_spare_export(), name="sc_side_pair")
    _store_root({"poc_link": "tie_in"})
    r = client.put("/api/simulation/solver_config",
                   json={"commercial": {"poc_link": "tie_in", "export_link": "tie_in"}})
    # Refused (the request model already refuses one Link as both; the
    # route's pair check is the same rule behind it).
    assert r.status_code == 422, r.text


def test_check_site_connection_can_skip_a_side():
    n = _microgrid_untagged_with_spare_export()
    binding.check_site_connection(n, "tie_in", "tie_out", check_poc=False)
    with pytest.raises(binding.BindingRefusal):
        binding.check_site_connection(n, "tie_in", None, check_export=False)
    with pytest.raises(binding.BindingRefusal) as exc:   # the pair check always runs
        binding.check_site_connection(n, "tie_in", "tie_in", check_poc=False,
                                      check_export=False)
    assert exc.value.code == "site_connection_invalid"


def test_a_new_poc_rechecks_the_unchanged_export_link_against_its_site_side():
    """The export Link's site-side test is a pair check: a new PoC moves the
    site side, so it runs even when only the PoC changed."""
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.check_site_connection(_net(reverse_export=True), "import", "export",
                                      check_export=False)
    assert exc.value.code == "site_connection_wrong_direction"
