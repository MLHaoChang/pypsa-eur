"""
`binding.bind_commercial_on_context` (IC U1 follow-up, GS Q15).

The router's `_bind_commercial` bound the ACTIVE context only. The helper takes
any `ProjectContext`, so a study-owned fork (GS U2 `compile.bind_on_fork`) binds
its own network, off the foreground, with its Library series resolved in the
fork's own org: `ctx.org_id`, which a fork inherits from its base project
(`project_registry.create_scenario`), and which is `local_mode.LOCAL_ORG_ID` in local mode.
"""
from __future__ import annotations

import threading
import uuid

import numpy as np
import pytest

from models.commercial import CommercialConfig
from services.commercial import binding
from services.commercial import lp_bindings as L
from services.project_context import ProjectContext
from tests.fixtures.investment_case.edge_15min import build_edge_15min


def _with_export(n):
    n.add("Link", "export", bus0="poc", bus1="grid", p_nom=80.0, carrier="AC")
    return n


def _series_ref(client, n, name="fork_px"):
    idx = n.snapshots.tz_localize("UTC")
    r = client.post("/api/library/series", json={
        "name": name, "timestamps": [t.isoformat() for t in idx],
        "values": [float(i % 96) for i in range(len(idx))], "meta": {"source": "t"}})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _cfg(ref=None, **kw):
    body = {"poc_link": "import", "export_link": "export", "timezone": "UTC", **kw}
    if ref is not None:
        body["export_price_ref"] = ref
    return CommercialConfig.model_validate(body)


def test_a_fork_context_binds_its_own_network_in_its_own_org(client, install_network,
                                                             session_ctx, seeded_identity):
    active = _with_export(build_edge_15min())
    install_network(active)
    ref = _series_ref(client, active)
    fork = ProjectContext(network=_with_export(build_edge_15min()),
                          org_id=str(seeded_identity["org_id"]))
    out = binding.bind_commercial_on_context(fork, _cfg(ref))
    assert out["poc_link"] == "import" and out["export_price_ref"]["id"] == ref["id"]
    got = fork.network.links_t[L.EXPORT_PRICE_ATTR]["export"].to_numpy()
    assert np.allclose(got, [float(i % 96) for i in range(len(got))])
    live = session_ctx(client).network
    assert L.EXPORT_PRICE_ATTR not in live.links_t or \
        "export" not in live.links_t[L.EXPORT_PRICE_ATTR].columns


def test_the_resolver_uses_the_contexts_org_not_the_users(monkeypatch):
    from services.library import series_store

    seen = []

    def fake_resolve(db, org, ref):
        seen.append(org)
        raise series_store.LibraryRefNotFound("not here")

    monkeypatch.setattr(series_store, "resolve", fake_resolve)
    org = uuid.uuid4()
    ctx = ProjectContext(network=_with_export(build_edge_15min()), org_id=str(org))
    ref = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.bind_commercial_on_context(ctx, _cfg(ref), user=object())
    assert exc.value.code == "library_ref_stale" and exc.value.status == 409
    assert seen == [org]


def test_an_unsaved_context_with_no_user_is_library_org_unknown():
    ctx = ProjectContext(network=_with_export(build_edge_15min()))
    ref = {"id": "px", "version": 1, "hash": "a" * 64, "source": "t"}
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.bind_commercial_on_context(ctx, _cfg(ref))
    assert (exc.value.status, exc.value.code) == (409, "library_org_unknown")


def test_a_config_without_refs_binds_without_any_org():
    ctx = ProjectContext(network=_with_export(build_edge_15min()))
    out = binding.bind_commercial_on_context(ctx, _cfg())
    assert out["export_link"] == "export" and out["timezone"] == "UTC"


def test_a_context_with_a_live_solve_refuses():
    ctx = ProjectContext(network=_with_export(build_edge_15min()))
    gate = threading.Event()
    t = threading.Thread(target=gate.wait, daemon=True)
    t.start()
    ctx.solver_state["thread"] = t
    try:
        with pytest.raises(binding.BindingRefusal) as exc:
            binding.bind_commercial_on_context(ctx, _cfg())
        assert (exc.value.status, exc.value.code) == (409, "solver_in_flight")
    finally:
        gate.set()
        t.join()


def test_an_injected_in_flight_check_is_used():
    ctx = ProjectContext(network=_with_export(build_edge_15min()))
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.bind_commercial_on_context(ctx, _cfg(), in_flight=lambda c: c is ctx)
    assert exc.value.code == "solver_in_flight"


def test_clearing_on_a_context_returns_none():
    ctx = ProjectContext(network=_with_export(build_edge_15min()))
    assert binding.bind_commercial_on_context(ctx, None) is None
