"""AS-17b (owner decision FILL-10-2): the sizing loop reads the project's PoC X/R.

AS-17 (#1035) stored `poc_xr` in the project's `metadata.json` settings and the grid-readiness
scorecard uses it. The sizing loop's Pst check takes `SizingInputs.xr`; a run started for a project
(the AS-10 intent comparison) now takes that X/R from the project when the request gives none, and
falls back to `flicker_inputs.DEFAULT_XR` (4, assumed) only when the project has none. A request's own
`inputs.xr` still wins. The runner is stubbed; no LP.
"""
from __future__ import annotations

import json

import pytest

from services.adequacy import sizing_loop_runner as slr
from services.adequacy.flicker_inputs import DEFAULT_XR
from services.compare import intent_jobs
from tests.test_intent_comparison_endpoint import INPUTS, URL, _wait
from tests.test_intent_table import _result


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr("services.adequacy.sizing_loop_runner._check_inputs", lambda n, inp: None)
    intent_jobs.clear()
    yield
    intent_jobs.clear()


@pytest.fixture
def seen_xr(monkeypatch):
    seen: list[float] = []

    def fake_run(network, config, inp, **kw):
        seen.append(inp.xr)
        return _result()

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    return seen


def _run(client, name, inputs=INPUTS) -> None:
    body = {"intents": [{"intent": "A"}, {"intent": "B"}], "critical_mw": 80.0, "inputs": inputs}
    r = client.post(URL.format(name), json=body)
    assert r.status_code == 202, r.text
    assert _wait(client, name)["status"] == "done"


def _enter_xr(client, name, xr) -> None:
    r = client.patch(f"/api/projects/{name}/settings", json={"poc_xr": xr})
    assert r.status_code == 200, r.text


def test_a_run_on_a_project_with_poc_xr_uses_it(client, api_project, seen_xr):
    name = api_project("xr-entered")
    _enter_xr(client, name, 2.5)
    _run(client, name)
    assert seen_xr == [2.5, 2.5]


def test_a_run_on_a_project_without_poc_xr_uses_the_default(client, api_project, seen_xr):
    _run(client, api_project("xr-unset"))
    assert seen_xr == [DEFAULT_XR, DEFAULT_XR]


def test_a_cleared_poc_xr_falls_back_to_the_default(client, api_project, seen_xr):
    name = api_project("xr-cleared")
    _enter_xr(client, name, 2.5)
    _enter_xr(client, name, None)
    _run(client, name)
    assert seen_xr == [DEFAULT_XR, DEFAULT_XR]


def test_the_requests_own_xr_wins_over_the_project(client, api_project, seen_xr):
    name = api_project("xr-request")
    _enter_xr(client, name, 2.5)
    _run(client, name, {**INPUTS, "xr": 7.0})
    assert seen_xr == [7.0, 7.0]


def test_a_stored_invalid_poc_xr_reads_as_unset(client, api_project, project_storage_dir, seen_xr):
    name = api_project("xr-invalid")
    path = project_storage_dir(name) / "metadata.json"
    meta = json.loads(path.read_text())
    meta["settings"] = {**(meta.get("settings") or {}), "poc_xr": 0}
    path.write_text(json.dumps(meta))
    _run(client, name)
    assert seen_xr == [DEFAULT_XR, DEFAULT_XR]


def test_the_422_input_check_sees_the_project_xr(client, api_project, monkeypatch):
    """The pre-start check runs on the same resolved inputs the runs get."""
    checked: list[float] = []
    monkeypatch.setattr(slr, "_check_inputs", lambda n, inp: checked.append(inp.xr))
    monkeypatch.setattr(slr, "run_sizing", lambda network, config, inp, **kw: _result())
    name = api_project("xr-check")
    _enter_xr(client, name, 2.5)
    _run(client, name)
    assert checked == [2.5]


@pytest.mark.parametrize("inputs, stored, xr", [
    ({"link": "l"}, 2.5, 2.5),                  # the project's
    ({"link": "l"}, None, None),                # none: the dataclass default applies
    ({"link": "l", "xr": 7.0}, 2.5, 7.0),       # the request's own
    ({"link": "l"}, 0, None),                   # a stored invalid value reads as none
])
def test_project_inputs(tmp_path, inputs, stored, xr):
    if stored is not None:
        (tmp_path / "metadata.json").write_text(json.dumps({"settings": {"poc_xr": stored}}))
    out = slr.project_inputs(inputs, tmp_path)
    assert out.get("xr") == xr
    assert {k: v for k, v in out.items() if k != "xr"} == {k: v for k, v in inputs.items() if k != "xr"}
    assert "xr" not in inputs or inputs["xr"] == 7.0             # the caller's dict is not changed
