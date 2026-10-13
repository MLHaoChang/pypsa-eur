"""
P18 backend — choosing per-Load DtC attribution without writing a
dtc_config (the panel derives it from tags), and readiness predicting a
per_load refusal (both deferred from P16).
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from models.energy_hub import default_weak_flexible_pack
from tests.test_energy_hub_frontier_fmea import _feeder_hub


def _row(ready, stage):
    return next(r for r in ready["stages"] if r["stage"] == stage)


def test_the_derived_config_takes_the_requested_attribution():
    from services.adequacy import eh_study as S
    n = _feeder_hub()
    cfg = S.derive_dtc_config(n, default_weak_flexible_pack(),
                              attribution="per_load")
    assert cfg.attribution == "per_load"
    assert S.derive_dtc_config(n, default_weak_flexible_pack()).attribution \
        == "bus_aggregate_not_per_load"


def test_readiness_reports_per_load_critical_loads():
    from services.adequacy.eh_readiness import eh_readiness
    ready = eh_readiness(_feeder_hub(), default_weak_flexible_pack(),
                         budget_solves=30, voll=3000.0,
                         dtc_attribution="per_load")
    assert ready["dtc"]["attribution"] == "per_load"
    assert ready["dtc"]["critical_loads"] == ["hub_load"]
    assert _row(ready, "dtc_stress")["prediction"] == "run"


def test_readiness_predicts_a_per_load_refusal():
    """A critical bus with no Load: bus aggregate still 'resolves' the bus,
    per_load has no critical Load and the stress refuses — predict it."""
    from services.adequacy.eh_readiness import eh_readiness
    n = _feeder_hub()
    n.buses.loc[:, "eh_critical"] = False
    n.buses.at["f0", "eh_critical"] = True           # a feeder bus, no Load
    agg = eh_readiness(n, default_weak_flexible_pack(), budget_solves=30,
                       voll=3000.0)
    assert _row(agg, "dtc_stress")["prediction"] == "run"
    per = eh_readiness(n, default_weak_flexible_pack(), budget_solves=30,
                       voll=3000.0, dtc_attribution="per_load")
    row = _row(per, "dtc_stress")
    assert row["prediction"] == "not_established"
    assert "no critical Loads" in row["reason"]
    assert per["dtc"]["critical_loads"] == []


def test_readiness_http_validates_the_attribution(client, install_network):
    install_network(_feeder_hub())
    ok = client.get("/api/results/eh_readiness", params={
        "archetype": "weak_flexible", "dtc_attribution": "per_load"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["dtc"]["attribution"] == "per_load"
    bad = client.get("/api/results/eh_readiness", params={
        "archetype": "weak_flexible", "dtc_attribution": "auto"})
    assert bad.status_code == 422


@pytest.mark.parametrize("body,needle", [
    ({"dtc_attribution": "auto"}, "dtc_attribution"),
    ({"dtc_attribution": "per_load",
      "dtc_config": {"critical_bus_ids": ["hub"],
                     "islanding_contingencies": ["import"],
                     "attribution": "bus_aggregate_not_per_load"}},
     "conflicts"),
])
def test_the_request_refuses_bad_or_conflicting_attribution(
        client, install_network, body, needle):
    install_network(_feeder_hub())
    r = client.post("/api/results/eh_study",
                    json={"archetype": "weak_flexible", **body})
    assert r.status_code == 422, r.text
    assert needle in r.text


def test_the_request_resolves_the_attribution():
    from services.adequacy import eh_study_runner as R
    dtc, attr = R.resolve_dtc_attribution(
        {"critical_bus_ids": ["hub"], "islanding_contingencies": ["import"]},
        "per_load")
    assert dtc["attribution"] == "per_load" and attr == "per_load"
    dtc, attr = R.resolve_dtc_attribution(None, "per_load")
    assert dtc is None and attr == "per_load"
    # an explicit config's own attribution is what the record reports
    _dtc, attr = R.resolve_dtc_attribution(
        {"critical_bus_ids": ["hub"], "islanding_contingencies": ["import"],
         "attribution": "per_load"}, None)
    assert attr == "per_load"
    with pytest.raises(HTTPException):
        R.resolve_dtc_attribution(
            {"critical_bus_ids": ["hub"], "islanding_contingencies": ["import"],
             "attribution": "bus_aggregate_not_per_load"}, "per_load")


def test_the_post_hands_dtc_attribution_to_the_driver(
        client, install_network, monkeypatch):
    """P18 gate (BINDING): the panel's choice must reach run_eh_study — not
    just validate — and the record must say what runs."""
    import threading

    seen: dict = {}
    reached = threading.Event()

    def fake_run(network, pack, cfg, **kw):
        seen.update(kw)
        reached.set()
        raise RuntimeError("stop here")

    monkeypatch.setattr("services.adequacy.eh_study.run_eh_study", fake_run)
    install_network(_feeder_hub())
    r = client.post("/api/results/eh_study", json={
        "archetype": "weak_flexible", "dtc_attribution": "per_load"})
    assert r.status_code in (200, 202), r.text
    assert reached.wait(10)
    assert seen["dtc_attribution"] == "per_load"
    assert seen["dtc_config"] is None
    body = client.get("/api/results/eh_study").json()
    assert body.get("dtc_attribution") == "per_load"


@pytest.mark.live_solve
def test_the_driver_derives_a_per_load_config():
    """No dtc_config: the stage derives one from the tags WITH the chosen
    attribution (a regression would silently run bus aggregate)."""
    import queue
    import threading

    from services.adequacy import eh_study as S
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig

    n = _feeder_hub()
    PyPSAService.set_network(n)
    report = S.run_eh_study(
        n, default_weak_flexible_pack(), SolverConfig(voll=3000.0),
        lock=PyPSAService.get_lock(), stop_event=threading.Event(),
        log_queue=queue.SimpleQueue(),
        stages=["apply_pack", "ens_solve", "dtc_stress"],
        dtc_attribution="per_load")
    stages = {r.stage: r.status for r in report.pipeline.stages}
    assert stages["dtc_stress"] == "run", stages
    assert report.sections["dtc"].payload["attribution"] == "per_load"


def test_chat_declares_dtc_attribution():
    import inspect

    from services import chat_tools as T
    from services.chat_tools_schema import TOOLS
    props = next(t for t in TOOLS if t["name"] == "run_eh_study")[
        "input_schema"]["properties"]
    assert props["dtc_attribution"]["enum"] == [
        "bus_aggregate_not_per_load", "per_load"]
    assert "dtc_attribution" in inspect.signature(T.run_eh_study).parameters
