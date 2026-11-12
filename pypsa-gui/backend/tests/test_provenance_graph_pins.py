"""
provenance-graph 06: the campus, site-binding and assistant-brief checks read their verdict from the graph.

Each moved check is held against a frozen copy of the check it replaced (the oracle below, written out here and
importing nothing from the graph): same answer, same wording of what it returns. Unreadable is stale (ADR-0001).
"""

from __future__ import annotations

import inspect
import json

import pytest

from harness import outcomes
from services import (
    assistant_tasks,
    campus_electrical_service as ce,
    component_identity,
    project_registry,
    provenance_graph as G,
    site_bindings,
)
from services.asset_schema import provenance
from services.site_scene import associations


# ── the primitive ─────────────────────────────────────────────────────────────


def test_a_pin_that_matches_is_fresh_and_one_that_differs_is_stale_with_both_values():
    assert G.pin_verdict("x", [G.Pin("a", 1, lambda: 1)]).state == "fresh"
    v = G.pin_verdict("x", [G.Pin("a", 1, lambda: 2), G.Pin("b", 3, lambda: 3)])
    assert v.state == "stale"
    assert [(b.field, b.recorded, b.current) for b in v.broken] == [("a", 1, 2)]


def test_an_unreadable_current_value_is_stale_never_fresh():
    def boom():
        raise OSError("gone")

    for read in (boom, lambda: G.UNREADABLE):
        v = G.pin_verdict("x", [G.Pin("a", 1, read)])
        assert v.state == "stale" and v.broken[0].reason.startswith(
            "cannot be read now"
        )


def test_a_none_current_is_a_value_not_unreadable():
    assert G.pin_verdict("x", [G.Pin("a", None, lambda: None)]).state == "fresh"


def test_no_pin_compares_nothing_and_reads_uncompared():
    assert G.pin_verdict("x", []).state == "uncompared"


# ── site bindings (oracle: master's stale_keys and associations._stale) ───────


def _old_stale_keys(n, keys, bindings):
    return {
        k
        for k in keys
        if isinstance(bindings.get(k), str)
        and site_bindings._resolved_key(n, k, bindings) != k
    }


def test_stale_keys_matches_the_old_uid_check(monkeypatch):
    live = {
        "Generator:a": "Generator:a",
        "Generator:b": "Generator:renamed",
        "Generator:c": None,
    }
    monkeypatch.setattr(
        site_bindings, "_resolved_key", lambda n, key, bindings: live[key]
    )
    keys = [*live, "Generator:d"]
    bindings = {k: "uid" for k in live}  # d has no binding
    assert site_bindings.stale_keys(None, keys, bindings) == _old_stale_keys(
        None, keys, bindings
    )
    assert site_bindings.stale_keys(None, keys, bindings) == {
        "Generator:b",
        "Generator:c",
    }


@pytest.mark.parametrize(
    "recorded, held",
    [("u1", "u1"), ("u1", "u2"), ("u1", None), (None, "u1"), (5, "u1")],
)
def test_the_association_uid_check_matches_the_old_one(monkeypatch, recorded, held):
    monkeypatch.setattr(component_identity, "get", lambda n, cls, name: held)
    old = isinstance(recorded, str) and held != recorded
    assert associations._stale(None, "Generator", "g", recorded) is old


# ── the assistant's task brief (oracle: master's `_stale`) ────────────────────


def _old_task_stale(task, current, moved):
    if not task.get("brief") or task["status"] in ("completed", "cancelled"):
        return False
    if current is None or current != task.get("fingerprint"):
        return True
    return moved


@pytest.mark.parametrize("current", ["fp", "other", None])
@pytest.mark.parametrize("moved", [False, True])
@pytest.mark.parametrize("status", ["planned", "completed"])
def test_the_task_brief_check_matches_the_old_one(monkeypatch, current, moved, status):
    task = {"id": "t", "brief": {"x": 1}, "status": status, "fingerprint": "fp"}
    monkeypatch.setattr(assistant_tasks, "_fingerprint", lambda: current)
    monkeypatch.setattr(assistant_tasks, "_requirements_moved", lambda t: moved)
    assert assistant_tasks._stale(task) is _old_task_stale(task, current, moved)


# ── the campus study ──────────────────────────────────────────────────────────


def _old_dips(as_run, now):
    return sorted(u for u in set(as_run) | set(now) if as_run.get(u) != now.get(u))


def test_dips_out_of_date_lists_the_changed_gone_and_new_units(tmp_path, monkeypatch):
    nc = tmp_path / ce.NETWORK_FILE
    nc.write_bytes(b"net")
    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    as_run = {"a": {"t": 1}, "b": {"t": 2}, "gone": {"t": 3}}
    now = {"a": {"t": 1}, "b": {"t": 9}, "new": {"t": 4}}
    monkeypatch.setattr(ce, "_DIPS_INPUTS_CACHE", {(str(nc), ce._file_digest(nc)): now})
    assert (
        ce._dips_out_of_date("p", tmp_path, as_run)
        == _old_dips(as_run, now)
        == ["b", "gone", "new"]
    )


def _campus(tmp_path, monkeypatch, *, campus_sha="c", network_sha="n"):
    """A run directory whose manifest records `campus_sha` and `network_sha`."""
    d = tmp_path / "campus"
    (d / "run").mkdir(parents=True)
    (d / ce.CAMPUS_FILE).write_text("a: 1\n")
    (tmp_path / ce.NETWORK_FILE).write_bytes(b"net")
    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    import hashlib

    good = hashlib.sha256(
        ce.yaml.safe_dump({"a": 1}, sort_keys=False).encode()
    ).hexdigest()
    (d / "run" / ce.cs.CAMPUS_MANIFEST).write_text(
        json.dumps(
            {
                "campus_sha256": good if campus_sha == "c" else campus_sha,
                "network_sha256": ce._file_digest(tmp_path / ce.NETWORK_FILE)
                if network_sha == "n"
                else network_sha,
            }
        )
    )
    return d


def test_the_campus_study_is_fresh_until_one_recorded_hash_differs(
    tmp_path, monkeypatch
):
    d = _campus(tmp_path, monkeypatch)
    assert ce._stale("p", d) is False
    assert (
        ce._stale("p", _campus(tmp_path / "x", monkeypatch, campus_sha="other")) is True
    )
    assert (
        ce._stale("p", _campus(tmp_path / "y", monkeypatch, network_sha="other"))
        is True
    )


def test_the_campus_study_with_no_run_is_not_stale_as_before(tmp_path, monkeypatch):
    d = _campus(tmp_path, monkeypatch)
    (d / "run" / ce.cs.CAMPUS_MANIFEST).unlink()
    assert ce._stale("p", d) is False


def test_a_library_side_file_that_cannot_be_read_is_stale(tmp_path, monkeypatch):
    d = _campus(tmp_path, monkeypatch)
    (d / "run" / ce.USED_LIBRARY_FILE).write_text("lib")
    (d / "run" / ce.COST_BASIS_FILE).write_text("{not json")
    monkeypatch.setattr(ce, "_engine_library", lambda project, d: ("lib", {}, {}))
    assert ce._library_stale("p", d) is True
    (d / "run" / ce.COST_BASIS_FILE).write_text(json.dumps({"k": 1}))
    monkeypatch.setattr(ce, "_engine_library", lambda project, d: ("lib", {}, {"k": 1}))
    assert ce._library_stale("p", d) is False
    monkeypatch.setattr(ce, "_engine_library", lambda project, d: ("lib", {}, {"k": 2}))
    assert ce._library_stale("p", d) is True
    monkeypatch.setattr(
        ce, "_engine_library", lambda project, d: ("other", {}, {"k": 1})
    )
    assert ce._library_stale("p", d) is True

    def boom(project, d):
        raise RuntimeError("no library")

    monkeypatch.setattr(ce, "_engine_library", boom)
    assert ce._library_stale("p", d) is True


def test_a_run_that_did_not_invest_does_not_depend_on_the_library(
    tmp_path, monkeypatch
):
    d = _campus(tmp_path, monkeypatch)
    assert ce._library_stale("p", d) is False


def test_the_milp_is_stale_when_its_basis_settings_or_copied_inputs_differ(
    tmp_path, monkeypatch
):
    d = _campus(tmp_path, monkeypatch)
    (d / "run" / "inv.csv").write_text("x")
    (d / ce.MILP_SUBDIR).mkdir()
    basis = {
        "settings": {"a": 1},
        "inputs": {"inv.csv": ce._file_digest(d / "run" / "inv.csv")},
    }
    (d / ce.MILP_SUBDIR / ce.MILP_BASIS_FILE).write_text(json.dumps(basis))
    (d / ce.SETTINGS_FILE).write_text(json.dumps({"a": 1}))
    assert ce._milp_stale("p", d) is False
    (d / ce.SETTINGS_FILE).write_text(json.dumps({"a": 2}))
    assert ce._milp_stale("p", d) is True
    (d / ce.SETTINGS_FILE).write_text(json.dumps({"a": 1}))
    (d / "run" / "inv.csv").write_text("changed")
    assert ce._milp_stale("p", d) is True
    (d / "run" / "inv.csv").unlink()  # a copied input that is gone
    assert ce._milp_stale("p", d) is True
    (d / ce.MILP_SUBDIR / ce.MILP_BASIS_FILE).write_text(
        "{"
    )  # a basis that cannot be read
    assert ce._milp_stale("p", d) is True


# ── guards ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fn",
    [
        ce._stale,
        ce._library_stale,
        ce._milp_stale,
        ce._grid_code_changed,
        ce._dips_out_of_date,
        site_bindings.stale_keys,
        associations._stale,
        assistant_tasks._stale,
    ],
)
def test_a_moved_check_asks_the_graph_and_keeps_no_comparison_of_its_own(fn):
    src = inspect.getsource(fn)
    assert "pin_stale" in src or "pin_broken" in src
    assert " != " not in src.replace("!= None", "")


@pytest.mark.parametrize(
    "module", ["time_aggregation_service", "adequacy.portfolio", "assistant_tools"]
)
def test_no_cache_key_reads_a_graph_node(module):
    import importlib

    src = inspect.getsource(importlib.import_module(f"services.{module}"))
    assert "provenance_graph" not in src


def test_the_outcomes_store_name_is_the_provenance_key():
    assert outcomes._PROVENANCE_STORE == provenance._KEY
