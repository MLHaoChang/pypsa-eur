# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Adequacy loops 01: the loops sample the Links that supply an electrical
bus (`services.adequacy.link_supply`), under GK A (20) AL-1..AL-9."""

from __future__ import annotations

import dataclasses
import math
import pickle

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import coupling, link_supply
from services.adequacy.link_supply import nothing_to_sample, sampled_fleet
from services.adequacy.mc import mc_adequacy, sample_capacity, snapshot_inputs
from services.adequacy.occurrence import OutageRateError

WEIGHT = 3.0  # AL / point 3: non-unit snapshot weightings throughout


def _outage(df, name, rate, mttr):
    for col in ("outage_rate_value", "mttr_hours"):
        if col not in df.columns:
            df[col] = float("nan")
    df.at[name, "outage_rate_value"] = rate
    df.at[name, "mttr_hours"] = mttr


def _net(
    *, periods=False, load=50.0, import_mw=40.0, diesel_mw=5.0, import_data=(0.01, 12.0)
):
    """A template-like hub: a 1000 MW grid generator (no outage data) behind
    an `eh_role=grid_import` Link, a hub load and a small diesel unit."""
    n = pypsa.Network()
    if periods:
        n.set_snapshots(pd.MultiIndex.from_product([[2030, 2040], range(6)]))
        n.investment_periods = [2030, 2040]
    else:
        n.set_snapshots(range(12))
    n.snapshot_weightings.loc[:, :] = WEIGHT
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Generator", "grid_supply", bus="grid", p_nom=1000.0)
    n.add("Load", "site", bus="hub", p_set=load)
    n.add("Generator", "diesel", bus="hub", p_nom=diesel_mw)
    _outage(n.generators, "diesel", 0.05, 24.0)
    n.add(
        "Link", "grid_import", bus0="grid", bus1="hub", p_nom=import_mw, efficiency=1.0
    )
    n.links["eh_role"] = ""
    n.links.at["grid_import", "eh_role"] = "grid_import"
    if import_data is not None:
        _outage(n.links, "grid_import", *import_data)
    return n


def _fuel_cell(
    n,
    *,
    bus0="gas",
    carrier="gas",
    p_nom=40.0,
    eff=0.5,
    data=(0.0, 24.0),
    fuel=True,
    **kw,
):
    if bus0 not in n.buses.index:
        n.add("Bus", bus0, carrier=carrier)
    if fuel:
        n.add("Generator", f"{bus0}_supply", bus=bus0, p_nom=1000.0)
    n.add("Link", "fuel_cell", bus0=bus0, bus1="hub", p_nom=p_nom, efficiency=eff, **kw)
    if "eh_role" in n.links.columns:
        n.links.at["fuel_cell", "eh_role"] = "fuel_cell"
    if data is not None:
        _outage(n.links, "fuel_cell", *data)
    return n


def _lole(inputs, draws=200):
    return float(
        mc_adequacy(inputs, draws=draws, seed=7, max_draws=draws)["lole_hours"]
    )


def _names(inputs):
    return [u.name for u in inputs.units]


# ── AL-5: byte-equal ──────────────────────────────────────────────────────


def test_a_generator_only_network_gets_snapshot_inputs_unchanged():
    n = _net()
    n.remove("Link", "grid_import")
    for kz in (False, True):
        a = snapshot_inputs(n, keep_zero_capacity=kz)
        b = sampled_fleet(n, keep_zero_capacity=kz)
        assert pickle.dumps(a) == pickle.dumps(b)
        assert nothing_to_sample(b) is None
    assert "fleet_notes" not in mc_adequacy(b, draws=50, max_draws=50)


@pytest.mark.parametrize("builder", ["build_3bus", "build_ieee14", "build_ieee39"])
def test_shipped_templates_without_a_trusted_import_link_are_byte_equal(builder):
    """AL-5 on the shipped non-EH templates (belgium needs workflow output
    the test tree does not carry; every EH template has an
    `eh_role=grid_import` Link and is split on purpose)."""
    from project_templates import _build

    n = getattr(_build, builder)()
    for kz in (False, True):
        assert pickle.dumps(snapshot_inputs(n, keep_zero_capacity=kz)) == pickle.dumps(
            sampled_fleet(n, keep_zero_capacity=kz)
        )


def test_an_untrusted_import_link_leaves_the_fleet_byte_equal():
    """No eh_role and no eh_poc: the carrier fallback never splits."""
    n = _net()
    n.links.at["grid_import", "eh_role"] = ""
    a, b = snapshot_inputs(n), sampled_fleet(n)
    assert pickle.dumps(a) == pickle.dumps(b)


# ── Grid import (AL-2, AL-3, AL-9) ────────────────────────────────────────


def test_grid_import_is_sampled_on_the_hub_side_and_the_grid_drops_out():
    n = _net()
    b = sampled_fleet(n)
    assert _names(b) == ["diesel", "link:grid_import"]
    unit = b.units[-1]
    assert unit.capacity_mw == pytest.approx(40.0)
    assert (unit.q, unit.mttr_hours) == (pytest.approx(0.01), pytest.approx(12.0))
    # The 1000 MW grid generator is no longer netted: residual = hub load.
    np.testing.assert_allclose(b.residual, 50.0)
    assert b.supply_links == ("grid_import",)
    assert b.weights[0] == WEIGHT


def test_the_import_cap_below_peak_demand_now_binds():
    """AL-9: delivered cap (p_nom x p_max_pu x eff) 40 + diesel 5 < 50 MW."""
    n = _net()
    old = snapshot_inputs(n)
    assert _lole(old) == 0.0  # the copper plate netted 1000 MW must-take
    assert _lole(sampled_fleet(n)) > 0.0


def test_an_import_link_without_outage_data_is_a_disclosed_firm_block():
    n = _net(import_data=None)
    b = sampled_fleet(n)
    assert _names(b) == ["diesel"]
    np.testing.assert_allclose(b.residual, 50.0 - 40.0)
    assert b.fleet_notes == (
        "grid import taken as never failing: no outage data for grid_import",
    )
    assert mc_adequacy(b, draws=50, max_draws=50)["fleet_notes"] == list(b.fleet_notes)


def test_eh_h2_hub_names_its_firm_grid_import():
    """AL-2 on the shipped hub: with grid_import's data cleared it is a firm
    block, the refusal's words name it, and nothing else is claimed."""
    from project_templates import eh_templates as T

    n = T.BUILDERS["eh_h2_hub"]()
    n.links.loc["grid_import", ["outage_rate_value", "mttr_hours"]] = float("nan")
    b = sampled_fleet(n, keep_zero_capacity=True)
    assert (
        "grid import taken as never failing: no outage data for grid_import"
        in b.fleet_notes
    )
    msg = nothing_to_sample(b)
    assert msg.startswith(coupling.NOTHING_TO_SAMPLE)
    assert "grid import taken as never failing: no outage data for grid_import" in msg
    assert "firm Link fuel_cell: not credited (0 MW)" in msg


def test_import_capacity_is_per_period_on_a_multi_period_network():
    """AL-3: a Link built for 2040 only delivers nothing in 2030."""
    n = _net(periods=True)
    n.links.at["grid_import", "build_year"] = 2040
    n.links.at["grid_import", "lifetime"] = 30
    b = sampled_fleet(n)
    unit = b.units[-1]
    assert unit.name == "link:grid_import" and unit.capacity_mw == pytest.approx(40.0)
    np.testing.assert_allclose(unit.capacity_series[:6], 0.0)
    np.testing.assert_allclose(unit.capacity_series[6:], 40.0)


def test_eh_h2_hub_staged_import_is_per_period_under_its_own_weightings():
    """AL-3 on the shipped staged hub (weightings of 52 h): an import Link
    built for 2030 delivers nothing in 2027."""
    from project_templates import eh_templates as T

    n = T.BUILDERS["eh_h2_hub_staged"]()
    assert float(n.snapshot_weightings.objective.iloc[0]) > 1.0
    n.links.at["grid_import", "build_year"] = 2030
    n.links.at["grid_import", "lifetime"] = 30
    [unit] = [u for u in sampled_fleet(n).units if u.name == "link:grid_import"]
    period = n.snapshots.get_level_values(0)
    assert unit.capacity_mw == pytest.approx(200.0)
    np.testing.assert_allclose(unit.capacity_series[period == 2027], 0.0)
    np.testing.assert_allclose(unit.capacity_series[period != 2027], 200.0)


def test_an_extendable_import_link_uses_its_solved_capacity():
    n = _net()
    n.links.at["grid_import", "p_nom_extendable"] = True
    n.links["p_nom_opt"] = 0.0
    n.links.at["grid_import", "p_nom_opt"] = 25.0
    assert sampled_fleet(n).units[-1].capacity_mw == pytest.approx(25.0)


def test_links_that_do_not_separate_the_sides_fall_back_to_the_whole_network():
    """AL-6: a parallel Line means no hub boundary; no import Link sampled."""
    n = _net()
    n.add("Line", "parallel", bus0="grid", bus1="hub", x=0.1, s_nom=100.0)
    b = sampled_fleet(n)
    assert _names(b) == ["diesel"]
    assert any("do not separate the hub from the grid" in s for s in b.fleet_notes)
    np.testing.assert_allclose(b.residual, snapshot_inputs(n).residual)


def test_a_bad_import_rate_is_refused():
    n = _net(import_data=(1.5, 12.0))
    with pytest.raises(OutageRateError, match="import Link"):
        sampled_fleet(n)


# ── Firm backup (AL-1) ────────────────────────────────────────────────────


def test_a_fuel_cell_on_an_unlimited_fuel_is_credited_at_its_delivered_cap():
    n = _fuel_cell(_net())
    b = sampled_fleet(n)
    assert _names(b) == ["diesel", "link:grid_import", "link:fuel_cell"]
    assert b.units[-1].capacity_mw == pytest.approx(20.0)  # 40 x 0.5
    assert b.supply_links == ("grid_import", "fuel_cell")


def test_a_store_fed_fuel_cell_is_not_credited_and_lole_shows_it():
    """AL-1: full output in every hour would hide the empty store."""

    def hub(store_fed):
        n = _net(load=10.0, import_mw=0.0, diesel_mw=5.0)
        n.remove("Link", "grid_import")
        n.remove("Generator", "grid_supply")
        if store_fed:
            _fuel_cell(n, bus0="h2", carrier="H2", fuel=False)
            n.add("Store", "h2_store", bus="h2", e_nom=1.0)
        else:
            _fuel_cell(n)
        return sampled_fleet(n)

    limited, unlimited = hub(True), hub(False)
    assert _names(limited) == ["diesel"]
    assert any(
        s.startswith("firm Link fuel_cell: not credited (0 MW)")
        for s in limited.fleet_notes
    )
    assert _lole(unlimited) == 0.0
    assert _lole(limited) > 0.0


@pytest.mark.parametrize(
    "exclusion, words",
    [
        ("store", "bus0 gas carries a Store (energy-limited): s"),
        ("storage_unit", "bus0 gas carries a StorageUnit (energy-limited): su"),
        ("load", "bus0 gas carries a Load: gas_use"),
        ("feeder", "bus0 gas is fed by another Link: p2g"),
        ("sharer", "bus0 gas is shared with another Link: boiler"),
        ("sharer_port2", "bus0 gas is shared with another Link: chp"),
        (
            "capped_fuel",
            "bus0 gas has no fuel Generator without an energy cap (e_sum_max)",
        ),
        ("no_fuel", "bus0 gas has no fuel Generator without an energy cap (e_sum_max)"),
    ],
)
def test_each_exclusion_of_the_unlimited_fuel_rule_alone_withholds_credit(
    exclusion, words
):
    """#1257 F1a: every condition of the rule, one at a time."""
    n = _fuel_cell(_net(), fuel=exclusion != "no_fuel")
    if exclusion == "store":
        n.add("Store", "s", bus="gas", e_nom=10.0)
    elif exclusion == "storage_unit":
        n.add("StorageUnit", "su", bus="gas", p_nom=10.0, max_hours=1.0)
    elif exclusion == "load":
        n.add("Load", "gas_use", bus="gas", p_set=1.0)
    elif exclusion == "feeder":
        n.add("Link", "p2g", bus0="hub", bus1="gas", p_nom=5.0)
    elif exclusion == "sharer":
        n.add("Bus", "heat", carrier="heat")
        n.add("Link", "boiler", bus0="gas", bus1="heat", p_nom=5.0)
    elif exclusion == "sharer_port2":
        n.add("Bus", "heat", carrier="heat")
        n.add("Bus", "steam", carrier="heat")
        n.add("Link", "chp", bus0="heat", bus1="steam", bus2="gas", p_nom=5.0)
    elif exclusion == "capped_fuel":
        n.generators.at["gas_supply", "e_sum_max"] = 100.0
    b = sampled_fleet(n)
    assert "link:fuel_cell" not in _names(b)
    [note] = [s for s in b.fleet_notes if "fuel_cell" in s]
    assert note == f"firm Link fuel_cell: not credited (0 MW) — {words}"


def test_a_fuel_cell_without_outage_data_is_not_credited():
    n = _fuel_cell(_net(), data=None)
    b = sampled_fleet(n)
    assert "link:fuel_cell" not in _names(b)
    assert "firm Link fuel_cell: not credited (0 MW) — no outage data" in b.fleet_notes


def test_the_fuel_generators_capacity_bounds_the_credit():
    n = _fuel_cell(_net())
    n.generators.at["gas_supply", "p_nom"] = 10.0
    assert sampled_fleet(n).units[-1].capacity_mw == pytest.approx(5.0)  # 10 x 0.5


def test_a_split_storage_power_link_is_never_a_firm_backup():
    from services.adequacy.link_supply import firm_backup_units
    from services.solver import storage_inventory

    n = _fuel_cell(_net())

    class _E:
        charger, discharger = "x", "fuel_cell"

    orig = storage_inventory.list_storages
    storage_inventory.list_storages = lambda _n: [_E()]
    try:
        assert firm_backup_units(n) == ([], [], [])
    finally:
        storage_inventory.list_storages = orig


def test_firm_backup_is_per_period_on_a_multi_period_network():
    n = _fuel_cell(_net(periods=True))
    n.links.at["fuel_cell", "build_year"] = 2040
    n.links.at["fuel_cell", "lifetime"] = 30
    unit = sampled_fleet(n).units[-1]
    assert unit.name == "link:fuel_cell"
    np.testing.assert_allclose(unit.capacity_series[:6], 0.0)
    np.testing.assert_allclose(unit.capacity_series[6:], 20.0)


def test_a_bad_firm_rate_is_refused():
    n = _fuel_cell(_net(), data=(-0.2, 24.0))
    with pytest.raises(OutageRateError, match="firm Link"):
        sampled_fleet(n)


# ── AL-4: CRN and membership ──────────────────────────────────────────────


def test_generator_substreams_are_unchanged_by_the_appended_links():
    n = _fuel_cell(_net())
    b = sampled_fleet(n)
    gens_only = b.units[:1]
    H = len(b.residual)
    links = frozenset(i for i, u in enumerate(b.units) if u.name.startswith("link:"))
    with_links = sample_capacity(b.units, H, 64, 11, exclude=links)
    alone = sample_capacity(gens_only, H, 64, 11)
    np.testing.assert_array_equal(with_links, alone)


def test_link_membership_is_stable_across_iterates_that_build_differently():
    def iterate(built):
        n = _fuel_cell(_net())
        n.links["p_nom_extendable"] = True
        n.links["p_nom_opt"] = built
        return sampled_fleet(n, keep_zero_capacity=True)

    a, b = iterate(40.0), iterate(0.0)
    assert _names(a) == _names(b) == ["diesel", "link:grid_import", "link:fuel_cell"]
    assert [u.capacity_mw for u in b.units[1:]] == [0.0, 0.0]


# ── The shared refusal (AL-7, #1318 F1) ───────────────────────────────────


def test_every_loop_site_imports_the_one_predicate():
    from services.adequacy import (
        coupling_loop_runner,
        eh_stages,
        margin_loop_runner,
        mc_loop_runner,
    )

    for mod in (coupling_loop_runner, margin_loop_runner, mc_loop_runner, eh_stages):
        assert mod.nothing_to_sample is link_supply.nothing_to_sample
    assert coupling.NOTHING_TO_SAMPLE == (
        "nothing to sample: no electrical generator carries resolvable occurrence data"
    )


def test_the_refusal_without_any_supplying_link_says_only_that():
    n = _net(diesel_mw=5.0)
    n.remove("Link", "grid_import")
    n.generators.loc["diesel", ["outage_rate_value", "mttr_hours"]] = float("nan")
    msg = nothing_to_sample(sampled_fleet(n))
    assert msg.startswith(
        f"{coupling.NOTHING_TO_SAMPLE} (unavailability + MTTR), "
        "so the sampled fleet is empty"
    )
    assert "Link" not in msg  # nothing about Links was established


def test_two_firm_links_on_one_fuel_bus_are_not_each_credited_the_whole_fuel():
    """Review of adequacy loops 01: the fuel would be counted twice."""
    n = _fuel_cell(_net())
    n.add("Link", "fuel_cell_2", bus0="gas", bus1="hub", p_nom=40.0, efficiency=0.5)
    _outage(n.links, "fuel_cell_2", 0.0, 24.0)
    b = sampled_fleet(n)
    assert _names(b) == ["diesel", "link:grid_import"]
    for lk, other in (("fuel_cell", "fuel_cell_2"), ("fuel_cell_2", "fuel_cell")):
        assert (
            f"firm Link {lk}: not credited (0 MW) — "
            f"bus0 gas is shared with another Link: {other}"
        ) in b.fleet_notes


def test_a_slack_generator_on_the_fuel_bus_is_not_a_fuel():
    from services.adequacy.slack import SLACK_NAME_PREFIXES

    n = _fuel_cell(_net(), fuel=False)
    n.add("Generator", f"{SLACK_NAME_PREFIXES[0]}gas", bus="gas", p_nom=1000.0)
    b = sampled_fleet(n)
    assert "link:fuel_cell" not in _names(b)
    assert (
        "firm Link fuel_cell: not credited (0 MW) — "
        "bus0 gas has no fuel Generator without an energy cap (e_sum_max)"
    ) in b.fleet_notes


def test_a_fuel_cell_without_a_finite_mttr_is_not_credited():
    n = _fuel_cell(_net(), data=(0.05, float("inf")))
    b = sampled_fleet(n)
    assert "link:fuel_cell" not in _names(b)
    assert (
        "firm Link fuel_cell: not credited (0 MW) — no finite MTTR in its outage data"
    ) in b.fleet_notes


def test_the_fuel_generators_availability_bounds_the_credit():
    n = _fuel_cell(_net())
    n.generators.at["gas_supply", "p_nom"] = 10.0
    n.generators.at["gas_supply", "p_max_pu"] = 0.2
    assert sampled_fleet(n).units[-1].capacity_mw == pytest.approx(1.0)  # 2 x 0.5


def test_the_refusal_does_not_borrow_a_note_about_a_longer_link_name():
    n = _fuel_cell(_net(import_data=None), data=None)
    n.generators.loc["diesel", ["outage_rate_value", "mttr_hours"]] = float("nan")
    b = dataclasses.replace(sampled_fleet(n), supply_links=("fuel",))
    assert not b.units
    msg = nothing_to_sample(b)
    assert "and no Link supplying an electrical bus is sampled (fuel)" in msg


def test_the_refusal_names_each_supplying_links_own_reason():
    n = _fuel_cell(_net(import_data=None), data=None)
    n.generators.loc["diesel", ["outage_rate_value", "mttr_hours"]] = float("nan")
    msg = nothing_to_sample(sampled_fleet(n))
    assert "grid import taken as never failing: no outage data for grid_import" in msg
    assert "firm Link fuel_cell: not credited (0 MW) — no outage data" in msg
    assert "and no Link supplying an electrical bus is sampled (" in msg


def test_freeze_fixed_plan_refuses_with_the_shared_message_and_reads_bad_rates():
    import threading

    from services.adequacy.eh_stages import freeze_fixed_plan

    class _Cfg:
        voll = 1000.0

    n = _net()
    n.remove("Link", "grid_import")
    n.generators.loc["diesel", ["outage_rate_value", "mttr_hours"]] = float("nan")
    snap = freeze_fixed_plan(n, _Cfg(), threading.Lock())
    assert snap.mc_error == nothing_to_sample(snapshot_inputs(n))
    bad = _fuel_cell(_net(), data=(2.0, 24.0))
    snap = freeze_fixed_plan(bad, _Cfg(), threading.Lock())
    assert snap.mc_error.startswith(
        "MC snapshot refused: outage rate outside [0, 1) on firm Link(s)"
    )


def test_freeze_fixed_plan_samples_the_same_firm_backup():
    import threading

    from services.adequacy.eh_stages import freeze_fixed_plan

    class _Cfg:
        voll = 1000.0

    n = _fuel_cell(_net())
    n.remove("Link", "grid_import")
    snap = freeze_fixed_plan(n, _Cfg(), threading.Lock())
    assert [u.name for u in snap.mc_inputs.units] == _names(sampled_fleet(n))
    assert math.isfinite(snap.mc_inputs.nyears)


# ── The routes: each site refuses on the fleet it samples (GK A (20)) ─────


def _h2_hub():
    from project_templates import eh_templates as T

    return T.BUILDERS["eh_h2_hub"]()


def _install(client, install_network, n):
    from project_templates import eh_templates as T

    install_network(n)
    body = {
        k: v
        for k, v in {
            **T.SOLVER_CONFIG,
            **T.SOLVER_CONFIG_EXTRA.get("eh_h2_hub", {}),
        }.items()
        if k in ("solver_name", "voll", "solve_strategy")
    }
    body.setdefault("voll", 3000.0)
    assert client.put("/api/simulation/solver_config", json=body).status_code == 200


def test_the_mc_study_refuses_the_h2_hub_on_its_own_generator_only_fleet(
    client, install_network
):
    """mc_loop keeps `snapshot_inputs` (follow-up 03): it refuses the H2 hub
    and its reason claims nothing about Links (#1318 F1)."""
    _install(client, install_network, _h2_hub())
    r = client.post("/api/results/mc", json={"draws": 50})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail.startswith(coupling.NOTHING_TO_SAMPLE) and "Link" not in detail


@pytest.mark.parametrize(
    "url", ["/api/results/coupling_loop", "/api/results/margin_loop"]
)
def test_a_bad_import_rate_is_a_typed_refusal_at_both_loops(
    client, install_network, url
):
    n = _h2_hub()
    n.links.at["grid_import", "outage_rate_value"] = 1.5
    _install(client, install_network, n)
    r = client.post(url, json={"max_solves": 2, "target_lole_h": 2.0})
    assert r.status_code == 422, r.text
    assert "outage rate outside [0, 1) on import Link(s): grid_import" in str(
        r.json()["detail"]
    )
