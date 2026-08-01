"""
The three asset-results chat tools: schema/signature parity, the statistics
default, and the raw-mode cap.
"""
import json

import pandas as pd
import pytest

from services import chat_tools as T
from services import chat_tools_schema as S
from tests.conftest import build_network


def _schema(name: str) -> dict:
    return next(t for t in S.TOOLS if t["name"] == name)


ASSET_TOOLS = ("get_asset_results", "ui_open_asset_detail", "export_asset_results")


def test_every_asset_tool_is_registered_and_dispatchable():
    for name in ASSET_TOOLS:
        assert _schema(name), f"{name} missing from TOOLS"
        assert callable(T.DISPATCHERS[name]), f"{name} missing from DISPATCHERS"


def test_schema_optional_fields_all_have_python_defaults():
    """
    Every field NOT in `required` must have a default, or the dispatcher
    raises TypeError the moment the model correctly omits it.
    """
    import inspect
    for name in ASSET_TOOLS:
        sch = _schema(name)["input_schema"]
        sig = inspect.signature(T.DISPATCHERS[name])
        for field in sch["properties"]:
            if field in sch.get("required", []):
                continue
            assert field in sig.parameters, f"{name}: schema field {field} not a param"
            assert sig.parameters[field].default is not inspect.Parameter.empty, \
                f"{name}: optional field {field} has no Python default"


def test_results_tab_enum_offers_the_new_tab():
    assert "asset" in S.RESULTS_TAB_ENUM


def test_default_resolution_is_statistics_not_raw_arrays(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch", metrics=["p"])
    assert out["resolution"] == "stats"
    assert "series" not in out
    st = out["series_stats"]["p"]
    for key in ("min", "max", "mean", "sum", "p50", "p95", "peak_at",
                "zero_count", "sparkline"):
        assert key in st, f"missing statistic {key}"
    assert len(st["sparkline"]) <= 48


def test_zero_count_is_the_unweighted_twin_of_the_weighted_zero_hours_scalar(
        install_network):
    """
    `_series_stats`'s `zero_count` is a bare snapshot count; the SAME
    response's `scalars["zero_hours"]` (the registry metric, computed via
    `ctx.weights`) is the weighted hour count. Before the rename both fields
    were called `zero_hours` — identical names, different values, in the
    same payload. A non-default weighting makes the two numbers diverge, so
    this network carries `snapshot_weightings.generators = 3.0` and forces
    exactly one zero-output snapshot for `solar` via a `p_max_pu` dip.
    """
    n = build_network(solve=False, gens_weight=3.0)
    n.generators_t.p_max_pu = pd.DataFrame(
        {"solar": [0.6, 0.0, 0.6, 0.6]}, index=n.snapshots)
    n.optimize(solver_name="highs")
    install_network(n)

    out = T.get_asset_results("Generator", "solar", category="dispatch")
    zero_snapshots = sum(
        1 for v in n.generators_t.p["solar"] if abs(float(v)) < 1e-9)
    assert zero_snapshots == 1, "fixture must produce exactly one zero snapshot"

    assert out["series_stats"]["p"]["zero_count"] == zero_snapshots
    assert out["scalars"]["zero_hours"] == pytest.approx(zero_snapshots * 3.0)
    assert out["series_stats"]["p"]["zero_count"] != out["scalars"]["zero_hours"]


def test_the_default_response_stays_small_enough_to_be_worth_sending(install_network):
    """
    An hourly year x 10 metrics is ~87k numbers. The default must not be
    anywhere near that.
    """
    n = build_network(solve=True)
    install_network(n)
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p", "curtailment", "capacity_factor"])
    assert len(json.dumps(out)) < 8_000


def test_raw_resolution_returns_arrays_and_flags_truncation(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p"], resolution="raw", max_rows=2)
    assert out["resolution"] == "raw"
    assert len(out["series"]["p"]) == 2
    assert out["truncated"] is True
    assert out["n_total"] == 4
    assert "export_asset_results" in out["note"]


def test_raw_resolution_is_not_truncated_when_it_fits(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p"], resolution="raw", max_rows=100)
    assert out["truncated"] is False


def test_blocked_metrics_are_reported_with_their_reason(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch",
                              metrics=["p", "status"])
    unavailable = {u["id"]: u for u in out["unavailable"]}
    assert "status" in unavailable
    assert unavailable["status"]["status"] == "blocked"
    assert "committable" in unavailable["status"]["reason"]


def test_scalars_are_always_returned_for_the_category(install_network):
    install_network(build_network(solve=True))
    out = T.get_asset_results("Generator", "gas", category="dispatch")
    assert out["scalars"]["energy_mwh"] is not None


def test_ui_tool_emits_a_typed_ui_event_and_never_mutates():
    ev = T.ui_open_asset_detail("Generator", "Gas 1", category="dispatch",
                                metrics=["p"], mode="duration", chart=True)
    assert ev["_ui_event"] is True
    assert ev["kind"] == "open_asset_detail"
    assert ev["component_class"] == "Generator"
    assert ev["name"] == "Gas 1"
    assert ev["metrics"] == ["p"]
    assert ev["chart"] is True


def test_ui_tool_omits_unset_optionals_so_the_panel_keeps_its_state():
    ev = T.ui_open_asset_detail("Generator", "Gas 1")
    assert "category" not in ev and "metrics" not in ev and "mode" not in ev


def test_export_tool_writes_an_upload_the_chat_panel_can_offer(
    tmp_projects_dir, install_network,
):
    # export_asset_results persists into the ACTIVE PROJECT's uploads/ dir
    # (via the shared `_save_agent_export` writer), so — same recipe as
    # `install_with_uploads` in test_chat_upload_tools.py — a project name
    # must be bound AND the directory must exist on disk. The brief's
    # original version of this test called `install_network(build_network(
    # solve=True))` with no name, which leaves `_loaded_project` at None
    # (asserted by `install_network`'s own docstring + `_reset_backend_state`
    # running before every test) and the tool would raise
    # HTTPException(400, "No project is loaded ...") before ever reaching
    # the assertions below.
    n = build_network(solve=True)
    install_network(n, name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    out = T.export_asset_results("Generator", "gas", scope="view",
                                 category="dispatch", metrics=["p"])
    assert out["filename"].endswith(".xlsx")
    assert out["kind"] == "agent_export"
    assert out["bytes"] > 0


def test_export_tool_rejects_an_unknown_asset(install_network):
    install_network(build_network(solve=True))
    with pytest.raises(Exception):
        T.export_asset_results("Generator", "nope")
