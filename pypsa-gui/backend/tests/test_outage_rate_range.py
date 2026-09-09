"""
An outage rate is a probability-like unavailability: finite and in [0, 1).

Whole-branch review (2026-09-08), finding S1. The five create/update schemas
accepted any float for `outage_rate_value`, the bulk route accepted any
finite one, the occurrence validator only WARNED, and `/results/copt` runs on
demand with no gate — so a typed `1.5` (or `−0.1`) was convolved into the
capacity table and the whole fleet's LOLE went NEGATIVE, silently, while the
MC hit an `AssertionError` and the reserve margin credited the unit at 0.
The four surfaces disagreed on exactly the input 12h had made them agree on.

One correction to the review's own statement: through the NETWORK a NaN rate
is "unset" (the resolver's `_is_set`), so the NaN poisoning it measured is
reachable only by building `CoptUnit`s directly. That path is closed too.

What ships, boundary by boundary:
* schema — `OutageRate = Annotated[float, Field(ge=0, lt=1, allow_inf_nan=False)]`
  on all five models; `null` is still "unset → carrier default";
* `PATCH /_bulk` — the same refusal, 422;
* engines — `fleet_and_residual` refuses, NAMED, before any unit is built
  (`OutageRateError`), so `/copt`, `/mc` and both loops answer 422 on a
  network that carries such a rate (a bundle or netCDF the schemas never
  saw); `build_copt` refuses a bad `q` for direct callers;
* margin — a rate outside [0, 1) is `unpriceable`, the same refusal as no
  rate at all, so preflight ERRORS on it when a margin is set.

Bites (each verified red against the named removal):
* schema range → `test_schema_refuses_*` accept 1.5;
* `_bulk` check → `test_bulk_refuses_*` answers 200;
* the walk's refusal → `test_copt_route_refuses_*` answers 200 with a
  negative LOLE;
* the margin's range test → `test_margin_treats_*` prices the unit.
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd
import pydantic
import pypsa
import pytest

from models import schemas as S
from services.adequacy import copt as C
from services.adequacy.occurrence import OutageRateError, rate_is_usable

MODELS = [S.GeneratorCreate, S.StorageUnitCreate, S.StoreCreate,
          S.LinkCreate, S.LineCreate]
BAD = [-0.1, 1.0, 1.5, float("nan"), float("inf"), float("-inf")]


def _required(model):
    """The minimum keyword set each create model needs besides the rate."""
    return {
        S.GeneratorCreate: dict(name="g", bus="b"),
        S.StorageUnitCreate: dict(name="s", bus="b"),
        S.StoreCreate: dict(name="e", bus="b"),
        S.LinkCreate: dict(name="l", bus0="a", bus1="b"),
        S.LineCreate: dict(name="ln", bus0="a", bus1="b"),
    }[model]


def _network(rate: float | None = 0.1, *, with_bad: bool = True) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Generator", "good", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, outage_rate_value=0.1, outage_rate_basis="EFORd",
          mttr_hours=24.0)
    if with_bad:
        # Written straight to the frame — the bundle / netCDF path the
        # schemas never see.
        n.add("Generator", "bad", bus="b", carrier="gas", p_nom=100.0,
              marginal_cost=12.0, outage_rate_value=rate,
              outage_rate_basis="EFORd", mttr_hours=24.0)
    n.add("Load", "l", bus="b", p_set=150.0)
    return n


# ── the predicate ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("v,ok", [(0.0, True), (0.5, True), (0.999, True),
                                  (1.0, False), (1.5, False), (-0.1, False),
                                  (float("nan"), False), (float("inf"), False),
                                  ("0.2", True), ("x", False), (None, False)])
def test_rate_is_usable(v, ok):
    assert rate_is_usable(v) is ok


# ── schema ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
@pytest.mark.parametrize("bad", BAD, ids=lambda v: repr(v))
def test_schema_refuses_a_rate_outside_the_unit_interval(model, bad):
    """★ Every create/update model refuses −0.1, 1.0, 1.5, NaN and ±inf.

    Bite (verified): `outage_rate_value: float | None` — all accepted."""
    with pytest.raises(pydantic.ValidationError) as ei:
        model(**_required(model), outage_rate_value=bad)
    assert "outage_rate_value" in str(ei.value)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_schema_keeps_null_and_the_unit_interval(model):
    assert model(**_required(model), outage_rate_value=None).outage_rate_value is None
    assert model(**_required(model), outage_rate_value=0.0).outage_rate_value == 0.0
    assert model(**_required(model), outage_rate_value=0.99).outage_rate_value == 0.99
    assert model(**_required(model)).outage_rate_value is None


def test_create_route_refuses_and_names_the_field(client, install_network):
    install_network(_network(with_bad=False))
    r = client.post("/api/network/generators",
                    json={"name": "x", "bus": "b", "carrier": "gas",
                          "p_nom": 10.0, "outage_rate_value": 1.5})
    assert r.status_code == 422, r.text
    assert "outage_rate_value" in r.text
    r = client.post("/api/network/generators",
                    json={"name": "x", "bus": "b", "carrier": "gas",
                          "p_nom": 10.0, "outage_rate_value": 0.2})
    assert r.status_code in (200, 201), r.text


# ── bulk ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [-0.1, 1.0, 1.5, "nan", "inf"],
                         ids=lambda v: repr(v))
def test_bulk_refuses_a_rate_outside_the_unit_interval(client, install_network,
                                                        bad):
    """★ `PATCH /_bulk` numeric path. Strings "nan"/"inf" pass `float()`
    and the finite-default rule does not know this custom column.

    Bite (verified): drop the `outage_rate_value` clause — 200, written."""
    n = _network(with_bad=False)
    install_network(n)
    r = client.patch("/api/network/_bulk",
                     json={"component_class": "Generator", "names": ["good"],
                           "updates": {"outage_rate_value": bad}})
    assert r.status_code == 422, r.text
    assert "[0, 1)" in r.text
    assert float(n.generators.at["good", "outage_rate_value"]) == pytest.approx(0.1)


def test_bulk_keeps_null_and_the_unit_interval(client, install_network):
    n = _network(with_bad=False)
    install_network(n)
    r = client.patch("/api/network/_bulk",
                     json={"component_class": "Generator", "names": ["good"],
                           "updates": {"outage_rate_value": 0.3}})
    assert r.status_code == 200, r.text
    assert float(n.generators.at["good", "outage_rate_value"]) == pytest.approx(0.3)
    r = client.patch("/api/network/_bulk",
                     json={"component_class": "Generator", "names": ["good"],
                           "updates": {"outage_rate_value": None}})
    assert r.status_code == 200, r.text
    assert math.isnan(float(n.generators.at["good", "outage_rate_value"]))


# ── engines ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rate", [1.5, -0.1, 1.0])
def test_the_walk_refuses_and_names_the_unit(rate):
    """★ `fleet_and_residual` raises `OutageRateError` naming the unit and
    its value, before any unit is built — the one walk `/copt`, `/mc` and
    both loops share."""
    with pytest.raises(OutageRateError) as ei:
        C.fleet_and_residual(_network(rate))
    msg = str(ei.value)
    assert "bad" in msg and "[0, 1)" in msg and f"{rate:g}" in msg
    assert "good" not in msg.split(":")[-1]


def test_a_valid_fleet_is_untouched():
    units, residual, _ = C.fleet_and_residual(_network(with_bad=False))
    assert [u.name for u in units] == ["good"]
    assert units[0].q == pytest.approx(0.1)


def test_build_copt_refuses_a_bad_q_directly():
    """The direct-construction path the review's NaN measurement used."""
    good = C.CoptUnit("good", 100.0, 0.1, "EFORd", 24.0, "asset")
    for q in (float("nan"), -0.1, 1.5, 1.0):
        with pytest.raises(OutageRateError):
            C.build_copt([good, C.CoptUnit("bad", 100.0, q, "FOR", 24.0, "asset")])
    d = C.build_copt([good])
    assert d.probs.sum() == pytest.approx(1.0)


def test_copt_route_refuses_and_names_the_unit(client, install_network):
    """★ `/results/copt` answers 422 naming the unit — where before it
    answered 200 with a NEGATIVE LOLE.

    Bite (verified): drop the refusal in the walk — 200, `lole_h < 0`."""
    install_network(_network(1.5))
    r = client.get("/api/results/copt")
    assert r.status_code == 422, r.text
    assert "bad" in r.json()["detail"] and "[0, 1)" in r.json()["detail"]
    # …and a valid fleet still answers.
    install_network(_network(with_bad=False))
    r = client.get("/api/results/copt")
    assert r.status_code == 200, r.text
    assert r.json()["metrics"]["lole_hours"] >= 0.0


def test_mc_and_loop_routes_refuse_the_same_way(client, install_network,
                                                 session_state):
    install_network(_network(1.5))
    st = session_state(client)
    st["solver_config"] = dataclasses.replace(st["solver_config"], voll=1000.0)
    for path, body in (("/api/results/mc", {"draws": 10}),
                       ("/api/results/coupling_loop", {"target_lole_h": 1.0}),
                       ("/api/results/margin_loop", {"target_lole_h": 1.0})):
        r = client.post(path, json=body)
        assert r.status_code == 422, (path, r.text)
        assert "bad" in r.json()["detail"], (path, r.text)


# ── margin ────────────────────────────────────────────────────────────────

def test_margin_treats_the_rate_as_unpriceable(client, install_network,
                                                session_state):
    """★ With a reserve margin set, preflight ERRORS on the unit as
    unpriceable — the same refusal as no rate — instead of crediting it at
    `(1 − 1.5) × p_nom`, a negative firm contribution.

    Bite (verified): `if math.isnan(q):` alone — the unit is priced."""
    install_network(_network(1.5))
    st = session_state(client)
    st["solver_config"] = dataclasses.replace(st["solver_config"],
                                              reserve_margin=0.2)
    out = client.post("/api/simulation/preflight").json()
    codes = {i["code"]: i for i in out["issues"]}
    assert "reserve_margin_unpriceable_assets" in codes, list(codes)
    assert "bad" in codes["reserve_margin_unpriceable_assets"]["message"]
    assert codes["reserve_margin_unpriceable_assets"]["severity"] == "error"
    # The plain range warning still names it too.
    assert "outage_params_implausible" in codes
