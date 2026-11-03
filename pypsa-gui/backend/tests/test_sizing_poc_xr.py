"""AS-17b (owner decision FILL-10-2): the sizing loop reads the project's PoC X/R.

AS-17 (#1035) stored `poc_xr` in the project's `metadata.json` settings and the grid-readiness
scorecard uses it. The sizing loop's Pst check takes `SizingInputs.xr`; a run started for a project
(the AS-10 intent comparison) now takes that X/R from the project when the request gives none, and
falls back to `flicker_inputs.DEFAULT_XR` (4, assumed) only when the project has none. A request's own
`inputs.xr` still wins. The loop's result carries `xr` and `xr_source`, and its Pst check's note names
the X/R (GK A plan conditions, AS-17b). The route tests stub the runner; no LP.
"""
from __future__ import annotations

import json
import math

import pytest

from services.adequacy import sizing_loop_runner as slr
from services.adequacy.flicker_inputs import DEFAULT_XR
from services.compare import intent_jobs
from services.adequacy import grid_readiness as G
from services.solver import sizing_cuts as sc
from tests.test_grid_readiness import PROFILE, _project, _rows
from tests.test_intent_comparison_endpoint import INPUTS, URL, _wait
from tests.test_intent_table import _result


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr("services.adequacy.sizing_loop_runner._check_inputs", lambda n, inp: None)
    intent_jobs.clear()
    yield
    intent_jobs.clear()


@pytest.fixture
def seen(monkeypatch):
    """(where, xr, xr_source) for the 422 pre-check and each intent's run."""
    out: list[tuple[str, float, str]] = []
    monkeypatch.setattr(slr, "_check_inputs", lambda n, inp: out.append(("check", inp.xr, inp.xr_source)))

    def fake_run(network, config, inp, **kw):
        out.append(("run", inp.xr, inp.xr_source))
        return _result()

    monkeypatch.setattr(slr, "run_sizing", fake_run)
    return out


def _run(client, name, inputs=INPUTS) -> None:
    body = {"intents": [{"intent": "A"}, {"intent": "B"}], "critical_mw": 80.0, "inputs": inputs}
    r = client.post(URL.format(name), json=body)
    assert r.status_code == 202, r.text
    assert _wait(client, name)["status"] == "done"


def _enter_xr(client, name, xr) -> None:
    r = client.patch(f"/api/projects/{name}/settings", json={"poc_xr": xr})
    assert r.status_code == 200, r.text


def _all(xr, source):
    """The pre-check and both intents' runs saw the same X/R (GK A condition 3)."""
    return [("check", xr, source), ("run", xr, source), ("run", xr, source)]


def test_a_run_on_a_project_with_poc_xr_uses_it_in_the_check_and_every_run(client, api_project, seen):
    name = api_project("xr-entered")
    _enter_xr(client, name, 2.5)
    _run(client, name)
    assert seen == _all(2.5, "project")


def test_a_run_on_a_project_without_poc_xr_uses_the_default(client, api_project, seen):
    _run(client, api_project("xr-unset"))
    assert seen == _all(DEFAULT_XR, "assumed")


def test_a_cleared_poc_xr_falls_back_to_the_default(client, api_project, seen):
    name = api_project("xr-cleared")
    _enter_xr(client, name, 2.5)
    _enter_xr(client, name, None)
    _run(client, name)
    assert seen == _all(DEFAULT_XR, "assumed")


def test_the_requests_own_xr_wins_over_the_project(client, api_project, seen):
    name = api_project("xr-request")
    _enter_xr(client, name, 2.5)
    _run(client, name, {**INPUTS, "xr": 7.0})
    assert seen == _all(7.0, "request")


def test_a_stored_invalid_poc_xr_reads_as_unset(client, api_project, project_storage_dir, seen):
    name = api_project("xr-invalid")
    path = project_storage_dir(name) / "metadata.json"
    meta = json.loads(path.read_text())
    meta["settings"] = {**(meta.get("settings") or {}), "poc_xr": 0}
    path.write_text(json.dumps(meta))
    _run(client, name)
    assert seen == _all(DEFAULT_XR, "assumed")


def test_a_sent_xr_source_is_422(client, api_project):
    """`xr_source` is the server's to set: a request carrying one is refused before any run starts."""
    name = api_project("xr-source")
    body = {"intents": [{"intent": "A"}, {"intent": "B"}], "critical_mw": 80.0,
            "inputs": {**INPUTS, "xr": 2.0, "xr_source": "project"}}
    r = client.post(URL.format(name), json=body)
    assert r.status_code == 422 and "xr_source" in r.text, r.text
    assert client.get(URL.format(name)).status_code == 404


@pytest.mark.parametrize("xr", [-1.0, 0.0, float("nan"), float("inf")])
def test_a_bad_request_xr_meets_the_one_input_check(tmp_path, monkeypatch, xr):
    """GK A condition 2: a request's X/R and the project's go through the same `_check_inputs` gate."""
    from tests.test_sizing_loop_runner import _inputs, _tiny

    monkeypatch.undo()                                    # the real input check

    resolved = slr.project_inputs({"xr": xr}, tmp_path)
    assert resolved == {"xr": xr, "xr_source": "request"}
    with pytest.raises(slr.SizingLoopRefusal, match="xr must be > 0"):
        slr._check_inputs(_tiny(), _inputs(**resolved))


@pytest.mark.parametrize("inputs, stored, xr, source", [
    ({"link": "l"}, 2.5, 2.5, "project"),
    ({"link": "l"}, None, None, "assumed"),        # the dataclass resolves None to DEFAULT_XR
    ({"link": "l", "xr": 7.0}, 2.5, 7.0, "request"),
    ({"link": "l", "xr": None}, 2.5, 2.5, "project"),   # an explicit null is no X/R
    ({"link": "l"}, 0, None, "assumed"),           # a stored invalid value reads as none
])
def test_project_inputs(tmp_path, inputs, stored, xr, source):
    if stored is not None:
        (tmp_path / "metadata.json").write_text(json.dumps({"settings": {"poc_xr": stored}}))
    before = dict(inputs)
    out = slr.project_inputs(inputs, tmp_path)
    assert (out["xr"], out["xr_source"]) == (xr, source)
    assert {k: v for k, v in out.items() if k not in ("xr", "xr_source")} == {"link": "l"}
    assert inputs == before                                   # the caller's dict is not changed


def _bare(**kw) -> slr.SizingInputs:
    """`SizingInputs` with placeholder fields: only the X/R fields matter here."""
    return slr.SizingInputs(link="l", envelope={}, ramp_mw_per_min=1.0, sk_mva=1.0, pst_limit=1.0,
                            transformer_library_mva=(1.0,), **kw)


def test_the_dataclass_resolves_and_gates_the_source():
    assert (_bare().xr, _bare().xr_source) == (DEFAULT_XR, "assumed")
    assert _bare(xr=2.0).xr_source == "request"
    with pytest.raises(slr.SizingLoopRefusal):
        _bare(xr=2.0, xr_source="operator")


def test_the_scorecard_and_the_loop_read_the_same_project_xr(tmp_path):
    """GK A condition 4: the grid-readiness flicker envelope and the loop's Pst check cannot drift."""
    d, n = _project(tmp_path, "eh_datacenter", dc_load={})
    for stored, xr in ((None, DEFAULT_XR), (2.5, 2.5)):
        if stored is not None:
            path = d / "metadata.json"
            meta = json.loads(path.read_text()) if path.is_file() else {}
            meta["settings"] = {**(meta.get("settings") or {}), "poc_xr": stored}
            path.write_text(json.dumps(meta))
        flicker = _rows(G.grid_readiness(d, n, PROFILE))["flicker"]
        loop = slr.SizingInputs(**slr.project_inputs({**INPUTS}, d))
        assert flicker["flicker"]["envelope"]["xr"] == loop.xr == xr


def test_the_pst_step_by_hand_at_xr_1_5_and_4():
    """GK A condition 5, with both residuals > 0. Swing 10 MW, P_sm 6 MW, tan φ 0.2, Q_dyn 0.5 Mvar:
    P_res = 10 - 6 = 4 MW, Q_res = 0.2·10 - 0.5 = 1.5 Mvar. Step = cos ψ·P_res + sin ψ·Q_res, ψ = atan(X/R):
      X/R 4:   (4 + 1.5·4)   / √17   = 10   / 4.123106 = 2.425356 MVA;
      X/R 1.5: (4 + 1.5·1.5) / √3.25 = 6.25 / 1.802776 = 3.466876 MVA.
    The real residual dominates here, so the lower X/R gives the larger step."""
    at4 = sc.pst_step_mva(10.0, 6.0, 0.5, 0.2, 4.0)
    at15 = sc.pst_step_mva(10.0, 6.0, 0.5, 0.2, 1.5)
    assert at4 == pytest.approx(10 / math.sqrt(17), rel=1e-12) == pytest.approx(2.425356, rel=1e-6)
    assert at15 == pytest.approx(6.25 / math.sqrt(3.25), rel=1e-12) == pytest.approx(3.466876, rel=1e-6)


@pytest.mark.parametrize("source, words", [
    ("request", "X/R 2.5 (given for this run)"),
    ("project", "X/R 2.5 (entered for the project)"),
])
def test_the_xr_note(source, words):
    assert slr.xr_note(_bare(xr=2.5, xr_source=source)) == words


def test_the_assumed_xr_note_says_so():
    assert slr.xr_note(_bare()) == "X/R 4 (assumed; typical grid X/R, replace with the network operator's value)"


@pytest.mark.parametrize("source", ["project", "assumed"])
def test_the_loop_result_and_its_pst_check_carry_the_xr(source):
    """A real (two-hour) loop: the result names the X/R its Pst check used, and the check's note says so."""
    from tests.test_sizing_loop_runner import _config, _inputs, _tiny

    kw = {"xr": 2.5, "xr_source": "project"} if source == "project" else {"xr": None}
    out = slr.run_sizing(_tiny(), _config(), _inputs(**kw))
    xr = 2.5 if source == "project" else DEFAULT_XR
    assert (out["xr"], out["xr_source"]) == (xr, source)
    pst = [c for r in out["rounds"] for c in r["checks"] if c["name"] == "pst"]
    assert pst and all(c["note"].startswith(f"X/R {xr:g} (") for c in pst)
