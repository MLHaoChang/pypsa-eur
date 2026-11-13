# ruff: noqa: F811  (pytest fixtures imported from sibling test modules)
# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Study harness issue 33 (GK B (49) plan GO, S1-S8): eh_site@3 fetches the
site's map context in the inputs stage when the intake names a site, and the
input gate's `eh.site.context` (warn) reads it.

Offline (L7): every fetch goes through an `httpx.MockTransport` serving the
synthetic Overpass fixture (`tests/fixtures/overpass_eemshaven.json`); the
terrain tiles answer 404, which the service tolerates.
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import time
import uuid

import httpx
import pytest

from harness import run_site
from services import (
    run_actions,
    run_envelope,
    run_gates,
    run_intake,
    run_profiles,
    site_context,
)
from services import assistant_tasks as tasks
from services.pypsa_service import PyPSAService
from services.reports import harness_chapter
from tests.test_assistant_tasks_study_brief import (  # noqa: F401
    baseline,
    dispatch,
    edit,
    helpers_state,
    session,
    wait_until,
)
from tests.test_harness_envelope import granted, on_run, profile  # noqa: F401
from tests.test_harness_run_eh_site_v2 import _start_v2, _tick_to, goal_host  # noqa: F401
from tests.test_harness_run_report_handoff import fake_report  # noqa: F401
from tests.test_harness_run_skeleton import RUN, site, start  # noqa: F401
from tests.test_harness_runner import detached, task_path  # noqa: F401

# sha256 of json.dumps(PROFILES[ref], sort_keys=True) on master 203d95c71 (L8).
PINNED = {
    "eh_site@1": "8d8f09dec396579ec12bd9344959ac6ecd5cef1eaa0a47de737bd83bca51cf46",
    "eh_site@2": "2eefcb14bc7d5f05a465be591bf54238d9e4f1c7bf7948cff935c461b381f9f8",
}
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "overpass_eemshaven.json"
SITE = {"lat": 53.4396, "lon": 6.8321}
SITE_TEXT = "The site is at 53.4396° N and 6.8321° E."


TILE = None


def _tile() -> bytes:
    """One flat Terrarium tile (10 m), built once (`test_site_context.terrarium_png`)."""
    global TILE
    if TILE is None:
        from tests.test_site_context import terrarium_png

        TILE = terrarium_png(lambda x, y: 10.0)
    return TILE


def overpass(calls=None, status=200, delay=0.0, body=None, tiles=200):
    """The offline map services: Overpass answers the fixture (or `body`),
    and every terrain tile answers a flat tile (or the status `tiles`)."""

    def handle(request):
        if calls is not None:
            calls.append(str(request.url))
        if request.method == "GET":
            return (
                httpx.Response(200, content=_tile())
                if tiles == 200
                else httpx.Response(tiles)
            )
        if delay:
            time.sleep(delay)
        return httpx.Response(
            status, content=body if body is not None else FIXTURE.read_bytes()
        )

    return httpx.MockTransport(handle)


@pytest.fixture
def offline(monkeypatch):
    calls = []
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(calls))
    return calls


# ── L8: versions (S7) ───────────────────────────────────────────────────────


@pytest.mark.parametrize("ref", sorted(PINNED))
def test_older_eh_site_versions_are_byte_equal_to_master(ref):
    blob = json.dumps(run_profiles.PROFILES[ref], sort_keys=True).encode()
    assert hashlib.sha256(blob).hexdigest() == PINNED[ref]


def test_v3_differs_from_v2_only_by_the_site_params_its_fetch_and_its_rule():
    v2, v3 = (
        json.loads(json.dumps(run_profiles.PROFILES[r]))
        for r in ("eh_site@2", "eh_site@3")
    )
    assert (v2.pop("version"), v3.pop("version")) == (2, 3)
    assert v3["params"]["ask"][-2:] == [
        run_profiles.SITE_LAT_PARAM,
        run_profiles.SITE_LON_PARAM,
    ]
    del v3["params"]["ask"][-2:]
    for p in (run_profiles.SITE_LAT_PARAM, run_profiles.SITE_LON_PARAM):
        assert v3["params"]["fields"].pop(p)["unit_storage"] == "°"
    assert v3["stages"].pop("inputs")["actions"] == [
        {"tool": "get_weather_choice", "args": {"project_id": run_profiles.PROJECT}},
        {
            "tool": run_actions.SITE_FETCH,
            "args": {"frozen": run_actions.SITE_KIND},
            "native": run_actions.KIND,
        },
    ]
    assert v3["stages"]["input_gate"]["rules"].pop() == run_profiles.SITE_CONTEXT_RULE
    assert run_profiles.SITE_CONTEXT_RULE["severity"] == "warn"  # Q1
    assert v2 == v3


def test_a_run_granted_on_v2_keeps_v2_and_never_fetches():
    v2 = run_profiles.resolve("eh_site@2")
    assert v2["version"] == 2
    steps = run_profiles.materialise(v2, "inputs", "p-1")
    assert [s["tool"] for s in steps] == ["get_weather_choice"]
    assert "eh.site.context" not in {
        r["id"] for r in v2["stages"]["input_gate"]["rules"]
    }
    v3 = run_profiles.resolve("eh_site@3")
    assert [s["tool"] for s in run_profiles.materialise(v3, "inputs", "p-1")] == [
        "get_weather_choice",
        run_actions.SITE_FETCH,
    ]


def test_a_profile_cannot_give_the_fetch_args_or_move_it_out_of_the_inputs_stage():
    bad_args = copy.deepcopy(run_profiles.EH_SITE_V3)
    bad_args["stages"]["inputs"]["actions"][1]["args"] = {
        "frozen": run_actions.SITE_KIND,
        "site": SITE,
    }
    with pytest.raises(run_profiles.ProfileError, match="frozen marker"):
        run_profiles.load(bad_args)
    moved = copy.deepcopy(run_profiles.EH_SITE_V3)
    moved["stages"]["setup"]["actions"] = [moved["stages"]["inputs"]["actions"].pop()]
    with pytest.raises(run_profiles.ProfileError, match="inputs stage only"):
        run_profiles.load(moved)


# ── S1, S7: the intake ──────────────────────────────────────────────────────


def _row(param, value, unit="°", status="proposed"):
    return {
        "param": param,
        "value": value,
        "unit": unit,
        "status": status,
        "source": {"kind": "text"},
    }


def _site_rows(lat=SITE["lat"], lon=SITE["lon"]):
    return [_row("site.lat", lat), _row("site.lon", lon)]


def test_the_site_is_never_a_creation_param():
    rows = [_row("connection_mw", 12.0, "MW"), *_site_rows()]
    assert run_intake.params(rows) == {"connection_mw": 12.0}
    assert run_intake.site(rows) == (SITE, None)
    assert run_intake.site([_row("connection_mw", 12.0, "MW")]) == (None, None)


@pytest.mark.parametrize(
    "lat, lon",
    [
        (float("nan"), 6.8),
        (float("inf"), 6.8),
        (53.4, float("-inf")),
        (85.06, 6.8),
        (-85.06, 6.8),
        (53.4, 180.01),
        (53.4, -180.01),
        (True, 6.8),
        ("53.4", 6.8),
    ],
)
def test_a_bad_site_value_is_refused_never_clamped(lat, lon):
    point, gap = run_intake.site(_site_rows(lat, lon))
    assert point is None and gap["kind"] == "site_invalid"
    items = run_intake.missing(run_profiles.resolve("eh_site@3"), [], [gap])
    assert [i["kind"] for i in items if i["param"].startswith("site.")] == [
        "site_invalid"
    ]


def _quoted(param, value, quote, raw):
    return dict(_row(param, value), source={"kind": "text", "quote": quote, "raw": raw})


@pytest.mark.parametrize(
    "lat_q, lon_q",
    [
        ("51.50° N", "0.12° W"),
        ("33.9° S", "18.4° E"),
        ("51.50° E", "0.12° E"),
        ("-33.9° N", "18.4°"),
    ],
)
def test_a_compass_letter_other_than_n_or_e_is_refused_never_guessed(lat_q, lon_q):
    lat_raw, lon_raw = (q.split("°")[0].lstrip("-") for q in (lat_q, lon_q))
    rows = [
        _quoted("site.lat", float(lat_q.split("°")[0]), lat_q, lat_raw),
        _quoted("site.lon", float(lon_q.split("°")[0]), lon_q, lon_raw),
    ]
    point, gap = run_intake.site(rows)
    assert (
        point is None and gap["kind"] == "site_invalid" and "negative" in gap["message"]
    )


def test_n_and_e_and_a_signed_number_name_the_site():
    rows = [
        _quoted("site.lat", 51.5, "51.50° N", "51.50"),
        _quoted("site.lon", -0.12, "-0.12°", "0.12"),
    ]
    assert run_intake.site(rows) == ({"lat": 51.5, "lon": -0.12}, None)


def test_the_bounds_themselves_are_a_site():
    assert run_intake.site(_site_rows(-85.05, 180.0)) == (
        {"lat": -85.05, "lon": 180.0},
        None,
    )


def test_half_a_site_or_a_wrong_unit_is_asked_for():
    _, gap = run_intake.site([_row("site.lat", 53.4)])
    assert gap["param"] == "site.lon" and gap["kind"] == "site_invalid"
    _, gap = run_intake.site([_row("site.lat", 53.4, "rad"), _row("site.lon", 6.8)])
    assert gap["param"] == "site.lat"


@pytest.fixture
def site_host(goal_host, site, monkeypatch):
    """`goal_host`, whose typed turn also names the site."""
    real = run_intake.host_for  # goal_host's: the host with its one typed turn
    owner = tasks._authority()[2]
    (turn,) = real(PyPSAService.get_active_context(), owner, site[0]).turns
    turn = {**turn, "user": turn["user"] + " " + SITE_TEXT}
    tid = run_intake.turn_id(turn)

    def host_for(ctx, owner_id, project_id):
        found = real(ctx, owner_id, project_id)
        return run_intake.Host(found.name, found.dir, found.owner_id, (turn,))

    monkeypatch.setattr(run_intake, "host_for", host_for)
    for c in [*goal_host["citations"], goal_host["goal"]]:
        if "turn_id" in c:
            c["turn_id"] = tid
    goal_host["site"] = [
        {"param": "site.lat", "turn_id": tid, "quote": "53.4396° N", "unit": "°"},
        {"param": "site.lon", "turn_id": tid, "quote": "6.8321° E", "unit": "°"},
    ]
    return goal_host


def _start(citations, name, ref="eh_site@3"):
    return start(
        request_id=f"v3-{uuid.uuid4().hex[:6]}",
        run={
            **RUN,
            "profile": ref,
            "intake": {"project_name": name, "citations": citations},
        },
    )


def _intake(view, site):
    return json.loads(task_path(site[1], view).read_text())["run"]["intake"]


def test_a_typed_site_reads_into_two_rows_and_v2_rows_are_unchanged(site_host, site):
    base = [*site_host["citations"], site_host["goal"]]
    v3 = _intake(
        _start([*base, *site_host["site"]], f"Site {uuid.uuid4().hex[:6]}"), site
    )
    rows = {r["param"]: r for r in v3["rows"]}
    assert (rows["site.lat"]["value"], rows["site.lon"]["value"]) == (
        SITE["lat"],
        SITE["lon"],
    )
    assert not [g for g in v3["gaps"] if g["param"].startswith("site.")]
    v2 = _intake(_start(base, f"Site {uuid.uuid4().hex[:6]}", ref="eh_site@2"), site)
    assert v2["rows"] == [r for r in v3["rows"] if not r["param"].startswith("site.")]
    assert "site" not in run_intake.params(v3["rows"])


# ── S1: the frozen site ─────────────────────────────────────────────────────


def _task(rows, steps=()):
    return {"task_id": "t-1", "steps": list(steps), "run": {"intake": rows}}


def test_the_site_is_frozen_from_the_granted_rows_only():
    frozen = run_actions.stage_frozen(_task(_site_rows()), run_actions.SITE_KIND)
    assert frozen["site"] == SITE and frozen["solves"] == 0 and run_site.valid(frozen)
    assert frozen["digest"] == run_actions.digest(run_site.core(frozen))
    # A host run keeps its intake as a dict with rows.
    assert run_site.freeze({"run": {"intake": {"rows": _site_rows()}}})["site"] == SITE
    assert run_site.freeze(_task([]))["site"] is None


def test_a_model_cannot_move_the_fetched_site(offline, tmp_path):
    """#1072: the step's args are the marker, and the fetch reads the frozen
    record; coordinates offered elsewhere (args, a turn) change nothing, and
    a record edited after its card was shown stops the step."""
    profile = run_profiles.resolve("eh_site@3")
    steps = run_profiles.materialise(profile, "inputs", "p-1")
    run_actions.fill(
        steps,
        run_actions.stage_frozen(_task(_site_rows()), run_actions.SITE_KIND),
        run_actions.SITE_KIND,
    )
    step = steps[1]
    step["args"]["site"] = {"lat": 0.0, "lon": 0.0}  # what a model might pass
    task = _task(_site_rows(), [dict(steps[0], status="completed"), step])
    task["project_id"] = "p-1"
    task["run"].update(status="running", stages={"inputs": {"status": "active"}})
    path = tmp_path / "t-1.json"
    path.write_text(json.dumps(task))
    ctx = type("Ctx", (), {"project_uuid": "p-1"})()
    assert run_actions.start(path, ctx, step, instance=None) == "site_args"
    assert offline == []  # nothing fetched
    step["args"] = {"frozen": run_actions.SITE_KIND}
    step["frozen"]["site"] = {"lat": 0.0, "lon": 0.0}  # the record moved after the card
    path.write_text(
        json.dumps(dict(task, steps=[task["steps"][0], dict(step, status="pending")]))
    )
    assert run_actions.start(path, ctx, step, instance=None) == "site_args"
    assert offline == []


# ── S2: model turns ─────────────────────────────────────────────────────────


def test_a_model_turn_is_refused_the_fetch_under_a_v3_grant():
    envelope = {
        **copy.deepcopy(run_profiles.PROFILES["eh_site@3"]["envelope"]),
        "state": "granted",
    }
    assert (
        run_envelope._refusal(
            None,
            envelope,
            {},
            run_actions.SITE_FETCH,
            {},
            "read",
            False,
            None,
            None,
            {},
        )
        == "unknown_tool"
    )
    assert not any(
        e["tool"] == "fetch_site_context" for e in envelope["edits"] + envelope["runs"]
    )
    assert (
        "fetch_site_context"
        not in run_profiles.L1_TOOLS
        + run_profiles.L2_TOOLS
        + run_profiles.EXPLORE_TOOLS
    )


# ── S3, S5, S6: the fetch ───────────────────────────────────────────────────


def test_the_fetch_keeps_the_whole_context_in_the_runs_own_record(offline, tmp_path):
    path = tmp_path / "t-1.json"
    result = run_site.fetch(path, SITE, "a1")
    assert result["state"] == "fetched" and result["truncated"] is False
    assert result["terrain_missing"] == 0
    assert not run_site.context_path(
        path
    ).exists()  # kept only by `_publish`, under the held check
    doc = json.loads(run_site._pending_path(path, "a1").read_text())
    assert (
        result["digest"]
        == hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
    )
    assert result["found"]["buildings"] == len(doc["buildings"]) > 0
    assert result["found"]["power_lines"] == sum(
        1 for x in doc["lines"] if x["kind"] == "power_line"
    )
    assert not run_site.context_path(path).name.endswith(
        ".json"
    )  # never read as a task
    (posted,) = [c for c in offline if "interpreter" in c]
    assert posted == site_context.DEFAULT_OVERPASS_URL


def test_missing_terrain_tiles_are_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(tiles=404))
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert result["state"] == "fetched" and result["terrain_missing"] > 0


@pytest.mark.parametrize("body", [b"[1, 2]", b'{"elements": 5}', b"not json"])
def test_an_answer_of_the_wrong_shape_settles_unavailable(body, tmp_path, monkeypatch):
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(body=body))
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert result["state"] == "unavailable" and "Overpass" in result["reason"]


def test_a_run_taken_over_keeps_neither_the_result_nor_the_document(offline, tmp_path):
    out, task = _dispatched(tmp_path, _site_rows(), instance="me", lease_owner="me")
    assert out == "taken_over"
    assert task["steps"][1]["status"] == "running" and "result" not in task["steps"][1]
    assert not run_site.context_path(tmp_path / "t-1.json").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_counts_come_from_the_whole_answer_when_the_service_truncates(
    offline, tmp_path, monkeypatch
):
    monkeypatch.setattr(site_context, "MAX_FEATURES", 1)
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert result["truncated"] is True and result["found"]["buildings"] > 1


def test_an_upstream_refusal_settles_unavailable_with_its_message(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(status=502))
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert result["state"] == "unavailable" and "HTTP 502" in result["reason"]
    assert not run_site.context_path(tmp_path / "t-1.json").exists()


def test_a_hung_upstream_settles_unavailable_inside_the_bound(tmp_path, monkeypatch):
    from harness import runner

    assert run_site.FETCH_TIMEOUT_S < runner.LEASE_SECONDS
    monkeypatch.setattr(run_site, "FETCH_TIMEOUT_S", 0.3)
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(delay=2.0))
    begun = time.monotonic()
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert time.monotonic() - begun < 1.5
    assert (
        result["state"] == "unavailable" and "did not answer within" in result["reason"]
    )


@pytest.mark.parametrize(
    "error",
    [
        site_context.SiteContextUnavailable("Overpass refused"),
        site_context.SiteContextTooLarge("too large"),
        httpx.ReadError("reset"),
        OSError("network refused"),
    ],
)
def test_every_fetch_failure_settles_unavailable(error, tmp_path, monkeypatch):
    def fail(*a, **k):
        raise error

    monkeypatch.setattr(site_context, "fetch_context", fail)
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert result == {
        "kind": run_actions.SITE_KIND,
        "version": run_site.VERSION,
        "state": "unavailable",
        "reason": str(error),
    }


def test_a_programming_error_is_not_swallowed(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise TypeError("bug")

    monkeypatch.setattr(site_context, "fetch_context", broken)
    with pytest.raises(TypeError):
        run_site.fetch(tmp_path / "t-1.json", SITE, "a1")


def test_the_transport_is_only_the_modules():
    import inspect

    assert run_site._TRANSPORT is None
    assert "transport" not in json.dumps(run_profiles.PROFILES["eh_site@3"])
    assert "_TRANSPORT" not in inspect.getsource(run_intake) + inspect.getsource(
        run_actions
    )


# ── the step, end to end in the runner's dispatch ───────────────────────────


def _dispatched(tmp_path, rows, instance=None, lease_owner=None, prior=()):
    steps = run_profiles.materialise(run_profiles.resolve("eh_site@3"), "inputs", "p-1")
    run_actions.fill(
        steps,
        run_actions.stage_frozen(_task(rows, prior), run_actions.SITE_KIND),
        run_actions.SITE_KIND,
    )
    task = {
        "task_id": "t-1",
        "project_id": "p-1",
        "status": "running",
        "steps": [*prior, dict(steps[0], status="completed"), steps[1]],
        "run": {
            "intake": rows,
            "status": "running",
            "stages": {"inputs": {"status": "active"}},
            "runner": {"lease_owner": lease_owner},
        },
    }
    path = tmp_path / "t-1.json"
    path.write_text(json.dumps(task))
    if lease_owner is not None:
        # the lease moves to another instance while the fetch runs
        real = run_site.fetch

        def fetch_then_lose(path, site, attempt):
            out = real(path, site, attempt)
            t = json.loads(path.read_text())
            t["run"]["runner"]["lease_owner"] = "other"
            path.write_text(json.dumps(t))
            return out

        run_site.fetch, restore = fetch_then_lose, real
    try:
        out = run_actions.start(
            path,
            type("Ctx", (), {"project_uuid": "p-1"})(),
            steps[1],
            instance=instance,
        )
    finally:
        if lease_owner is not None:
            run_site.fetch = restore
    return out, json.loads(path.read_text())


def test_a_named_site_is_fetched_by_the_step(offline, tmp_path):
    out, task = _dispatched(tmp_path, _site_rows())
    assert out == run_actions.SITE_FETCH
    step = task["steps"][1]
    assert step["status"] == "completed" and step["result"]["state"] == "fetched"
    kept = run_site.context_path(tmp_path / "t-1.json").read_text()
    assert hashlib.sha256(kept.encode()).hexdigest() == step["result"]["digest"]
    assert not list(tmp_path.glob("*.tmp"))
    assert step["result"]["site"] == SITE


def test_no_site_named_settles_not_named_without_a_request(offline, tmp_path):
    out, task = _dispatched(tmp_path, [])
    assert out == run_actions.SITE_FETCH and offline == []
    assert task["steps"][-1]["result"] == {
        "kind": run_actions.SITE_KIND,
        "version": 1,
        "state": "not_named",
        "reason": run_site.NOT_NAMED,
    }


def test_a_programming_error_stops_the_step_for_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(site_context, "fetch_context", lambda *a, **k: 1 / 0)
    out, task = _dispatched(tmp_path, _site_rows())
    assert out == "site_start_failed"
    assert task["steps"][1]["status"] == "failed"
    assert task["run"]["stages"]["inputs"]["status"] == "failed"
    assert task["run"]["status"] == "needs_user"
    assert "ZeroDivisionError" in task["steps"][1]["error"]


def test_the_fetch_holds_no_solve():
    steps = run_profiles.materialise(run_profiles.resolve("eh_site@3"), "inputs", "p-1")
    run_actions.fill(steps, run_site.freeze(_task(_site_rows())), run_actions.SITE_KIND)
    assert [s["frozen"]["solves"] for s in steps if run_actions.is_action(s)] == [0]


# ── the rule and the report (item 4, S6) ────────────────────────────────────


def _env(task):
    return run_gates.Env(task=task, ctx=None, project=None, project_dir=None)


def _verdict(task):
    (result,) = run_gates.evaluate([run_profiles.SITE_CONTEXT_RULE], _env(task))
    return result


def _with(result):
    step = {
        "id": "inputs.2",
        "tool": run_actions.SITE_FETCH,
        "native": run_actions.KIND,
        "status": "completed",
        "result": result,
    }
    return {"steps": [step], "run": {}}


FETCHED = {
    "kind": run_actions.SITE_KIND,
    "version": 1,
    "state": "fetched",
    "truncated": False,
    "found": {"buildings": 7, "power_lines": 1, "power_substations": 1},
    "terrain_missing": 0,
}


def test_the_rule_passes_on_a_whole_fetch():
    result = _verdict(_with(FETCHED))
    assert result["verdict"] == "pass"
    assert result["facts"][0]["detail"] == [
        "7 buildings",
        "1 power lines",
        "1 power substations",
    ]
    assert run_site.gap_line(_with(FETCHED)) is None


@pytest.mark.parametrize(
    "record, reason",
    [
        ({**FETCHED, "truncated": True}, "cut to the nearest features"),
        ({**FETCHED, "terrain_missing": 3}, "3 terrain tile(s)"),
        (
            {**FETCHED, "terrain_missing": None},
            "terrain around the site could not be fetched",
        ),
        (
            {
                "kind": run_actions.SITE_KIND,
                "version": 1,
                "state": "not_named",
                "reason": run_site.NOT_NAMED,
            },
            run_site.NOT_NAMED,
        ),
        (
            {
                "kind": run_actions.SITE_KIND,
                "version": 1,
                "state": "unavailable",
                "reason": "Overpass answered HTTP 502",
            },
            "HTTP 502",
        ),
        ({"version": 9, "state": "fetched"}, "cannot be read"),
    ],
)
def test_the_rule_is_not_evaluated_with_its_reason_and_the_report_lists_the_gap(
    record, reason
):
    task = _with(record)
    result = _verdict(task)
    assert result["verdict"] == "not_evaluated" and reason in result["reason"]
    assert result["severity"] == "warn"
    assert run_gates.passed([result])  # warn: an outage never stops the study (Q1)
    payload, tables = harness_chapter.build(task, None, [])
    gap = f"Site context not established: {result['reason']}"
    assert gap in payload["gaps"] and [gap] in tables["harness_gaps"][1]


def test_a_run_record_without_the_fetch_reads_not_evaluated_and_lists_no_gap():
    task = {"steps": [], "run": {"stages": {}}}
    result = _verdict(task)
    assert (
        result["verdict"] == "not_evaluated" and "has not fetched" in result["reason"]
    )
    payload, _ = harness_chapter.build(task, None, [])
    assert payload["gaps"] == []  # @1/@2 reports are unchanged


# ── mutant survivors (S8 follow-up) ─────────────────────────────────────────


def test_only_usable_rows_that_are_not_writes_name_the_site():
    lat, lon = _site_rows()
    for row in ({**lat, "write": True}, {**lat, "status": "rejected"}):
        assert run_intake.site([row, lon])[1]["param"] == "site.lat"
    assert run_intake.site(
        [{"param": None, "value": 1.0, "status": "proposed"}, lat, lon]
    ) == (SITE, None)


def test_a_zero_with_its_own_letter_is_a_site():
    rows = [_quoted("site.lat", 0.0, "0° N", "0"), _row("site.lon", 6.8)]
    assert run_intake.site(rows) == ({"lat": 0.0, "lon": 6.8}, None)


def test_the_bad_site_is_asked_for_in_its_own_words_and_other_reasons_are_kept():
    _, gap = run_intake.site([_row("site.lat", 53.4)])
    profile = run_profiles.resolve("eh_site@3")
    required = profile["params"]["required"][0]
    other = {"param": required, "kind": "not_found", "message": "x"}
    items = run_intake.missing(profile, [], [gap, other])
    (asked,) = [i for i in items if i["param"] == required]
    assert asked["tried"] == ["not_found"]
    (site_item,) = [i for i in items if i["kind"] == "site_invalid"]
    (line,) = run_intake.request_lines([site_item])
    assert gap["message"].rstrip(".") in line


@pytest.mark.parametrize(
    "point",
    [
        {"lat": 53, "lon": 6.8},
        {"lat": 85.06, "lon": 6.8},
        {"lat": 53.4, "lon": -180.01},
        {"lat": 53.4},
        None,
    ],
)
def test_a_frozen_point_is_two_floats_within_bounds(point):
    assert not run_intake.site_point_ok(point)


def test_a_frozen_point_on_the_bounds_is_valid():
    assert run_intake.site_point_ok({"lat": -85.05, "lon": 180.0})
    assert run_intake.site_point_ok({"lat": 85.05, "lon": -180.0})


def test_a_badly_named_site_settles_not_named_with_its_reason(offline, tmp_path):
    _, task = _dispatched(tmp_path, [_row("site.lat", 53.4)])
    result = task["steps"][1]["result"]
    assert (
        result["state"] == "not_named"
        and "Site longitude is missing" in result["reason"]
    )
    assert offline == []


def test_a_run_without_its_record_freezes_no_site():
    assert run_site.freeze({"run": None})["site"] is None
    assert run_site.gap_line(None) is None


def test_the_square_is_as_wide_as_it_is_tall_in_metres():
    (lon0, lat1), (lon1, _), _, (_, lat0) = run_site.boundary({"lat": 60.0, "lon": 6.0})
    assert lon1 - lon0 == pytest.approx(2 * (lat1 - lat0), rel=1e-6)


def test_an_unavailable_fetch_always_has_a_reason():
    assert run_site._unavailable("")["reason"]


def test_terrain_without_a_missing_count_lacks_no_tile(tmp_path, monkeypatch):
    doc = {"found": {"buildings": 0}, "truncated": False, "terrain": {}}
    monkeypatch.setattr(site_context, "fetch_context", lambda *a, **k: doc)
    assert run_site.fetch(tmp_path / "t-1.json", SITE, "a1")["terrain_missing"] == 0


@pytest.mark.parametrize(
    "bad",
    [
        {**FETCHED, "state": "done"},
        {**FETCHED, "truncated": "no"},
        {**FETCHED, "found": []},
        {**FETCHED, "terrain_missing": "3"},
        {"version": 1, "state": "done", "reason": "x"},
        {"version": 9, "state": "not_named", "reason": "x"},
        "fetched",
    ],
)
def test_a_record_of_the_wrong_shape_reads_unreadable(bad):
    assert run_site.outcome(_with(bad))[0] == "unreadable"


def test_a_fetch_step_that_has_not_completed_reads_not_run():
    task = _with(FETCHED)
    task["steps"][0]["status"] = "running"
    assert run_site.outcome(task)[0] == "not_run"


def test_a_step_takes_its_stage_from_its_registry_row():
    assert run_actions.stage_of({"tool": run_actions.SITE_FETCH}) == "inputs"
    assert run_actions.stage_of({"tool": "unknown"}) == run_actions.STAGE
    assert run_actions.stage_of(None) == run_actions.STAGE


def test_found_counts_substations_only():
    ring = [
        {"lat": 0, "lon": 0},
        {"lat": 0, "lon": 1},
        {"lat": 1, "lon": 1},
        {"lat": 0, "lon": 0},
    ]
    way = lambda i, tags: {"type": "way", "id": i, "tags": tags, "geometry": ring}  # noqa: E731
    elements = [
        way(1, {"power": "substation"}),
        way(2, {"landuse": "industrial"}),
        way(3, {"natural": "water"}),
    ]
    assert site_context.elements_to_context(elements)["found"]["power_substations"] == 1


@pytest.mark.parametrize(
    "change",
    [{"version": 9}, {"solves": 1}, {"site": {"lat": 91.0, "lon": 6.8}}],
)
def test_a_frozen_record_of_another_version_solve_or_site_is_invalid(change):
    frozen = run_site.freeze(_task(_site_rows()))
    assert run_site.valid(frozen) and not run_site.valid({**frozen, **change})


def test_the_context_file_sits_beside_a_task_named_by_a_string():
    assert run_site.context_path("runs/t-1.json") == pathlib.Path(
        "runs/t-1" + run_site.SUFFIX
    )


@pytest.mark.parametrize(
    "source",
    [None, {"quote": "53.4° S"}, {"raw": "53.4"}],
)
def test_a_row_without_a_whole_citation_has_no_compass_letter(source):
    lat, lon = _site_rows()
    assert run_intake.site([{**lat, "source": source}, lon]) == (SITE, None)


# ── GK B (50) F1 and F2 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "elements",
    [
        [1],
        [{"type": "way", "id": 1, "geometry": "x"}],
        [{"type": "way", "tags": {"building": "yes"}}],
        [{"type": "way", "id": 1, "tags": {"height": 3}}],
        [{"type": "way", "id": 1, "geometry": [{"lat": "53", "lon": 6.8}]}],
        [{"type": "way", "id": 1, "geometry": [1, 2]}],
        [{"type": "way", "id": 1, "nodes": 5}],
    ],
)
def test_elements_of_the_wrong_shape_settle_unavailable_before_parsing(
    elements, tmp_path, monkeypatch
):
    body = json.dumps({"elements": elements}).encode()
    monkeypatch.setattr(run_site, "_TRANSPORT", overpass(body=body))
    parsed = []
    monkeypatch.setattr(site_context, "elements_to_context", parsed.append)
    result = run_site.fetch(tmp_path / "t-1.json", SITE, "a1")
    assert result["state"] == "unavailable" and "cannot read" in result["reason"]
    assert parsed == []


def test_the_fixture_has_the_shape_the_parser_reads():
    elements = json.loads(FIXTURE.read_bytes())["elements"]
    assert site_context.elements_shape_problem(elements) is None


def test_an_error_inside_our_parser_on_a_good_answer_propagates(
    offline, tmp_path, monkeypatch
):
    def broken(elements):
        raise TypeError("a parser bug")

    monkeypatch.setattr(site_context, "elements_to_context", broken)
    with pytest.raises(TypeError, match="a parser bug"):
        run_site.fetch(tmp_path / "t-1.json", SITE, "a1")


def test_a_frozen_site_is_never_rebuilt_from_later_intake_rows(offline, tmp_path):
    first = run_actions.stage_frozen(_task(_site_rows()), run_actions.SITE_KIND)
    steps = run_profiles.materialise(run_profiles.resolve("eh_site@3"), "inputs", "p-1")
    run_actions.fill(steps, first, run_actions.SITE_KIND)
    prior = [dict(steps[1], id="inputs.prior", status="completed")]
    moved = _site_rows(52.0, 5.0)
    # inputs entered again after the intake rows changed: the first record is kept
    again = run_actions.stage_frozen(_task(moved, prior), run_actions.SITE_KIND)
    assert again == first and again["digest"] == first["digest"]
    out, task = _dispatched(tmp_path, moved, prior=prior)
    step = task["steps"][-1]
    assert out == run_actions.SITE_FETCH and step["frozen"] == first
    assert step["result"]["state"] == "fetched" and step["result"]["site"] == SITE


# Study harness issue 44: the catalogue offers eh_site@3 to start_task.
def _start_task_run_args(profile):
    return {
        "title": "t",
        "request_id": "r",
        "steps": [],
        "run": {"profile": profile, "mode": "stepwise"},
    }


@pytest.mark.parametrize("profile", ["eh_site@2", "eh_site@3"])
def test_start_task_offers_v2_and_v3(profile):
    from harness import catalogue, guards

    tool = next(t for t in catalogue.TOOLS if t["name"] == "start_task")
    enum = tool["input_schema"]["properties"]["run"]["properties"]["profile"]["enum"]
    assert enum == ["eh_site@2", "eh_site@3"]
    assert guards.check("start_task", _start_task_run_args(profile), "write") is None


@pytest.mark.parametrize("profile", ["eh_site@1", "eh_site@4", None, 3, ["eh_site@3"]])
def test_start_task_refuses_every_other_profile(profile):
    from harness import guards

    refusal = guards.check("start_task", _start_task_run_args(profile), "write")
    assert refusal is not None and refusal[0] == "required_guard_failed"


def test_a_chat_started_v3_run_fetches_only_the_granted_point(
    site_host, site, offline, tmp_path
):
    base = [*site_host["citations"], site_host["goal"]]
    view = _start([*base, *site_host["site"]], f"Site {uuid.uuid4().hex[:6]}")
    intake = _intake(view, site)
    rows = intake["rows"]
    point = {r["param"]: r["value"] for r in rows if r["param"].startswith("site.")}
    assert point == {"site.lat": SITE["lat"], "site.lon": SITE["lon"]}
    # The plan-review card shows the point before the grant (O3).
    frozen = run_actions.stage_frozen(_task(rows), run_actions.SITE_KIND)
    assert run_site.card(frozen)["site"] == "53.43960, 6.83210"
    out, task = _dispatched(tmp_path, rows)
    assert out == run_actions.SITE_FETCH
    assert task["steps"][1]["result"]["site"] == SITE
    assert any(c == site_context.DEFAULT_OVERPASS_URL for c in offline)


def test_a_chat_started_v3_run_without_a_site_reads_none_named(
    goal_host, site, offline, tmp_path
):
    base = [*goal_host["citations"], goal_host["goal"]]
    view = _start(base, f"Site {uuid.uuid4().hex[:6]}")
    rows = _intake(view, site)["rows"]
    frozen = run_actions.stage_frozen(_task(rows), run_actions.SITE_KIND)
    assert run_site.card(frozen)["site"] == "none named"
    _, task = _dispatched(tmp_path, rows)
    assert offline == [] and task["steps"][-1]["result"]["state"] != "fetched"


@pytest.mark.parametrize(
    "lat_quote, lon_quote",
    [
        ("10.0000° N", "20.0000° E"),  # coordinates the user never typed
        ("53.4396° N", "20.0000° E"),  # half of them typed
    ],
)
def test_a_site_the_user_did_not_type_is_never_fetched(
    site_host, site, offline, tmp_path, lat_quote, lon_quote
):
    base = [*site_host["citations"], site_host["goal"]]
    cites = [
        {
            "param": "site.lat",
            "turn_id": site_host["site"][0]["turn_id"],
            "quote": lat_quote,
            "unit": "°",
        },
        {
            "param": "site.lon",
            "turn_id": site_host["site"][0]["turn_id"],
            "quote": lon_quote,
            "unit": "°",
        },
    ]
    view = _start([*base, *cites], f"Site {uuid.uuid4().hex[:6]}")
    intake = _intake(view, site)
    assert not [r for r in intake["rows"] if r["param"].startswith("site.")] or (
        run_intake.site(intake["rows"])[0] is None
    )
    _, task = _dispatched(tmp_path, intake["rows"])
    assert offline == []
