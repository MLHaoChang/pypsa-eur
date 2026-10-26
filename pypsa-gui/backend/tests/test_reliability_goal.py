"""The project's one reliability goal (W3b-R, `services/reliability_goal.py`).

Pins the gatekeeper's plan conditions: the goal fields the LP never reads stay
out of the solve fingerprint and the investment-case digest while the ENS
target stays in (C1); every study fills an omitted field from the stored goal
and names the source, an explicit value wins (C3, C4); the schema bounds and
the partial PUT (C5); readiness and the run resolve the same goal (C7).
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd
import pypsa
import pytest

from services import reliability_goal as G
from services.adequacy.eh_study_runner import EhStudyRequest

CONFIG_URL = "/api/simulation/solver_config"
TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "project_templates"
GOAL = {"target_lole_h": 2.5, "mc_draws": 300, "site_archetype": "off_grid"}


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "g", bus="b", p_nom=120.0, marginal_cost=10.0)
    return n


def _put(client, **body):
    r = client.put(CONFIG_URL, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _fingerprint(ctx):
    from services.workflow_tools import _network_input_fingerprint

    return (_network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True))


# ── C1: the goal stays out of the fingerprints, ENS stays in ────────────────

def test_saving_a_goal_leaves_the_solve_fingerprint_and_ens_moves_it(
        client, install_network, session_ctx):
    install_network(_network())
    _put(client, solver_name="highs", voll=150.0)
    ctx = session_ctx(client)
    before = _fingerprint(ctx)
    _put(client, **GOAL)
    assert _fingerprint(ctx) == before
    _put(client, ens_cap_permyriad=5.0)
    assert _fingerprint(ctx)[0] != before[0]


def test_the_investment_case_digest_ignores_the_goal_but_not_ens():
    from services.finance.investment_case_runner import assumptions_digest

    def digest(solver):
        return assumptions_digest(finance=None, commercial=None, dispatch=None,
                                  packs={}, solver=solver)

    base = {"voll": 150.0, "ens_cap_permyriad": None}
    plain, plain_parts = digest(base)
    assert digest({**base, **GOAL}) == (plain, plain_parts)
    moved, parts = digest({**base, "ens_cap_permyriad": 5.0})
    assert moved != plain and parts["solver_config"] != plain_parts["solver_config"]


def test_goal_free_config_drops_only_the_inert_fields():
    from dataclasses import asdict

    from services.solver_service import SolverConfig

    cfg = SolverConfig(ens_cap_permyriad=5.0, **GOAL)
    free = G.goal_free_config(cfg)
    assert free == {k: v for k, v in asdict(cfg).items() if k not in G.GOAL_FIELDS}
    assert free["ens_cap_permyriad"] == 5.0
    assert G.goal_free_config(asdict(cfg)) == free


# ── C5: schema bounds and the partial PUT ───────────────────────────────────

@pytest.mark.parametrize("body", [
    {"target_lole_h": -0.1}, {"target_lole_h": "inf"}, {"target_lole_h": "nan"},
    {"mc_draws": 0}, {"mc_draws": 2001}, {"mc_draws": 1.5}, {"mc_draws": "300"},
    {"mc_draws": True}, {"site_archetype": "mega_hub"},
])
def test_put_refuses_an_out_of_range_goal(client, install_network, body):
    install_network(_network())
    stored = client.get(CONFIG_URL).json()
    r = client.put(CONFIG_URL, json=body)
    assert r.status_code == 422, r.text
    assert client.get(CONFIG_URL).json() == stored


@pytest.mark.parametrize("body", [
    {"target_lole_h": 0}, {"mc_draws": 1}, {"mc_draws": 2000},
    {"site_archetype": "weak_flexible"}, {"target_lole_h": None},
])
def test_put_accepts_the_bounds(client, install_network, body):
    install_network(_network())
    assert {k: _put(client, **body)[k] for k in body} == body


def test_partial_put_keeps_the_other_side(client, install_network):
    install_network(_network())
    _put(client, voll=777.0)
    after_goal = _put(client, **GOAL)
    assert after_goal["voll"] == 777.0
    after_voll = _put(client, voll=888.0)
    assert {k: after_voll[k] for k in GOAL} == GOAL
    assert _put(client, mc_draws=None)["target_lole_h"] == 2.5


def test_a_loaded_config_drops_an_invalid_goal_value():
    from routers.projects import _solver_config_from_dict

    cfg = _solver_config_from_dict({"target_lole_h": -1, "mc_draws": 9999,
                                    "site_archetype": "x", "voll": 200.0})
    assert (cfg.target_lole_h, cfg.mc_draws, cfg.site_archetype) == (None, None, None)
    ok = _solver_config_from_dict(dict(GOAL))
    assert {k: getattr(ok, k) for k in GOAL} == GOAL


# ── The resolver order: stored → template → default ─────────────────────────

def test_effective_goal_order():
    template = {"recommended_archetype": "weak_flexible",
                "pack_overrides": {"target_lole_h": 1.0, "ens_cap_permyriad": 3.0}}
    assert G.effective_goal({}, None) == {
        "target_lole_h": {"value": None, "source": "default"},
        "ens_cap_permyriad": {"value": None, "source": "default"},
        "mc_draws": {"value": G.DEFAULT_MC_DRAWS, "source": "default"},
        "site_archetype": {"value": "strong_grid", "source": "default"},
    }
    templated = G.effective_goal({}, template)
    assert {k: v["source"] for k, v in templated.items()} == {
        "target_lole_h": "template", "ens_cap_permyriad": "template",
        "mc_draws": "default", "site_archetype": "template"}
    # The stored ENS target wins over the template's (the C2 conflict).
    stored = G.effective_goal({"ens_cap_permyriad": 7.0, **GOAL}, template)
    assert {k: (v["value"], v["source"]) for k, v in stored.items()} == {
        "target_lole_h": (2.5, "stored"), "ens_cap_permyriad": (7.0, "stored"),
        "mc_draws": (300, "stored"), "site_archetype": ("off_grid", "stored")}
    # A stored 0 ENS is "no target": the template's still applies.
    assert G.effective_goal({"ens_cap_permyriad": 0}, template)["ens_cap_permyriad"] \
        == {"value": 3.0, "source": "template"}


# ── C3/C4: each entry point fills an omitted field; an explicit one wins ────

def test_eh_request_fills_omitted_fields_and_names_overrides():
    goal = G.effective_goal({"ens_cap_permyriad": 7.0, **GOAL}, None)
    body, sources = G.eh_request_with_goal(EhStudyRequest(), goal)
    assert body.archetype == "off_grid"
    assert body.pack_overrides == {"target_lole_h": 2.5, "ens_cap_permyriad": 7.0,
                                   "certification_metric": "mc_lole"}
    assert body.mc == {"draws": 300}
    assert set(sources.values()) == {"stored"}

    explicit = EhStudyRequest(archetype="strong_grid", mc={"draws": 50},
                              pack_overrides={"target_lole_h": 9.0})
    body, sources = G.eh_request_with_goal(explicit, goal)
    assert (body.archetype, body.mc["draws"], body.pack_overrides["target_lole_h"]) \
        == ("strong_grid", 50, 9.0)
    assert body.pack_overrides["ens_cap_permyriad"] == 7.0
    assert sources == {"site_archetype": "override", "mc_draws": "override",
                       "target_lole_h": "override", "ens_cap_permyriad": "stored"}


def test_eh_request_leaves_the_pack_defaults_alone_without_a_goal():
    body, sources = G.eh_request_with_goal(EhStudyRequest(), G.effective_goal({}, None))
    assert body.archetype == "strong_grid"
    assert not body.pack_overrides and not body.mc
    assert set(sources.values()) == {"default"}


def test_loop_goal_puts_the_target_on_the_horizon_basis():
    class Body:
        target_lole_h = None
        draws = None

    goal = G.effective_goal(GOAL, None)
    assert G.loop_goal(Body(), goal, 3.0) == (7.5, 300, {
        "target_lole_h": "stored", "mc_draws": "stored"})
    assert G.loop_goal(Body(), goal, None)[0] == 2.5
    explicit = Body()
    explicit.target_lole_h, explicit.draws = 4.0, 20
    target, draws, sources = G.loop_goal(explicit, goal, 3.0)
    assert (target, draws) == (4.0, 20)
    assert sources["target_lole_h"] == sources["mc_draws"] == "override"
    assert G.loop_goal(Body(), G.effective_goal({}, None), 3.0)[:2] == (None, G.DEFAULT_MC_DRAWS)


def test_the_mc_study_fills_its_draws_from_the_goal(client, install_network):
    from tests.test_adequacy_mc_endpoint import COV, SEED
    from tests.test_adequacy_mc_endpoint import _network as mc_network
    from tests.test_adequacy_mc_endpoint import _poll as mc_poll

    install_network(mc_network())
    _put(client, mc_draws=16)
    r = client.post("/api/results/mc", json={"seed": SEED, "cov_target": COV})
    assert r.status_code == 200, r.text
    assert (r.json()["draws"], r.json()["goal_sources"]) == (16, {"mc_draws": "stored"})
    assert mc_poll(client)["status"] == "done"

    r = client.post("/api/results/mc", json={"draws": 8, "seed": SEED, "cov_target": COV})
    assert (r.json()["draws"], r.json()["goal_sources"]) == (8, {"mc_draws": "override"})
    mc_poll(client)


# ── C7: readiness and the run resolve the same goal ─────────────────────────

def _templates() -> dict[str, dict]:
    return {p.parent.name: json.loads(p.read_text())
            for p in sorted(TEMPLATES.glob("eh_*/eh_template.json"))}


@pytest.mark.parametrize("cfg", [{}, {"ens_cap_permyriad": 7.0, **GOAL},
                                 {"site_archetype": "weak_flexible"}],
                         ids=["no-goal", "stored-goal", "site-only"])
@pytest.mark.parametrize("name", sorted(_templates()))
def test_readiness_and_run_agree_field_by_field(name, cfg):
    from services.flow_service import site_card_study_body

    meta = _templates()[name]
    readiness = site_card_study_body(meta, cfg)
    if readiness is None:
        pytest.skip("the template's own goal is out of range")
    # The run sends no goal field (PR2's read-only panels); the server fills them.
    run, _ = G.eh_request_with_goal(EhStudyRequest(), G.effective_goal(cfg, meta))
    assert run.archetype == readiness["archetype"]
    for key in ("target_lole_h", "ens_cap_permyriad"):
        assert (run.pack_overrides or {}).get(key) == \
            (readiness.get("pack_overrides") or {}).get(key), key


def test_the_goal_route_resolves_stored_over_template(client, tmp_projects_dir):
    from tests.test_project_flow import _create

    name = _create(client, "eh_microgrid", "goal_route")
    meta = _templates()["eh_microgrid"]
    url = f"/api/projects/{name}/flow/reliability_goal"
    r = client.get(url)
    assert r.status_code == 200, r.text
    assert r.json() == G.effective_goal({}, meta)
    assert r.json()["site_archetype"]["source"] == "template"

    _put(client, site_archetype="weak_flexible", mc_draws=40)
    goal = client.get(url).json()
    assert goal["site_archetype"] == {"value": "weak_flexible", "source": "stored"}
    assert goal["mc_draws"] == {"value": 40, "source": "stored"}


def test_a_bundle_with_an_invalid_goal_imports_and_names_the_drop(
        client, api_project, project_storage_dir):
    import io
    import zipfile

    nc = (project_storage_dir(api_project("goal-src")) / "network.nc").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("network.nc", nc)
        zf.writestr("solver_config.json", json.dumps(
            {"target_lole_h": -1, "mc_draws": 300, "site_archetype": "mega_hub"}))
        zf.writestr("metadata.json", json.dumps({"name": "goal-import"}))
    r = client.post("/api/projects/import_bundle", params={"name": "goal-import"},
                    files={"file": ("g.zip", buf.getvalue(), "application/zip")})
    assert r.status_code in (200, 201), r.text[:300]
    assert r.json()["dropped_goal"] == [{"field": "target_lole_h", "value": -1},
                                        {"field": "site_archetype", "value": "mega_hub"}]
    live = client.get(CONFIG_URL).json()
    assert (live["target_lole_h"], live["mc_draws"], live["site_archetype"]) == (None, 300, None)
    # PUT refuses the same value.
    assert client.put(CONFIG_URL, json={"target_lole_h": -1}).status_code == 422
