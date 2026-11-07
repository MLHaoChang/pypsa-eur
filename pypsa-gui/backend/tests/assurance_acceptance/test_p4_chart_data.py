"""A4.1/A4.3 (AA-05 S1): a chart draws only what the solver uses, and says
which solve it came from."""

# ruff: noqa: F811 -- imported shared fixtures.
import json

import pandas as pd
import pytest
from fastapi import HTTPException

from services import assurance_artifacts, chat_tools, upload_service, workflow_tools
from tests.assurance_acceptance.test_p4_artifacts import canonical_sha256
from tests.test_assurance_metrics import solved  # noqa: F401
from tests.test_workflow_tools import baseline  # noqa: F401

# Master's (1af1369a6a) hashes for the G1 p_nom chart: the stored format is unchanged.
MASTER_DATASET_SHA256 = (
    "d18bbada25bf34562a691f9b91221631dcce6ac6d55948a127cbf5055ada822d"
)
MASTER_RENDER_SPEC_SHA256 = (
    "245b2f59491374b1b5110169ebb9cc8d0a4e0f2e57a4ba4a24c5f025f43ded89"
)


def refused(*args):
    with pytest.raises(HTTPException) as caught:
        chat_tools.create_chart(*args)
    return caught.value


def with_series(ctx):
    n = ctx.network
    n.add("Generator", "G2", bus="B1", p_nom=10, p_max_pu=0.9)
    # G2's series overrides its static 0.9; G1 has no series (static 1.0).
    n.generators_t.p_max_pu = pd.DataFrame({"G2": [0.25, 0.5]}, index=n.snapshots)
    return n


def test_a_series_shadowed_static_value_is_never_drawn(baseline):
    with_series(baseline[0])
    error = refused("Generator", "p_max_pu", ["G1", "G2"])
    assert (
        error.status_code == 409 and error.detail["error_kind"] == "chart_data_partial"
    )
    assert "G1" in error.detail["message"]


def test_a_series_selection_draws_the_series(baseline):
    n = with_series(baseline[0])
    chart = chat_tools.create_chart("Generator", "p_max_pu", ["G2"])
    assert chart["manifest"]["dataset"]["index"] == [str(s) for s in n.snapshots]
    assert chart["manifest"]["dataset"]["values"] == [[0.25], [0.5]]


def test_an_all_static_selection_beside_other_series_is_drawn_static(baseline):
    with_series(baseline[0])
    chart = chat_tools.create_chart("Generator", "p_max_pu", ["G1"])
    assert chart["manifest"]["dataset"]["index"] == ["G1"]
    assert chart["manifest"]["dataset"]["values"] == [[1]]


def test_an_empty_series_frame_draws_the_static_value(baseline):
    n = baseline[0].network
    n.generators_t.p_max_pu = pd.DataFrame(index=n.snapshots)
    chart = chat_tools.create_chart("Generator", "p_max_pu", ["G1"])
    assert chart["manifest"]["dataset"]["values"] == [[1]]


def test_a_result_selection_missing_one_series_is_partial(solved, monkeypatch):
    n = solved.network
    # The result frame holds G1 only, under a current solve stamp, so only
    # the partial selection can refuse.
    n.add("Generator", "G9", bus="B1", p_nom=0)
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(n, solved.solver_state["solver_config"])
    n.generators_t.p = n.generators_t.p[["G1"]]
    # The freshness check already refuses a frame missing a column ...
    assert (
        refused("Generator", "p", ["G1", "G9"], "result").detail["error_kind"]
        == "chart_results_unavailable"
    )
    # ... and the partial guard holds behind it on its own.
    monkeypatch.setattr(chat_tools, "dispatch_status", lambda: {"state": "fresh"})
    error = refused("Generator", "p", ["G1", "G9"], "result")
    assert error.detail["error_kind"] == "chart_data_partial"


def test_a_non_finite_value_names_the_asset_and_snapshot(baseline):
    n = with_series(baseline[0])
    n.generators_t.p_max_pu.iloc[1, 0] = float("nan")
    error = refused("Generator", "p_max_pu", ["G2"])
    assert error.detail["error_kind"] == "chart_invalid_data"
    assert f"G2 at {n.snapshots[1]}" in error.detail["message"]


def test_a_result_chart_matches_the_hand_solved_dispatch(solved, client):
    # Hand oracle: one bus, one generator; it serves the load exactly.
    expected = [[5], [7]]
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    manifest = chart["manifest"]
    assert manifest["source_kind"] == "captured_result"
    assert manifest["dataset"] == {
        "index": [str(s) for s in solved.network.snapshots],
        "columns": ["G1"],
        "values": expected,
        "units": {"G1": "MW"},
    }
    assert manifest["scope"] == "current"
    meta = solved.network.meta
    assert manifest["solve_stamp"] == {
        "input": meta["assistant_solve_input_fingerprint"],
        "exact": meta["assistant_solve_exact_input_fingerprint"],
    }
    response = client.get(chart["download_url"])
    assert (
        response.status_code == 200 and response.headers["X-Chart-Scope"] == "current"
    )


def test_a_chart_from_an_earlier_solve_fails_verification(solved, client):
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    n = solved.network
    n.loads_t.p_set = pd.DataFrame({"L1": [6.0, 8.0]}, index=n.snapshots)
    assert n.optimize(solver_name="highs") == ("ok", "optimal")
    workflow_tools._record_solve_inputs(n, solved.solver_state["solver_config"])
    assert client.get(chart["download_url"]).status_code == 409
    with pytest.raises(HTTPException) as caught:
        chat_tools.build_delivery([chart["file_id"]])
    assert caught.value.status_code == 409


def test_an_input_chart_has_no_solve_stamp(baseline, client):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    manifest = chart["manifest"]
    assert manifest["scope"] == "current" and manifest["solve_stamp"] is None
    # H4: the existing manifest format is unchanged.
    assert manifest["dataset_sha256"] == MASTER_DATASET_SHA256
    assert manifest["render_spec_sha256"] == MASTER_RENDER_SPEC_SHA256
    assert manifest["validity"] == {
        "coverage": "captured_rows",
        "input_fingerprint": chart["provenance"]["input_fingerprint"],
        "limitations": [
            "Data/render/byte integrity; this does not certify engineering validity."
        ],
    }
    assert (
        client.get(chart["download_url"]).headers["X-Chart-Scope"] == "input_as_drawn"
    )


def reseal(baseline, chart, change):
    """Rewrite the sidecar AND its marker, as a store-level forger would."""
    root = baseline[2]
    sidecar = root / "assistant_artifacts" / (chart["file_id"] + ".json")
    record = json.loads(sidecar.read_text())
    change(record["manifest"])
    sidecar.write_text(json.dumps(record))
    meta_path = (
        upload_service.get_upload_path(
            "Tool Baseline", chart["file_id"], project_dir=root
        ).parent
        / "meta.json"
    )
    meta = json.loads(meta_path.read_text())
    meta["assurance_sha256"] = canonical_sha256(record)
    meta_path.write_text(json.dumps(meta))


def strip(manifest):
    del manifest["scope"], manifest["solve_stamp"]


@pytest.mark.parametrize("source", ["input", "result"])
def test_a_manifest_without_the_scope_keys_verifies_as_legacy(solved, client, source):
    args = ("Generator", "p_nom" if source == "input" else "p", ["G1"], source)
    chart = chat_tools.create_chart(*args)
    reseal((solved, None, chat_tools_root()), chart, strip)
    response = client.get(chart["download_url"])
    assert response.status_code == 200 and response.headers["X-Chart-Scope"] == "legacy"


def chat_tools_root():
    from services.assistant_tools import _project

    return _project()[1]


TAMPER = {
    "scope_historic": lambda m: m.update(scope="historic"),
    "scope_none": lambda m: m.update(scope=None),
    "one_key_only": lambda m: m.pop("solve_stamp"),
    "stamp_on_input": lambda m: m.update(solve_stamp={"input": "x", "exact": "y"}),
    "stamp_falsy": lambda m: m.update(solve_stamp={}),
}


@pytest.mark.parametrize("fault", sorted(TAMPER))
def test_a_tampered_scope_is_refused_not_downgraded(baseline, client, fault):
    chart = chat_tools.create_chart("Generator", "p_nom", ["G1"])
    reseal(baseline, chart, TAMPER[fault])
    assert client.get(chart["download_url"]).status_code == 409


RESULT_TAMPER = {
    "stamp_none": lambda m: m.update(solve_stamp=None),
    "stamp_other_solve": lambda m: m["solve_stamp"].update(exact="0" * 64),
    "stamp_not_the_inputs": lambda m: m["solve_stamp"].update(input="0" * 64),
    "stamp_extra_key": lambda m: m["solve_stamp"].update(run="x"),
    "stamp_value_not_text": lambda m: m["solve_stamp"].update(exact=123),
}


@pytest.mark.parametrize("fault", sorted(RESULT_TAMPER))
def test_a_tampered_result_stamp_is_refused(solved, client, fault):
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    reseal((solved, None, chat_tools_root()), chart, RESULT_TAMPER[fault])
    assert client.get(chart["download_url"]).status_code == 409


def test_an_unloaded_project_reads_as_not_rechecked(solved, monkeypatch):
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    root = chat_tools_root()
    meta = upload_service.get_upload_meta(
        "Tool Baseline", chart["file_id"], project_dir=root
    )
    data = upload_service.get_upload_path(
        "Tool Baseline", chart["file_id"], project_dir=root
    ).read_bytes()
    monkeypatch.setattr(assurance_artifacts, "_resident", lambda project_id: None)
    checked = assurance_artifacts.verify_chart(
        root, meta, data, chart["manifest"]["project_id"]
    )
    assert checked["scope_status"] == "solve_not_rechecked"


# A well-formed stamp of another solve is caught only against the loaded solve.
@pytest.mark.parametrize("fault", sorted(set(RESULT_TAMPER) - {"stamp_other_solve"}))
def test_a_tampered_result_stamp_is_refused_while_the_project_is_unloaded(
    solved, client, monkeypatch, fault
):
    # Without a loaded solve to compare, the stamp's own shape check must refuse.
    chart = chat_tools.create_chart("Generator", "p", ["G1"], source="result")
    reseal((solved, None, chat_tools_root()), chart, RESULT_TAMPER[fault])
    monkeypatch.setattr(assurance_artifacts, "_resident", lambda project_id: None)
    assert client.get(chart["download_url"]).status_code == 409


class _Network:
    meta = None


def test_a_network_without_meta_has_no_solve_stamp():
    assert assurance_artifacts.solve_stamp(_Network()) is None


@pytest.mark.parametrize("value", ["", 123, None])
def test_a_blank_or_non_text_fingerprint_is_no_solve_stamp(value):
    network = _Network()
    network.meta = {
        "assistant_solve_input_fingerprint": "a" * 64,
        "assistant_solve_exact_input_fingerprint": value,
    }
    assert assurance_artifacts.solve_stamp(network) is None
    network.meta["assistant_solve_exact_input_fingerprint"] = "b" * 64
    assert assurance_artifacts.solve_stamp(network) == {
        "input": "a" * 64,
        "exact": "b" * 64,
    }


class _Ctx:
    def __init__(self, project_uuid):
        self.project_uuid = project_uuid


@pytest.fixture
def registry(monkeypatch):
    from services.pypsa_service import PyPSAService

    state = {"contexts": {}, "request": None, "active": _Ctx("other")}
    monkeypatch.setattr(
        PyPSAService, "list_ids", classmethod(lambda cls: list(state["contexts"]))
    )
    monkeypatch.setattr(
        PyPSAService,
        "get_context",
        classmethod(lambda cls, key: state["contexts"].get(key)),
    )
    monkeypatch.setattr(
        PyPSAService, "get_request_context", classmethod(lambda cls: state["request"])
    )
    monkeypatch.setattr(
        PyPSAService, "get_active_context", classmethod(lambda cls: state["active"])
    )
    return state


def test_the_loaded_project_is_found_by_its_registry_key(registry):
    mine, theirs = _Ctx("p1"), _Ctx("p2")
    registry["contexts"].update({"org:p2": theirs, "org:p1": mine})
    assert assurance_artifacts._resident("p1") is mine
    assert assurance_artifacts._resident("p3") is None


def test_a_bare_project_key_is_found_too(registry):
    registry["contexts"]["p1"] = mine = _Ctx("p1")
    assert assurance_artifacts._resident("p1") is mine


def test_the_request_context_is_preferred_over_the_active_one(registry):
    registry["request"] = request = _Ctx("p1")
    registry["active"] = _Ctx("p1")
    assert assurance_artifacts._resident("p1") is request


def test_the_active_context_counts_only_for_its_own_project(registry):
    registry["active"] = active = _Ctx("p1")
    assert assurance_artifacts._resident("p1") is active
    assert assurance_artifacts._resident("p2") is None
