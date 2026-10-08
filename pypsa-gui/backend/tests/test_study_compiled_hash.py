"""
U2 WP8 (plan §2 C10; gate C6 / WP6 W4): `FindingsHashes.compiled_hash` and
the stale path of a study whose engine inputs moved or were never recorded.

The run writes each option fork's engine inputs — the compiled commercial
config with its `single_owner` value flows, and the compiled finance inputs
(`CompiledFinance.finance()`) — into the fork's `solver_config.json`, and
records what it wrote: the per-fork digest of those two blocks
(`run_hashes.fork_engine_digest`) and the run's `compiled_hash`, built from
the run's `commercial_digest` and the forks' finance digests. Every reader
applies the ONE rule (`run_hashes.compiled_matches`):

* a fork whose commercial or finance block was edited after the run (an
  Expert edit of its solver config): the case and the findings answer 409
  `engine_inputs_changed_since_run`, the stored report reads stale
  (`engine_inputs_changed_since_findings:<option>`);
* a run recorded before the engine inputs were (pre-WP6: no export series;
  pre-WP7: a battery without its two upfront parts; pre-WP8: no compiled
  hash): the same 409, with a plain reason and a re-run offered, and the
  report reads stale (`run_by_an_earlier_version`) — never a silent "not
  established", never the pro forma's numbers shown as current.

The run is the S4 runner with the fake solver (`test_study_runner.FakeSolver`),
which leaves the engine's solve record on each fork, so the case and the
findings are the engine's.
"""
from __future__ import annotations

import json

import pytest

from services.study import run_hashes, store
from tests.study_s4_support import create_pack_study, enable_studies, wait_run
from tests.test_study_runner import FakeSolver

OPTIONS = ("none", "bess_1h", "bess_2h", "bess_4h", "bess_pv_2h")


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver


def _run(client, api_project, name):
    api_project(f"{name}-src")
    r = create_pack_study(client, f"{name}-src", name)
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    r = client.post(f"/api/projects/{name}/studies/{sid}/run", json={})
    assert r.status_code == 202, r.text
    rec = wait_run(client, name, sid)
    assert rec["status"] == "done", rec
    return sid


def _cfg_path(project_storage_dir, name, option):
    return project_storage_dir(f"{name}-opt-{option}") / "solver_config.json"


def _url(name, sid, tail):
    return f"/api/projects/{name}/studies/{sid}{tail}"


def _refused(r, code="engine_inputs_changed_since_run"):
    assert r.status_code == 409, (r.status_code, r.text[:400])
    detail = r.json()["detail"]
    assert detail["error_kind"] == code, detail
    return detail["message"]


# ── what the run writes and records ───────────────────────────────────────

def test_each_fork_carries_the_compiled_commercial_and_finance_and_the_run_records_them(
        client, api_project, studies_on, fake, project_storage_dir, project_row):
    """
    The runner writes BOTH `commercial` (with the option's value flows, row
    28) and `finance` (C4: real basis, the ledger's rate, the storage
    lifetime) into each fork's solver config, and the findings record the
    run's `compiled_hash` and each fork's digest of those blocks.
    """
    sid = _run(client, api_project, "ch-write")
    findings = store.load_aux(project_storage_dir("ch-write"), sid, "findings")
    hashes = findings["hashes"]
    assert hashes["compiled_hash"]
    per = hashes["option_compiled_hashes"]
    assert len(per) == len(OPTIONS)
    run = store.load_aux(project_storage_dir("ch-write"), sid, "run")
    for option in OPTIONS:
        cfg = json.loads(_cfg_path(project_storage_dir, "ch-write", option).read_text())
        assert cfg["commercial"]["export_price_ref"]["id"] == run["export_series"]["id"]
        assert cfg["commercial"]["value_flows"], option
        fin = cfg["finance"]
        assert fin is not None, option
        assert (fin["price_basis"], fin["wacc_nominal"], fin["analysis_years"]) == (
            "real", 0.07, 25), option
        assert fin["terminal_value"]["method"] == "remaining_life_annuity"
        row = project_row(f"ch-write-opt-{option}")
        assert per[str(row.id)] == run_hashes.fork_engine_digest(row), option
    assert hashes["compiled_hash"] == run_hashes.compiled_hash(
        run["commercial_digest"], {o: d["finance_digest"]
                                   for o, d in run["details"].items()})


def test_the_engine_digest_reads_only_the_engine_inputs():
    cfg = {"commercial": {"poc_link": "grid_import"}, "finance": {"wacc_nominal": 0.07},
           "solver_name": "highs"}
    d = run_hashes.engine_inputs_digest(cfg)
    assert d == run_hashes.engine_inputs_digest({**cfg, "solver_name": "gurobi"})
    assert d != run_hashes.engine_inputs_digest({**cfg, "finance": {"wacc_nominal": 0.08}})
    assert d != run_hashes.engine_inputs_digest({**cfg, "commercial": {"poc_link": "x"}})


def test_a_missing_digest_never_matches():
    hashes = {"compiled_hash": "c" * 16, "option_compiled_hashes": {"f": "d" * 16}}
    assert run_hashes.compiled_matches(hashes, "f", "d" * 16)
    assert not run_hashes.compiled_matches(hashes, "f", None)
    assert not run_hashes.compiled_matches(hashes, "g", "d" * 16)
    assert not run_hashes.compiled_matches({**hashes, "compiled_hash": None}, "f", "d" * 16)
    assert not run_hashes.compiled_matches({"option_compiled_hashes": {"f": "d" * 16}},
                                           "f", "d" * 16)


# ── an Expert edit of a fork's engine inputs after the run ────────────────

def test_editing_a_forks_commercial_config_after_the_run_refuses_and_stales(
        client, api_project, studies_on, fake, project_storage_dir):
    """
    Plan §2 C10 (mutation: skip `compiled_hash`, the case route reads the
    fork as current → this test red). The fork's network is untouched, so
    only the engine-input rule can see the edit.
    """
    name = "ch-edit"
    sid = _run(client, api_project, name)
    case = client.get(_url(name, sid, "/options/bess_2h/case"))
    assert case.status_code == 200, case.text
    assert case.json()["engine"] == "finance_engine"
    r = client.post(_url(name, sid, "/report"))
    assert r.status_code == 200, r.text
    assert r.json()["stale"] is False, r.json()["stale_reasons"]

    path = _cfg_path(project_storage_dir, name, "bess_2h")
    cfg = json.loads(path.read_text())
    [energy] = [i for i in cfg["commercial"]["import_tariff"]["items"] if i["id"] == "energy"]
    energy["periods"][0]["rate"] *= 1.5
    path.write_text(json.dumps(cfg, indent=2))

    message = _refused(client.get(_url(name, sid, "/options/bess_2h/case")))
    assert "edited" in message and "re-run" in message
    _refused(client.get(_url(name, sid, "/options/bess_2h/case.xlsx")))
    # An untouched option still reads.
    assert client.get(_url(name, sid, "/options/bess_1h/case")).status_code == 200
    _refused(client.get(_url(name, sid, "/findings")))
    _refused(client.post(_url(name, sid, "/findings/tornado"), json={}))
    report = client.get(_url(name, sid, "/report")).json()
    assert report["stale"] is True
    assert report["stale_reasons"] == ["engine_inputs_changed_since_findings:bess_2h"]

    # The finance block is an engine input too.
    cfg["commercial"] = json.loads(path.read_text())["commercial"]
    energy = next(i for i in cfg["commercial"]["import_tariff"]["items"] if i["id"] == "energy")
    energy["periods"][0]["rate"] /= 1.5
    path.write_text(json.dumps(cfg, indent=2))
    assert client.get(_url(name, sid, "/options/bess_2h/case")).status_code == 200
    cfg["finance"]["contingency_share"] = 0.1
    path.write_text(json.dumps(cfg, indent=2))
    _refused(client.get(_url(name, sid, "/options/bess_2h/case")))


# ── a run recorded before the engine inputs were ──────────────────────────

def _strip(project_storage_dir, name, sid, *, hashes=(), run=()):
    base = project_storage_dir(name)
    f = store.load_aux(base, sid, "findings")
    for key in hashes:
        f["hashes"].pop(key, None)
    store.save_aux(base, sid, "findings", f)
    rec = store.load_aux(base, sid, "run")
    for key in run:
        rec.pop(key, None)
    store.save_aux(base, sid, "run", rec)
    # The stored report keeps the hashes it was assembled from.
    report = store.load_aux(base, sid, "report")
    if report is not None:
        for key in hashes:
            report["report"]["hashes_at_findings"].pop(key, None)
        store.save_aux(base, sid, "report", report)


@pytest.mark.parametrize("stripped", [
    pytest.param({"hashes": ("compiled_hash", "option_compiled_hashes")}, id="pre_wp8"),
    pytest.param({"run": ("export_series", "commercial_digest")}, id="pre_wp6"),
])
def test_a_run_recorded_before_the_engine_inputs_is_stale_with_a_rerun(
        client, api_project, studies_on, fake, project_storage_dir, stripped):
    name = f"ch-old-{'a' if 'hashes' in stripped else 'b'}"
    sid = _run(client, api_project, name)
    assert client.post(_url(name, sid, "/report")).status_code == 200
    _strip(project_storage_dir, name, sid, **stripped)

    message = _refused(client.get(_url(name, sid, "/options/bess_2h/case")))
    assert "earlier version" in message and "Run the study again" in message
    _refused(client.get(_url(name, sid, "/findings")))
    _refused(client.post(_url(name, sid, "/report")))
    _refused(client.post(_url(name, sid, "/findings/tornado"), json={}))
    report = client.get(_url(name, sid, "/report")).json()
    if "hashes" in stripped:
        assert report["stale"] is True
        assert report["stale_reasons"] == ["run_by_an_earlier_version"]


def test_a_fork_without_the_batterys_two_parts_is_stale_not_the_pro_forma(
        client, api_project, studies_on, fake, project_storage_dir, project_row):
    """
    A fork written before WP7 (the battery priced by `capital_cost` alone):
    never valued on the pro forma as current — the case answers 409 with the
    plain reason. The recorded network hash is re-pinned so only the
    engine rule can refuse it.
    """
    import pypsa

    name = "ch-noparts"
    sid = _run(client, api_project, name)
    path = project_storage_dir(f"{name}-opt-bess_2h") / "network.nc"
    n = pypsa.Network(str(path))
    parts = [c for c in n.storage_units.columns if c.startswith(("inv_power_", "inv_energy_"))]
    assert parts
    n.storage_units[parts] = float("nan")
    n.export_to_netcdf(str(path))
    base = project_storage_dir(name)
    f = store.load_aux(base, sid, "findings")
    fork = project_row(f"{name}-opt-bess_2h")
    f["hashes"]["option_network_hashes"][str(fork.id)] = run_hashes.fork_file_hash(fork)
    store.save_aux(base, sid, "findings", f)

    message = _refused(client.get(_url(name, sid, "/options/bess_2h/case")))
    assert "earlier version" in message
    _refused(client.get(_url(name, sid, "/findings")))
