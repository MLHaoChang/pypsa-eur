"""The reserve rows' typed refusals where they are caught (DC grid readiness 06,
GK A R8, GK B L10): `POST /api/simulation/run` answers each `RP.KINDS` kind as
`{code, error_kind, message}` before it starts a worker, and a refusal that still
reaches the worker is a `reserve_refused` failure card, not a traceback.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from routers import simulation as SIM  # noqa: E402
from services.failure_taxonomy import classify_failure  # noqa: E402
from services.solver import reserve_products as RP  # noqa: E402
from tests.test_reserve_products import STRING, _bind, _cfg, _product, _site  # noqa: E402

FRAGMENT = (pathlib.Path(__file__).resolve().parents[2] / "tool-error-kinds.d"
            / "claude-dc06-flex-revenue.json")


def test_every_kind_has_a_literal_emitter_and_a_fragment_entry():
    assert set(SIM._RESERVE_REFUSALS) == set(RP.KINDS)
    assert set(json.loads(FRAGMENT.read_text())["kinds"]) == set(RP.KINDS)


def _refusal(n, products, floors, monkeypatch, bind=True):
    if bind:
        _bind(n, products, {p.id: 1.0 for p in products})
    monkeypatch.setattr(RP, "project_backup", lambda _n: (floors, frozenset({STRING})))
    return SIM._reserve_refusal(n, _cfg(products))


def test_no_products_is_no_refusal(monkeypatch):
    assert _refusal(_site(), [], {}, monkeypatch) is None


def test_a_buildable_config_is_no_refusal(monkeypatch):
    assert _refusal(_site(), [_product()], {STRING: 20.0}, monkeypatch) is None


@pytest.mark.parametrize("case, kind", [
    ("no_floor", RP.NOT_ELIGIBLE),
    ("floor_above", RP.FLOOR_EXCEEDS),
    ("unbound", RP.PRICE_MISSING),
    ("stale", RP.PRICE_STALE),
    ("initial_soc", RP.INITIAL_SOC),
    ("hold_edited", RP.STRING_AVAIL),
])
def test_each_refusal_is_typed(case, kind, monkeypatch):
    n = _site()
    p = _product()
    floors = {STRING: 20.0}
    products = [p]
    bind = True
    if case == "no_floor":
        floors = {}
    elif case == "floor_above":
        floors = {STRING: 25.5}
    elif case == "unbound":
        bind = False
    elif case == "stale":
        _bind(n, [p], {"fcr": 1.0})
        bind = False
        products = [p.model_copy(update={"capacity_price": p.capacity_price.model_copy(update={"version": 2})})]
    elif case == "initial_soc":
        n.storage_units.at[STRING, "cyclic_state_of_charge"] = False
        n.storage_units.at[STRING, "state_of_charge_initial"] = 5.0
    elif case == "hold_edited":
        n.storage_units_t.p_max_pu.loc[n.snapshots[1], STRING] = 0.5
    out = _refusal(n, products, floors, monkeypatch, bind=bind)
    assert out is not None
    assert out["error_kind"] == out["code"] == kind
    assert out["message"]


def test_the_run_route_refuses_before_starting_a_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(SIM, "_reserve_refusal", lambda n, cfg: (
        calls.append(1) or {"error_kind": RP.NOT_ELIGIBLE, "code": RP.NOT_ELIGIBLE, "message": "m"}))
    monkeypatch.setattr(SIM._study_state, "blocking_study_detail", lambda: None)
    started = []
    monkeypatch.setattr(SIM.threading, "Thread", lambda *a, **k: started.append(1))
    with pytest.raises(HTTPException) as exc:
        SIM.run()
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == RP.NOT_ELIGIBLE
    assert calls == [1] and started == []


def test_a_refusal_in_the_worker_is_a_typed_failure_card():
    card = classify_failure("error", f"reserve_refused: {RP.PRICE_STALE}: reserve product 'fcr': …")
    assert card["category"] == "reserve_refused"
    assert RP.PRICE_STALE in card["detail"]
