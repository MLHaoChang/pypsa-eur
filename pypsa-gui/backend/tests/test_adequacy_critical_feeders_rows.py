"""``critical_feeders.critical_feeder_rows``: which Links feed a must-stay-on
bus, and how each pocket is modelled (walkthrough 2026-11-17 issue 02; GK A
F5, F6'). Killing the mutants the hub-level tests leave alive."""

from __future__ import annotations

import pytest

from services.adequacy.critical_feeders import (
    COMPOSED,
    NO_OUTAGE_DATA,
    UNSAMPLED,
    certification_note,
    critical_feeder_rows,
)
from tests.test_energy_hub_critical_feeder import (
    _hub_with_one_critical_feeder,
    _with_backup,
)


def _rows(n):
    return critical_feeder_rows(
        n, hub_buses=["island", "crit"], import_links=["import_poc"]
    )


def _by_name(rows):
    return {r["name"]: r for r in rows}


def test_a_composed_feeder_is_neither_bounded_nor_chained():
    (r,) = _rows(_hub_with_one_critical_feeder())
    assert (r["name"], r["model"]) == ("crit_feeder", COMPOSED)
    assert r["chained"] is False and r["upper_bound"] is False
    assert r["q"] == 0.01 and r["mttr_hours"] == 8.0


def test_own_units_bound_the_pocket_and_say_so():
    (r,) = _rows(_with_backup(_hub_with_one_critical_feeder()))
    assert (r["model"], r["chained"], r["upper_bound"]) == (UNSAMPLED, False, True)
    assert "units of its own" in r["reason"]


@pytest.mark.parametrize(
    "p_nom, extendable, model",
    [
        (0.0, False, COMPOSED),  # a 0 MW unit is no supply
        (0.0, True, UNSAMPLED),  # an extendable one may be built
    ],
)
def test_a_unit_counts_as_supply_when_it_has_or_can_get_capacity(
    p_nom, extendable, model
):
    n = _with_backup(_hub_with_one_critical_feeder())
    n.generators.at["crit_backup", "p_nom"] = p_nom
    n.generators.at["crit_backup", "p_nom_extendable"] = extendable
    assert _rows(n)[0]["model"] == model


def test_an_extendable_store_counts_as_supply():
    n = _hub_with_one_critical_feeder()
    n.add("Store", "crit_store", bus="crit", e_nom=0.0, e_nom_extendable=True)
    assert _rows(n)[0]["model"] == UNSAMPLED


def _reversed(n, *, p_min_pu, p_nom=20.0):
    """The feeder drawn from the pocket to the hub: it feeds the pocket only
    when it can run backwards."""
    n.links.at["crit_feeder", "bus0"] = "crit"
    n.links.at["crit_feeder", "bus1"] = "island"
    n.links.at["crit_feeder", "p_min_pu"] = p_min_pu
    n.links.at["crit_feeder", "p_nom"] = p_nom
    return n


def test_a_reversed_link_that_runs_backwards_is_a_feeder():
    rows = _rows(_reversed(_hub_with_one_critical_feeder(), p_min_pu=-1.0))
    assert [r["name"] for r in rows] == ["crit_feeder"]


@pytest.mark.parametrize("p_min_pu, p_nom", [(0.0, 20.0), (-1.0, 0.0)])
def test_a_reversed_link_that_cannot_deliver_is_not_a_feeder(p_min_pu, p_nom):
    n = _reversed(_hub_with_one_critical_feeder(), p_min_pu=p_min_pu, p_nom=p_nom)
    assert _rows(n) == []


@pytest.mark.parametrize("extendable, n_rows", [(False, 0), (True, 1)])
def test_a_0_mw_feeder_feeds_only_if_it_can_be_built(extendable, n_rows):
    n = _hub_with_one_critical_feeder()
    n.links.at["crit_feeder", "p_nom"] = 0.0
    n.links.at["crit_feeder", "p_nom_extendable"] = extendable
    assert len(_rows(n)) == n_rows


@pytest.mark.parametrize(
    "rate, mttr",
    [
        (float("nan"), 8.0),
        (1.5, 8.0),
        (-0.1, 8.0),
        (0.01, 0.0),
        (0.01, float("nan")),
        (0.01, float("inf")),
    ],
)
def test_unusable_outage_data_is_named_not_composed(rate, mttr):
    n = _hub_with_one_critical_feeder()
    n.links.at["crit_feeder", "outage_rate_value"] = rate
    n.links.at["crit_feeder", "mttr_hours"] = mttr
    (r,) = _rows(n)
    assert (r["name"], r["model"], r["buses"]) == (
        "crit_feeder",
        NO_OUTAGE_DATA,
        ["crit"],
    )


def test_the_import_link_is_never_a_feeder():
    # The import Link with outage data is sampled by the MC, not composed here.
    n = _hub_with_one_critical_feeder()
    n.links.at["import_poc", "outage_rate_value"] = 0.05
    n.links.at["import_poc", "mttr_hours"] = 8.0
    assert set(_by_name(_rows(n))) == {"crit_feeder"}


def test_a_link_leaving_the_hub_is_not_internal():
    n = _hub_with_one_critical_feeder()
    n.add("Bus", "outside", carrier="AC")
    n.add("Link", "export", bus0="crit", bus1="outside", p_nom=5.0)
    n.links.at["export", "outage_rate_value"] = float("nan")
    n.links.at["export", "mttr_hours"] = float("nan")
    assert set(_by_name(_rows(n))) == {"crit_feeder"}


def _second_path(n, *, rate):
    """A second Link from the hub into the pocket, with or without data."""
    n.add("Link", "crit_feeder_2", bus0="island", bus1="crit", p_nom=20.0)
    n.links.at["crit_feeder_2", "outage_rate_value"] = rate
    n.links.at["crit_feeder_2", "mttr_hours"] = 8.0
    return n


def test_a_no_data_link_beside_a_usable_feeder_is_named_and_nothing_else():
    # Taken as never failing, the no-data Link hides the usable feeder's
    # outages: it is named (F5); the usable feeder adds nothing. An unrelated
    # no-data Link elsewhere in the hub is not on the pocket's path.
    n = _second_path(_hub_with_one_critical_feeder(), rate=float("nan"))
    n.add("Bus", "aux", carrier="AC")
    n.add("Link", "aux_link", bus0="island", bus1="aux", p_nom=5.0)
    n.links.at["aux_link", "outage_rate_value"] = float("nan")
    n.links.at["aux_link", "mttr_hours"] = float("nan")
    rows = critical_feeder_rows(
        n, hub_buses=["island", "crit", "aux"], import_links=["import_poc"]
    )
    assert [(r["name"], r["model"]) for r in rows] == [
        ("crit_feeder_2", NO_OUTAGE_DATA)
    ]


def test_a_no_data_link_elsewhere_is_not_named():
    # An unrelated internal Link without data cuts nothing off.
    n = _hub_with_one_critical_feeder()
    n.add("Bus", "aux", carrier="AC")
    n.add("Load", "aux_load", bus="aux", p_set=1.0)
    n.add("Link", "aux_link", bus0="island", bus1="aux", p_nom=5.0)
    n.links.at["aux_link", "outage_rate_value"] = float("nan")
    n.links.at["aux_link", "mttr_hours"] = float("nan")
    rows = critical_feeder_rows(
        n, hub_buses=["island", "crit", "aux"], import_links=["import_poc"]
    )
    assert set(_by_name(rows)) == {"crit_feeder"}


def test_a_chained_pocket_is_flagged_and_not_bounded():
    n = _hub_with_one_critical_feeder()
    n.add("Bus", "mid", carrier="AC")
    n.links.at["crit_feeder", "bus0"] = "mid"
    n.add("Link", "mid_feeder", bus0="island", bus1="mid", p_nom=20.0)
    n.links.at["mid_feeder", "outage_rate_value"] = 0.01
    n.links.at["mid_feeder", "mttr_hours"] = 8.0
    _with_backup(n)  # units of its own do not make a chained pocket bounded
    rows = critical_feeder_rows(
        n, hub_buses=["island", "mid", "crit"], import_links=["import_poc"]
    )
    r = _by_name(rows)["crit_feeder"]
    assert (r["model"], r["chained"], r["upper_bound"]) == (UNSAMPLED, True, False)
    assert "chained" in r["reason"]


def test_the_certification_note_states_a_bound_only_once_it_is_computed():
    row = {"name": "f", "model": UNSAMPLED, "buses": ["h"], "upper_bound": True}
    assert "at most" not in certification_note([{**row, "lole_h": None}])
    assert "f at most 43.8 h/yr" in certification_note([{**row, "lole_h": 43.8}])
    composed = {**row, "name": "c", "model": COMPOSED, "upper_bound": False}
    chained = {**row, "name": "g", "upper_bound": False, "lole_h": None}
    note = certification_note([{**composed, "lole_h": 87.6}, chained])
    assert "not simulated for g" in note and "at most" not in note


def _stage_rows(monkeypatch, nyears):
    """``eh_stages._critical_pockets`` on two pockets of its own, and a
    no-data row: each row gets its own pocket's figures per year."""
    from types import SimpleNamespace

    import numpy as np

    from services.adequacy import critical_feeders as cf
    from services.adequacy import eh_stages

    pockets = [
        cf.Pocket(["a"], ["fa"], 0.1, np.ones(10)),
        cf.Pocket(["b"], ["fb"], 0.2, np.full(10, 2.0), bound=True),
    ]
    monkeypatch.setattr(cf, "pocket_series", lambda *a, **k: pockets)
    rows = [
        {"name": "fa", "model": COMPOSED, "lole_h": None, "eue_mwh": None},
        {"name": "fb", "model": UNSAMPLED, "lole_h": None, "eue_mwh": None},
        {"name": "fc", "model": NO_OUTAGE_DATA, "lole_h": None, "eue_mwh": None},
    ]
    scope = SimpleNamespace(critical_feeders=rows)
    inputs = SimpleNamespace(nyears=nyears, weights=np.ones(10))
    assert eh_stages._critical_pockets(None, scope, inputs, cfg=None) == pockets
    return _by_name(rows)


def test_each_feeder_row_gets_its_own_pockets_figures_per_year(monkeypatch):
    r = _stage_rows(monkeypatch, 2.0)
    assert (r["fa"]["lole_h"], r["fa"]["eue_mwh"]) == pytest.approx((0.5, 0.5))
    assert (r["fb"]["lole_h"], r["fb"]["eue_mwh"]) == pytest.approx((1.0, 2.0))
    assert (r["fc"]["lole_h"], r["fc"]["eue_mwh"]) == (None, None)


@pytest.mark.parametrize("nyears", [0.0, None])
def test_no_horizon_leaves_the_figures_unstated(monkeypatch, nyears):
    r = _stage_rows(monkeypatch, nyears)
    assert all(v["lole_h"] is None and v["eue_mwh"] is None for v in r.values())


def test_a_pocket_reads_its_load_per_snapshot_not_its_flat_set_point():
    # Load only in the first half of the hours: the pocket can be short only
    # then. The flat p_set (10 MW) would claim every hour.
    from services.adequacy.critical_feeders import pocket_series

    n = _hub_with_one_critical_feeder()
    half = len(n.snapshots) // 2
    series = [10.0] * half + [0.0] * (len(n.snapshots) - half)
    n.loads_t.p_set["hospital"] = series
    (p,) = pocket_series(n, _rows(n), demand_scaled_in_place=True)
    assert list(p.load) == series
