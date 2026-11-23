"""
Catalogue 06: a slack Generator's `sk_min_mva` never exceeds its `sk_max_mva` (GK B X8).

A cross-field check, so the single-field checks of DGS-EXTRAS-1 cannot make it. Every write path refuses
a reversed pair with the same 422; a one-sided edit is checked against the stored other value; a blank on
either side skips the check; an import keeps a reversed pair (C9).
"""

from __future__ import annotations

import pypsa
import pytest

from tests.test_dgs_extras_fields import _refusal

RULE = "Minimum short-circuit power: it is above the maximum short-circuit power (3000 MVA)."


def _net(sk_min=1200.0, sk_max=3000.0) -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "g", bus="b", carrier="AC", p_nom=10.0, control="Slack")
    n.generators.loc["g", "sk_min_mva"] = sk_min
    n.generators.loc["g", "sk_max_mva"] = sk_max
    return n


def _stored(n):
    return n.generators.at["g", "sk_min_mva"], n.generators.at["g", "sk_max_mva"]


def test_post_refuses_a_reversed_pair(client, install_network):
    install_network(pypsa.Network())
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    install_network(n)
    body = {"name": "grid", "bus": "b", "carrier": "AC", "p_nom": 10.0, "control": "Slack"}
    r = client.post("/api/network/generators", json={**body, "sk_min_mva": 4000.0, "sk_max_mva": 3000.0})
    assert r.status_code == 422 and RULE in r.text, r.text
    assert "grid" not in n.generators.index
    ok = client.post("/api/network/generators", json={**body, "sk_min_mva": 3000.0, "sk_max_mva": 3000.0})
    assert ok.status_code in (200, 201), ok.text


def test_put_refuses_a_reversed_pair_and_checks_a_one_sided_edit(client, install_network):
    n = _net()
    install_network(n)
    base = {"name": "g", "bus": "b", "carrier": "AC", "p_nom": 10.0, "control": "Slack"}
    r = client.put("/api/network/generators/g", json={**base, "sk_min_mva": 4000.0, "sk_max_mva": 3000.0})
    assert r.status_code == 422 and RULE in r.text, r.text
    r = client.put("/api/network/generators/g", json={**base, "sk_min_mva": 3500.0})  # stored max is 3000
    assert r.status_code == 422 and RULE in r.text, r.text
    r = client.put("/api/network/generators/g", json={**base, "sk_max_mva": 1000.0})  # stored min is 1200
    assert r.status_code == 422, r.text
    assert _stored(n) == (1200.0, 3000.0)
    r = client.put("/api/network/generators/g", json={**base, "sk_min_mva": 2000.0})
    assert r.status_code == 200, r.text
    assert _stored(n) == (2000.0, 3000.0)


def test_a_blank_on_either_side_skips_the_check(client, install_network):
    n = _net(sk_min=float("nan"), sk_max=3000.0)
    install_network(n)
    base = {"name": "g", "bus": "b", "carrier": "AC", "p_nom": 10.0, "control": "Slack"}
    r = client.put("/api/network/generators/g", json={**base, "sk_max_mva": 100.0})  # stored min is blank
    assert r.status_code == 200, r.text
    n.generators.loc["g", ["sk_min_mva", "sk_max_mva"]] = [5000.0, float("nan")]
    r = client.put("/api/network/generators/g", json={**base, "sk_min_mva": 9000.0})  # stored max is blank
    assert r.status_code == 200, r.text
    assert n.generators.at["g", "sk_min_mva"] == 9000.0


def test_an_unrelated_edit_of_a_reversed_stored_pair_goes_through(client, install_network):
    n = _net(sk_min=4000.0, sk_max=3000.0)  # as an import leaves it (C9)
    install_network(n)
    base = {"name": "g", "bus": "b", "carrier": "AC", "p_nom": 20.0, "control": "Slack"}
    r = client.put("/api/network/generators/g", json=base)
    assert r.status_code == 200, r.text
    assert _stored(n) == (4000.0, 3000.0)


def test_bulk_refuses_a_reversed_pair_and_changes_nothing(client, install_network):
    n = _net()
    install_network(n)
    r = client.patch("/api/network/_bulk", json={
        "component_class": "Generator", "names": ["g"], "updates": {"sk_min_mva": 3500.0}})
    assert r.status_code == 422 and RULE in r.text, r.text
    r = client.patch("/api/network/_bulk", json={
        "component_class": "Generator", "names": ["g"], "updates": {"sk_min_mva": 4000.0, "sk_max_mva": 3000.0}})
    assert r.status_code == 422 and RULE in r.text, r.text
    assert _stored(n) == (1200.0, 3000.0)
    r = client.patch("/api/network/_bulk", json={
        "component_class": "Generator", "names": ["g"], "updates": {"sk_min_mva": 2500.0}})
    assert r.status_code == 200, r.text


def test_chat_edit_and_bulk_tools_refuse(bound_ctx, client, install_network):
    from services import chat_tools

    n = _net()
    install_network(n)
    with bound_ctx(client):
        assert RULE in _refusal(lambda: chat_tools.update_component("Generator", "g", attrs={"sk_min_mva": 3500.0}))
        assert RULE in _refusal(lambda: chat_tools.bulk_update_components("Generator", ["g"], {"sk_min_mva": 3500.0}))
    assert _stored(n) == (1200.0, 3000.0)


def test_the_edit_card_preview_refuses(bound_ctx, client, install_network):
    from fastapi import HTTPException

    from harness import edit_card

    install_network(_net())
    with bound_ctx(client):
        for tool, args in (
            ("update_component", {"component_class": "Generator", "name": "g", "attrs": {"sk_min_mva": 3500.0}}),
            ("bulk_update_components", {"component_class": "Generator", "names": ["g"], "updates": {"sk_min_mva": 3500.0}}),
        ):
            with pytest.raises(HTTPException) as exc:
                edit_card.preview(tool, args, with_state=False)
            assert exc.value.status_code == 422 and RULE in str(exc.value.detail), tool


def test_check_function_unit_cases():
    from services.adequacy.eh_columns import check_grid_strength_pair

    nan = float("nan")
    check_grid_strength_pair("Generator", {"sk_min_mva": 1.0, "sk_max_mva": 1.0})
    check_grid_strength_pair("Generator", {"sk_min_mva": nan, "sk_max_mva": 1.0})
    check_grid_strength_pair("Generator", {"sk_min_mva": 5.0})
    check_grid_strength_pair("Load", {"sk_min_mva": 5.0, "sk_max_mva": 1.0})
    with pytest.raises(ValueError, match="above the maximum"):
        check_grid_strength_pair("Generator", {"sk_min_mva": 5.0, "sk_max_mva": 1.0})


def test_a_load_keeps_a_reversed_pair_without_swapping(client, install_network):
    """Imports (DGS, legacy, PSS/E) reach the network through the load normaliser: it neither refuses nor swaps (C9)."""
    from services.adequacy.eh_columns import normalise_eh_columns

    n = _net(sk_min=4000.0, sk_max=3000.0)
    normalise_eh_columns(n)
    assert _stored(n) == (4000.0, 3000.0)


def test_an_experiment_case_cannot_reach_the_pair():
    """Experiment copy edits only the schema's engineering inputs; neither short-circuit column is one."""
    from services.workflow_tools import _ATTRIBUTES

    assert not ({"sk_min_mva", "sk_max_mva"} & set().union(*_ATTRIBUTES.values()))
