"""
Non-finite floats in a study request are refused before the study exists.

Whole-branch review (2026-09-08), finding S6. The five study request models
typed their floats as plain `float`, which accepts the JSON `Infinity` and
`NaN` literals — the same hole 12f closed on the asset schemas and 12g on
every finite-default LP input. A frontier POST with `[Infinity, 1.0]` was
admitted, the record PUBLISHED and the worker RUN; the POST's own response
then failed to encode (`ValueError: Out of range float values are not JSON
compliant`, a 500 on the wire), and every later `GET /results/frontier`
answered 500 until a swap cleared the record. The coupling loop's own
`target > 0` check passes `inf`; negative and zero frontier targets were
not refused either.

What ships: `Finite` (12g's type, `allow_inf_nan=False`) on
`targets_permyriad`, `cov_target`, both `target_lole_h` and `eps0`; the
12f `RequestValidationError` handler renders the refusal as a 422 (it
already rewrites the echoed non-finite input); and the frontier refuses a
non-positive target before publishing.

Bites (verified red against the named removal):
* `_Finite` back to `float` → the mc and coupling cases of the parametrised
  422 test answer 500 (the frontier and margin cases are held by their
  routes' own finite checks either way);
* the positive-target check → `[-1, 0, 1]` is admitted.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from services.project_context import record_is_running
from tests.conftest import build_network
from tests.test_study_mesh_claim import _drain, _slow_sweep, _voll

CASES = [
    ("frontier", "/api/results/frontier", {"targets_permyriad": [float("inf"), 1.0]}),
    ("frontier", "/api/results/frontier", {"targets_permyriad": [float("nan")]}),
    ("mc", "/api/results/mc", {"cov_target": float("nan"), "draws": 5}),
    ("mc", "/api/results/mc", {"cov_target": float("inf"), "draws": 5}),
    ("coupling_loop", "/api/results/coupling_loop", {"target_lole_h": float("inf")}),
    ("coupling_loop", "/api/results/coupling_loop", {"target_lole_h": 1.0, "eps0": float("nan")}),
    ("margin_loop", "/api/results/margin_loop", {"target_lole_h": float("nan")}),
    ("margin_loop", "/api/results/margin_loop", {"target_lole_h": float("-inf")}),
]


def _post_raw(client, path, body):
    """`json.dumps` writes the bare `NaN`/`Infinity` literals starlette's
    parser accepts — the exact wire shape the review sent."""
    return client.post(path, content=json.dumps(body),
                       headers={"content-type": "application/json"})


@pytest.mark.parametrize("key,path,body", CASES,
                         ids=[f"{k}:{list(b)[-1]}" for k, _, b in CASES])
def test_non_finite_study_floats_are_refused_before_publish(
        client, install_network, session_state, monkeypatch, key, path, body):
    """★ S6. 422, a JSON body that parses, and NO study record published.

    Bite (verified): `_Finite` back to `float` — the four mc and coupling
    cases answer 500 (their routes' own checks pass `inf`/`NaN`); the
    frontier and margin cases are held by the routes' own finite checks
    (the frontier's is this fix's second gate), so the type and the route
    each cover what the other cannot.
    """
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    import services.adequacy.sweep as SW
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)
    try:
        r = _post_raw(client, path, body)
        assert r.status_code == 422, (r.status_code, r.text[:300])
        detail = r.json()["detail"]      # the body must itself be encodable
        assert detail
        assert not record_is_running(st.get(key)), "study published anyway"
        assert st.get(key) is None or st.get(key).get("status") != "running"
    finally:
        _drain(st)


@pytest.mark.parametrize("targets", [[-1.0, 1.0], [0.0], [1.0, -0.5, 2.0]])
def test_frontier_refuses_a_non_positive_target(client, install_network,
                                                 session_state, targets):
    """★ A zero or negative ENS cap is not a point on the frontier; refused
    before the record is published.

    Bite (verified): drop the positive-target check — admitted, and the
    worker spends a solve proving it infeasible.
    """
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    try:
        r = client.post("/api/results/frontier", json={"targets_permyriad": targets})
        assert r.status_code == 422, r.text
        assert "positive" in r.json()["detail"]
        assert not record_is_running(st.get("frontier"))
    finally:
        _drain(st)


def test_finite_study_floats_still_pass_validation(client, install_network,
                                                    session_state, monkeypatch):
    """The types are a refusal of non-finite only: a finite body reaches the
    route's own checks (here the sweep, which we stub, runs)."""
    install_network(build_network())
    st = session_state(client)
    _voll(st)
    import services.adequacy.sweep as SW
    monkeypatch.setattr(SW, "run_class_b_sweep", _slow_sweep)
    try:
        r = client.post("/api/results/fmea_sweep", json={"scenarios": []})
        assert r.status_code == 200, r.text
    finally:
        _drain(st)
    # And a finite frontier body is not refused by the type (the route may
    # still refuse it for its own reasons, but never as "not finite").
    r = client.post("/api/results/frontier", json={"targets_permyriad": [1.0, 2.0]})
    assert r.status_code != 422 or "finite" not in r.text.lower(), r.text
    _drain(session_state(client))
