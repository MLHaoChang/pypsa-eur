# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Adequacy loops 03: the MC study (`POST /results/mc`), its ELCC table and
the portfolio sample the loops' Link-aware fleet (`link_supply`)."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import elcc, link_supply
from services.adequacy.mc import mc_adequacy, snapshot_inputs

WEIGHT = 3.0
H = 12


def _outage(df, name, rate, mttr):
    for col, val in (("outage_rate_value", rate), ("mttr_hours", mttr)):
        if col not in df.columns:
            df[col] = np.nan
        df.at[name, col] = val


def _net(*, import_data=(0.01, 12.0), import_mw=40.0, load=50.0):
    """A template-like hub: a 1000 MW grid generator and a grid-side PV farm
    (no outage data) behind an `eh_role=grid_import` Link; on the hub, a
    load, a small diesel unit with outage data and a must-take PV farm."""
    n = pypsa.Network()
    n.set_snapshots(range(H))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Generator", "grid_supply", bus="grid", p_nom=1000.0)
    n.add("Generator", "grid_pv", bus="grid", p_nom=300.0, carrier="solar")
    n.add("Load", "site", bus="hub", p_set=load)
    n.add("Generator", "diesel", bus="hub", p_nom=5.0)
    n.add("Generator", "hub_pv", bus="hub", p_nom=20.0, carrier="solar")
    shape = np.clip(np.sin(np.linspace(0.0, np.pi, H)), 0.0, 1.0)
    shape[H // 2] = 1.0
    n.generators_t.p_max_pu = pd.DataFrame(
        {"grid_pv": shape, "hub_pv": shape}, index=n.snapshots
    )
    _outage(n.generators, "diesel", 0.05, 24.0)
    n.add(
        "Link", "grid_import", bus0="grid", bus1="hub", p_nom=import_mw, efficiency=1.0
    )
    n.links["eh_role"] = ""
    n.links.at["grid_import", "eh_role"] = "grid_import"
    if import_data is not None:
        _outage(n.links, "grid_import", *import_data)
    return n


def _study_inputs(n, vre=()):
    scope = link_supply.loop_scope(n)
    side = link_supply.fleet_side(n, scope)
    return scope, side, link_supply.assemble_fleet(n, scope, side=side, vre_assets=vre)


# ── the fleet the study samples ───────────────────────────────────────────


def test_the_study_fleet_is_the_loops_fleet_with_the_profiles_it_keeps():
    n = _net()
    _, _, inputs = _study_inputs(n, vre=["hub_pv"])
    loops = link_supply.sampled_fleet(n)
    assert [u.name for u in inputs.units] == [u.name for u in loops.units]
    assert [u.name for u in inputs.units] == ["diesel", "link:grid_import"]
    assert set(inputs.vre_profiles) == {"hub_pv"}
    # The grid-side generator and PV are no longer netted as must-take.
    np.testing.assert_allclose(inputs.residual, loops.residual)
    assert float(inputs.residual.max()) == pytest.approx(50.0)


def test_sampled_fleet_keeps_a_requested_profile():
    n = _net()
    assert set(link_supply.sampled_fleet(n, vre_assets=["hub_pv"]).vre_profiles) == {
        "hub_pv"
    }
    assert not link_supply.sampled_fleet(n).vre_profiles


def test_the_import_cap_binds_in_the_study_where_the_copper_plate_gave_zero():
    n = _net()
    old = snapshot_inputs(n)
    _, _, new = _study_inputs(n)
    run = dict(draws=200, seed=7, max_draws=200)
    assert mc_adequacy(old, **run)["lole_hours"] == 0.0
    assert mc_adequacy(new, **run)["lole_hours"] > 0.0


def test_a_network_without_a_supplying_link_is_byte_equal():
    n = _net()
    n.remove("Link", "grid_import")
    n.remove("Bus", "grid")
    n.remove("Generator", ["grid_supply", "grid_pv"])
    n.generators_t.p_max_pu = n.generators_t.p_max_pu[["hub_pv"]]
    _, side, new = _study_inputs(n, vre=["hub_pv"])
    assert side is n
    old = snapshot_inputs(n, vre_assets=["hub_pv"])
    assert new is not old
    assert [u.name for u in new.units] == [u.name for u in old.units]
    np.testing.assert_array_equal(new.residual, old.residual)
    np.testing.assert_array_equal(
        new.vre_profiles["hub_pv"], old.vre_profiles["hub_pv"]
    )
    assert new.fleet_notes == () and new.supply_links == ()


# ── ELCC: hub-side candidates, a link unit priced or refused by name ──────


def test_a_sampled_import_link_resolves_as_a_generator_and_is_offered():
    n = _net()
    _, _, inputs = _study_inputs(n)
    reduced, exclude, ex_st, nameplate = elcc._resolve(
        inputs, "generator", "link:grid_import"
    )
    assert exclude == frozenset({1}) and ex_st == frozenset()
    assert nameplate == pytest.approx(40.0)
    rows = {(r["kind"], r["name"]) for r in elcc.elcc_candidates(n)}
    assert ("generator", "link:grid_import") in rows


def test_candidates_are_the_hub_side_and_each_one_resolves():
    n = _net()
    rows = elcc.elcc_candidates(n)
    names = {r["name"] for r in rows}
    assert names == {"link:grid_import", "diesel", "hub_pv"}
    _, _, inputs = _study_inputs(n, vre=["hub_pv"])
    for r in rows:
        elcc._resolve(inputs, r["kind"], r["name"])  # raises on a mismatch


@pytest.mark.parametrize("asked", ["link:grid_import", "grid_import"])
def test_an_import_link_taken_as_a_firm_block_is_refused_by_name(asked):
    n = _net(import_data=None)
    _, _, inputs = _study_inputs(n)
    assert "link:grid_import" not in [u.name for u in inputs.units]
    with pytest.raises(ValueError) as exc:
        elcc._resolve(inputs, "generator", asked)
    msg = str(exc.value)
    assert "Link grid_import supplies" in msg and "never failing: no outage data" in msg
    assert "not priced" in msg
    assert "link:grid_import" not in {r["name"] for r in elcc.elcc_candidates(n)}


def test_an_unknown_name_is_still_a_key_error():
    n = _net(import_data=None)
    _, _, inputs = _study_inputs(n)
    with pytest.raises(KeyError):
        elcc._resolve(inputs, "generator", "link:nowhere")


@pytest.mark.parametrize(
    "kind,name", [("generator", "grid_supply"), ("vre", "grid_pv")]
)
def test_a_grid_side_asset_is_refused_naming_the_import_link(kind, name):
    n = _net()
    scope = link_supply.loop_scope(n)
    msg = link_supply.grid_side_refusal(n, scope, kind, name)
    assert msg is not None
    assert (
        f"{name} is on the grid side" in msg
        and "grid_import" in msg
        and "grid side" in msg
    )
    assert link_supply.grid_side_refusal(n, scope, kind, "hub_pv") is None


def test_no_grid_side_refusal_when_the_network_is_not_split():
    n = _net()
    n.links.at["grid_import", "eh_role"] = ""
    scope = link_supply.loop_scope(n)
    assert scope.mode != "hub_side"
    assert link_supply.grid_side_refusal(n, scope, "generator", "grid_supply") is None


# ── the portfolio: the hub-side must-takes ────────────────────────────────


def test_the_portfolio_population_is_the_hub_side():
    from services.adequacy.copt import must_take_generators
    from services.adequacy.portfolio import portfolio_population

    n = _net()
    _, side, inputs = _study_inputs(n, vre=list(must_take_generators(_net())))
    names = {m.name for m in portfolio_population(side, inputs)["members"]}
    assert names == {"hub_pv"}


# ── the endpoint ──────────────────────────────────────────────────────────


def _poll(client, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    body: dict | None = None
    while time.time() < deadline:
        body = client.get("/api/results/mc").json()
        if body.get("status") in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"the MC run never finished: {body!r}")


def _post(client, **body):
    return client.post("/api/results/mc", json={"draws": 50, "seed": 0, **body})


def test_the_h2_hub_study_samples_its_import_link(client, install_network):
    """It refused ("nothing to sample") on the generator-only fleet."""
    from project_templates import eh_templates as T

    install_network(T.BUILDERS["eh_h2_hub"]())
    r = _post(client)
    assert r.status_code == 200, r.text
    done = _poll(client)
    assert done["status"] == "done", done
    provenance = [u["name"] for u in done["result"]["units_provenance"]]
    assert "link:grid_import" in provenance


def test_the_endpoint_prices_the_link_and_the_portfolio_on_the_hub_side(
    client, install_network
):
    install_network(_net())
    r = _post(
        client,
        elcc_assets=[{"kind": "generator", "name": "link:grid_import"}],
        elcc_portfolio=True,
    )
    assert r.status_code == 200, r.text
    done = _poll(client)
    assert done["status"] == "done", done
    res = done["result"]
    assert [row["name"] for row in res["elcc"]] == ["link:grid_import"]
    members = res["elcc_portfolio"]["population"]["members"]
    assert {m["name"] for m in members} == {"hub_pv"}
    assert res["metrics"]["lole_hours"] > 0.0


@pytest.mark.parametrize(
    "asset,words",
    [
        ({"kind": "generator", "name": "grid_supply"}, "grid side of import Link"),
        ({"kind": "vre", "name": "grid_pv"}, "grid side of import Link"),
    ],
)
def test_the_endpoint_refuses_a_grid_side_asset_by_name(
    client, install_network, asset, words
):
    install_network(_net())
    r = _post(client, elcc_assets=[asset])
    assert r.status_code == 422, r.text
    assert words in r.json()["detail"] and asset["name"] in r.json()["detail"]


def test_the_endpoint_refuses_a_firm_block_import_by_name(client, install_network):
    install_network(_net(import_data=None))
    r = _post(client, elcc_assets=[{"kind": "generator", "name": "link:grid_import"}])
    assert r.status_code == 422, r.text
    assert "never failing" in r.json()["detail"]
