"""
The nine adequacy / solution-FMEA chat tools.

Covers what the reliability surface needs and the generic registry tests
cannot see:

  * registration parity (TOOLS / DISPATCHERS / TOOL_ROUTES) and the
    optional-field-has-a-Python-default pitfall;
  * safety-tier classification — a study starter that lands on the read tier
    would run minutes of LP solves with no confirmation card;
  * the 204 → `{"status": "no_data", …}` mapping. Every GET here answers 204
    when nothing has been computed, and a bare `Response` would reach the
    model as "<Response object at 0x…>";
  * the guard paths of the four starters, none of which may publish a worker
    thread;
  * the fidelity caveats in the system prompt, which are the difference
    between reporting a screening proxy and reporting a standard.
"""
from __future__ import annotations

import inspect
import json

import pytest
from fastapi import HTTPException

from services import chat_service
from services import chat_tools as T
from services import chat_tools_schema as S
from tests.conftest import build_network


ADEQUACY_TOOLS = (
    "get_adequacy_results",
    "get_fmea_worksheet",
    "get_stress_scenarios",
    "run_fmea_sweep",
    "run_frontier_study",
    "run_mc_study",
    "run_coupling_loop",
    "run_margin_loop",
    "abort_adequacy_study",
)

# Kinds whose emptiness is a property of SESSION STATE (nothing has been run,
# or the last solve stashed nothing), so a fresh backend answers 204 for every
# one of them. `copt`, `fmea_modes` and `mc_elcc_candidates` are computed from
# the network instead and may legitimately return rows here, so they are
# exercised by the serialisability test below rather than pinned to no_data.
STATE_BACKED_KINDS = (
    "fmea_sweep", "frontier", "mc", "coupling_loop", "margin_loop",
    "adequacy", "reserve_margin",
)


def _schema(name: str) -> dict:
    return next(t for t in S.TOOLS if t["name"] == name)


# ── Registration ───────────────────────────────────────────────────────────


def test_every_adequacy_tool_is_registered_and_dispatchable():
    for name in ADEQUACY_TOOLS:
        assert _schema(name), f"{name} missing from TOOLS"
        assert callable(T.DISPATCHERS[name]), f"{name} missing from DISPATCHERS"
        assert name in S.TOOL_ROUTES, f"{name} missing from TOOL_ROUTES"


def test_schema_optional_fields_all_have_python_defaults():
    """A field the model correctly omits must not raise TypeError."""
    for name in ADEQUACY_TOOLS:
        sch = _schema(name)["input_schema"]
        sig = inspect.signature(T.DISPATCHERS[name])
        for field in sch["properties"]:
            if field in sch["required"]:
                continue
            assert field in sig.parameters, (
                f"{name}: schema field {field!r} is not a parameter")
            assert sig.parameters[field].default is not inspect.Parameter.empty, (
                f"{name}: optional field {field!r} has no Python default")


def test_schema_required_fields_have_no_python_default():
    """The mirror: a required field with a default hides a missing argument."""
    for name in ADEQUACY_TOOLS:
        sig = inspect.signature(T.DISPATCHERS[name])
        for field in _schema(name)["input_schema"]["required"]:
            assert sig.parameters[field].default is inspect.Parameter.empty, (
                f"{name}: required field {field!r} has a Python default")


@pytest.mark.parametrize(
    "name,expected",
    [
        ("get_adequacy_results", "read"),
        ("get_fmea_worksheet", "read"),
        ("get_stress_scenarios", "read"),
        ("run_fmea_sweep", "execution"),
        ("run_frontier_study", "execution"),
        ("run_mc_study", "execution"),
        ("run_coupling_loop", "execution"),
        ("run_margin_loop", "execution"),
        ("abort_adequacy_study", "destructive"),
    ],
)
def test_safety_tiers(name, expected):
    assert chat_service._safety_tier_for(name) == expected


def test_every_study_starter_is_confirmation_gated():
    """Each starter is minutes of solves — none may skip the card."""
    for name in ("run_fmea_sweep", "run_frontier_study", "run_mc_study",
                 "run_coupling_loop", "run_margin_loop",
                 "abort_adequacy_study"):
        assert chat_service._safety_tier_for(name) in chat_service.DESTRUCTIVE_TIERS


def test_abort_enum_covers_exactly_the_threaded_studies():
    """A read-only surface has no worker thread and must not be abortable."""
    assert set(S.ADEQUACY_STUDY_ENUM) == set(T._ADEQUACY_ABORT_HANDLER_NAMES)
    assert set(S.ADEQUACY_STUDY_ENUM) <= set(S.ADEQUACY_KIND_ENUM)
    for read_only in ("copt", "fmea_modes", "adequacy", "reserve_margin",
                      "mc_elcc_candidates"):
        assert read_only not in S.ADEQUACY_STUDY_ENUM


# ── Kind → handler / route mapping ─────────────────────────────────────────


def test_every_kind_resolves_to_a_real_handler():
    for kind in S.ADEQUACY_KIND_ENUM:
        assert callable(T._resolve_adequacy_handler(kind))


def test_every_kind_handler_takes_no_arguments():
    """The dispatcher calls each handler bare; a new query param would 500."""
    for kind in S.ADEQUACY_KIND_ENUM:
        sig = inspect.signature(T._resolve_adequacy_handler(kind))
        assert not [
            p for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
        ], f"{kind}: handler now takes a required argument"


def test_no_adequacy_handler_declares_an_unresolved_dependency():
    """
    These tools call their route handlers BARE — no `_route`, because none of
    the adequacy routes takes db/user/session. If one grows a `Depends`, the
    raw sentinel reaches the handler body and dies deep inside it (the same
    failure `_route`'s own guard exists to prevent). Fail here instead.
    """
    from fastapi import params as fastapi_params
    from routers import adequacy_worksheet, results as results_router

    handlers = (
        [T._resolve_adequacy_handler(k) for k in S.ADEQUACY_KIND_ENUM]
        + [T._resolve_adequacy_handler(s, T._ADEQUACY_ABORT_HANDLER_NAMES)
           for s in S.ADEQUACY_STUDY_ENUM]
        + [results_router.post_fmea_sweep, results_router.post_frontier,
           results_router.post_mc, results_router.post_coupling_loop,
           results_router.post_margin_loop]
    )
    for handler in handlers:
        for pname, param in inspect.signature(handler).parameters.items():
            assert not isinstance(param.default, fastapi_params.Depends), (
                f"{handler.__name__} now declares dependency {pname!r} — the "
                f"chat tool calls it bare and would pass the raw sentinel"
            )

    # The two sidecar reads DO take a `Depends`, and resolve it explicitly
    # through `_authorized_project` — assert that is still the shape.
    for handler in (adequacy_worksheet.get_worksheet,
                    adequacy_worksheet.get_stress_scenarios):
        assert isinstance(
            inspect.signature(handler).parameters["project"].default,
            fastapi_params.Depends,
        )


def test_path_lookup_handles_the_mc_elcc_outlier():
    assert T.adequacy_path_for("mc_elcc_candidates") == (
        "/api/results/mc/elcc_candidates")
    for kind in S.ADEQUACY_KIND_ENUM:
        if kind == "mc_elcc_candidates":
            continue
        assert T.adequacy_path_for(kind) == f"/api/results/{kind}"


def test_every_kind_has_a_no_data_hint():
    for kind in S.ADEQUACY_KIND_ENUM:
        assert T._ADEQUACY_NO_DATA_HINTS.get(kind), f"{kind} has no hint"


def test_unknown_kind_is_refused_with_400():
    with pytest.raises(HTTPException) as exc:
        T.get_adequacy_results("lole")
    assert exc.value.status_code == 400
    assert "copt" in str(exc.value.detail)


def test_unknown_study_is_refused_with_400():
    with pytest.raises(HTTPException) as exc:
        T.abort_adequacy_study("copt")
    assert exc.value.status_code == 400


# ── The 204 mapping ────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", STATE_BACKED_KINDS)
def test_unrun_study_reports_no_data_not_a_response_object(kind, install_network):
    install_network(build_network())
    out = T.get_adequacy_results(kind)
    assert isinstance(out, dict), f"{kind} leaked a {type(out).__name__}"
    assert out["status"] == "no_data"
    assert out["kind"] == kind
    assert out["message"] == T._ADEQUACY_NO_DATA_HINTS[kind]


def test_every_kind_serialises_to_json(install_network):
    """
    What the chat layer actually does to a tool result. A `Response` survives
    `default=str` as a repr — the assertion is that no result does.
    """
    install_network(build_network())
    for kind in S.ADEQUACY_KIND_ENUM:
        out = T.get_adequacy_results(kind)
        body = json.dumps(out, default=str)
        assert "Response object" not in body, f"{kind} serialised as a Response"


def test_payload_passes_a_real_result_through_untouched():
    payload = {"status": "done", "rows": [{"mode": "L1"}]}
    assert T._adequacy_payload("fmea_sweep", payload) is payload


# ── Study starters: the guard paths (no worker thread may start) ───────────


def _assert_idle(kind: str) -> None:
    """Nothing was published on the study surface."""
    out = T.get_adequacy_results(kind)
    assert out.get("status") == "no_data", f"{kind} published a study record"


@pytest.mark.parametrize("tool,kind", [
    (T.run_fmea_sweep, "fmea_sweep"),
    (T.run_frontier_study, "frontier"),
])
def test_solving_studies_refuse_without_voll(tool, kind, install_network):
    install_network(build_network())
    with pytest.raises(HTTPException) as exc:
        tool()
    assert exc.value.status_code == 422
    assert "VOLL" in str(exc.value.detail) or "VoLL" in str(exc.value.detail)
    _assert_idle(kind)


@pytest.mark.parametrize("tool,kind", [
    (T.run_coupling_loop, "coupling_loop"),
    (T.run_margin_loop, "margin_loop"),
])
def test_loops_refuse_a_non_positive_target(tool, kind, install_network):
    install_network(build_network())
    with pytest.raises(HTTPException) as exc:
        tool(target_lole_h=0)
    assert exc.value.status_code == 422
    assert "target_lole_h" in str(exc.value.detail)
    _assert_idle(kind)


def test_loops_refuse_an_out_of_range_restore(install_network):
    install_network(build_network())
    with pytest.raises(HTTPException) as exc:
        T.run_coupling_loop(target_lole_h=3.0, restore="somewhere_else")
    assert exc.value.status_code == 422
    _assert_idle("coupling_loop")


def test_restore_enum_matches_what_the_route_accepts():
    assert S.ADEQUACY_RESTORE_ENUM == ["base", "final"]


def test_mc_refuses_a_non_positive_draw_count(install_network):
    install_network(build_network())
    with pytest.raises(HTTPException) as exc:
        T.run_mc_study(draws=0)
    assert exc.value.status_code == 422
    _assert_idle("mc")


def test_abort_of_a_never_run_study_is_404(install_network):
    install_network(build_network())
    for study in S.ADEQUACY_STUDY_ENUM:
        with pytest.raises(HTTPException) as exc:
            T.abort_adequacy_study(study)
        assert exc.value.status_code == 404, study


# ── Per-project sidecars ───────────────────────────────────────────────────


def test_worksheet_and_stress_scenarios_read_through_authorization(api_project):
    name = api_project("adequacy-demo")

    sheet = T.get_fmea_worksheet(name)
    assert "manual_rows" in sheet and "overlays" in sheet

    stress = T.get_stress_scenarios(name)
    assert stress["scenarios"] == []


def test_sidecar_tools_refuse_an_unknown_project(api_project):
    api_project("adequacy-demo")
    with pytest.raises(HTTPException) as exc:
        T.get_fmea_worksheet("no-such-project")
    assert exc.value.status_code in (403, 404)


# ── System prompt ──────────────────────────────────────────────────────────


def test_system_prompt_carries_the_fidelity_caveats():
    prompt = chat_service._build_system_prompt(chat_service.ChatSession())
    assert "MET MARGIN IS NOT A MET RELIABILITY TARGET" in prompt
    lowered = prompt.lower()
    for fragment in ("lp_proxy", "screening", "horizon-basis hours",
                     "get_adequacy_results"):
        assert fragment.lower() in lowered, f"missing prompt guidance: {fragment}"
