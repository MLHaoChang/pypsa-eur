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


def test_a_pin_with_no_recorded_value_is_stale_never_fresh():
    """P3: the record exists but lacks the field (sha or fingerprint None)."""
    for read in (lambda: None, lambda: "x"):
        v = G.pin_verdict("x", [G.Pin("a", None, read)])
        assert v.state == "stale" and v.broken[0].reason == "not recorded"


def test_a_pin_edge_is_json_safe_on_the_wire_and_never_shows_its_callable():
    """P5: `why` and a broken edge emit the label and the recorded value only."""
    from services.provenance_graph import pins
    from services.provenance_graph.explain import _recorded
    from services.provenance_graph.types import Edge

    pin = G.Pin("lib", b"bytes", lambda: float("nan"))
    wire = _recorded(Edge("derived_from", "derived:x", "input:x/lib", pin))
    assert set(wire) == {"label", "recorded"} and wire["recorded"].startswith("sha256:")
    json.dumps(wire, allow_nan=False)
    broke = G.pin_verdict("x", [pin]).broken[0]
    json.dumps(broke.wire(), allow_nan=False)
    assert pins.shown(float("nan")) == "nan"
    w = G.why(_graph_with(pin), "derived:x")
    assert [type(s.via.recorded) for s in w.steps[1:]] == [G.Pin]


def _graph_with(pin):
    from services.provenance_graph.queries import Builder
    from services.provenance_graph.types import Edge, Node

    b = Builder(None)
    b.node(Node("derived", "derived:x"))
    b.node(Node("input", "input:x/lib"))
    b.edge(Edge("derived_from", "derived:x", "input:x/lib", pin))
    return b.build()


def test_a_compared_derived_from_edge_does_not_walk_into_its_dst():
    """P4: a pin is the comparison itself; the dst's own staleness is not asked. A derived_from edge with no
    record still walks (money's)."""
    from services.provenance_graph.queries import Builder, staleness
    from services.provenance_graph.types import Edge, Node, Unrecorded

    b = Builder(None)
    for nid, kind in (("top", "derived"), ("mid", "derived")):
        b.node(Node(kind, nid))
    b.edge(Edge("derived_from", "top", "mid", G.Pin("m", 1, lambda: 1)))
    b.edge(
        Edge("priced_through", "mid", "scale", Unrecorded("scale input not recorded"))
    )
    g = b.build()
    assert staleness(g, "mid").state == "stale"  # the dst is itself stale
    assert staleness(g, "top").state == "fresh"  # and the pin alone decides
    b.edge(Edge("derived_from", "top2", "mid"))
    assert (
        staleness(b.build(), "top2").state == "stale"
    )  # an unrecorded derived_from walks on


def test_a_derived_from_edge_with_another_record_compares_nothing():
    from services.provenance_graph.queries import Builder, staleness
    from services.provenance_graph.types import Edge, Node

    b = Builder(None)
    b.node(Node("derived", "t"))
    b.edge(Edge("derived_from", "t", "i", "not a pin"))
    assert staleness(b.build(), "t").state == "uncompared"


def test_no_pin_compares_nothing_and_reads_uncompared():
    """P1: never fresh; each caller maps it to today's answer."""
    assert G.pin_verdict("x", []).state == "uncompared"
    assert G.pin_stale("x", []) is False and G.pin_broken("x", []) == set()


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


def test_dips_with_no_units_is_an_empty_list(tmp_path, monkeypatch):
    nc = tmp_path / ce.NETWORK_FILE
    nc.write_bytes(b"net")
    monkeypatch.setattr(project_registry, "project_dir", lambda project: tmp_path)
    monkeypatch.setattr(ce, "_DIPS_INPUTS_CACHE", {(str(nc), ce._file_digest(nc)): {}})
    assert ce._dips_out_of_date("p", tmp_path, {}) == []


def test_a_manifest_that_lacks_a_hash_is_stale(tmp_path, monkeypatch):
    """P3: the record exists but has no `campus_sha256` / `network_sha256`."""
    d = _campus(tmp_path, monkeypatch)
    manifest = d / "run" / ce.cs.CAMPUS_MANIFEST
    for key in ("campus_sha256", "network_sha256"):
        full = json.loads(manifest.read_text())
        manifest.write_text(json.dumps({k: v for k, v in full.items() if k != key}))
        assert ce._stale("p", d) is True
        manifest.write_text(json.dumps(full))
    assert ce._stale("p", d) is False


def test_a_task_with_no_recorded_fingerprint_is_stale_and_one_with_no_brief_is_not(
    monkeypatch,
):
    monkeypatch.setattr(assistant_tasks, "_fingerprint", lambda: "fp")
    monkeypatch.setattr(assistant_tasks, "_requirements_moved", lambda t: False)
    assert (
        assistant_tasks._stale({"id": "t", "brief": {"x": 1}, "status": "planned"})
        is True
    )
    assert assistant_tasks._stale({"id": "t", "status": "planned"}) is False


def test_a_milp_basis_without_settings_or_inputs_is_stale(tmp_path, monkeypatch):
    d = _campus(tmp_path, monkeypatch)
    (d / ce.MILP_SUBDIR).mkdir()
    (d / ce.SETTINGS_FILE).write_text("{}")
    for basis in (
        {},
        {"settings": {}},
        {"settings": {}, "inputs": []},
        {"settings": None, "inputs": {}},
    ):
        (d / ce.MILP_SUBDIR / ce.MILP_BASIS_FILE).write_text(json.dumps(basis))
        assert ce._milp_stale("p", d) is True


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


@pytest.mark.parametrize(
    "now, changed", [("sha", False), ("other", True), (None, True)]
)
def test_the_grid_code_check_matches_the_old_one(tmp_path, monkeypatch, now, changed):
    monkeypatch.setattr(
        ce, "_profile_used", lambda run: {"profile": "p1", "sha256": "sha"}
    )
    monkeypatch.setattr(ce, "profile_fingerprint", lambda project, profile: now)
    assert ce._grid_code_changed("p", tmp_path) is changed
    monkeypatch.setattr(ce, "_profile_used", lambda run: None)
    assert ce._grid_code_changed("p", tmp_path) is False


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


# ── GK B's review of #1443 (K1–K5) ────────────────────────────────────────────


def _boom(*args, **kwargs):
    raise OSError("unreadable")


def test_an_unreadable_network_digest_reads_stale_where_it_used_to_raise(
    tmp_path, monkeypatch
):
    """K1 (fail-closed change): `_file_digest` of the saved network raising was an error, and is stale now."""
    d = _campus(tmp_path, monkeypatch)
    assert ce._stale("p", d) is False
    monkeypatch.setattr(ce, "_file_digest", _boom)
    assert ce._stale("p", d) is True


def test_an_unreadable_grid_code_reads_changed_where_it_used_to_raise(
    tmp_path, monkeypatch
):
    """K2 (fail-closed change): an OSError or YAML error while loading the profile was a 500; now it reads True."""
    monkeypatch.setattr(
        ce, "_profile_used", lambda run: {"profile": "p1", "sha256": "sha"}
    )
    monkeypatch.setattr(ce, "profile_fingerprint", _boom)
    assert ce._grid_code_changed("p", tmp_path) is True


def test_an_unreadable_binding_reads_stale_where_it_used_to_raise(monkeypatch):
    """K2: `site_bindings.stale_keys` and the association check, when the identity read raises."""
    monkeypatch.setattr(site_bindings, "_resolved_key", _boom)
    assert site_bindings.stale_keys(None, ["Generator:a"], {"Generator:a": "u"}) == {
        "Generator:a"
    }
    monkeypatch.setattr(component_identity, "get", _boom)
    assert associations._stale(None, "Generator", "g", "u1") is True


def test_the_requirements_thread_is_not_read_once_the_fingerprint_is_broken(
    monkeypatch,
):
    """K3 (P6): the first broken pin stops the check, so the thread is read only while the inputs still match."""
    calls = []
    monkeypatch.setattr(
        assistant_tasks, "_requirements_moved", lambda t: calls.append(1) or False
    )
    task = {"id": "t", "brief": {"x": 1}, "status": "planned", "fingerprint": "fp"}
    for current in ("other", None):
        monkeypatch.setattr(
            assistant_tasks, "_fingerprint", lambda current=current: current
        )
        assert assistant_tasks._stale(task) is True
    assert calls == []
    monkeypatch.setattr(assistant_tasks, "_fingerprint", lambda: "fp")
    assert assistant_tasks._stale(task) is False and calls == [1]


def test_duplicate_pin_labels_are_refused():
    """K5: `Builder.edge` keeps the first edge of a (kind, src, dst); a second label would be dropped."""
    with pytest.raises(ValueError, match="duplicate pin labels"):
        G.pin_verdict("x", [G.Pin("a", 1, lambda: 1), G.Pin("a", 2, lambda: 3)])


def test_why_reports_the_pin_edge_state_not_the_dst_state():
    """Non-blocking note: a compared edge's step reads what the pin said."""
    stale = _graph_with(G.Pin("lib", 1, lambda: 2))
    fresh = _graph_with(G.Pin("lib", 1, lambda: 1))
    assert [s.state for s in G.why(stale, "derived:x").steps] == ["stale", "stale"]
    assert [s.state for s in G.why(fresh, "derived:x").steps] == ["fresh", "fresh"]
