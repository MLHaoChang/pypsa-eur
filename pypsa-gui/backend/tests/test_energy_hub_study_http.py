"""
P1.5 follow-up — HTTP surface for EH study: GET/POST /results/eh_study (+ abort).

Mirrors frontier/margin_loop: thin router + ``eh_study_runner.start_eh_study``,
mesh registration under ``STUDY_KEYS``, abort via ``_abort_study``.

Driven over the authenticated TestClient. ``run_eh_study`` is stubbed so the
suite stays fast; the sync driver already has live-solve coverage.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pandas as pd
import pypsa
import pytest

from models.energy_hub import (
    AvailabilityTarget,
    ReferenceDesignReport,
    default_strong_grid_pack,
)

STUDY_URL = "/api/results/eh_study"
ABORT_URL = "/api/results/eh_study/abort"
REPORT_URL = "/api/results/eh_reference_design"


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=120.0,
          marginal_cost=10.0)
    return n


def _setup(client, install_network, network=None, **cfg):
    install_network(network if network is not None else _network())
    body = {"solver_name": "highs", "voll": 150.0}
    body.update(cfg)
    r = client.put("/api/simulation/solver_config", json=body)
    assert r.status_code == 200, r.text
    return r.json()


@contextmanager
def _fake_running(state: dict, key: str):
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True, name=f"fake-{key}")
    t.start()
    state[key] = {
        "status": "running", "error": None, "started_at": time.time(),
        "thread": t, "stop_event": threading.Event(),
    }
    try:
        yield
    finally:
        release.set()
        t.join(timeout=5)
        state.pop(key, None)


def _stub_report(*, archetype: str = "strong_grid") -> ReferenceDesignReport:
    from models.energy_hub import EHStudyPipeline
    from services.adequacy.eh_report import assemble_reference_design_report
    pack = default_strong_grid_pack().model_copy(update={
        "availability": AvailabilityTarget(ens_cap_permyriad=1000.0),
    })
    return assemble_reference_design_report(
        archetype=archetype,
        pack_hash=pack.archetype + "-stub",
        assumptions_hash="deadbeef",
        section_payloads={},
        pipeline=EHStudyPipeline(solves_consumed=0, aborted=False),
    )


def _install_stub(monkeypatch, *, delay: float = 0.0, hold=None):
    """Replace ``run_eh_study``; optional hold Event blocks until cleared."""
    import services.adequacy.eh_study as eh_mod

    def fake(network, pack, cfg, **kw):
        stop = kw.get("stop_event")
        store = kw.get("store")
        if hold is not None:
            hold.wait(timeout=30.0)
        if delay:
            time.sleep(delay)
        if stop is not None and stop.is_set():
            report = _stub_report(archetype=pack.archetype)
            report = report.model_copy(update={
                "pipeline": report.pipeline.model_copy(update={"aborted": True}),
            })
        else:
            report = _stub_report(archetype=pack.archetype)
        if store is not None:
            store["eh_reference_design_report"] = report.model_dump(mode="json")
        return report

    monkeypatch.setattr(eh_mod, "run_eh_study", fake)
    # Runner imports the symbol at call time from the module — also patch
    # where the runner will bind it if already imported.
    try:
        import services.adequacy.eh_study_runner as runner
        monkeypatch.setattr(runner, "run_eh_study", fake, raising=False)
    except ImportError:
        pass
    return fake


def _poll(client, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    body: dict | None = None
    while time.time() < deadline:
        r = client.get(STUDY_URL)
        assert r.status_code == 200, r.text
        body = r.json()
        if body.get("status") not in ("running",):
            return body
        time.sleep(0.02)
    raise AssertionError(f"eh_study never finished: {body!r}")


def test_get_eh_study_is_204_before_any_run(client, install_network):
    _setup(client, install_network)
    r = client.get(STUDY_URL)
    assert r.status_code == 204, r.text
    assert r.content == b""


def test_post_refuses_missing_archetype(client, install_network):
    _setup(client, install_network)
    r = client.post(STUDY_URL, json={})
    assert r.status_code == 422, r.text
    assert "archetype" in r.json()["detail"]
    assert client.get(STUDY_URL).status_code == 204


def test_post_refuses_unknown_archetype(client, install_network):
    _setup(client, install_network)
    r = client.post(STUDY_URL, json={"archetype": "mega_hub"})
    assert r.status_code == 422, r.text
    assert "archetype" in r.json()["detail"].lower()


def test_post_starts_study_and_persists_report(
        client, install_network, monkeypatch):
    _install_stub(monkeypatch)
    _setup(client, install_network)

    r = client.post(STUDY_URL, json={
        "archetype": "strong_grid",
        "stages": ["apply_pack", "ens_solve", "assemble"],
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"
    assert r.json()["study"] == "eh_study"
    assert r.json()["archetype"] == "strong_grid"

    body = _poll(client)
    assert body["status"] == "done", body
    assert body["error"] is None, body
    assert body.get("report") is not None
    assert body["report"]["archetype"] == "strong_grid"

    rr = client.get(REPORT_URL)
    assert rr.status_code == 200, rr.text
    assert rr.json()["archetype"] == "strong_grid"


def test_abort_is_idempotent_and_404s_only_with_no_record(
        client, install_network, session_state, monkeypatch):
    _setup(client, install_network)
    r = client.post(ABORT_URL)
    assert r.status_code == 404, r.text
    assert "eh" in r.json()["detail"].lower() or "study" in r.json()["detail"].lower()

    hold = threading.Event()
    _install_stub(monkeypatch, hold=hold)
    r = client.post(STUDY_URL, json={"archetype": "strong_grid"})
    assert r.status_code == 200, r.text

    r = client.post(ABORT_URL)
    assert r.status_code == 200, r.text
    assert r.json()["aborting"] is True
    hold.set()
    body = _poll(client)
    assert body["status"] in ("aborted", "done"), body

    r2 = client.post(ABORT_URL)
    assert r2.status_code == 200
    assert r2.json()["aborting"] is False


def test_eh_study_is_registered_in_the_shared_mesh():
    from services import study_state

    assert "eh_study" in study_state.STUDY_KEYS
    assert "energy hub" in study_state.STUDY_LABELS["eh_study"].lower() \
        or "eh" in study_state.STUDY_LABELS["eh_study"].lower()


def test_409_mesh_covers_eh_study_both_directions(
        client, install_network, session_state, monkeypatch):
    _install_stub(monkeypatch)
    _setup(client, install_network)
    st = session_state(client)
    body = {"archetype": "strong_grid"}

    with _fake_running(st, "eh_study"):
        assert client.post(STUDY_URL, json=body).status_code == 409
        assert client.post("/api/results/frontier", json={}).status_code == 409
        rr = client.post("/api/simulation/run")
        assert rr.status_code == 409, rr.text

    with _fake_running(st, "frontier"):
        r = client.post(STUDY_URL, json=body)
        assert r.status_code == 409, r.text


def test_get_stays_serialisable_while_running(
        client, install_network, session_state):
    _setup(client, install_network)
    st = session_state(client)
    with _fake_running(st, "eh_study"):
        r = client.get(STUDY_URL)
        assert r.status_code == 200, r.text
        payload = r.json()
        assert "thread" not in payload
        assert "stop_event" not in payload
        assert payload["status"] == "running"


def test_handlers_and_request_model_on_router_surface():
    import routers.results as R
    assert hasattr(R, "post_eh_study")
    assert hasattr(R, "get_eh_study")
    assert hasattr(R, "post_eh_study_abort")
    assert hasattr(R, "EhStudyRequest")


def test_runner_exports_start_and_never_imports_routers():
    import ast
    import importlib
    import pathlib

    runner = importlib.import_module("services.adequacy.eh_study_runner")
    assert callable(runner.start_eh_study)

    path = (pathlib.Path(__file__).resolve().parent.parent
            / "services" / "adequacy" / "eh_study_runner.py")
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("routers"), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("routers"), alias.name
