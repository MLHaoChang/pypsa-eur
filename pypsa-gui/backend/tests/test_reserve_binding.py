"""Reserve products on the commercial config: the field, its hashing, the price
binding and the currency gate (DC grid readiness 06; GK B L2, L3, L4; GK A R4, R8).

`bind_commercial` writes each product's capacity price to `buses_t["ic_reserve_price"]`
(`ic:reserve:<id>`) with full coverage or 422 `reserve_price_coverage`, and refuses a
price series in another currency than the project's (409 `currency_mismatch`). A
series with no unit is the stored EUR default. The preflight re-checks the currency
the binding recorded, so a later finance-currency change is caught before the solve.
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import pypsa
import pytest
from pydantic import ValidationError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from models import commercial as M  # noqa: E402
from models.schemas import SolverConfigSchema  # noqa: E402
from services.commercial import binding as B  # noqa: E402
from services.commercial import hashing as H  # noqa: E402
from services.commercial import preflight as PF  # noqa: E402
from services.commercial import settlement_inputs as SI  # noqa: E402
from services.solver import reserve_products as RP  # noqa: E402


def _ref(pid: str) -> M.PriceSeriesRef:
    return M.PriceSeriesRef(id=f"series-{pid}", version=1, hash=f"hash-{pid}-0000", source="user")


def _product(pid="fcr", **kw) -> dict:
    return {"id": pid, "kind": "fast_symmetric", "direction": "symmetric", "sustain_h": 0.5,
            "capacity_price": _ref(pid).model_dump(mode="json"), **kw}


def _cfg(*products) -> M.CommercialConfig:
    return M.CommercialConfig(poc_link="import", reserve_products=list(products))


def _net(snaps=4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=snaps, freq="h"))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "supply", bus="grid", p_nom=100.0)
    n.add("Link", "import", bus0="grid", bus1="site", p_nom=50.0)
    n.add("Load", "dc", bus="site", p_set=1.0)
    return n


def _series(n, value=10.0, gap=False) -> pd.Series:
    s = pd.Series(value, index=n.snapshots, dtype=float)
    return s.iloc[:-1] if gap else s


def _bind(n, cfg, *, currency=None, units=None, gap=False, meta=True):
    units = units or {}
    return B.bind_commercial(
        n, cfg, project_dir=None, resolve_ref=lambda ref: _series(n, gap=gap),
        currency=currency,
        series_meta=(lambda ref: {"unit": units[ref.id]} if ref.id in units else {}) if meta else None)


# ── the field and its hashing (GK A R4, GK B L4) ────────────────────────────


def test_no_products_dumps_and_hashes_as_before_the_field():
    cfg = M.CommercialConfig(poc_link="import")
    assert "reserve_products" not in cfg.model_dump(mode="json")
    assert "reserve_products" not in cfg.model_dump()
    assert H.FIELDS_AFTER_V1[("CommercialConfig", "reserve_products")] == []
    explicit = M.CommercialConfig(poc_link="import", reserve_products=[])
    for v in (1, 2):
        assert H.digest(explicit, version=v) == H.digest(cfg, version=v)


def test_products_are_content_and_round_trip():
    cfg = _cfg(_product())
    dumped = cfg.model_dump(mode="json")
    assert [p["id"] for p in dumped["reserve_products"]] == ["fcr"]
    assert M.CommercialConfig.model_validate(dumped) == cfg
    assert H.digest(cfg) != H.digest(M.CommercialConfig(poc_link="import"))


def test_the_goal_free_config_carries_the_products_only_when_set():
    from services.reliability_goal import goal_free_config

    bare = SolverConfigSchema(commercial={"poc_link": "import"}).model_dump(mode="json")
    assert "reserve_products" not in goal_free_config(bare)["commercial"]
    with_p = SolverConfigSchema(commercial=_cfg(_product()).model_dump(mode="json")).model_dump(mode="json")
    assert goal_free_config(with_p)["commercial"]["reserve_products"][0]["id"] == "fcr"


@pytest.mark.parametrize("bad", [{"sustain_h": 0}, {"sustain_h": -1}, {"max_commit_mw": -0.1},
                                 {"sustain_h": float("nan")}])
def test_every_write_path_refuses_a_bad_product(bad):
    """R8: the solver-config schema (PUT, bulk import, the document-values preview)
    validates through CommercialConfig."""
    payload = {"poc_link": "import", "reserve_products": [_product(**bad)]}
    with pytest.raises(ValidationError):
        M.CommercialConfig.model_validate(payload)
    with pytest.raises(ValidationError):
        SolverConfigSchema(commercial=payload)


def test_a_product_id_twice_is_refused():
    with pytest.raises(ValidationError, match="more than once"):
        _cfg(_product(), _product())


# ── the binding (GK B L3) ────────────────────────────────────────────────────


def test_the_binding_writes_each_price_fully_covered():
    n = _net()
    stored = _bind(n, _cfg(_product("fcr"), _product("afrr", kind="secondary", direction="up")))
    frame = n.buses_t[RP.PRICE_FRAME]
    assert sorted(frame.columns) == ["ic:reserve:afrr", "ic:reserve:fcr"]
    assert (frame["ic:reserve:fcr"] == 10.0).all()
    assert [p["id"] for p in stored["reserve_products"]] == ["fcr", "afrr"]
    # The solve reads it back as bound (not missing, not stale).
    s, flags = SI._read(n, RP.PRICE_FRAME, "ic:reserve:fcr", _ref("fcr"), RP.PRICE_MISSING)
    assert flags == [] and s is not None


def test_a_gap_is_refused_and_nothing_is_written():
    n = _net()
    with pytest.raises(B.BindingRefusal) as exc:
        _bind(n, _cfg(_product()), gap=True)
    assert (exc.value.status, exc.value.code) == (422, "reserve_price_coverage")
    assert RP.PRICE_FRAME not in n.buses_t or n.buses_t[RP.PRICE_FRAME].empty


def test_a_product_that_leaves_the_config_loses_its_column_and_clearing_drops_all():
    n = _net()
    _bind(n, _cfg(_product("fcr"), _product("mfrr", kind="tertiary", direction="down")))
    _bind(n, _cfg(_product("fcr")))
    assert list(n.buses_t[RP.PRICE_FRAME].columns) == ["ic:reserve:fcr"]
    assert set(n.meta[RP.CURRENCY_META]) == {"fcr"}
    _bind(n, M.CommercialConfig(poc_link="import"))
    assert RP.PRICE_FRAME not in n.buses_t or n.buses_t[RP.PRICE_FRAME].empty
    assert RP.CURRENCY_META not in n.meta
    _bind(n, _cfg(_product("fcr")))
    B.bind_commercial(n, None, project_dir=None, resolve_ref=lambda ref: None)
    assert RP.PRICE_FRAME not in n.buses_t or n.buses_t[RP.PRICE_FRAME].empty
    assert RP.CURRENCY_META not in n.meta


def test_without_products_the_binding_writes_nothing_new():
    n = _net()
    stored = _bind(n, M.CommercialConfig(poc_link="import"))
    assert "reserve_products" not in stored
    assert RP.PRICE_FRAME not in n.buses_t or n.buses_t[RP.PRICE_FRAME].empty
    assert RP.CURRENCY_META not in (n.meta or {})


# ── the currency gate (GK B L2) ──────────────────────────────────────────────


@pytest.mark.parametrize("unit, have", [("EUR/MW/h", "EUR"), ("gbp/MW/h", "GBP"), ("USD", "USD"),
                                        (None, "EUR"), ("", "EUR"), ("share", None),
                                        ("MW", None)])
def test_a_series_currency_is_read_from_its_unit(unit, have):
    assert B.series_currency({"unit": unit} if unit is not None else {}) == have


def test_a_price_in_another_currency_is_refused_before_any_write():
    n = _net()
    with pytest.raises(B.BindingRefusal) as exc:
        _bind(n, _cfg(_product()), currency="EUR", units={"series-fcr": "GBP/MW/h"})
    assert (exc.value.status, exc.value.code) == (409, "currency_mismatch")
    assert "GBP" in exc.value.message and "EUR" in exc.value.message
    assert RP.PRICE_FRAME not in n.buses_t or n.buses_t[RP.PRICE_FRAME].empty


def test_the_stored_eur_default_is_refused_in_a_gbp_project_and_binds_in_a_eur_one():
    with pytest.raises(B.BindingRefusal) as exc:
        _bind(_net(), _cfg(_product()), currency="GBP")
    assert exc.value.code == "currency_mismatch" and "priced in EUR" in exc.value.message
    n = _net()
    _bind(n, _cfg(_product()), currency="EUR")
    assert n.meta[RP.CURRENCY_META] == {"fcr": "EUR"}


def test_a_unit_naming_no_currency_is_refused_in_a_project_that_states_one():
    with pytest.raises(B.BindingRefusal, match="states no currency"):
        _bind(_net(), _cfg(_product()), currency="EUR", units={"series-fcr": "share"})


def test_an_unstated_project_currency_gates_nothing():
    n = _net()
    _bind(n, _cfg(_product()), currency=None, units={"series-fcr": "GBP/MW/h"})
    assert n.meta[RP.CURRENCY_META] == {"fcr": "GBP"}


def test_a_stated_currency_with_no_meta_reader_is_refused_not_unchecked():
    with pytest.raises(B.BindingRefusal) as exc:
        _bind(_net(), _cfg(_product()), currency="EUR", meta=False)
    assert exc.value.code == "currency_mismatch"
    n = _net()
    _bind(n, _cfg(_product()), currency=None, meta=False)
    assert n.meta[RP.CURRENCY_META] == {"fcr": None}


def test_the_preflight_catches_a_finance_currency_changed_after_binding():
    n = _net()
    cfg = _cfg(_product())
    _bind(n, cfg, currency="EUR")
    commercial = cfg.model_dump(mode="json")

    def codes(currency):
        return [(sev, code) for sev, code, *_ in PF.commercial_findings(n, commercial, currency=currency)
                if code == "commercial.currency_mismatch"]

    assert codes("EUR") == []
    assert codes(None) == []
    assert codes("GBP") == [("error", "commercial.currency_mismatch")]
    # A price bound where its currency was not read is not guessed.
    n.meta[RP.CURRENCY_META] = {"fcr": None}
    assert codes("GBP") == []


# ── the investment case's staleness key (GK A R4, GK B L3/L4) ──────────────


def _ic_digest(stored):
    from services.finance.investment_case_runner import assumptions_digest

    return assumptions_digest(finance={"currency": "EUR"}, commercial=stored, dispatch=None, packs={})


def test_without_products_the_ic_digest_is_masters():
    """Pinned on master 33028f86 with the same bound config (no products)."""
    stored = _bind(_net(), M.CommercialConfig(poc_link="import", timezone="Europe/Amsterdam"))
    h, parts = _ic_digest(stored)
    assert parts["commercial"] == "11496092cfbcd746ded5a5b5355aa5fb"
    assert h == "a1ec2cfba17637325f25f239fb0dd178"


def test_with_products_the_ic_digest_carries_the_price_series_hash():
    base = _ic_digest(_bind(_net(), M.CommercialConfig(poc_link="import")))[1]["commercial"]
    one = _ic_digest(_bind(_net(), _cfg(_product())))[1]["commercial"]
    other_series = _product()
    other_series["capacity_price"] = {**other_series["capacity_price"], "hash": "hash-fcr-1111"}
    two = _ic_digest(_bind(_net(), _cfg(other_series)))[1]["commercial"]
    assert len({base, one, two}) == 3
