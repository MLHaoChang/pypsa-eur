"""
P13 — pack parameters over HTTP / chat (plan 2026-09-25 P13).

Overrides merge onto the factory pack and RE-VALIDATE; every refusal is a 422
before any worker starts (no record is published — the #51 guard discipline).
"""
from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from models.energy_hub import default_strong_grid_pack, default_weak_flexible_pack
from services.adequacy import archetypes as A
from services.adequacy import eh_study_runner as RUN

STUDY_URL = "/api/results/eh_study"


def _setup(client, install_network, n, **cfg):
    install_network(n)
    body = {"solver_name": "highs", "voll": 5000.0}
    body.update(cfg)
    r = client.put("/api/simulation/solver_config", json=body)
    assert r.status_code == 200, r.text


def _poll(client, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(STUDY_URL).json()
        if body.get("status") != "running":
            return body
        time.sleep(0.1)
    raise AssertionError("eh_study never finished")


def _weak_net():
    from tests.test_energy_hub_mvp_b import _weak_mvp_b_network
    return _weak_mvp_b_network()


# ── merge + re-validate ─────────────────────────────────────────────────────


def test_overrides_merge_onto_the_factory_pack_and_change_its_hash():
    base = default_weak_flexible_pack()
    pack = RUN.apply_pack_overrides(base, {
        "ens_cap_permyriad": 5000.0, "target_lole_h": 1.5,
        "import_p_nom_mw": 80.0, "levers": {"storage_duration": False},
        "frontier_default": True,
    })
    assert pack.availability.ens_cap_permyriad == 5000.0
    assert pack.availability.target_lole_h == 1.5
    assert pack.availability.certification_metric == "mc_lole"   # kept
    assert pack.import_overlay.import_p_nom_mw == 80.0
    assert pack.levers.storage_duration is False
    assert pack.levers.import_cap is True                        # kept
    assert pack.frontier_default is True
    assert A.pack_hash(pack) != A.pack_hash(base)


def test_no_overrides_is_the_factory_pack():
    base = default_strong_grid_pack()
    assert RUN.apply_pack_overrides(base, None) == base
    assert RUN.apply_pack_overrides(base, {}) == base


@pytest.mark.parametrize("overrides,path", [
    ({"ens_cap_permyriad": -1.0}, "pack_overrides.ens_cap_permyriad"),
    ({"target_lole_h": -2.0}, "pack_overrides.target_lole_h"),
    ({"certification_metric": "vibes"}, "pack_overrides.certification_metric"),
    ({"import_p_nom_mw": -5.0}, "pack_overrides.import_p_nom_mw"),
    ({"levers": {"redundancy": "yes please"}}, "pack_overrides.levers.redundancy"),
    ({"not_a_knob": 1}, "pack_overrides.not_a_knob"),
])
def test_invalid_overrides_are_422_with_the_field_path(overrides, path):
    with pytest.raises(HTTPException) as exc:
        RUN.apply_pack_overrides(default_weak_flexible_pack(), overrides,
                                 raise_http=True)
    assert exc.value.status_code == 422
    assert path in str(exc.value.detail)


# ── HTTP: refusals publish nothing ──────────────────────────────────────────


@pytest.mark.parametrize("extra,needle", [
    ({"pack_overrides": {"ens_cap_permyriad": 0}}, "ens_cap_permyriad"),
    ({"dtc_config": {"critical_bus_ids": ["nope"],
                     "islanding_contingencies": ["import_poc"]}}, "nope"),
    ({"dtc_config": {"critical_bus_ids": ["crit"],
                     "islanding_contingencies": ["no_link"]}}, "no_link"),
    ({"dtc_config": {"critical_bus_ids": ["crit"],
                     "islanding_contingencies": []}}, "dtc_config"),
    ({"dsr_buses": ["ghost"]}, "ghost"),
    ({"mc": {"draws": 0}}, "mc.draws"),
    ({"mc": {"cov_target": 2.0}}, "mc.cov_target"),
])
def test_http_refusals_are_422_and_publish_no_record(
        client, install_network, extra, needle):
    _setup(client, install_network, _weak_net())
    r = client.post(STUDY_URL, json={"archetype": "weak_flexible", **extra})
    assert r.status_code == 422, r.text
    assert needle in r.text
    assert client.get(STUDY_URL).status_code == 204


def test_dsr_buses_on_a_pack_without_dsr_opt_in_is_refused(
        client, install_network):
    from tests.test_energy_hub_study import _ens_bind_network
    _setup(client, install_network, _ens_bind_network())
    r = client.post(STUDY_URL, json={"archetype": "strong_grid",
                                     "dsr_buses": ["b"]})
    assert r.status_code == 422, r.text
    assert "dsr_opt_in" in r.text
    assert client.get(STUDY_URL).status_code == 204


# ── HTTP live: overrides reach the study ────────────────────────────────────


@pytest.mark.live_solve
def test_weak_override_makes_the_mvp_b_fixture_feasible(client, install_network):
    _setup(client, install_network, _weak_net(), voll=500.0)
    r = client.post(STUDY_URL, json={"archetype": "weak_flexible"})
    assert r.status_code == 200, r.text
    default = _poll(client)
    assert default["status"] == "failed"                 # 10 ‱ infeasible
    r = client.post(STUDY_URL, json={
        "archetype": "weak_flexible",
        "stages": ["apply_pack", "ens_solve", "assemble"],
        "pack_overrides": {"ens_cap_permyriad": 5000.0},
    })
    assert r.status_code == 200, r.text
    body = _poll(client)
    assert body["status"] == "done", body.get("error")
    rep = body["report"]
    assert rep["completeness"]["target"] == "ok"
    assert rep["ens_cap_permyriad"] == 5000.0
    assert rep["pack_hash"] != default["report"]["pack_hash"]
    assert body["pack_overrides"] == {"ens_cap_permyriad": 5000.0}


@pytest.mark.live_solve
def test_mc_options_and_dtc_config_reach_their_stages(client, install_network):
    from tests.test_energy_hub_mc_certify import _cert_network
    _setup(client, install_network, _cert_network())
    r = client.post(STUDY_URL, json={
        "archetype": "weak_flexible",
        "stages": ["apply_pack", "ens_solve", "mc_certify", "dtc_stress",
                   "assemble"],
        "pack_overrides": {"ens_cap_permyriad": 5000.0},
        "mc": {"draws": 300, "seed": 7},
        "dtc_config": {"critical_bus_ids": ["hub"],
                       "islanding_contingencies": ["import"]},
    })
    assert r.status_code == 200, r.text
    rep = _poll(client)["report"]
    cert = rep["sections"]["certification"]["payload"]
    assert cert["seed"] == 7 and cert["draws"] == 300
    dtc = rep["sections"]["dtc"]["payload"]
    assert [c["contingency"] for c in dtc["contingencies"]] == ["import"]


# ── chat ────────────────────────────────────────────────────────────────────


def test_chat_schema_declares_the_nested_objects():
    from services.chat_tools_schema import TOOLS
    props = next(t for t in TOOLS if t["name"] == "run_eh_study")[
        "input_schema"]["properties"]
    po = props["pack_overrides"]
    assert po["type"] == "object" and po.get("additionalProperties") is False
    assert {"ens_cap_permyriad", "target_lole_h", "certification_metric",
            "import_p_nom_mw", "levers"} <= set(po["properties"])
    assert po["properties"]["certification_metric"]["enum"] == ["mc_lole", "none"]
    assert set(props["dtc_config"]["properties"]) == {
        "critical_bus_ids", "critical_load_ids", "islanding_contingencies"}
    assert props["dsr_buses"]["items"] == {"type": "string"}
    assert set(props["mc"]["properties"]) == {"draws", "seed", "cov_target"}


def test_chat_invalid_override_is_422_and_idle(install_network):
    from services import chat_tools as T
    install_network(_weak_net())
    with pytest.raises(HTTPException) as exc:
        T.run_eh_study(archetype="weak_flexible",
                       pack_overrides={"ens_cap_permyriad": -1})
    assert exc.value.status_code == 422
    assert "pack_overrides.ens_cap_permyriad" in str(exc.value.detail)


# ── P13 gate: overrides must never be accepted and then ignored or broken ───


def test_certification_metric_none_conflicting_with_a_lole_target_is_refused():
    with pytest.raises(HTTPException) as exc:
        RUN.apply_pack_overrides(default_weak_flexible_pack(),
                                 {"certification_metric": "none"},
                                 raise_http=True)
    assert exc.value.status_code == 422
    assert "certification_metric" in str(exc.value.detail)


def test_certification_metric_none_on_a_pack_without_a_lole_target_is_fine():
    pack = RUN.apply_pack_overrides(default_strong_grid_pack(),
                                    {"certification_metric": "none"},
                                    raise_http=True)
    assert pack.availability.certification_metric == "none"


@pytest.mark.parametrize("archetype", ["strong_grid", "off_grid"])
def test_import_cap_on_a_pack_that_does_not_apply_it_is_refused(archetype):
    from models.energy_hub import default_off_grid_pack
    factory = {"strong_grid": default_strong_grid_pack,
               "off_grid": default_off_grid_pack}[archetype]
    with pytest.raises(HTTPException) as exc:
        RUN.apply_pack_overrides(factory(), {"import_p_nom_mw": 80.0},
                                 raise_http=True)
    assert exc.value.status_code == 422
    assert "import_p_nom_mw" in str(exc.value.detail)


def test_zero_import_cap_is_refused_before_it_can_break_the_run():
    with pytest.raises(HTTPException) as exc:
        RUN.apply_pack_overrides(default_weak_flexible_pack(),
                                 {"import_p_nom_mw": 0.0}, raise_http=True)
    assert "pack_overrides.import_p_nom_mw" in str(exc.value.detail)


def test_import_cap_lever_on_off_grid_is_refused_as_a_known_no_op():
    from models.energy_hub import default_off_grid_pack
    with pytest.raises(HTTPException) as exc:
        RUN.apply_pack_overrides(default_off_grid_pack(),
                                 {"levers": {"import_cap": True}},
                                 raise_http=True)
    assert "import_cap" in str(exc.value.detail)


@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_non_finite_numbers_are_refused(value):
    with pytest.raises(HTTPException):
        RUN.apply_pack_overrides(default_weak_flexible_pack(),
                                 {"ens_cap_permyriad": value}, raise_http=True)


@pytest.mark.parametrize("body,needle", [
    ({"archetype": "off_grid", "pack_overrides": {"import_p_nom_mw": 80.0}},
     "import_p_nom_mw"),
    ({"archetype": "weak_flexible",
      "pack_overrides": {"certification_metric": "none"}},
     "certification_metric"),
    ({"archetype": "weak_flexible",
      "dtc_config": {"critical_bus_ids": ["crit"],
                     "islanding_contingencies": ["import_poc"],
                     "critical_load_idz": ["x"]}}, "critical_load_idz"),
])
def test_http_gate_refusals_publish_no_record(client, install_network, body,
                                              needle):
    _setup(client, install_network, _weak_net())
    r = client.post(STUDY_URL, json=body)
    assert r.status_code == 422, r.text
    assert needle in r.text
    assert client.get(STUDY_URL).status_code == 204


def test_chat_refusal_leaves_campaign_spend_unchanged(install_network):
    from services import chat_tools as T
    from services.adequacy import campaign as C
    install_network(_weak_net())
    C.start("certify the hub")
    before = C.status()["spent_solves"]
    with pytest.raises(HTTPException):
        T.run_eh_study(archetype="off_grid",
                       pack_overrides={"import_p_nom_mw": 80.0})
    assert C.status()["spent_solves"] == before


@pytest.mark.live_solve
def test_record_carries_normalised_overrides_and_dsr_buses_are_deduplicated(
        client, install_network, monkeypatch):
    import services.adequacy.eh_study as S
    seen = {}
    real = S.run_eh_study

    def spy(*a, **k):
        seen["dsr_buses"] = k.get("dsr_buses")
        return real(*a, **k)

    monkeypatch.setattr(S, "run_eh_study", spy)
    _setup(client, install_network, _weak_net(), voll=500.0)
    r = client.post(STUDY_URL, json={
        "archetype": "weak_flexible",
        "stages": ["apply_pack", "ens_solve", "assemble"],
        "pack_overrides": {"ens_cap_permyriad": "5000",
                           "target_lole_h": None},
        "dsr_buses": ["flex", "flex"],
    })
    assert r.status_code == 200, r.text
    body = _poll(client)
    assert body["pack_overrides"] == {"ens_cap_permyriad": 5000.0}
    assert seen["dsr_buses"] == ["flex"]


def test_schema_limits_are_the_engine_constants():
    from models.energy_hub import MAX_EH_BUDGET_SOLVES
    from services.adequacy import mc
    from services.chat_tools_schema import TOOLS
    props = next(t for t in TOOLS if t["name"] == "run_eh_study")[
        "input_schema"]["properties"]
    assert props["mc"]["properties"]["draws"]["maximum"] == mc.MAX_DRAWS
    assert props["budget_solves"]["maximum"] == MAX_EH_BUDGET_SOLVES
    assert props["pack_overrides"]["properties"]["import_p_nom_mw"] == {
        "type": "number", "exclusiveMinimum": 0}
