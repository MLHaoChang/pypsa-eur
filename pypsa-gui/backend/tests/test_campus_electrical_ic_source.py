"""Which campus result feeds the investment case (owner decision 2026-10-12).

"MILP when fresh, else least cost." The extra owner assets
(``extra_owner_assets``), their ``source_hash`` (``campus_study_hash``) and
the route's body (``owner_assets_response``) come from the joint optimisation
in ``run_milp/`` when it has a result of its own that is not stale, and from
the least-cost run in ``run/`` otherwise, exactly as before. Which one is
said: ``meta.study_method`` on every entry, ``study_method`` in the body and
``owner_assets_method`` in the state.

A MILP result that kept the least-cost choice (a fallback, cancelled or not)
is not a result of its own: the least-cost run is the source, and its hash is
unchanged. A cancelled job that had found a cheaper AC-feasible point is.

The jobs run on the fake engine of the C12 tests (the real least-cost pick
plus a scripted loop), which writes, here, a hand-made MILP table when it has
a result of its own, so its rows are told apart from the least-cost ones.
"""
import hashlib
import json

import pandas as pd
import pytest
import yaml

from services import campus_electrical_service as ce
from tests.test_campus_electrical_milp_job import (  # noqa: F401  (fixtures)
    SETTINGS, FakeEngine, _clean_slot, finish, hub, record_of, wait_until)
from tests.test_campus_electrical_service import (  # noqa: F401  (fixtures)
    INVEST_COLUMNS, hash_by_the_contract, inv_row, library_with, scaled_capex, user_and_db)

MILP_ROWS = [
    inv_row("transformer GRID_IMPORT", "TR_132_33_63", "transformer", 2, capex=5_000_000.0),
    inv_row("switchgear GRID", "SG_132_31p5", "switchgear", 3, capex=1_350_000.0),
]
MILP_NAMES = ["campus:TR_132_33_63#1", "campus:SG_132_31p5#1"]


class MilpEngine(FakeEngine):
    """The fake loop; when it ends with a point of its own (no fallback) the
    investment table it writes is ``MILP_ROWS``, as the real MILP writes its
    own choice. A fallback keeps the least-cost table it wrote."""

    def __call__(self, run_dir, library, **kw):
        out = super().__call__(run_dir, library, **kw)
        if out["fallback"] is None:
            pd.DataFrame(MILP_ROWS, columns=INVEST_COLUMNS).to_csv(run_dir / ce.cs.INVESTMENT_CSV, index=False)
        return out


def run_milp(project, monkeypatch, iterations=2):
    engine = MilpEngine(iterations=iterations)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(project, SETTINGS)
    finish(project, engine)
    return engine


def milp_dir(project):
    return ce.campus_dir(project) / ce.MILP_SUBDIR


def run_dir(project):
    return ce.campus_dir(project) / "run"


def least_cost_names(project):
    """The names the least-cost table gives, read independently of the service."""
    inv = pd.read_csv(run_dir(project) / ce.cs.INVESTMENT_CSV)
    keep = inv[(~inv["existing"].astype(bool)) & (inv["status"] != "unresolved") & inv["library_id"].notna()]
    seen, names = {}, []
    for lib_id in keep["library_id"]:
        seen[lib_id] = seen.get(lib_id, 0) + 1
        names.append(f"campus:{lib_id}#{seen[lib_id]}")
    return names


def milp_hash_by_the_contract(d):
    """The least-cost contract's digest, preceded by the method: ``study_method``, a NUL, ``milp``."""
    h = hashlib.sha256(b"study_method\0milp")
    for name in ("campus_investment.csv", "campus_assets_used.yaml", "campus.yaml", "campus_cost_basis.json"):
        f = d / name
        if f.is_file():
            h.update(name.encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:16]


# ── no MILP: as before, said ────────────────────────────────────────────────

def test_without_a_milp_the_least_cost_run_feeds_the_case_and_says_so(hub):
    ce.run(hub, SETTINGS)
    assets = ce.extra_owner_assets(hub)
    assert assets and [a["name"] for a in assets] == least_cost_names(hub)
    assert {a["meta"]["study_method"] for a in assets} == {"least_cost"}
    assert ce.campus_study_hash(hub) == hash_by_the_contract(run_dir(hub))       # the hash is the old contract's
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "least_cost" and out["stale"] is False
    state = ce.get_state(hub)
    assert state["owner_assets_method"] == "least_cost" and state["owner_assets_count"] == len(assets)


def test_without_assets_there_is_no_method(hub):
    assert ce.owner_assets_response(hub)["study_method"] is None
    assert ce.get_state(hub)["owner_assets_method"] is None
    ce.run(hub, {**SETTINGS, "invest": False})
    assert ce.owner_assets_response(hub) == {"assets": [], "source_hash": None, "stale": False, "study_method": None}
    assert ce.get_state(hub)["owner_assets_method"] is None


# ── a fresh MILP feeds the case ─────────────────────────────────────────────

def test_a_fresh_milp_feeds_the_case_with_its_rows_and_says_so(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    assert ce.get_state(hub)["milp"]["stale"] is False
    assert least_cost_names(hub) != MILP_NAMES                     # the two are told apart
    assets = ce.extra_owner_assets(hub)
    assert [a["name"] for a in assets] == MILP_NAMES
    assert {a["meta"]["study_method"] for a in assets} == {"milp"}
    tr = assets[0]
    assert tr["quantity"] == 2.0 and tr["parts"][0]["upfront_per_unit"] == 2_500_000.0 and tr["basis"] == "lump"
    assert assets[1]["basis"] == "per_bay" and assets[1]["quantity"] == 3.0
    out = ce.owner_assets_response(hub)
    assert list(out) == ["assets", "source_hash", "stale", "study_method"]
    assert out["study_method"] == "milp" and out["stale"] is False and out["assets"] == assets
    assert out["source_hash"] == ce.campus_study_hash(hub) == assets[0]["source_hash"] == assets[1]["source_hash"]
    state = ce.get_state(hub)
    assert state["owner_assets_method"] == "milp" and state["owner_assets_count"] == 2


def test_the_milp_assets_are_priced_from_the_library_the_milp_bought_from(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    (milp_dir(hub) / ce.USED_LIBRARY_FILE).write_text(library_with(scaled_capex(2)))
    tr = ce.extra_owner_assets(hub)[0]
    assert tr["meta"]["study_method"] == "milp" and tr["parts"][0]["upfront_per_unit"] == 5_000_000.0


def test_the_milp_hash_is_the_method_then_the_milp_runs_files_and_differs_from_the_least_cost_one(
        hub, monkeypatch):
    ce.run(hub, SETTINGS)
    least_cost = ce.campus_study_hash(hub)
    run_milp(hub, monkeypatch)
    digest = ce.campus_study_hash(hub)
    assert digest == milp_hash_by_the_contract(milp_dir(hub)) and len(digest) == 16
    assert digest != least_cost and digest != hash_by_the_contract(milp_dir(hub))
    assert ce.campus_study_hash(hub) == digest                      # stable while nothing changes
    ce.run(hub, SETTINGS)                                            # the least-cost run again, alike
    assert ce.get_state(hub)["milp"]["stale"] is False and ce.campus_study_hash(hub) == digest


@pytest.mark.parametrize("name", ["campus_investment.csv", "campus_assets_used.yaml", "campus.yaml",
                                  "campus_cost_basis.json"])
def test_each_file_of_the_milp_run_is_in_its_hash(hub, monkeypatch, name):
    run_milp(hub, monkeypatch)
    assert (milp_dir(hub) / name).is_file()
    before = ce.campus_study_hash(hub)
    with open(milp_dir(hub) / name, "ab") as f:
        f.write(b"\n")
    assert ce.campus_study_hash(hub) != before


# ── a stale MILP does not ───────────────────────────────────────────────────

def test_a_milp_made_stale_by_a_least_cost_rerun_gives_way_to_it_and_its_hash(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    milp_hash = ce.campus_study_hash(hub)
    ce.run(hub, {**SETTINGS, "margin": 0.3})
    state = ce.get_state(hub)
    assert state["milp"]["stale"] is True and state["stale"] is False
    assets = ce.extra_owner_assets(hub)
    assert [a["name"] for a in assets] == least_cost_names(hub)
    assert {a["meta"]["study_method"] for a in assets} == {"least_cost"}
    digest = ce.campus_study_hash(hub)
    assert digest == hash_by_the_contract(run_dir(hub)) and digest != milp_hash
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "least_cost" and out["stale"] is False and out["source_hash"] == digest
    assert state["owner_assets_method"] == "least_cost"


def test_a_milp_made_stale_by_the_campus_gives_way_to_a_stale_least_cost_run(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    spec = yaml.safe_load(ce.get_state(hub)["campus_yaml"])
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 2500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "least_cost" and out["stale"] is True
    assert [a["name"] for a in out["assets"]] == least_cost_names(hub)


def test_a_milp_made_stale_by_the_library_gives_way(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    ce.save_library(hub, library_with(scaled_capex(2)))
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "least_cost" and out["stale"] is True


# ── a MILP without a result of its own does not ─────────────────────────────

def test_a_milp_that_kept_the_least_cost_choice_leaves_the_least_cost_run_as_the_source(hub, monkeypatch):
    ce.run(hub, SETTINGS)
    least_cost = ce.campus_study_hash(hub)
    run_milp(hub, monkeypatch, iterations=1)
    res = ce.get_state(hub)["milp"]
    assert res["results"]["fallback"] and res["stale"] is False
    assert ce.owner_assets_response(hub)["study_method"] == "least_cost"
    assert ce.campus_study_hash(hub) == least_cost                 # nothing switched, nothing to rebuild


def test_a_cancelled_milp_that_kept_the_least_cost_choice_does_not_count(hub, monkeypatch):
    ce.run(hub, SETTINGS)
    least_cost = ce.campus_study_hash(hub)
    engine = MilpEngine(iterations=5)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    wait_until(lambda: record_of(hub)["iteration"] == 0)
    ce.cancel_milp_job(hub)
    finish(hub, engine, steps=1)
    res = ce.get_state(hub)["milp"]["results"]
    assert res["stop"] == "cancelled" and res["fallback"]
    assert ce.owner_assets_response(hub)["study_method"] == "least_cost"
    assert ce.campus_study_hash(hub) == least_cost


def test_a_cancelled_milp_with_a_cheaper_point_of_its_own_counts(hub, monkeypatch):
    engine = MilpEngine(iterations=5)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    engine.step.release()
    wait_until(lambda: record_of(hub)["iteration"] == 1)
    ce.cancel_milp_job(hub)
    finish(hub, engine, steps=1)
    res = ce.get_state(hub)["milp"]["results"]
    assert res["stop"] == "cancelled" and res["fallback"] is None
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "milp" and [a["name"] for a in out["assets"]] == MILP_NAMES


def test_a_failed_or_running_milp_leaves_the_least_cost_run_as_the_source(hub, monkeypatch):
    engine = FakeEngine(fail=True)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)
    finish(hub, engine)
    assert ce.owner_assets_response(hub)["study_method"] == "least_cost"
    run_milp(hub, monkeypatch)
    assert ce.owner_assets_response(hub)["study_method"] == "milp"
    engine = MilpEngine(iterations=3)
    monkeypatch.setattr(ce, "_milp_engine", engine)
    ce.start_milp_job(hub, SETTINGS)                                 # a new job clears the previous result
    assert ce.owner_assets_response(hub)["study_method"] == "least_cost"
    finish(hub, engine)
    assert ce.owner_assets_response(hub)["study_method"] == "milp"


def test_a_milp_summary_without_its_investment_table_does_not_count(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    (milp_dir(hub) / ce.cs.INVESTMENT_CSV).unlink()
    assert ce.owner_assets_response(hub)["study_method"] == "least_cost"
    run_milp(hub, monkeypatch)
    (milp_dir(hub) / ce.USED_LIBRARY_FILE).unlink()
    assert ce.owner_assets_response(hub)["study_method"] == "least_cost"


def test_a_milp_summary_that_cannot_be_read_does_not_count(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    summary = milp_dir(hub) / ce.MILP_SUMMARY_FILE
    summary.write_text(json.dumps({"summary": {"stop": "converged"}}))     # no fallback key
    assert ce.extra_owner_assets(hub)[0]["meta"]["study_method"] == "least_cost"


# ── the copilot's reads ─────────────────────────────────────────────────────

def test_the_copilots_investment_read_says_which_result_feeds_the_case(hub, monkeypatch):
    ce.run(hub, SETTINGS)
    assert ce.get_investment(hub)["owner_assets_method"] == "least_cost"
    run_milp(hub, monkeypatch)
    assert ce.get_investment(hub)["owner_assets_method"] == "milp"


def test_the_copilot_tools_describe_the_method():
    from services.chat_tools_schema import TOOLS
    desc = {t["name"]: t["description"] for t in TOOLS}
    for name in ("campus_get_study", "campus_get_investment"):
        assert "owner_assets_method" in desc[name], name
        assert "milp|least_cost" in desc[name], name
