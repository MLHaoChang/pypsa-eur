# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02b: a staged template's connection limit per investment period,
the energization schedule ``links_t["eh_energization_mw"]``. One writer
(`energization.set_period_mw`, behind ``PUT /api/projects/{name}/flow/energization``)
sets one period flat; a period that varies is refused with its range, never
scaled (GK A W1-W6, GK B E1-E7)."""

from __future__ import annotations

import json
import pathlib
import threading
import types

import numpy as np
import pandas as pd
import pypsa
import pytest
from pandas.testing import assert_frame_equal

from project_templates.eh_templates import build_eh_datacenter, build_eh_datacenter_staged
from services.adequacy import energization as E

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "project_templates"
LINK = "grid_import"


def _staged():
    return build_eh_datacenter_staged()


def _single(values=(20.0,) * 4) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=len(values), freq="h"))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Link", LINK, bus0="grid", bus1="site", p_nom=40.0)
    n.add("Link", "other", bus0="grid", bus1="site", p_nom=10.0)
    n.links_t[E.ATTR] = pd.DataFrame({LINK: list(values)}, index=n.snapshots)
    return n


def _refused(n, kind, link, period, mw):
    before = E.energization_frame(n)
    before = None if before is None else before.copy()
    with pytest.raises(E.EnergizationEditError) as exc:
        E.set_period_mw(n, link, period, mw)
    assert exc.value.error_kind == kind
    after = E.energization_frame(n)
    if before is None:
        assert after is None                                  # never created (W3)
    else:
        assert_frame_equal(after, before)                     # W4: untouched
    return exc.value.message


# ── the writer ───────────────────────────────────────────────────────────────


def test_one_period_is_set_flat_and_no_other_moves():
    n = _staged()
    frame = E.energization_frame(n)
    before = frame.copy()
    out = E.set_period_mw(n, LINK, 2030, 30.0)
    assert out["period"] == "2030" and out["mw"] == 30.0 and out["previous_mw"] == 55.0
    assert out["note"] is None and len(out["series"]) == (frame.index.get_level_values(0) == 2030).sum()
    after = E.energization_frame(n)
    assert after is frame                                     # edited in place
    assert after.index.equals(before.index) and after.dtypes.equals(before.dtypes)
    assert (after.loc[2030, LINK] == 30.0).all()
    for p in (2027, 2035):
        assert_frame_equal(after.loc[[p]], before.loc[[p]])   # not every period
    assert E.by_period(n, LINK) == {"by_period": {"2027": 15.0, "2030": 30.0, "2035": 55.0}}


def test_the_period_may_be_given_as_its_digits():
    n = _staged()
    assert E.set_period_mw(n, LINK, "2035", 40)["period"] == "2035"
    assert (E.energization_frame(n).loc[2035, LINK] == 40.0).all()


@pytest.mark.parametrize("mw", [True, False, "30", None, [30.0]])
def test_a_value_that_is_not_a_number_is_refused(mw):
    assert _refused(_staged(), "energization_invalid_mw", LINK, 2030, mw) == E.NOT_A_NUMBER


@pytest.mark.parametrize("mw", [float("nan"), float("inf"), -float("inf"), np.float64("nan")])
def test_a_value_that_is_not_finite_is_refused(mw):
    assert _refused(_staged(), "energization_invalid_mw", LINK, 2030, mw) == E.NOT_A_NUMBER


@pytest.mark.parametrize("mw", [0, 0.0, -5.0])
def test_a_value_not_above_zero_is_refused(mw):
    assert _refused(_staged(), "energization_invalid_mw", LINK, 2030, mw) == E.NOT_ABOVE_ZERO


def test_a_numpy_number_is_accepted():
    n = _staged()
    assert E.set_period_mw(n, LINK, 2030, np.float32(12.5))["mw"] == 12.5


def test_a_link_without_a_schedule_is_refused_and_none_is_created():
    n = _staged()
    other = next(link for link in n.links.index if link != LINK)
    _refused(n, "energization_no_schedule", other, 2030, 10.0)
    assert other not in E.energization_frame(n).columns
    _refused(n, "energization_no_schedule", "nope", 2030, 10.0)
    plain = build_eh_datacenter()                             # no schedule at all
    assert E.energization_frame(plain) is None
    _refused(plain, "energization_no_schedule", LINK, "all", 10.0)
    assert E.ATTR not in plain.links_t or E.energization_frame(plain) is None


def test_a_column_not_stored_as_numbers_is_refused():
    n = _single()
    n.links_t[E.ATTR] = n.links_t[E.ATTR].astype(object)
    _refused(n, "energization_no_schedule", LINK, "all", 10.0)


@pytest.mark.parametrize("period", [2031, "2031", None, "all", True, 2030.0])
def test_a_period_not_in_level_zero_is_refused(period):
    message = _refused(_staged(), "energization_unknown_period", LINK, period, 10.0)
    assert "2027, 2030, 2035" in message


@pytest.mark.parametrize("period", [2030, "2030", 0])
def test_a_single_axis_takes_only_all(period):
    _refused(_single(), "energization_unknown_period", LINK, period, 10.0)


@pytest.mark.parametrize("period", ["all", None])
def test_a_single_axis_sets_all(period):
    n = _single()
    out = E.set_period_mw(n, LINK, period, 25.0)
    assert out["period"] == "all" and out["previous_mw"] == 20.0
    assert (E.energization_frame(n)[LINK] == 25.0).all()


def test_a_period_that_varies_is_refused_with_its_range_never_scaled():
    """E7 / W6 (non-flat check)."""
    n = _staged()
    frame = E.energization_frame(n)
    rows = np.flatnonzero(frame.index.get_level_values(0) == 2035)
    frame.iloc[rows[:10], 0] = 30.0
    frame.iloc[rows[10:], 0] = 40.0
    assert _refused(n, "energization_period_not_flat", LINK, 2035, 50.0) == \
        "2035 varies 30–40 MW: set it on the network."
    assert E.by_period(n, LINK)["by_period"]["2035"] == {"min": 30.0, "max": 40.0}
    # The other periods stay editable.
    assert E.set_period_mw(n, LINK, 2030, 20.0)["mw"] == 20.0


def test_a_period_with_hours_without_a_value_is_not_flat():
    n = _single((20.0, np.nan, 20.0, 20.0))
    assert _refused(n, "energization_period_not_flat", LINK, "all", 10.0) == \
        "The period varies 20–20 MW: set it on the network."
    assert E.by_period(n, LINK) == {"by_period": {"all": {"min": 20.0, "max": 20.0}}}


def test_a_period_without_any_value_is_set():
    n = _single((np.nan,) * 4)
    assert E.by_period(n, LINK) == {"by_period": {"all": None}}
    assert E.set_period_mw(n, LINK, "all", 10.0)["previous_mw"] is None


def test_above_the_rating_is_accepted_with_its_note():
    """W5: the per-period value does not cap or raise the link's p_nom."""
    n = _staged()
    p_nom = float(n.links.at[LINK, "p_nom"])
    out = E.set_period_mw(n, LINK, 2030, p_nom + 10)
    assert out["mw"] == p_nom + 10 and out["p_nom_mw"] == p_nom
    assert out["note"] == f"This is above the link's rating ({p_nom:g} MW): it doesn't raise the link's capacity."
    assert float(n.links.at[LINK, "p_nom"]) == p_nom
    assert E.set_period_mw(n, LINK, 2030, p_nom)["note"] is None


# ── W1: the input fingerprint ────────────────────────────────────────────────


def _fp_ctx(n):
    return types.SimpleNamespace(network=n, solver_state={"solver_config": {}}, user_ts={},
                                 storage_dir=None)


def _fps(n):
    from services.workflow_tools import _network_input_fingerprint

    ctx = _fp_ctx(n)
    return _network_input_fingerprint(ctx), _network_input_fingerprint(ctx, exact=True)


def test_the_fingerprint_moves_on_a_period_edit_both_branches():
    n = _staged()
    before = _fps(n)
    assert _fps(n) == before                                  # deterministic
    E.set_period_mw(n, LINK, 2030, 30.0)
    after = _fps(n)
    assert after[0] != before[0] and after[1] != before[1]


def test_the_fingerprint_moves_on_an_upload_equivalent_write():
    """The gap 02b fixes: a schedule written whole (an upload, a netCDF
    import) left the fingerprint as it was."""
    n = _staged()
    before = _fps(n)
    frame = E.energization_frame(n)
    n.links_t[E.ATTR] = pd.DataFrame({LINK: frame[LINK].to_numpy() * 0.5}, index=frame.index)
    after = _fps(n)
    assert after[0] != before[0] and after[1] != before[1]


def test_the_fingerprint_is_unchanged_without_the_column(monkeypatch):
    """Byte-identical: a network without a schedule (or an all-NaN one) hashes
    as it did before the block existed."""
    n = _single()
    with_schedule = _fps(n)
    n.links_t[E.ATTR] = pd.DataFrame({LINK: [np.nan] * 4}, index=n.snapshots)
    nan_only = _fps(n)
    del n.links_t[E.ATTR]
    without = _fps(n)
    assert nan_only == without != with_schedule
    monkeypatch.setattr(E, "energization_frame", lambda _n: None)   # the block dropped
    n2 = _single()
    del n2.links_t[E.ATTR]
    assert _fps(n2) == without


# ── W2: the investment case's key ───────────────────────────────────────────


def _ic_key(n):
    from routers import results

    cfg = types.SimpleNamespace(finance=None, commercial=None)
    return results._ic_assumptions(cfg, n, lock=threading.RLock())


def test_the_investment_case_key_moves_with_the_schedule():
    n = _staged()
    h0, parts = _ic_key(n)
    assert parts["energization"] == E.schedule_digest(n)
    E.set_period_mw(n, LINK, 2030, 30.0)
    h1, parts1 = _ic_key(n)
    assert h1 != h0 and parts1["energization"] != parts["energization"]
    frame = E.energization_frame(n)
    n.links_t[E.ATTR] = pd.DataFrame({LINK: frame[LINK].to_numpy() * 0.5}, index=frame.index)
    assert _ic_key(n)[0] != h1


def test_the_investment_case_key_is_unchanged_without_a_schedule():
    from services.finance.investment_case_runner import assumptions_digest

    n = build_eh_datacenter()
    h, parts = _ic_key(n)
    assert "energization" not in parts
    base = dict(finance=None, commercial=None, dispatch="d", packs={})
    assert assumptions_digest(**base) == assumptions_digest(**base, energization=None)
    assert assumptions_digest(**base)[0] != assumptions_digest(**base, energization="x")[0]


# ── E4: the run's freeze and admission ──────────────────────────────────────


def _run_ctx(tmp_path, n):
    from services import flow_service as F

    meta = json.loads((TEMPLATES / "eh_datacenter_staged" / "eh_template.json").read_text())
    (tmp_path / F.TEMPLATE_FILE).write_text(json.dumps(meta))
    return types.SimpleNamespace(storage_dir=str(tmp_path), project_uuid="p", network=n,
                                 network_revision=0, solver_state_lock=threading.RLock(),
                                 solver_state={"solver_config": {}})


def _admit(tmp_path, ctx, frozen):
    from harness import run_eh

    step = {"id": "solve.2", "tool": "run_eh_study", "args": dict(frozen["args"]),
            "frozen": frozen, "status": "pending", "attempt_id": "a1"}
    task = {"task_id": "t1", "project_id": "p", "status": "running", "steps": [step],
            "run": {"profile": "eh_site@1", "status": "running", "stage": "solve", "stages": {}}}
    return run_eh.admit(tmp_path / "task.json", task, step, step["args"], ctx)


def test_freeze_records_the_schedule_and_admit_compares_it_exactly(tmp_path):
    from harness import run_eh

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = json.loads(json.dumps(run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)))
    assert frozen["energization"] == {LINK: {"by_period": {"2027": 15.0, "2030": 55.0, "2035": 55.0}}}
    assert run_eh._energization_change(ctx, frozen) is None
    E.set_period_mw(n, LINK, 2030, 54.999999)                # exact, never a tolerance
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND
    assert message == ("The connection's energization schedule changed since the study's card "
                       "was drawn (grid_import). Review the run again.")


def test_a_record_frozen_before_02b_refuses_on_a_staged_network(tmp_path):
    from harness import run_eh

    n = _staged()
    ctx = _run_ctx(tmp_path, n)
    frozen = run_eh.freeze(ctx, {"eh_study": {"budget_solves": 2}}, 6)
    frozen.pop("energization")
    kind, message = _admit(tmp_path, ctx, frozen)
    assert kind == run_eh.ARGS_KIND and message == run_eh.ENERGIZATION_UNRECORDED
    # A network without a schedule has nothing to re-freeze.
    plain = types.SimpleNamespace(network=build_eh_datacenter())
    assert run_eh._energization_change(plain, {"args": {}}) is None


# ── E5: the route behaves as an asset edit does ─────────────────────────────


def _create(client, tid, name):
    r = client.post(f"/api/projects/from_template/{tid}", params={"name": name})
    assert r.status_code == 200, r.text
    return r.json().get("name", name)


def _row(client, name):
    r = client.get(f"/api/projects/{name}/flow")
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["sections"] if s["section"] == "connection")


def _put(client, name, **body):
    return client.put(f"/api/projects/{name}/flow/energization", json=body)


AS_BUILT = {"2027": 15.0, "2030": 55.0, "2035": 55.0}


def test_the_route_sets_one_period_with_undo_revision_log_and_reload(client, tmp_projects_dir):
    from services import change_log_service

    name = _create(client, "eh_datacenter_staged", "staged02b")
    assert _row(client, name)["energization"] == [{"link": LINK, "by_period": AS_BUILT}]
    info = client.get("/api/network/undo/info").json()
    r = _put(client, name, link=LINK, period=2030, mw=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"] == f"The energized capacity of {LINK} for 2030 is now 30 MW (was 55 MW)."
    assert body["period"] == "2030" and body["note"] is None and "series" not in body
    after = client.get("/api/network/undo/info").json()
    assert after["depth"] == info["depth"] + 1                 # one undo step
    assert after["network_revision"] > info["network_revision"]
    assert after["unsaved"] is True
    assert change_log_service.get_all()[0]["description"] == body["message"]
    assert _row(client, name)["energization"][0]["by_period"]["2030"] == 30.0
    # Undo restores the period.
    assert client.post("/api/network/undo").status_code == 200
    assert _row(client, name)["energization"][0]["by_period"] == AS_BUILT
    # Save and reload: the edit survives.
    assert _put(client, name, link=LINK, period="2035", mw=40.0).status_code == 200
    assert client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200
    assert client.get(f"/api/projects/{name}").status_code == 200
    assert _row(client, name)["energization"][0]["by_period"] == {**AS_BUILT, "2035": 40.0}


def test_the_route_names_the_rating_it_does_not_raise(client, tmp_projects_dir):
    name = _create(client, "eh_datacenter_staged", "staged02b_rating")
    r = _put(client, name, link=LINK, period=2030, mw=500)
    assert r.status_code == 200, r.text
    assert r.json()["message"].endswith(
        f"(was 55 MW). This is above the link's rating ({r.json()['p_nom_mw']:g} MW): "
        "it doesn't raise the link's capacity.")


@pytest.mark.parametrize("body, kind", [
    ({"link": LINK, "period": 2030, "mw": True}, "energization_invalid_mw"),
    ({"link": LINK, "period": 2030, "mw": 0}, "energization_invalid_mw"),
    ({"link": LINK, "period": 2030, "mw": "30"}, "energization_invalid_mw"),
    ({"link": "site_transformer", "period": 2030, "mw": 30}, "energization_no_schedule"),
    ({"link": LINK, "period": 2031, "mw": 30}, "energization_unknown_period"),
    ({"link": LINK, "mw": 30}, "energization_unknown_period"),
    ({"link": LINK, "period": 2030}, "energization_invalid_request"),
    ({"link": LINK, "period": 2030, "mw": 30, "scale": 2}, "energization_invalid_request"),
    ({"link": 3, "period": 2030, "mw": 30}, "energization_invalid_request"),
])
def test_each_refusal_is_a_typed_422_and_writes_nothing(client, tmp_projects_dir, body, kind):
    name = _create(client, "eh_datacenter_staged", "staged02b_refused")
    info = client.get("/api/network/undo/info").json()
    r = _put(client, name, **body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == kind
    assert client.get("/api/network/undo/info").json()["depth"] == info["depth"]
    assert _row(client, name)["energization"][0]["by_period"] == AS_BUILT


def test_a_period_that_varies_is_refused_by_the_route_and_shown_as_a_range(
        client, tmp_projects_dir, bound_ctx):
    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_datacenter_staged", "staged02b_varies")
    with bound_ctx(client), PyPSAService.get_lock():
        frame = E.energization_frame(PyPSAService.get_network())
        rows = np.flatnonzero(frame.index.get_level_values(0) == 2035)
        frame.iloc[rows[:5], 0] = 30.0
        frame.iloc[rows[5:], 0] = 40.0
    r = _put(client, name, link=LINK, period=2035, mw=35)
    assert r.status_code == 422
    assert r.json()["detail"] == {"error_kind": "energization_period_not_flat",
                                  "message": "2035 varies 30–40 MW: set it on the network."}


def test_a_template_without_a_schedule_keeps_the_single_limit(client, tmp_projects_dir):
    """UX condition 5: no energization column, no by_period on the row."""
    name = _create(client, "eh_datacenter", "single02b")
    row = _row(client, name)
    assert "energization" not in row and row["connection_limit"]["mw"] is not None
    r = _put(client, name, link=LINK, period="all", mw=30)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "energization_no_schedule"
