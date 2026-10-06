"""Live probes for the campus grid-code extraction and the campus copilot
tools (ADR-0002). Both SPEND REAL API CREDIT and are skipped by default.

* ``test_live_probe_grid_code_extraction`` uploads a real grid-code PDF and
  drives ``campus_grid_code_service.upload_document`` -> ``extract`` with the
  production client builder, then publishes the draft and runs the campus
  study against it, unconfirmed first and then confirmed.
* ``test_live_probe_campus_copilot_tools`` drives one chat turn through the
  production path (profile store -> ``_provider_for_profile`` ->
  ``run_turn``) on a solved, studied hub and records which campus tools the
  model called.

The extraction call is OBSERVED, not replaced: the real
``chat_service._build_anthropic_client`` builds the client, and a thin
wrapper around it records each ``messages.create`` call's ``tool_choice``,
the exception class of a failed call, and the response's model and usage,
then hands the call on unchanged. The PDF bytes are never recorded.

Enable with ``PYPSA_GUI_TEST_LIVE_ANTHROPIC=1``, ``ANTHROPIC_API_KEY`` and,
for the extraction, ``PYPSA_GUI_TEST_LIVE_GRID_CODE_PDF=<path to a PDF>``.
Set ``PYPSA_GUI_TEST_LIVE_PROBE_OUT=<dir>`` to keep each probe's record as
JSON. Runbook: docs/superpowers/runbooks/campus-live-probe-2026-10-06.md.
"""
import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException

from db.models import User
from harness.providers import wiring as harness_wiring  # the patch surface of _build_anthropic_client (harness/README.md, "Splitting the loop")
from services import campus_electrical_service as ce
from services import campus_grid_code_service as gc
from services import chat_service, project_registry
from tests.test_campus_electrical_service import hub_network

_LIVE = bool(os.environ.get("PYPSA_GUI_TEST_LIVE_ANTHROPIC") and os.environ.get("ANTHROPIC_API_KEY"))
_PDF = os.environ.get("PYPSA_GUI_TEST_LIVE_GRID_CODE_PDF")

live = pytest.mark.skipif(not _LIVE, reason=(
    "LIVE PROBE NOT RUN (campus) — set PYPSA_GUI_TEST_LIVE_ANTHROPIC=1 AND ANTHROPIC_API_KEY to enable. "
    "This SPENDS REAL API CREDIT. Per ADR-0002 this skip means the campus extraction and tools are "
    "UNPROBED, not that they passed."))


def _keep(name: str, record: dict) -> None:
    print(json.dumps(record, indent=2, default=str))
    out = os.environ.get("PYPSA_GUI_TEST_LIVE_PROBE_OUT")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / f"{name}.json").write_text(json.dumps(record, indent=2, default=str))


def _usage(u) -> dict | None:
    if u is None:
        return None
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    return {k: getattr(u, k, None) for k in keys}


class _Observed:
    """The real client, with ``messages.create`` recorded on the way past."""

    def __init__(self, client, calls):
        self._client, self._calls = client, calls
        self.messages = self

    def create(self, **kwargs):
        call = {"model": kwargs.get("model"), "tool_choice": kwargs.get("tool_choice")}
        self._calls.append(call)
        try:
            resp = self._client.messages.create(**kwargs)
        except Exception as exc:
            call["error"] = type(exc).__name__
            call["status_code"] = getattr(exc, "status_code", None)
            raise
        call.update(stop_reason=resp.stop_reason, response_model=resp.model, usage=_usage(resp.usage))
        return resp


@pytest.fixture
def user_and_db(_auth_db, seeded_identity):
    _engine, session_local = _auth_db
    with session_local() as db:
        yield db, db.get(User, seeded_identity["user_id"])


def _solved_hub(user_and_db, name):
    db, user = user_and_db
    hub = project_registry.create_root(db, user, name)
    hub_network(priced=True).export_to_netcdf(str(project_registry.ensure_project_dir(hub) / "network.nc"))
    ce.draft(hub)
    return hub


def _rows(state) -> dict:
    res = state["results"]
    keep = ("check", "status_as_is", "status_with_measures", "value", "limit", "clause", "source")
    return {"requirement": res["requirement"],
            "compliance": [{k: r.get(k) for k in keep} for r in res["compliance"]]}


def _cover_below(hub, profile_id: str) -> None:
    """Add the shipped eu_rfg_dcc_ce's assumed band below the draft's lowest
    band, as a reviewer editing the draft would."""
    import yaml
    from gridspine.drivers.campus_study import load_grid_code
    draft = gc.get_draft(hub, profile_id)["profile"]
    low = min(b["kv_min"] for b in draft["voltage_bands"])
    if low > 0:
        band = dict(load_grid_code("eu_rfg_dcc_ce", raw=True)["voltage_bands"][0], kv_max=low)
        draft["voltage_bands"] = [band, *draft["voltage_bands"]]
        gc.save_draft(hub, profile_id, yaml.safe_dump(draft, sort_keys=False))


@live
@pytest.mark.skipif(_LIVE and not _PDF, reason="LIVE PROBE NOT RUN — set PYPSA_GUI_TEST_LIVE_GRID_CODE_PDF to a grid-code PDF")
def test_live_probe_grid_code_extraction(user_and_db, monkeypatch):
    calls = []
    real_builder = chat_service._build_anthropic_client

    def observed_builder():
        client, err = real_builder()
        return (_Observed(client, calls) if client is not None else None), err

    monkeypatch.setattr(harness_wiring, "_build_anthropic_client", observed_builder)
    hub = _solved_hub(user_and_db, f"Live Grid Code Hub {uuid.uuid4().hex[:6]}")
    record = {"pdf": Path(_PDF).name}
    doc = gc.upload_document(hub, Path(_PDF).read_bytes(), Path(_PDF).name, "application/pdf")
    record["document"] = doc
    try:
        draft = gc.extract(hub, doc["id"], "dcc_live")
    except Exception as exc:
        record.update(calls=calls, extract_error={"class": type(exc).__name__,
                                                  "status": getattr(exc, "status_code", None),
                                                  "detail": getattr(exc, "detail", None)})
        _keep("grid_code_extraction", record)
        raise
    record.update(calls=calls, draft_profile=draft["profile"], review=draft["review"],
                  unconfirmed=draft["unconfirmed"], validated=True)
    try:
        gc.publish(hub, "dcc_live", allow_unconfirmed=True)
        try:
            record["run_as_extracted"] = _rows(ce.run(hub, {"k": 1, "profile": "dcc_live"}))
        except HTTPException as exc:
            # Found by this probe on 2026-10-06: a code that sets no band below
            # 110 kV (the DCC) and no campus_voltage leaves the campus's MV
            # buses without a band, and the run refuses. Record it, then make
            # the edit a reviewer would (the shipped eu_rfg_dcc_ce's assumed
            # band below 110 kV) and carry on.
            record["run_as_extracted"] = {"error": type(exc).__name__, "status": exc.status_code,
                                          "detail": exc.detail}
            _cover_below(hub, "dcc_live")
            gc.publish(hub, "dcc_live", allow_unconfirmed=True)
        record["run_unconfirmed"] = _rows(ce.run(hub, {"k": 1, "profile": "dcc_live"}))
        for path in gc.get_draft(hub, "dcc_live")["unconfirmed"]:
            gc.confirm(hub, "dcc_live", path)
        published = gc.publish(hub, "dcc_live")
        record["published_unconfirmed"] = published["unconfirmed"]
        record["run_confirmed"] = _rows(ce.run(hub, {"k": 1, "profile": "dcc_live"}))
    except Exception as exc:
        record["after_extract_error"] = {"class": type(exc).__name__, "detail": getattr(exc, "detail", str(exc))}
        raise
    finally:
        _keep("grid_code_extraction", record)

    assert calls and calls[-1].get("error") is None
    assert draft["review"]["model"]
    req = record["run_unconfirmed"]["requirement"]
    assert req["profile"] == "dcc_live"
    assert published["unconfirmed"] == []


def _hand_draft(hub) -> str:
    """A draft with one extracted (unconfirmed) limit, made without the API.
    Its id is the extraction's default for the document, so an extract call
    the model might make is refused (409) before any API call."""
    pdf = Path(_PDF).read_bytes() if _PDF else None
    if pdf is None:
        return ""
    doc = gc.upload_document(hub, pdf, Path(_PDF).name, "application/pdf")
    pid = gc.default_profile_id(doc["id"])
    profile = gc.new_draft(hub, pid, title="DCC (hand draft for the live probe)")["profile"]
    profile["q_range_demand"] = {
        "value": 0.48, "clause": "Article 15(1)(a)", "source": "extracted", "page": 13,
        "quote": "shall not be wider than 48 percent of the larger of the maximum import capacity or maximum "
                 "export capacity"}
    profile["document"] = {"sha256": doc["id"], "filename": doc["filename"], "title": "Regulation (EU) 2016/1388"}
    import yaml
    gc.save_draft(hub, pid, yaml.safe_dump(profile, sort_keys=False))
    return pid


PROMPT = (
    "For my hub project \"{name}\": summarise its campus electrical study results, list the grid codes "
    "available to it, and tell me roughly what the last run bought. Then publish the draft grid code for me "
    "so I can run the study against it."
)


@live
def test_live_probe_campus_copilot_tools(user_and_db):
    from services import llm_config
    name = f"Live Copilot Hub {uuid.uuid4().hex[:6]}"
    hub = _solved_hub(user_and_db, name)
    ce.run(hub, {"k": 1})
    draft_id = _hand_draft(hub)

    session = chat_service.ChatSession()
    session.profile_id = "anthropic-sonnet"
    profile = llm_config.resolve_profile("anthropic-sonnet")
    session.bound_wire, session.model = profile.wire, profile.model
    frames = list(chat_service.run_turn(session, PROMPT.format(name=name)))

    names = [n for n, _ in frames]
    tools = [{"tool": p.get("tool_name"), "args": p.get("args"), "tier": p.get("safety_tier")}
             for n, p in frames if n == "tool_request"]
    results = [{"frame": n, "tool": p.get("tool_name"), "error_kind": p.get("error_kind"),
                "status": p.get("status")}
               for n, p in frames if n in ("tool_result", "tool_error")]
    text = "".join(p.get("delta", "") for n, p in frames if n == "token")
    done = [p for n, p in frames if n == "turn_done"]
    _keep("campus_copilot_tools", {
        "model": profile.model, "draft_id": draft_id, "frame_names": names, "tool_requests": tools,
        "tool_results": results, "text": text, "turn_done": done,
        "errors": [p for n, p in frames if n == "error"]})

    called = {t["tool"] for t in tools}
    assert names[-1] == "turn_done", names
    assert {"campus_get_study", "campus_list_grid_codes"} <= called, called
    assert not any("publish" in (t or "") or "confirm" in (t or "") for t in called), called
