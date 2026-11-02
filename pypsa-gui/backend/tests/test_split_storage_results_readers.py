"""AS-11 slice 2, PR B: the results readers count a split storage (Store + charger/discharger Links) like the
equal StorageUnit. Each pair test reads a network whose StorageUnits were migrated to the split layout
(`migrate_storage_unit`) against the same network with the StorageUnits, both solved.

The fixture discriminates: charge and discharge efficiencies differ from 1 and from each other, the storage loses
charge, snapshot weightings are not 1 and the price varies every hour. Two separate solves agree to `REL`, not to
the bit."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pypsa
import pytest

from models.commercial import CommercialConfig
from services.compare.economics import _compute_economics_summary
from services.compare.storage_cycling import _compute_storage_cycling_summary
from services.results import billing as B
from services.results.dispatch_digest import dispatch_digest
from services.results.load_shape import load_shape
from services.solver.split_storage import _wrap_with_split_storage, add_split_storage, migrate_storage_unit
from services.solver.storage_inventory import results_splits

HOURS = 48
REL = 1e-6
W = 3.0                                     # every snapshot stands for 3 h


def _price(hours: int) -> np.ndarray:
    return np.array([40.0 + 160.0 * (t % 12 < 4) + 3.0 * (t % 7) for t in range(hours)])


def _add_assets(n: pypsa.Network, price: np.ndarray) -> None:
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "aux", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=50.0)
    n.add("Load", "aux load", bus="aux", p_set=5.0)
    n.add("Generator", "import", bus="ac", carrier="import", p_nom=1e4,
          marginal_cost=pd.Series(price, n.snapshots))
    # A Link that only shares a split charger's carrier string: it stays in the generic walk.
    n.add("Link", "aux feed", bus0="ac", bus1="aux", carrier="battery charger", p_nom=10.0,
          efficiency=0.98, marginal_cost=1.0)
    n.add("StorageUnit", "bess", bus="ac", carrier="battery", p_nom=100.0, max_hours=4.0,
          efficiency_store=0.95, efficiency_dispatch=0.9, standing_loss=0.002, marginal_cost=2.0,
          capital_cost=5000.0)
    n.add("StorageUnit", "fw", bus="ac", carrier="flywheel", p_nom=10.0, max_hours=1.0,
          efficiency_store=0.97, efficiency_dispatch=0.97)


def _site(split: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-06", periods=HOURS, freq="h"))
    n.snapshot_weightings.loc[:, :] = W
    _add_assets(n, _price(HOURS))
    if split:
        migrate_storage_unit(n, "bess")
        migrate_storage_unit(n, "fw")
    return n


def _solve(n: pypsa.Network, **kw) -> pypsa.Network:
    ef = _wrap_with_split_storage(n, None) if len(n.stores) else None
    assert n.optimize(solver_name="highs", extra_functionality=ef, **kw)[0] == "ok"
    return n


@pytest.fixture(scope="module")
def pair() -> tuple[pypsa.Network, pypsa.Network]:
    legacy, split = _solve(_site(False)), _solve(_site(True))
    assert float(split.objective) == pytest.approx(float(legacy.objective), rel=REL)
    assert {e.name for e in results_splits(split)} == {"bess", "fw"}
    return legacy, split


def _close(got, want, what=""):
    if isinstance(want, float):
        assert got == pytest.approx(want, rel=REL, abs=1e-6), what
    elif isinstance(want, dict):
        assert set(got) == set(want), what
        for k in want:
            _close(got[k], want[k], f"{what}.{k}")
    else:
        assert got == want, what


# ── dispatch digest ────────────────────────────────────────────────────────────────────────────────────

def test_the_dispatch_digest_lists_a_split_storage_like_the_storage_unit(pair):
    a, b = (dispatch_digest(n)["storage_dispatch"] for n in pair)
    assert [u["name"] for u in b["units"]] == [u["name"] for u in a["units"]] == ["bess", "fw"]
    ua = a["units"][0]
    assert ua["carrier"] == "battery" and ua["energy_out_mwh"] > 0 and ua["energy_in_mwh"] > 0
    for ra, rb in zip(a["units"] + a["by_carrier"], b["units"] + b["by_carrier"]):
        _close(rb, ra, ra["name"])


# ── load shape ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_load_shape_battery_soc_reads_a_split_storage_like_the_storage_unit(pair):
    a, b = (load_shape(n)["week"]["series"]["battery_soc"] for n in pair)
    assert a["status"] == b["status"] == "ok"
    assert max(a["mw"]) > 0
    assert b["mw"] == pytest.approx(a["mw"], rel=REL, abs=1e-4)    # the flywheel counts in neither


# ── billing ────────────────────────────────────────────────────────────────────────────────────────────

def _result_df(n, attr, col, _kind):
    return getattr(getattr(n, attr), col, None)


def _cfg(*contracts) -> CommercialConfig:
    return CommercialConfig(poc_link="import", contracts=list(contracts))


def _eaas(cid: str, *assets: str) -> dict:
    return {"type": "eaas", "id": cid, "provider": "ESCO", "customer": "site", "fee_eur_per_mwh": 20.0,
            "tenor_years": 10, "asset_ids": list(assets)}


def _settle(n, cfg):
    return B.settlement_lines(n, cfg, {"intervals": {}, "result_df": _result_df})


def test_billing_settles_a_split_storage_contract_like_the_storage_unit_contract(pair):
    a, b = (B._raw_frames(n, _result_df)["storage_discharge"] for n in pair)
    assert list(b.columns) == list(a.columns) == ["bess", "fw"]
    assert b.to_numpy() == pytest.approx(a.to_numpy(), rel=REL, abs=1e-6)
    assert a["bess"].sum() > 0

    cfg = _cfg(_eaas("bess fee", "bess"))
    (la,), fa = _settle(pair[0], cfg)
    (lb,), fb = _settle(pair[1], cfg)
    assert fa == fb == []
    assert la.amount > 0 and lb.amount == pytest.approx(la.amount, rel=REL)


def test_billing_refuses_a_contract_that_names_a_split_storage_and_its_discharger(pair):
    cfg = _cfg(_eaas("twice", "bess", "bess discharger"), _eaas("link only", "bess discharger"))
    lines, flags = _settle(pair[1], cfg)
    assert [ln.contract_id for ln in lines] == ["link only"]
    (flag,) = flags
    assert flag.startswith("contract_not_settled:twice:") and "bess discharger" in flag


# ── compare economics ──────────────────────────────────────────────────────────────────────────────────

ECON = ("dispatch_gwh", "revenue_meur", "opex_meur", "gen_cost_meur", "storage_charge_cost_meur", "capex_meur",
        "lcoe_eur_per_mwh")


def _econ(n, periods=(), multi=False) -> dict:
    return _compute_economics_summary(n, list(periods), multi, True, prices_from_state=False).model_dump()["by_carrier"]


def _assert_same_buckets(a: dict, b: dict) -> None:
    assert set(b) == set(a)                     # no split 'battery discharger' bucket; 'battery charger' is aux feed
    for carrier in a:
        for k in ECON:
            _close(b[carrier][k], a[carrier][k], f"{carrier}.{k}")


def test_compare_economics_books_a_split_storage_like_the_storage_unit(pair):
    a, b = (_econ(n) for n in pair)
    assert {"battery", "flywheel", "battery charger"} <= set(a)
    bat = a["battery"]
    assert bat["capex_meur"]["total"] > 0 and bat["storage_charge_cost_meur"]["total"] > 0
    assert bat["gen_cost_meur"]["total"] > 0 and bat["revenue_meur"]["total"] > 0
    _assert_same_buckets(a, b)


PERIODS = [2030, 2040]


def _two_period_site(split: bool) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product([PERIODS, range(24)]))
    n.investment_periods = PERIODS
    n.snapshot_weightings.loc[:, :] = W
    n.investment_period_weightings.loc[:, :] = 10.0
    _add_assets(n, _price(2 * 24))
    if split:
        migrate_storage_unit(n, "bess")
        migrate_storage_unit(n, "fw")
    return n


def test_compare_economics_books_a_split_storage_like_the_storage_unit_on_two_periods():
    a, b = (_econ(_solve(_two_period_site(s), multi_investment_periods=True), PERIODS, True) for s in (False, True))
    assert set(a["battery"]["dispatch_gwh"]["by_period"]) == {"2030", "2040"}
    _assert_same_buckets(a, b)


def _hand_split(p_nom: float | None = 30.0, power_cc: float = 4000.0, energy_cc: float = 900.0) -> pypsa.Network:
    """A split storage built as one (not migrated): energy capex on the Store, power capex on the charger, a VOM
    on the discharger and on the Store."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-06", periods=24, freq="h"))
    n.snapshot_weightings.loc[:, :] = W
    n.add("Bus", "ac", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=20.0)
    n.add("Generator", "import", bus="ac", carrier="import", p_nom=1e3,
          marginal_cost=pd.Series(_price(24), n.snapshots))
    add_split_storage(n, "bat", "ac", "energy_bess", carrier="battery", p_nom=p_nom, power_capital_cost=power_cc,
                      energy_capital_cost=energy_cc,
                      efficiency_charge=0.93, efficiency_discharge=0.92, marginal_cost=1.5,
                      standing_loss=0.01)
    n.stores.loc["bat", "marginal_cost"] = 0.7
    return _solve(n)


def test_compare_economics_capex_and_vom_of_a_built_split_storage_by_hand():
    n = _hand_split()
    (e,) = results_splits(n)
    bucket = _econ(n)[e.carrier.lower()]
    capex = sum(float(df.at[r, "capital_cost"]) * float(df.at[r, col])
                for df, r, col in ((n.links, "bat charger", "p_nom_opt"), (n.links, "bat discharger", "p_nom_opt"),
                                   (n.stores, "bat", "e_nom_opt")))
    assert capex > 0
    w = n.snapshot_weightings.objective
    # the walk's rule for every row, the StorageUnit's included: annual capex pro rata to the modelled hours
    assert bucket["capex_meur"]["total"] == pytest.approx(capex * w.sum() / 8760 / 1e6, rel=REL)
    vom = sum((n.links_t.p0[l] * n.links.at[l, "marginal_cost"] * w).sum() for l in ("bat charger", "bat discharger"))
    store_vom = (n.stores_t.p["bat"] * 0.7 * w).sum()
    assert store_vom != 0
    assert bucket["gen_cost_meur"]["total"] == pytest.approx((vom + store_vom) / 1e6, rel=REL)
    assert set(_econ(n)) == {e.carrier.lower(), "import"}


def test_compare_economics_capex_still_reconciles_with_the_capacity_tab_on_a_split_storage():
    """Moving the split's capex into its Store's carrier keeps the Economics total equal to the Capacity tab's
    (both walk extendable Links and Stores; `test_capacity_and_economics_agree_on_total_capex`)."""
    import routers.compare as CMP

    n = _hand_split(p_nom=None, power_cc=20.0, energy_cc=4.0)       # cheap enough for 72 h of arbitrage
    cap = CMP._compute_capacity_summary(n, [], False, True)
    cap_total = sum(c.total for c in cap.capex_meur_by_carrier.values())
    econ_total = sum(e["capex_meur"]["total"] for e in _econ(n).values())
    assert econ_total > 0 and cap_total == pytest.approx(econ_total, rel=REL)


def test_compare_economics_leaves_an_untagged_store_battery_as_it_was():
    """An imported PyPSA-Eur battery (Store + Links, no split tag) keeps its per-Link buckets: dispatch is each
    Link's p0, as before AS-11."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-06", periods=24, freq="h"))
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "b", carrier="battery")
    n.add("Load", "load", bus="ac", p_set=20.0)
    n.add("Generator", "import", bus="ac", carrier="import", p_nom=1e3,
          marginal_cost=pd.Series(_price(24), n.snapshots))
    n.add("Store", "b", bus="b", carrier="battery", e_nom=100.0, e_cyclic=True)
    n.add("Link", "b charger", bus0="ac", bus1="b", carrier="battery charger", p_nom=25.0, efficiency=0.95)
    n.add("Link", "b discharger", bus0="b", bus1="ac", carrier="battery discharger", p_nom=25.0, efficiency=0.95)
    _solve(n)
    assert results_splits(n) == []
    got = _econ(n)
    assert {"battery charger", "battery discharger"} <= set(got)
    for link, carrier in (("b charger", "battery charger"), ("b discharger", "battery discharger")):
        assert n.links_t.p0[link].sum() > 0
        assert got[carrier]["dispatch_gwh"]["total"] == pytest.approx(n.links_t.p0[link].sum() / 1e3, rel=REL)


# ── storage cycling: per-period vintages of a split storage's Store ───────────────────────────────────────

def _multi(split: bool, store_vintages: bool = True, vintage_key: str = "e_nom_opt") -> pypsa.Network:
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
        # `p_nom_opt` differs from `e_nom_opt` so a reader taking the wrong one fails.
        vr = {"Store": {"bess": {"capacity_field": "e_nom", "initial_capacity": 200.0,
                                 "periods": [{"build_year": 2040, "e_nom_opt": 200.0, "p_nom_opt": 999.0}
                                             if vintage_key == "e_nom_opt" else
                                             {"build_year": 2040, "p_nom_opt": 200.0}]}}}
    n.meta = {"vintage_results": vr if store_vintages or not split else {}}
    return n


def test_storage_cycling_reads_per_period_vintages_of_a_split_storage_store():
    a, b = (_compute_storage_cycling_summary(_multi(s), PERIODS, True, True) for s in (False, True))
    (ua,), (ub,) = a.by_unit, b.by_unit
    ca, cb = ua.cycles.model_dump(), ub.cycles.model_dump()
    assert ca["by_period"]["2030"] == pytest.approx(2 * ca["by_period"]["2040"])   # the 2040 vintage doubles E
    assert cb["by_period"] == pytest.approx(ca["by_period"])
    assert cb["total"] == pytest.approx(ca["total"])


def test_storage_cycling_reads_a_store_vintage_written_with_p_nom_opt_only():
    """`vintage_service` writes a Store vintage under `p_nom_opt` alone (`myopic` writes both keys)."""
    a = _compute_storage_cycling_summary(_multi(False), PERIODS, True, True)
    b = _compute_storage_cycling_summary(_multi(True, vintage_key="p_nom_opt"), PERIODS, True, True)
    assert b.by_unit[0].cycles.model_dump()["by_period"] == pytest.approx(a.by_unit[0].cycles.model_dump()["by_period"])


def test_storage_cycling_keeps_the_final_store_size_without_a_vintage_entry():
    out = _compute_storage_cycling_summary(_multi(True, store_vintages=False), PERIODS, True, True)
    (u,) = out.by_unit
    # 300 MWh of throughput per period over 2 x 400 MWh, in both periods.
    assert u.cycles.model_dump()["by_period"] == pytest.approx({"2030": 300 / 800, "2040": 300 / 800})


# ── one name rule: a Store named like a StorageUnit stays out of every reader ───────────────────────────

@pytest.fixture(scope="module")
def collision() -> tuple[pypsa.Network, pypsa.Network]:
    """`bess` is a StorageUnit; the second network also has a split storage whose Store is named `bess`."""
    plain = _solve(_site(False))
    clash = _site(False)
    add_split_storage(clash, "bess", "ac", "energy_bess", carrier="battery", p_nom=20.0, efficiency_charge=0.9, efficiency_discharge=0.9)
    _solve(clash)
    assert results_splits(clash) == [] and clash.links_t.p0["bess discharger"].abs().sum() > 0
    return plain, clash


def test_a_colliding_split_storage_stays_out_of_the_dispatch_digest(collision):
    plain, clash = collision
    units = dispatch_digest(clash)["storage_dispatch"]["units"]
    assert [u["name"] for u in units] == ["bess", "fw"]
    assert units[0]["energy_out_mwh"] == pytest.approx(
        (clash.storage_units_t.p["bess"].clip(lower=0) * W).sum(), rel=REL)


def test_a_colliding_split_storage_stays_out_of_billing(collision):
    _, clash = collision
    raw = B._raw_frames(clash, _result_df)
    assert list(raw["storage_discharge"].columns) == ["bess", "fw"] and raw["split_dischargers"] == {}
    assert raw["storage_discharge"]["bess"].to_numpy() == pytest.approx(
        clash.storage_units_t.p["bess"].clip(lower=0).to_numpy())


def test_a_colliding_split_storage_stays_out_of_the_load_shape(collision):
    _, clash = collision
    got = load_shape(clash)["week"]["series"]["battery_soc"]["mw"]
    # the StorageUnit's state of charge, once: the same-named Store is not a results split (was counted too)
    assert clash.stores_t.e["bess"].max() > 1.0
    want = clash.storage_units_t.state_of_charge["bess"]
    assert got == pytest.approx(want.to_numpy()[: len(got)], rel=REL, abs=1e-4)


def test_a_store_named_like_a_storage_unit_keeps_its_own_carrier_in_the_load_shape(collision):
    """The EV series reads the colliding Store's own carrier, not the StorageUnit's."""
    clash = _copy(collision[1])
    clash.stores.loc["bess", "carrier"] = "ev"
    got = load_shape(clash)["week"]["series"]["ev"]
    assert got["status"] == "ok"
    want = (-clash.stores_t.p["bess"]).clip(lower=0)
    assert want.max() > 1.0
    assert got["mw"] == pytest.approx(want.to_numpy()[: len(got["mw"])], rel=REL, abs=1e-4)


def test_a_colliding_split_storage_stays_out_of_compare_economics(collision):
    _, clash = collision
    got = _econ(clash)
    assert "battery discharger" in got                     # its Links read as plain Links
    assert got["battery discharger"]["dispatch_gwh"]["total"] == pytest.approx(
        (clash.links_t.p0["bess discharger"] * W).sum() / 1e3, rel=REL)


def test_a_colliding_split_storage_stays_out_of_storage_cycling(collision):
    _, clash = collision
    out = _compute_storage_cycling_summary(clash, [], False, True)
    units = {u.name: u for u in out.by_unit}
    assert sorted(units) == ["bess", "fw"]
    assert units["bess"].energy_mwh == pytest.approx(400.0)


# ── a split storage charging at one bus and discharging at another stays out of every reader ─────────────

@pytest.fixture(scope="module")
def cross_bus() -> pypsa.Network:
    """`bat` charges at `ac` and discharges to `b2`, which has its own price."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-06", periods=24, freq="h"))
    n.snapshot_weightings.loc[:, :] = W
    n.add("Bus", "ac", carrier="AC")
    n.add("Bus", "b2", carrier="AC")
    n.add("Load", "load", bus="ac", p_set=20.0)
    n.add("Load", "load b2", bus="b2", p_set=20.0)
    n.add("Generator", "import", bus="ac", carrier="import", p_nom=1e3,
          marginal_cost=pd.Series(_price(24), n.snapshots))
    n.add("Generator", "import b2", bus="b2", carrier="import", p_nom=1e3,
          marginal_cost=pd.Series(_price(24)[::-1] + 50.0, n.snapshots))
    add_split_storage(n, "bat", "ac", "energy_bess", carrier="battery", p_nom=10.0, efficiency_charge=0.95,
                      efficiency_discharge=0.9)
    n.links.loc["bat discharger", "bus1"] = "b2"
    _solve(n)
    assert n.links_t.p0["bat discharger"].sum() > 0
    return n


def test_the_results_name_rule_leaves_out_a_cross_bus_split_storage(cross_bus):
    assert results_splits(cross_bus) == []


def test_a_cross_bus_split_storage_stays_out_of_the_dispatch_digest(cross_bus):
    assert dispatch_digest(cross_bus)["storage_dispatch"]["units"] == []


def test_a_cross_bus_split_storage_stays_out_of_billing(cross_bus):
    raw = B._raw_frames(cross_bus, _result_df)
    assert list(raw["storage_discharge"].columns) == [] and raw["split_dischargers"] == {}


def test_a_cross_bus_split_storage_stays_out_of_the_load_shape(cross_bus):
    assert load_shape(cross_bus)["week"]["series"]["battery_soc"]["status"] == "not_modelled"


def test_a_cross_bus_split_storage_stays_out_of_compare_economics(cross_bus):
    """Its Links stay in the generic walk, each priced at its own buses, as before AS-11."""
    got = _econ(cross_bus)
    assert {"battery charger", "battery discharger"} <= set(got)
    w = cross_bus.snapshot_weightings.objective
    prices = cross_bus.buses_t.marginal_price
    p0, p1 = cross_bus.links_t.p0["bat discharger"], cross_bus.links_t.p1["bat discharger"]
    rev = ((-p1 * prices["b2"] - p0 * prices["bat store"]) * w).sum()
    assert got["battery discharger"]["revenue_meur"]["total"] == pytest.approx(rev / 1e6, rel=1e-4)


def test_a_cross_bus_split_storage_stays_out_of_storage_cycling(cross_bus):
    assert _compute_storage_cycling_summary(cross_bus, [], False, True).by_unit == []


# ── AS-11 follow-ups: compare dispatch applies the one name rule ────────────────────────────────────────

def _copy(n: pypsa.Network) -> pypsa.Network:
    """A copy of a solved network (PyPSA refuses to copy one that still holds its solver model)."""
    if getattr(n, "model", None) is not None:
        n.model.solver_model = None
    return n.copy()


def _dispatch(n):
    from services.compare.dispatch import _compute_dispatch_summary
    return _compute_dispatch_summary(n, [], False, True)


def _without_split(n: pypsa.Network) -> pypsa.Network:
    """`n` with its split storage's Store and Links removed; the solved flows of everything else are kept."""
    ref = _copy(n)
    ref.remove("Store", "bess")
    ref.remove("Link", ["bess charger", "bess discharger"])
    return ref


def test_a_colliding_split_storage_stays_out_of_compare_dispatch(collision):
    _, clash = collision
    got, want = _dispatch(clash), _dispatch(_without_split(clash))
    assert want.dispatch_gwh_by_carrier["battery"].total > 0
    assert got.dispatch_gwh_by_carrier["battery"].total == pytest.approx(
        want.dispatch_gwh_by_carrier["battery"].total, rel=REL)
    assert got.storage_cycles_by_carrier["battery"].total == pytest.approx(
        want.storage_cycles_by_carrier["battery"].total, rel=REL)


def test_a_cross_bus_split_storage_stays_out_of_compare_dispatch(cross_bus):
    got = _dispatch(cross_bus)
    assert "battery" not in got.dispatch_gwh_by_carrier and "battery" not in got.storage_cycles_by_carrier


# ── AS-11 follow-ups: compare economics books marginal_cost_storage in both layouts ────────────────────

MCS = 0.4                                   # per MWh held for one hour


def _holding_site(split: bool, varying: bool = False) -> pypsa.Network:
    n = _site(split)
    mcs = pd.Series(MCS * (1 + (np.arange(HOURS) % 5)), n.snapshots) if varying else MCS
    if split:                               # migration refuses marginal_cost_storage, so it is set after
        if varying:
            n.stores_t.marginal_cost_storage["bess"] = mcs
        else:
            n.stores.loc["bess", "marginal_cost_storage"] = mcs
    elif varying:
        n.storage_units_t.marginal_cost_storage["bess"] = mcs
    else:
        n.storage_units.loc["bess", "marginal_cost_storage"] = mcs
    return n


@pytest.fixture(scope="module")
def holding_pair() -> tuple[pypsa.Network, pypsa.Network]:
    legacy, split = _solve(_holding_site(False)), _solve(_holding_site(True))
    assert float(split.objective) == pytest.approx(float(legacy.objective), rel=REL)
    return legacy, split


def _holding_lp(n: pypsa.Network, comp: str) -> float:
    """PyPSA's own marginal_cost_storage term for `comp` (Σ state of charge x cost x objective weight)."""
    return float(n.statistics.opex(components=comp, cost_types="marginal_cost_storage").sum())


def _zero_holding(n: pypsa.Network) -> pypsa.Network:
    m = _copy(n)
    for c in ("storage_units", "stores"):
        getattr(m, c)["marginal_cost_storage"] = 0.0
        getattr(m, c + "_t").marginal_cost_storage = getattr(m, c + "_t").marginal_cost_storage.iloc[:, :0]
    return m


@pytest.mark.parametrize("comp,idx", [("StorageUnit", 0), ("Store", 1)])
def test_compare_economics_books_marginal_cost_storage_as_the_lp_does(holding_pair, comp, idx):
    """Reconciliation: the holding cost the walk books equals PyPSA's objective term, in each layout."""
    n = holding_pair[idx]
    lp = _holding_lp(n, comp)
    assert lp > 0
    got, without = _econ(n)["battery"], _econ(_zero_holding(n))["battery"]
    for k in ("opex_meur", "gen_cost_meur"):
        assert got[k]["total"] - without[k]["total"] == pytest.approx(lp / 1e6, rel=REL), k


def test_compare_economics_books_marginal_cost_storage_alike_in_both_layouts(holding_pair):
    a, b = (_econ(n) for n in holding_pair)
    _assert_same_buckets(a, b)


def test_compare_economics_books_a_time_varying_marginal_cost_storage():
    for split, comp in ((False, "StorageUnit"), (True, "Store")):
        n = _solve(_holding_site(split, varying=True))
        soc = n.stores_t.e["bess"] if split else n.storage_units_t.state_of_charge["bess"]
        by_hand = float((soc * MCS * (1 + (np.arange(HOURS) % 5)) * n.snapshot_weightings.objective).sum())
        assert by_hand == pytest.approx(_holding_lp(n, comp), rel=REL) and by_hand > 0
        got = _econ(n)["battery"]["opex_meur"]["total"] - _econ(_zero_holding(n))["battery"]["opex_meur"]["total"]
        assert got == pytest.approx(by_hand / 1e6, rel=REL), comp


def test_compare_dispatch_reads_a_split_storage_like_the_storage_unit(pair):
    a, b = (_dispatch(n) for n in pair)
    for carrier in ("battery", "flywheel"):
        assert a.dispatch_gwh_by_carrier[carrier].total > 0 and a.storage_cycles_by_carrier[carrier].total > 0
        assert b.dispatch_gwh_by_carrier[carrier].total == pytest.approx(
            a.dispatch_gwh_by_carrier[carrier].total, rel=REL)
        assert b.storage_cycles_by_carrier[carrier].total == pytest.approx(
            a.storage_cycles_by_carrier[carrier].total, rel=REL)


def test_compare_economics_books_marginal_cost_storage_on_two_periods_by_hand():
    """Reconciled by hand, not with n.statistics.opex: the walk's cost weight carries the period weighting the LP's
    objective applies (10 here), which the statistics term leaves out."""
    for split in (False, True):
        n = _two_period_site(split)
        if split:
            n.stores.loc["bess", "marginal_cost_storage"] = MCS
        else:
            n.storage_units.loc["bess", "marginal_cost_storage"] = MCS
        _solve(n, multi_investment_periods=True)
        soc = n.stores_t.e["bess"] if split else n.storage_units_t.state_of_charge["bess"]
        pw = n.investment_period_weightings["objective"].reindex(n.snapshots.get_level_values(0)).to_numpy()
        held = soc * MCS * n.snapshot_weightings.objective * pw
        assert held.sum() > 0
        got = _econ(n, PERIODS, True)["battery"]["opex_meur"]
        without = _econ(_zero_holding(n), PERIODS, True)["battery"]["opex_meur"]
        assert got["total"] - without["total"] == pytest.approx(held.sum() / 1e6, rel=REL)
        for p in PERIODS:
            want = float(held.loc[p].sum()) / 1e6
            assert got["by_period"][str(p)] - without["by_period"][str(p)] == pytest.approx(want, rel=REL), p


def test_compare_economics_skips_a_non_finite_marginal_cost_storage(holding_pair):
    n = _copy(holding_pair[0])
    mcs = pd.Series(MCS, n.snapshots)
    mcs.iloc[5], mcs.iloc[9] = np.nan, np.inf
    n.storage_units_t.marginal_cost_storage["bess"] = mcs
    soc = n.storage_units_t.state_of_charge["bess"]
    assert soc.iloc[[5, 9]].sum() > 0
    want = (soc * mcs.where(np.isfinite(mcs), 0.0) * n.snapshot_weightings.objective).sum()
    got = _econ(n)["battery"]["opex_meur"]["total"] - _econ(_zero_holding(n))["battery"]["opex_meur"]["total"]
    assert got == pytest.approx(want / 1e6, rel=REL)


def test_compare_economics_without_marginal_cost_storage_is_byte_identical(pair):
    """mcs 0 (the default) books nothing: every bucket equals the walk with the attribute absent, to the bit."""
    for n in pair:
        m = _copy(n)
        for c in ("storage_units", "stores"):
            getattr(m, c).drop(columns="marginal_cost_storage", inplace=True)
        assert _econ(n) == _econ(m)
