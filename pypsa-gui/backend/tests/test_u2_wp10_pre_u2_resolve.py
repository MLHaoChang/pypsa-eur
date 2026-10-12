"""
U2 WP10, gate WP6 W4 (second half): an Expert re-solve of a network a
decision study wrote before U2 refuses; it never drops the demand charge
while keeping the tariff's prices in the Links.

Plan: docs/superpowers/plans/2026-10-05-guided-study-u2-engine-rewire.md,
WP10 "Also (gate WP6 W4, second half)".

Before U2 a study's base and option forks carried the tariff in two pieces:
the energy, network and export prices as PERMANENT ``links_t.marginal_cost``
on ``grid_import`` / ``grid_export``, and the demand charge as
``SolverConfig.demand_charge``, which GS's LP wrapper read. WP10 deletes that
field and the wrapper, and ``_solver_config_from_dict`` (the one loader of a
saved ``solver_config.json``: project load, bundle import, the queue's
snapshot) drops a key the dataclass no longer has. Re-solved as is, such a
network would keep its energy prices and lose its demand charge: a cheaper
tariff than the study's, with nothing said. The refusal reads the NETWORK
(a decision-study pack whose meter Links carry a price), not the config,
because any save of the project rewrites the config without the old key.

``tests/fixtures/pre_u2_fork/`` holds a base and a solved option fork written
the way the old code wrote them (``make_pre_u2_fork.py`` is the record).

Final U2 gate, condition U2: a network the study writes SINCE U2 carries the
engine mark (``n.meta[PACK_META_KEY]["tariff_on"] == "engine"``), so a price a
user types on its meter Link later is an ordinary Expert edit, never this
refusal; and the refusal's way out is a step any copy can take (clear the two
Links' prices, or start a new study), not "re-run the study", which never
rewrites a pre-U2 study's base.
"""
from __future__ import annotations

import json
import pathlib
import queue
import shutil
import threading

import pypsa
import pytest

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "pre_u2_fork"
CODE = "study_network_tariff_in_links"


def _fixture_nc(sub: str) -> pathlib.Path:
    """The stored netCDF, or a failure that says why it is missing (gate U1)."""
    path = FIXTURE / sub / "network.nc"
    if not path.is_file():
        pytest.fail(f"fixture {path} missing: it is committed with git add -f because *.nc "
                    "is gitignored, and it cannot be rebuilt (its recorder needs the code U2 "
                    "deleted); restore it from git (git checkout -- that path)", pytrace=False)
    return path


def _codes(n, cfg) -> set[str]:
    from services.validation_service import validate_for_run

    return {i.code for i in validate_for_run(n, cfg) if i.severity == "error"}


def _stored(tmp_path: pathlib.Path, sub: str):
    """The stored project as the Expert opens it: its netCDF and its config, via the loader."""
    from routers.projects import _solver_config_from_dict

    _fixture_nc(sub)
    d = tmp_path / sub
    shutil.copytree(FIXTURE / sub, d)
    n = pypsa.Network(str(d / "network.nc"))
    raw = json.loads((d / "solver_config.json").read_text(encoding="utf-8"))
    return n, _solver_config_from_dict(raw), raw


def _solve(cfg, n) -> tuple[tuple[str, str], list[str]]:
    from services.solver_service import run_simulation

    q: queue.SimpleQueue = queue.SimpleQueue()
    status = run_simulation(cfg, n, threading.RLock(), threading.Event(), q)
    lines = []
    while not q.empty():
        item = q.get()
        if item is not None:
            lines.append(str(item))
    return status, lines


def test_the_fixture_is_a_pre_u2_network():
    """The stored pieces are what W4 names: the demand charge in the config, prices in the Links."""
    for sub in ("base", "fork"):
        raw = json.loads((FIXTURE / sub / "solver_config.json").read_text(encoding="utf-8"))
        assert raw["demand_charge"]["price_per_mw_per_period"] > 0
        assert raw.get("commercial") is None
        n = pypsa.Network(str(_fixture_nc(sub)))
        mc = n.links_t.marginal_cost
        assert (mc["grid_import"] > 0).all() and (mc["grid_export"] < 0).all()
        assert "decision_study_pack" in n.meta
        # Written before the engine mark existed (gate U2).
        assert "tariff_on" not in n.meta["decision_study_pack"]


@pytest.mark.parametrize("sub", ["base", "fork"])
@pytest.mark.live_solve
def test_an_expert_resolve_of_a_stored_pre_u2_network_refuses(tmp_path, sub):
    n, cfg, _raw = _stored(tmp_path, sub)
    before = n.links_t.marginal_cost.copy()
    p_before = n.storage_units.get("p_nom_opt")
    (status, lines) = _solve(cfg, n)
    assert status == ("error", "validation_failed"), (status, lines[-5:])
    refusal = [ln for ln in lines if f"[{CODE}]" in ln]
    assert refusal, lines[-10:]
    # Plain words, with a way out any copy can take (gate U2).
    assert "new study" in refusal[0].lower()
    assert "re-run" not in refusal[0].lower()
    assert "demand charge" in refusal[0].lower()
    # Nothing was solved or rewritten.
    assert n.links_t.marginal_cost.equals(before)
    if p_before is not None:
        assert n.storage_units["p_nom_opt"].equals(p_before)


@pytest.mark.live_solve
@pytest.mark.parametrize("variant", ["as_stored", "config_resaved", "commercial_added"])
def test_the_demand_charge_is_never_dropped_while_the_link_prices_stay(tmp_path, variant):
    """
    Whatever the config now says — the file as stored, the file re-saved
    without the old key (any save does that once the field is gone), or an
    Expert who adds an engine commercial block on top (which would price the
    tariff a second time) — the network's Link prices make the solve refuse.
    """
    from routers.projects import _solver_config_from_dict

    n, cfg, raw = _stored(tmp_path, "base")
    if variant == "config_resaved":
        cfg = _solver_config_from_dict({k: v for k, v in raw.items() if k != "demand_charge"})
    elif variant == "commercial_added":
        import dataclasses

        from services.study import compile as C
        from services.study import library as L
        from services.study import questions as Q
        from tests.golden import site_fixture as sf
        from tests.u2_targets import FAKE_REF, flat_resolver

        defaults = L.load_defaults()
        ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults)
        compiled = C.commercial_from_ledger(sf.site_intake(), ledger, defaults, n.snapshots,
                                            export_series=FAKE_REF)
        compiled = C.bind_on_network(n, compiled, resolve_ref=flat_resolver(40.0, n.snapshots))
        cfg = dataclasses.replace(cfg, commercial=compiled.commercial())
    (status, lines) = _solve(cfg, n)
    assert status == ("error", "validation_failed"), (variant, status, lines[-5:])
    assert any(f"[{CODE}]" in ln for ln in lines), lines[-10:]


def test_the_preflight_names_the_links_and_the_way_out(tmp_path):
    from services.validation_service import validate_for_run

    n, cfg, _raw = _stored(tmp_path, "fork")
    issues = [i for i in validate_for_run(n, cfg) if i.code == CODE]
    assert len(issues) == 1
    [issue] = issues
    assert issue.severity == "error"
    assert "grid_import" in issue.message and "grid_export" in issue.message
    msg = issue.message.lower()
    # Gate U2: what is wrong, in plain words, and two ways out that work for
    # any copy (a Save-As, the study's base), honest about what each leaves.
    assert "earlier version of the decision study" in msg
    assert "demand charge" in msg
    # (a) clear the prices: they are hourly here, so the static price in
    # Properties would not replace them; then no tariff applies.
    assert "time series" in msg and "profile of zeros" in msg
    assert "no tariff" in msg
    # (b) a new study for the same site.
    assert "start a new study for the same site" in msg
    # Re-running never rewrites a pre-U2 study's base project: not offered.
    assert "re-run" not in msg and "run the study again" not in msg


def test_a_network_the_study_writes_now_is_not_refused():
    """Since U2 WP6 the pack writes no price: the engine prices the meter at solve."""
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from services.validation_service import validate_for_run
    from tests.golden import site_fixture as sf

    defaults = L.load_defaults()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults)
    n = packs.build_site_network(sf.site_intake(), ledger, "bess_2h", library=defaults)
    cfg = packs.option_solver_config(ledger, packs.option_commercial(
        sf.site_intake(), ledger, defaults, n.snapshots))
    assert CODE not in {i.code for i in validate_for_run(n, cfg)}


def test_a_network_without_the_study_pack_is_not_the_studys_business(tmp_path):
    """An ordinary network with priced Links is the user's tariff, not a study's: not refused."""
    from services.validation_service import validate_for_run

    n, cfg, _raw = _stored(tmp_path, "base")
    del n.meta["decision_study_pack"]
    assert CODE not in {i.code for i in validate_for_run(n, cfg)}


def test_the_detector_reads_the_packs_own_names():
    from services.study import packs, pre_u2

    assert pre_u2.PACK_META_KEY == packs.PACK_META_KEY
    assert pre_u2.METER_LINKS == (packs.IMPORT_LINK, packs.EXPORT_LINK)


# ── gate U2: the engine mark, and the way out ────────────────────────────


def _post_u2(option: str = "bess_2h"):
    """A network the study writes today, with its compiled engine config."""
    from services.study import library as L
    from services.study import packs
    from services.study import questions as Q
    from tests.golden import site_fixture as sf

    defaults = L.load_defaults()
    ledger = L.seed_ledger(Q.BESS_AT_SITE, sf.site_intake(), defaults)
    n = packs.build_site_network(sf.site_intake(), ledger, option, library=defaults)
    cfg = packs.option_solver_config(ledger, packs.option_commercial(
        sf.site_intake(), ledger, defaults, n.snapshots))
    return n, cfg


def test_a_network_the_study_writes_now_carries_the_engine_mark(tmp_path):
    from services.study import packs, pre_u2

    for option in ("none", "bess_2h"):
        n, _cfg = _post_u2(option)
        assert n.meta[packs.PACK_META_KEY][packs.TARIFF_ON_KEY] == packs.TARIFF_ON_ENGINE
        # It lives inside the pack record, so it travels wherever the pack
        # marker does (netCDF, Save-As, a copy, a scenario, a bundle).
        path = tmp_path / f"{option}.nc"
        n.export_to_netcdf(str(path))
        m = pypsa.Network(str(path))
        assert m.meta[packs.PACK_META_KEY][packs.TARIFF_ON_KEY] == packs.TARIFF_ON_ENGINE
        assert not pre_u2.is_pre_u2(m)
    assert pre_u2.TARIFF_ON_KEY == packs.TARIFF_ON_KEY
    assert pre_u2.TARIFF_ON_ENGINE == packs.TARIFF_ON_ENGINE


@pytest.mark.parametrize("how", ["static", "hourly", "both_links"])
@pytest.mark.parametrize("with_commercial", [True, False])
def test_a_post_u2_network_with_a_user_typed_link_price_is_not_refused(how, with_commercial):
    """
    Gate U2 / S2 (a): a price a user types on a post-U2 study network's
    meter Link is an ordinary Expert edit. W4 does not call it an earlier
    version's tariff. (With an engine tariff the commercial layer adds its
    prices ON TOP of the Link's own cost, `lp_bindings` "base cost + adders":
    the typed price is a second, separate charge, which IC states as its
    contract; nothing in GS refuses it.)
    """
    import dataclasses

    n, cfg = _post_u2()
    if not with_commercial:
        cfg = dataclasses.replace(cfg, commercial=None)
    if how == "static":
        n.links.loc["grid_import", "marginal_cost"] = 100.0
    else:
        n.links_t.marginal_cost["grid_import"] = 100.0
        if how == "both_links":
            n.links_t.marginal_cost["grid_export"] = -40.0
    assert CODE not in _codes(n, cfg)


@pytest.mark.parametrize("how", ["zeros", "dropped", "nan"])
def test_a_pre_u2_network_whose_link_prices_are_cleared_is_not_refused(tmp_path, how):
    """
    The way out (a) lifts the refusal: the detection is "no engine mark AND a
    priced meter Link", so once the user clears the two Links' prices the
    network is no longer the old tariff. `clear_probe2.py` (gate U2 report)
    walked the same through the routes: Time series -> Links -> Marginal cost,
    a profile of zeros uploaded for each Link, then save and reload.
    """
    n, cfg, _raw = _stored(tmp_path, "fork")
    assert CODE in _codes(n, cfg)
    links = ["grid_import", "grid_export"]
    if how == "zeros":
        n.links_t.marginal_cost.loc[:, links] = 0.0
    elif how == "nan":
        n.links_t.marginal_cost.loc[:, links] = float("nan")
    else:
        n.links_t.marginal_cost = n.links_t.marginal_cost.drop(columns=links)
    assert CODE not in _codes(n, cfg)
    # ... and stays lifted through a netCDF round trip (the pack marker stays).
    path = tmp_path / "cleared.nc"
    n.export_to_netcdf(str(path))
    m = pypsa.Network(str(path))
    assert "decision_study_pack" in m.meta
    assert CODE not in _codes(m, cfg)


def test_clearing_only_one_link_still_refuses_and_names_the_other(tmp_path):
    n, cfg, _raw = _stored(tmp_path, "fork")
    n.links_t.marginal_cost = n.links_t.marginal_cost.drop(columns=["grid_import"])
    from services.validation_service import validate_for_run

    [issue] = [i for i in validate_for_run(n, cfg) if i.code == CODE]
    assert "grid_export" in issue.message and "grid_import" not in issue.message


def test_a_static_pre_u2_price_points_at_properties(tmp_path):
    """The clearing step names where THIS price lives: a static one is in Properties."""
    from services.validation_service import validate_for_run

    n, cfg, _raw = _stored(tmp_path, "base")
    n.links_t.marginal_cost = n.links_t.marginal_cost.drop(columns=["grid_import", "grid_export"])
    n.links.loc["grid_import", "marginal_cost"] = 130.0
    [issue] = [i for i in validate_for_run(n, cfg) if i.code == CODE]
    msg = issue.message.lower()
    assert "properties" in msg and "profile of zeros" not in msg


@pytest.fixture
def studies_on(monkeypatch):
    from tests.study_s4_support import enable_studies

    yield from enable_studies(monkeypatch)


def test_the_studys_own_networks_carry_the_mark_through_every_save_path(
        client, api_project, studies_on, monkeypatch, project_storage_dir):
    """
    Gate U2 (a), through the routes: the base the study creates and the
    forks its run writes carry the engine mark, and so do a Save-As of a
    fork, a scenario branched from it and a bundle round trip. On the Save-As
    copy a price typed on `grid_import` through the Properties route is not
    refused by W4.
    """
    import io

    from services import solver_service
    from tests.study_s4_support import create_pack_study, wait_run
    from tests.test_study_runner import FakeSolver

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    api_project("mk-src")
    r = create_pack_study(client, "mk-src", "mk-base")
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert client.post(f"/api/projects/mk-base/studies/{sid}/run", json={}).status_code == 202
    wait_run(client, "mk-base", sid)

    def mark(name):
        n = pypsa.Network(str(project_storage_dir(name) / "network.nc"))
        return (n.meta.get("decision_study_pack") or {}).get("tariff_on")

    fork = "mk-base-opt-bess_2h"
    assert mark("mk-base") == "engine"
    assert mark(fork) == "engine"
    assert client.get(f"/api/projects/{fork}").status_code == 200
    assert client.post("/api/projects/mk-copy",
                       params={"force": True, "rebind": True}).status_code == 200
    assert mark("mk-copy") == "engine"
    r = client.post("/api/projects/mk-copy/scenarios", json={"name": "mk-scen"})
    assert r.status_code < 300, r.text
    assert mark("mk-scen") == "engine"
    bundle = client.get("/api/projects/mk-copy/bundle")
    assert bundle.status_code == 200
    r = client.post("/api/projects/import_bundle", params={"name": "mk-bundle"},
                    files={"file": ("mk.pypsaproj.zip", io.BytesIO(bundle.content),
                                    "application/zip")})
    assert r.status_code < 300, r.text
    assert mark("mk-bundle") == "engine"

    assert client.get("/api/projects/mk-copy").status_code == 200
    r = client.put("/api/network/links/grid_import",
                   json={"name": "grid_import", "bus0": "grid", "bus1": "site",
                         "marginal_cost": 100.0})
    assert r.status_code == 200, r.text
    issues = client.post("/api/simulation/preflight").json()["issues"]
    assert CODE not in {i["code"] for i in issues}


def test_every_cause_in_u2_deltas_leads_with_a_stable_code():
    """Gate N7: each recorded delta's cause starts with a snake_case code."""
    import re

    rows = json.loads((pathlib.Path(__file__).resolve().parent / "fixtures"
                       / "u2_deltas.json").read_text(encoding="utf-8"))
    rows = rows["deltas"] if isinstance(rows, dict) else rows
    assert rows
    for row in rows:
        cause = row["cause"]
        m = re.match(r"^([a-z0-9_]+)(?::| \(|$)", cause)
        assert m, cause
        code = m.group(1)
        assert code and not code.isdigit() and re.search(r"[a-z]", code), cause
