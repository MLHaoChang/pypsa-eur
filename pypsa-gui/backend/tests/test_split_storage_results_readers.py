"""AS-11 slice 2, PR B: the results readers count a split storage (Store + charger/discharger Links) like the
equal StorageUnit. Each test reads a network whose StorageUnit was migrated to the split layout
(`migrate_storage_unit`) against the same network with the StorageUnit."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.compare.economics import _compute_economics_summary
from services.compare.storage_cycling import _compute_storage_cycling_summary
from services.results.dispatch_digest import dispatch_digest
from services.results.load_shape import load_shape
from services.solver.split_storage import _wrap_with_split_storage, migrate_storage_unit

HOURS = 48


def _site(price: np.ndarray) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-06", periods=len(price), freq="h"))
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Generator", "import", bus="ac", carrier="import", p_nom=1e4,
          marginal_cost=pd.Series(price, n.snapshots))
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom=100.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.9, marginal_cost=2.0, capital_cost=5000.0)
    return n


@pytest.fixture(scope="module")
def pair() -> tuple[pypsa.Network, pypsa.Network]:
    """The AS-02 hand case: arbitrage on a two-price day, solved in both layouts."""
    price = np.array([200.0 if t % 12 < 4 else 10.0 for t in range(HOURS)])
    legacy = _site(price)
    assert legacy.optimize(solver_name="highs")[0] == "ok"
    split = _site(price)
    migrate_storage_unit(split, "bess")
    assert split.optimize(solver_name="highs", extra_functionality=_wrap_with_split_storage(split, None))[0] == "ok"
    assert float(split.objective) == pytest.approx(float(legacy.objective), rel=1e-6)
    return legacy, split


def test_the_dispatch_digest_lists_a_split_storage_like_the_storage_unit(pair):
    a, b = (dispatch_digest(n)["storage_dispatch"] for n in pair)
    (ua,), (ub,) = a["units"], b["units"]
    assert (ub["name"], ub["carrier"]) == ("bess", "battery")
    assert ua["energy_out_mwh"] > 0 and ua["energy_in_mwh"] > 0
    for k, v in ua.items():
        if isinstance(v, float):
            assert ub[k] == pytest.approx(v, rel=1e-6, abs=1e-6), k
        else:
            assert ub[k] == v, k
    assert [r["name"] for r in b["by_carrier"]] == ["battery"]
    assert b["by_carrier"][0]["energy_out_mwh"] == pytest.approx(a["by_carrier"][0]["energy_out_mwh"], rel=1e-6)


def test_the_load_shape_battery_soc_reads_a_split_storage_like_the_storage_unit(pair):
    a, b = (load_shape(n)["week"]["series"]["battery_soc"] for n in pair)
    assert a["status"] == b["status"] == "ok"
    assert max(a["mw"]) > 0
    assert b["mw"] == pytest.approx(a["mw"], rel=1e-6, abs=1e-4)


def _bucket(n, carrier: str) -> dict:
    return _compute_economics_summary(n, [], False, True).model_dump()["by_carrier"][carrier]


def test_compare_economics_books_a_split_storage_like_the_storage_unit(pair):
    a, b = (_compute_economics_summary(n, [], False, True).model_dump()["by_carrier"] for n in pair)
    assert set(b) == set(a)                      # no 'battery charger' / 'battery discharger' buckets
    for k in ("dispatch_mwh", "revenue_eur", "opex_eur", "gen_cost_eur", "storage_charge_cost_eur", "capex_eur"):
        assert b["battery"][k]["total"] == pytest.approx(a["battery"][k]["total"], rel=1e-6, abs=1e-6), k
    assert a["battery"]["capex_eur"]["total"] > 0 and a["battery"]["storage_charge_cost_eur"]["total"] > 0


# ── storage cycling: per-period vintages of a split storage's Store ───────────────────────────────────────

PERIODS = [2030, 2040]


def _multi(split: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([PERIODS, range(4)]))
    n.investment_periods = PERIODS
    n.add("Bus", "ac", carrier="AC")
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom=100.0, p_nom_opt=100.0, max_hours=4.0)
    p = pd.Series([100.0, -100.0, 50.0, -50.0] * 2, n.snapshots)
    vr = {"StorageUnit": {"bess": {"capacity_field": "p_nom", "initial_capacity": 50.0,
                                   "periods": [{"build_year": 2040, "p_nom_opt": 50.0}]}}}
    if not split:
        n.storage_units_t["p"] = pd.DataFrame({"bess": p})
    else:
        migrate_storage_unit(n, "bess")
        n.links_t["p0"] = pd.DataFrame({"bess charger": (-p).clip(lower=0.0), "bess discharger": p.clip(lower=0.0)})
        n.links_t["p1"] = pd.DataFrame({"bess charger": -(-p).clip(lower=0.0), "bess discharger": -p.clip(lower=0.0)})
        vr = {"Store": {"bess": {"capacity_field": "e_nom", "initial_capacity": 200.0,
                                 "periods": [{"build_year": 2040, "e_nom_opt": 200.0, "p_nom_opt": 200.0}]}}}
    n.meta = {"vintage_results": vr}
    return n


def test_storage_cycling_reads_per_period_vintages_of_a_split_storage_store():
    a, b = (_compute_storage_cycling_summary(_multi(s), PERIODS, True, True) for s in (False, True))
    (ua,), (ub,) = a.by_unit, b.by_unit
    ca, cb = ua.cycles.model_dump(), ub.cycles.model_dump()
    assert ca["by_period"]["2030"] == pytest.approx(2 * ca["by_period"]["2040"])   # the 2040 vintage doubles E
    assert cb["by_period"] == pytest.approx(ca["by_period"])
    assert cb["total"] == pytest.approx(ca["total"])
