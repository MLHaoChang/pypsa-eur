"""SCU06 K8a: market packs per bidding zone (GK B4 conditions 1-7).

Owner answers: SCU06-K8-2 = (a) AncillaryProduct is deleted; SCU06-K8-3 = (a)
reserve prices come from user upload only; SCU06-K8-4 = (a) flexible loads deferred.
"""

from __future__ import annotations

import json
import pathlib
import sys
import types
from typing import get_args

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from models import commercial as M  # noqa: E402
from services.commercial import market_packs as MP  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
REF = {"id": "series-da", "version": 1, "hash": "hash-da-0000", "source": "user"}
PRICE = {"id": "series-fcr", "version": 2, "hash": "hash-fcr-0000", "source": "user"}


def _pack(**kw) -> M.MarketPack:
    body = {
        "id": "no-1-2026",
        "region": "NO-1",
        "currency": "EUR",
        "valid_year": 2026,
        "day_ahead": None,
        "products": [_prod()],
        **kw,
    }
    return M.MarketPack.model_validate(
        {
            **body,
            "pack_hash": MP.pack_hash(
                M.MarketPack.model_validate({**body, "pack_hash": "0" * 8})
            ),
        }
    )


def _prod(pid="fast_symmetric", **kw) -> dict:
    return {
        "id": pid,
        "kind": "fast_symmetric",
        "direction": "symmetric",
        "sustain_h": 1.0,
        "capacity_price": None,
        "max_commit_mw": None,
        "storages": [],
        "counterparty": "tso",
        **kw,
    }


def _commercial(**kw) -> dict:
    return M.CommercialConfig(poc_link="import", **kw).model_dump(mode="json")


# ── condition 6: the region is a bidding zone ──────────────────────────────


def test_the_region_type_is_exactly_s1s_bidding_zones():
    from services.market_prices import BIDDING_ZONES

    assert set(get_args(M.BiddingZone)) == set(BIDDING_ZONES)


@pytest.mark.parametrize("bad", ["DE", "GB", "US_ERCOT", "no-1", ""])
def test_a_pack_for_no_bidding_zone_is_refused_at_load(bad):
    with pytest.raises(ValidationError):
        _pack(region=bad)


@pytest.mark.parametrize("bad", ["DE", "GB", "no-1", "", None, 7])
def test_a_stored_pack_for_no_bidding_zone_is_a_typed_refusal(bad):
    """GK B4 4a: `load_pack` (stored JSON) refuses with its code and names the
    field; no pydantic error passes through."""
    raw = {**_pack().model_dump(mode="json"), "region": bad}
    with pytest.raises(MP.PackRefusal) as e:
        MP.load_pack(raw)
    assert (e.value.code, e.value.status) == (MP.INVALID, 422)
    assert "region" in str(e.value)
    assert not isinstance(e.value, ValidationError)


def test_a_stored_legacy_pack_loads_through_load_pack():
    raw = json.loads((FIXTURES / "market_pack_legacy.json").read_text())
    assert MP.load_pack(raw).flags == [M.LEGACY_PACK_FIELDS_STRIPPED]


def test_a_zone_pack_loads():
    assert _pack(region="DE-LU").region == "DE-LU"


# ── condition 2: AncillaryProduct is deleted; old packs still load ─────────


def test_ancillary_product_is_gone():
    assert not hasattr(M, "AncillaryProduct")
    assert not hasattr(M, "MarketRegion")


def test_a_pack_with_the_old_fields_loads_stripped_with_a_named_flag():
    raw = json.loads((FIXTURES / "market_pack_legacy.json").read_text())
    pack = M.MarketPack.model_validate(raw)
    assert pack.flags == [M.LEGACY_PACK_FIELDS_STRIPPED]
    assert [p.id for p in pack.products] == [
        "fast_symmetric"
    ]  # the new-shape one stays
    assert "price_series" not in pack.model_dump()


def test_a_pack_without_old_fields_has_no_flag():
    assert _pack().flags == []


def test_configs_re_serialise_byte_equal_to_master():
    """Pinned on master c898a83c3: a config with a priced reserve product dumps
    and hashes as before (capacity_price is now nullable, never omitted)."""
    from services.commercial import hashing

    cfg = M.CommercialConfig(
        poc_link="import", reserve_products=[_prod("fcr", capacity_price=PRICE)]
    )
    assert cfg.model_dump_json() == MASTER_DUMP
    assert hashing.digest(cfg) == MASTER_DIGEST
    assert (
        M.CommercialConfig.model_validate_json(MASTER_DUMP).model_dump_json()
        == MASTER_DUMP
    )


MASTER_DUMP = '{"poc_link":"import","import_tariff_id":null,"import_tariff":null,"import_tariff_ref":null,"export_price_ref":null,"import_price_ref":null,"export_link":null,"timezone":null,"connection":null,"contracts":[],"grid_cfe_share_ref":null,"site_party":"site","group_contract":null,"group_members":[],"group_cap_mw":null,"demand_items":[],"meter_history_peaks_kw":{},"meter_history_energy_kwh":{},"initial_peak_lower_bound":{},"power_factor":null,"value_flows":null,"reference_purchases":[],"provenance":{},"co2_price":null,"reserve_products":[{"id":"fcr","kind":"fast_symmetric","direction":"symmetric","sustain_h":1.0,"capacity_price":{"id":"series-fcr","version":2,"hash":"hash-fcr-0000","source":"user","vintage_year":null,"provider":null},"max_commit_mw":null,"storages":[],"counterparty":"tso"}]}'
MASTER_DIGEST = "f926bd2f51dd3004"


# ── condition 7: the hash ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        {"sustain_h": 0.5},
        {"direction": "symmetric", "id": "other"},
        {"max_commit_mw": 3.0},
        {"capacity_price": PRICE},
        {"storages": ["bess"]},
        {"counterparty": "bsp"},
    ],
)
def test_the_hash_changes_with_a_product(change):
    assert _pack(products=[_prod(**change)]).pack_hash != _pack().pack_hash


def test_the_hash_changes_with_the_pack_fields_and_not_with_flags():
    base = _pack()
    assert (
        _pack(
            products=[_prod(), _prod("secondary_up", kind="secondary", direction="up")]
        ).pack_hash
        != base.pack_hash
    )
    assert _pack(day_ahead=REF).pack_hash != base.pack_hash
    assert _pack(currency="NOK").pack_hash != base.pack_hash
    assert _pack(valid_year=2027).pack_hash != base.pack_hash
    assert _pack(region="NO-2").pack_hash != base.pack_hash
    assert MP.pack_hash(base.model_copy(update={"flags": ["x"]})) == base.pack_hash
    assert MP.pack_hash(base) == base.pack_hash
    assert len(base.pack_hash) == 64


def test_the_shipped_templates_are_no1_to_no5_with_empty_refs():
    packs = MP.shipped()
    assert sorted(p.region for p in packs.values()) == [
        "NO-1",
        "NO-2",
        "NO-3",
        "NO-4",
        "NO-5",
    ]
    for p in packs.values():
        assert p.pack_hash == MP.pack_hash(p)
        assert p.day_ahead is None
        assert p.products and all(x.capacity_price is None for x in p.products)
        assert p.currency == "EUR" and p.flags == []
    with pytest.raises(MP.PackRefusal) as e:
        MP.get("de-lu-2026")
    assert (e.value.code, e.value.status) == (MP.UNKNOWN, 404)


# ── condition 6: each part of the zone/currency refusal ────────────────────


def test_no_project_zone_is_refused():
    with pytest.raises(MP.PackRefusal) as e:
        MP.check_fit(_pack(), None, "EUR")
    assert e.value.code == MP.ZONE_UNKNOWN


def test_another_zone_is_refused():
    with pytest.raises(MP.PackRefusal) as e:
        MP.check_fit(_pack(), "NO-2", "EUR")
    assert e.value.code == MP.ZONE_MISMATCH
    assert "NO-1" in str(e.value) and "NO-2" in str(e.value)


def test_another_currency_is_refused():
    with pytest.raises(MP.PackRefusal) as e:
        MP.check_fit(_pack(), "NO-1", "NOK")
    assert e.value.code == MP.CURRENCY_MISMATCH
    assert "EUR" in str(e.value) and "NOK" in str(e.value)


def test_the_same_zone_and_currency_fit():
    assert MP.check_fit(_pack(), "NO-1", "EUR") is None


@pytest.mark.parametrize("currency", [None, ""])
def test_no_stated_currency_is_refused(currency):
    with pytest.raises(MP.PackRefusal) as e:
        MP.check_fit(_pack(), "NO-1", currency)
    assert e.value.code == MP.CURRENCY_UNKNOWN
    assert "EUR" in str(e.value)


def test_the_project_zone_is_the_pick_else_the_pin(tmp_path, monkeypatch):
    from services import market_prices

    assert MP.project_zone(tmp_path) is None
    monkeypatch.setattr(market_prices, "pin_of", lambda _s: (59.9, 10.75))
    monkeypatch.setattr(market_prices, "zone_for_pin", lambda lat, lng: "NO-1")
    assert MP.project_zone(tmp_path) == "NO-1"
    market_prices.store_zone(tmp_path, "NO-2")
    assert MP.project_zone(tmp_path) == "NO-2"


# ── conditions 4 and 5: the per-cell plan ──────────────────────────────────


def test_a_first_apply_adds_the_products_without_a_price():
    plan = MP.plan_apply(_pack(), _commercial(), None)
    assert plan["commercial"]["reserve_products"] == [_prod()]
    cells = {c["cell"] for c in plan["changes"]}
    assert cells == {f"reserve_products/fast_symmetric/{f}" for f in _prod()}
    assert plan["conflicts"] == []
    assert plan["kept"] == [{"cell": MP.EXPORT_CELL, "reason": "empty_ref"}]
    assert plan["applied"]["reserve_products/fast_symmetric/capacity_price"] is None
    assert MP.EXPORT_CELL not in plan["applied"]


def test_an_empty_day_ahead_ref_never_clears_the_export_pick():
    have = _commercial(export_link="export", export_price_ref=PRICE)
    plan = MP.plan_apply(_pack(), have, None)
    assert plan["commercial"]["export_price_ref"] == have["export_price_ref"]
    assert {"cell": MP.EXPORT_CELL, "reason": "empty_ref"} in plan["kept"]
    # Even when the last apply wrote it.
    pin = {
        "pack_id": _pack().id,
        "pack_hash": "old",
        "applied": {MP.EXPORT_CELL: have["export_price_ref"]},
    }
    plan = MP.plan_apply(_pack(), have, pin)
    assert plan["commercial"]["export_price_ref"] == have["export_price_ref"]
    assert plan["applied"][MP.EXPORT_CELL] == have["export_price_ref"]


def test_a_day_ahead_ref_fills_an_empty_pick_never_a_users():
    pack = _pack(day_ahead=REF)
    no_link = MP.plan_apply(pack, _commercial(), None)
    assert {"cell": MP.EXPORT_CELL, "reason": "no_export_link"} in no_link["kept"]
    assert no_link["commercial"].get("export_price_ref") is None
    filled = MP.plan_apply(pack, _commercial(export_link="export"), None)
    full = M.PriceSeriesRef.model_validate(REF).model_dump(mode="json")
    assert filled["commercial"]["export_price_ref"] == full
    assert filled["applied"][MP.EXPORT_CELL] == full
    users = MP.plan_apply(
        pack, _commercial(export_link="export", export_price_ref=PRICE), None
    )
    assert users["commercial"]["export_price_ref"]["id"] == PRICE["id"]
    assert users["conflicts"][0]["cell"] == MP.EXPORT_CELL


def test_an_empty_capacity_price_never_overwrites_a_users_price():
    have = _commercial(reserve_products=[_prod(capacity_price=PRICE)])
    plan = MP.plan_apply(_pack(), have, None)
    assert (
        plan["commercial"]["reserve_products"][0]["capacity_price"]["id"] == PRICE["id"]
    )
    assert {
        "cell": "reserve_products/fast_symmetric/capacity_price",
        "reason": "empty_ref",
    } in plan["kept"]
    assert plan["changes"] == []


def test_an_update_changes_only_cells_the_user_left_as_applied():
    first = MP.plan_apply(_pack(), _commercial(), None)
    pin = {
        "pack_id": _pack().id,
        "pack_hash": _pack().pack_hash,
        "applied": first["applied"],
    }
    newer = _pack(products=[_prod(sustain_h=0.5, max_commit_mw=4.0)])
    assert MP.update_available(newer, pin)
    assert not MP.update_available(_pack(), pin)
    # The user edited max_commit_mw by hand after the first apply.
    stored = json.loads(json.dumps(first["commercial"]))
    stored["reserve_products"][0]["max_commit_mw"] = 2.0
    plan = MP.plan_apply(newer, stored, pin)
    assert plan["changes"] == [
        {"cell": "reserve_products/fast_symmetric/sustain_h", "from": 1.0, "to": 0.5}
    ]
    assert plan["conflicts"] == [
        {
            "cell": "reserve_products/fast_symmetric/max_commit_mw",
            "yours": 2.0,
            "pack": 4.0,
            "last_applied": None,
        }
    ]
    got = plan["commercial"]["reserve_products"][0]
    assert (got["sustain_h"], got["max_commit_mw"]) == (0.5, 2.0)
    # The pin keeps what the LAST apply wrote for the kept cell.
    assert plan["applied"]["reserve_products/fast_symmetric/max_commit_mw"] is None
    assert plan["applied"]["reserve_products/fast_symmetric/sustain_h"] == 0.5


def _pin(pack, plan) -> dict:
    return {"pack_id": pack.id, "pack_hash": pack.pack_hash, "applied": plan["applied"]}


def test_a_hand_revert_after_an_apply_is_kept_as_a_user_edit():
    """GK B4 1c: the user sets a cell the pack updated back to its old value by
    hand. Re-applying that pack, or a newer one, keeps the user's value."""
    v1, v2, v3 = (_pack(products=[_prod(sustain_h=h)]) for h in (1.0, 0.5, 0.25))
    first = MP.plan_apply(v1, _commercial(), None)
    second = MP.plan_apply(v2, first["commercial"], _pin(v1, first))
    cell = "reserve_products/fast_symmetric/sustain_h"
    assert second["changes"] == [{"cell": cell, "from": 1.0, "to": 0.5}]
    reverted = json.loads(json.dumps(second["commercial"]))
    reverted["reserve_products"][0]["sustain_h"] = 1.0
    for pack in (v2, v3):
        plan = MP.plan_apply(pack, reverted, _pin(v2, second))
        assert plan["changes"] == []
        assert plan["conflicts"] == [
            {
                "cell": cell,
                "yours": 1.0,
                "pack": pack.products[0].sustain_h,
                "last_applied": 0.5,
            }
        ]
        assert plan["commercial"] == reverted
        # The pin keeps what the pack last wrote, so the revert stays a user edit.
        assert plan["applied"][cell] == 0.5


def test_a_hand_entered_product_of_the_same_id_is_never_overwritten():
    have = _commercial(reserve_products=[_prod(sustain_h=2.0, capacity_price=PRICE)])
    plan = MP.plan_apply(_pack(), have, None)
    assert plan["changes"] == []
    assert [c["cell"] for c in plan["conflicts"]] == [
        "reserve_products/fast_symmetric/sustain_h"
    ]
    assert plan["commercial"]["reserve_products"][0]["sustain_h"] == 2.0


def test_a_product_the_user_removed_is_not_re_added():
    first = MP.plan_apply(_pack(), _commercial(), None)
    pin = {"pack_id": _pack().id, "pack_hash": "old", "applied": first["applied"]}
    plan = MP.plan_apply(_pack(), _commercial(), pin)
    assert plan["changes"] == []
    assert plan["conflicts"][0]["cell"] == "reserve_products/fast_symmetric/id"
    assert plan["commercial"].get("reserve_products", []) == []


def test_another_packs_pin_is_not_read_as_this_ones():
    first = MP.plan_apply(_pack(), _commercial(), None)
    pin = {"pack_id": "no-2-2026", "pack_hash": "x", "applied": first["applied"]}
    plan = MP.plan_apply(_pack(), _commercial(), pin)
    assert len(plan["changes"]) == len(_prod())


def test_no_commercial_config_is_refused():
    for none in (None, {}):
        with pytest.raises(MP.PackRefusal) as e:
            MP.plan_apply(_pack(), none, None)
        assert e.value.code == MP.NO_COMMERCIAL


def test_the_pin_round_trips_and_an_unreadable_one_is_refused(tmp_path):
    assert MP.read_pin(tmp_path) is None and MP.read_pin(None) is None
    MP.write_pin(tmp_path, _pack(), {"a": 1})
    pin = MP.read_pin(tmp_path)
    assert (pin["pack_id"], pin["pack_hash"], pin["applied"]) == (
        _pack().id,
        _pack().pack_hash,
        {"a": 1},
    )
    for bad in ("{not json", json.dumps({"applied": []}), json.dumps(["x"])):
        (tmp_path / MP.PIN_FILE).write_text(bad)
        with pytest.raises(MP.PackRefusal) as e:
            MP.read_pin(tmp_path)
        assert e.value.code == MP.PIN_UNREADABLE


# ── condition 4: no price is refused at solve and settlement ──────────────


def test_a_product_with_no_price_is_refused_at_solve_never_0():
    from services.solver import reserve_products as RP
    from tests.test_reserve_products import STRING, _site

    n = _site()
    p = M.ReserveProduct.model_validate(_prod())
    with pytest.raises(RP.ReserveRefusal) as e:
        RP.plan(n, [p], {STRING: 20.0}, frozenset({STRING}))
    assert e.value.kind == RP.PRICE_MISSING
    assert "no capacity price" in str(e.value)


def test_a_product_with_no_price_settles_to_none_flagged():
    from types import SimpleNamespace

    import pandas as pd

    from services.results.billing import reserve_lines
    from services.solver import reserve_products as RP
    from tests.test_reserve_products import STRING, _site

    n = _site()
    p = M.ReserveProduct.model_validate(_prod())
    cfg = SimpleNamespace(commercial=SimpleNamespace(reserve_products=[p]))
    n.storage_units_t[RP.OUT_FRAME] = pd.DataFrame(
        {f"{STRING}{RP.SEP}{p.id}": 1.0}, index=n.snapshots
    )
    lines, _ = reserve_lines(n, cfg, backup=({STRING: 20.0}, frozenset({STRING})))
    assert [ln.amount for ln in lines] == [None]
    assert lines[0].flags  # named, never 0


def test_binding_skips_a_product_with_no_price():
    from tests.test_reserve_binding import _bind, _net

    cfg = M.CommercialConfig(
        poc_link="import", reserve_products=[M.ReserveProduct.model_validate(_prod())]
    )
    stored = _bind(_net(), cfg)
    assert stored["reserve_products"][0]["capacity_price"] is None


# ── condition 3: apply is an ordinary edit ─────────────────────────────────


def _hand(commercial: dict) -> dict:
    return {**commercial, "reserve_products": [_prod()]}


def test_apply_equals_hand_entry_in_the_stored_config_the_fingerprint_and_the_ic_digest():
    from types import SimpleNamespace

    from services.solver_service import SolverConfig
    from services.workflow_tools import _network_input_fingerprint
    from tests.test_reserve_binding import _bind, _ic_digest, _net

    base = _commercial()
    applied = MP.plan_apply(_pack(), base, None)["commercial"]
    hand = _hand(base)
    a = _bind(_net(), M.CommercialConfig.model_validate(applied))
    h = _bind(_net(), M.CommercialConfig.model_validate(hand))
    assert json.dumps(a, sort_keys=True) == json.dumps(h, sort_keys=True)
    assert _ic_digest(a) == _ic_digest(h)
    n = _net()

    def fp(stored):
        return _network_input_fingerprint(
            SimpleNamespace(
                network=n,
                solver_state={"solver_config": SolverConfig(commercial=stored)},
                user_ts={},
                storage_dir=None,
            )
        )

    assert fp(a) == fp(h)
    # ... and it moves them as hand entry does.
    assert _ic_digest(a) != _ic_digest(
        _bind(_net(), M.CommercialConfig.model_validate(base))
    )
    assert fp(a) != fp(_bind(_net(), M.CommercialConfig.model_validate(base)))


def _route(monkeypatch, tmp_path, commercial, currency="EUR", zone="NO-1", saved=True):
    from routers import simulation as SIM

    calls = []
    monkeypatch.setattr(
        SIM,
        "_pack_context",
        lambda: (tmp_path if saved else None, commercial, currency),
    )
    monkeypatch.setattr(MP, "project_zone", lambda _d: zone)
    monkeypatch.setitem(
        SIM._state, "solver_config", types.SimpleNamespace(commercial=commercial)
    )

    def put(cfg, db, user):
        # The PUT's lock-entry guard and on-commit hook, as the real PUT runs them.
        guard, commit = SIM._PUT_HOOKS.get()
        guard(SIM._state["solver_config"])
        calls.append(cfg)
        commit()

    monkeypatch.setattr(SIM, "update_solver_config", put)
    return SIM, calls


def test_the_apply_route_goes_through_the_put_and_pins(monkeypatch, tmp_path):
    from models.schemas import SolverConfigSchema

    pack = MP.get("no-1-2026")
    SIM, calls = _route(monkeypatch, tmp_path, _commercial())
    out = SIM.apply_market_pack(
        pack.id, SIM.MarketPackApplyIn(pack_hash=pack.pack_hash), db=None, user=None
    )
    assert out["changes"] and "commercial" not in out
    assert out["changed"] == len(out["changes"])
    hand = {
        **_commercial(),
        "reserve_products": [p.model_dump(mode="json") for p in pack.products],
    }
    assert calls == [SolverConfigSchema.model_validate({"commercial": hand})]
    pin = MP.read_pin(tmp_path)
    assert (pin["pack_id"], pin["pack_hash"]) == (pack.id, pack.pack_hash)


def test_an_apply_with_nothing_to_change_writes_no_config(monkeypatch, tmp_path):
    pack = MP.get("no-1-2026")
    have = {
        **_commercial(),
        "reserve_products": [p.model_dump(mode="json") for p in pack.products],
    }
    SIM, calls = _route(monkeypatch, tmp_path, have)
    out = SIM.apply_market_pack(
        pack.id, SIM.MarketPackApplyIn(pack_hash=pack.pack_hash), db=None, user=None
    )
    assert out["changes"] == [] and calls == [] and out["changed"] == 0
    assert MP.read_pin(tmp_path)["pack_id"] == pack.id


def test_an_apply_never_writes_a_conflict_and_counts_only_its_changes(
    monkeypatch, tmp_path
):
    """GK B4 2b: a cell the user edited after the last apply is kept and listed;
    `changed` counts only the cells the apply wrote."""
    pack = MP.get("no-1-2026")
    first = MP.plan_apply(pack, _commercial(), None)
    mine = json.loads(json.dumps(first["commercial"]))
    mine["reserve_products"][0]["sustain_h"] = 9.0
    older = {**_pin(pack, first), "pack_hash": "an-older-hash"}
    older["applied"] = {**older["applied"]}
    # The pack's last apply wrote `counterparty` and the user left it as written.
    other = "counterparty"
    cell = f"reserve_products/{pack.products[0].id}/{other}"
    old_value = "an-older-value"
    older["applied"][cell] = old_value
    mine["reserve_products"][0][other] = old_value
    (tmp_path / MP.PIN_FILE).write_text(json.dumps(older))
    SIM, calls = _route(monkeypatch, tmp_path, mine)
    out = SIM.apply_market_pack(
        pack.id, SIM.MarketPackApplyIn(pack_hash=pack.pack_hash), db=None, user=None
    )
    kept = f"reserve_products/{pack.products[0].id}/sustain_h"
    assert [c["cell"] for c in out["conflicts"]] == [kept]
    assert [c["cell"] for c in out["changes"]] == [cell]
    assert out["changed"] == 1
    (written,) = calls
    prod = written.commercial.reserve_products[0]
    assert prod.sustain_h == 9.0
    assert getattr(prod, other) == getattr(pack.products[0], other)


@pytest.mark.parametrize(
    "case, code, status",
    [
        ("stale", MP.STALE, 409),
        ("unsaved", MP.UNSAVED, 409),
        ("zone", MP.ZONE_MISMATCH, 409),
        ("currency", MP.CURRENCY_MISMATCH, 409),
        ("unknown", MP.UNKNOWN, 404),
    ],
)
def test_the_apply_route_refusals_write_nothing(
    monkeypatch, tmp_path, case, code, status
):
    pack = MP.get("no-1-2026")
    SIM, calls = _route(
        monkeypatch,
        tmp_path,
        _commercial(),
        currency="NOK" if case == "currency" else "EUR",
        zone="NO-2" if case == "zone" else "NO-1",
        saved=case != "unsaved",
    )
    with pytest.raises(HTTPException) as e:
        SIM.apply_market_pack(
            "nope" if case == "unknown" else pack.id,
            SIM.MarketPackApplyIn(
                pack_hash="0" * 64 if case == "stale" else pack.pack_hash
            ),
            db=None,
            user=None,
        )
    assert (e.value.status_code, e.value.detail["error_kind"]) == (status, code)
    assert calls == [] and MP.read_pin(tmp_path) is None


def test_the_list_says_fit_pin_and_update_available(monkeypatch, tmp_path):
    SIM, _ = _route(monkeypatch, tmp_path, _commercial())
    MP.write_pin(tmp_path, MP.get("no-1-2026"), {})
    pin = json.loads((tmp_path / MP.PIN_FILE).read_text())
    pin["pack_hash"] = "an-older-hash"
    (tmp_path / MP.PIN_FILE).write_text(json.dumps(pin))
    out = SIM.list_market_packs()
    by = {p["region"]: p for p in out["packs"]}
    assert by["NO-1"]["refusal"] is None
    assert by["NO-2"]["refusal"]["code"] == MP.ZONE_MISMATCH
    assert by["NO-1"]["pinned"] and by["NO-1"]["update_available"]
    assert not by["NO-2"]["pinned"] and not by["NO-2"]["update_available"]
    prev = SIM.preview_market_pack("no-1-2026")
    assert prev["changes"] and "commercial" not in prev and "applied" not in prev


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{not json",
        "null",
        "[]",
        '"no-1-2026"',
        json.dumps({"pack_id": 1, "pack_hash": "h", "applied": {}}),
        json.dumps({"pack_id": "no-1-2026", "pack_hash": None, "applied": {}}),
        json.dumps({"pack_id": "no-1-2026", "pack_hash": "h", "applied": []}),
        json.dumps({"pack_id": "no-1-2026", "pack_hash": "h"}),
    ],
)
def test_an_unreadable_pin_shows_no_update_and_refuses_apply(
    monkeypatch, tmp_path, raw
):
    """GK B4 1b: an unreadable pin is never read as no pin: the list names the
    error and offers no update; preview and apply are refused and write nothing."""
    pack = MP.get("no-1-2026")
    SIM, calls = _route(monkeypatch, tmp_path, _commercial())
    (tmp_path / MP.PIN_FILE).write_text(raw)
    out = SIM.list_market_packs()
    assert out["pin_error"]["code"] == MP.PIN_UNREADABLE
    assert not any(p["pinned"] or p["update_available"] for p in out["packs"])
    with pytest.raises(HTTPException) as e:
        SIM.preview_market_pack(pack.id)
    assert (e.value.status_code, e.value.detail["error_kind"]) == (
        409,
        MP.PIN_UNREADABLE,
    )
    with pytest.raises(HTTPException) as e:
        SIM.apply_market_pack(
            pack.id, SIM.MarketPackApplyIn(pack_hash=pack.pack_hash), db=None, user=None
        )
    assert e.value.detail["error_kind"] == MP.PIN_UNREADABLE
    assert calls == [] and (tmp_path / MP.PIN_FILE).read_text() == raw


def test_every_refusal_code_has_a_literal_emitter_and_a_fragment_entry():
    from routers import simulation as SIM

    codes = {
        v
        for k, v in vars(MP).items()
        if k.isupper() and isinstance(v, str) and v.startswith("market_pack_")
    }
    assert set(SIM._MARKET_PACK_REFUSALS) == codes
    frag = (
        pathlib.Path(__file__).resolve().parents[2]
        / "tool-error-kinds.d"
        / "claude-scu06-k8a-market-packs.json"
    )
    assert set(json.loads(frag.read_text())["kinds"]) == codes


# ── the real app: PUT, lock, revision, the pin's carry (GK B4 1a, 2a, 2c) ──


def _live(client, install_network, project_storage_dir, monkeypatch, name="packpin"):
    """A saved project in NO-1 with an EUR currency and a commercial config whose
    connection is the network's `import` Link."""
    from services import market_prices
    from services.commercial import binding
    from tests.conftest import build_network

    n = build_network()
    n.add("Bus", "grid")
    n.add("Link", "import", bus0="grid", bus1="B1", p_nom=50.0)
    install_network(n, name=name)
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    pdir = project_storage_dir(name)
    market_prices.store_zone(pdir, "NO-1")
    monkeypatch.setattr(binding, "project_currency", lambda _fin: "EUR")
    r = client.put("/api/simulation/solver_config", json={"commercial": _commercial()})
    assert r.status_code == 200, r.text
    return name, pdir


def _apply(client, pack):
    return client.post(
        f"/api/simulation/commercial/market_packs/{pack.id}/apply",
        json={"pack_hash": pack.pack_hash},
    )


def test_the_apply_goes_through_the_real_put_and_bumps_the_revision(
    client, install_network, project_storage_dir, session_ctx, monkeypatch
):
    """GK B4 2c: the apply is an ordinary edit: the stored config is the hand-entry
    one and the edit revision moves once, as a PUT's does."""
    from services import dirty_state

    _name, pdir = _live(client, install_network, project_storage_dir, monkeypatch)
    pack = MP.get("no-1-2026")
    prev = client.get(f"/api/simulation/commercial/market_packs/{pack.id}/preview")
    assert prev.status_code == 200 and prev.json()["changes"], prev.text
    before = dirty_state.revision(session_ctx(client))
    r = _apply(client, pack)
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == len(prev.json()["changes"])
    assert dirty_state.revision(session_ctx(client)) == before + 1
    stored = client.get("/api/simulation/solver_config").json()["commercial"]
    assert stored["reserve_products"] == [
        p.model_dump(mode="json") for p in pack.products
    ]
    assert MP.read_pin(pdir)["pack_hash"] == pack.pack_hash
    # Applied again: nothing to change, no config write, no revision bump.
    r = _apply(client, pack)
    assert (r.status_code, r.json()["changed"]) == (200, 0)
    assert dirty_state.revision(session_ctx(client)) == before + 1


def test_a_change_at_the_puts_lock_entry_is_refused_stale(
    client, install_network, project_storage_dir, monkeypatch
):
    """GK B4 2a: the stale check runs INSIDE the PUT's solver-state lock. An edit
    that lands after the PUT's pre-lock steps and before it takes the lock is
    refused 409 and nothing is written; a check moved above the lock would miss it
    and apply over the edit."""
    import dataclasses

    from routers import simulation as SIM
    from services.pypsa_service import PyPSAService

    _name, pdir = _live(client, install_network, project_storage_dir, monkeypatch)
    pack = MP.get("no-1-2026")
    edited = {**_commercial(), "reserve_products": [_prod("mine", capacity_price=3.0)]}
    armed = []
    gate, get_lock = SIM._gate_user_code, PyPSAService.get_solver_state_lock

    def gate_then_arm(submitted, db, user):
        gate(submitted, db, user)
        armed.append(True)  # the PUT's last step before its lock

    class EditAtEntry:
        def __init__(self, lock):
            self.lock = lock

        def __enter__(self):
            self.lock.__enter__()
            if armed and armed.pop():
                SIM._state["solver_config"] = dataclasses.replace(
                    SIM._state["solver_config"], commercial=edited
                )
            return self

        def __exit__(self, *exc):
            return self.lock.__exit__(*exc)

    monkeypatch.setattr(SIM, "_gate_user_code", gate_then_arm)
    monkeypatch.setattr(
        PyPSAService,
        "get_solver_state_lock",
        classmethod(lambda cls: EditAtEntry(get_lock())),
    )
    r = _apply(client, pack)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_kind"] == MP.STALE
    stored = client.get("/api/simulation/solver_config").json()["commercial"]
    assert stored["reserve_products"] == edited["reserve_products"]
    assert MP.read_pin(pdir) is None


def test_the_pin_survives_save_save_as_save_a_copy_and_export_import_save(
    client, install_network, project_storage_dir, monkeypatch
):
    """GK B4 1a: `market_pack.json` is carried by every save path (project_contents
    `_ALL`), byte-equal, so an update is still told apart from a user edit."""
    name, pdir = _live(client, install_network, project_storage_dir, monkeypatch)
    assert _apply(client, MP.get("no-1-2026")).status_code == 200
    pin = (pdir / MP.PIN_FILE).read_bytes()
    assert client.post(f"/api/projects/{name}").status_code == 200
    assert (pdir / MP.PIN_FILE).read_bytes() == pin
    blob = client.get(f"/api/projects/{name}/bundle").content
    r = client.post(
        "/api/projects/import_bundle?name=packpin_imported",
        files={"file": ("a.pypsaproj.zip", blob, "application/zip")},
    )
    assert r.status_code in (200, 201), r.text
    for dst, rebind in (("packpin_copy", False), ("packpin_as", True)):
        r = client.post(f"/api/projects/{dst}", params={"rebind": rebind})
        assert r.status_code == 200, r.text
        assert (project_storage_dir(dst) / MP.PIN_FILE).read_bytes() == pin, dst
    dst = "packpin_imported"
    pid = next(p["id"] for p in client.get("/api/projects/").json() if p["name"] == dst)
    assert client.post(f"/api/projects/{pid}/activate").status_code == 200
    assert client.post(f"/api/projects/{dst}").status_code == 200
    assert (project_storage_dir(dst) / MP.PIN_FILE).read_bytes() == pin
