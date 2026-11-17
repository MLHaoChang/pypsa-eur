"""Walkthrough bugs 05 and 06 (asset investment, 2026-11-17): the intent comparison on the shipped Data Center
Energy Hub template. The point-of-connection values the PQ and Pst checks need are prefilled (assumed) and
applied to the comparison's own copy of the network; the project's saved network is never tagged. The other
inputs prefill from the one project value each duplicates."""

from __future__ import annotations

import time

import pytest

from services.compare import intent_jobs

URL = "/api/projects/{}/intent-comparison"
PQ_FIELDS = {"x_pu", "pf", "s_n_mva", "p_cap_mw", "pf_req", "statcom_capital_cost"}


@pytest.fixture
def hub(client, tmp_projects_dir, project_storage_dir):
    """A data-centre project made by the create-from-template route, with its defaults."""
    intent_jobs.clear()
    r = client.post(
        "/api/projects/from_template/eh_datacenter",
        params={"name": "intent_hub"},
        json={"dc_load": {}},
    )  # the create form sends the defaults
    assert r.status_code == 200, r.text
    name = r.json().get("name", "intent_hub")
    yield name, project_storage_dir(name)
    intent_jobs.clear()


def _defaults(client, name) -> dict:
    r = client.get(URL.format(name) + "/defaults")
    assert r.status_code == 200, r.text
    return r.json()


def _body(d: dict, intents, *, pq=True) -> dict:
    env = d["envelope"]["value"]
    inputs = {
        "link": d["link"]["value"],
        "ramp_mw_per_min": d["ramp_mw_per_min"]["value"],
        "sk_mva": 250.0,
        "pst_limit": 1.0,
        "transformer_library_mva": [40.0, 63.0, 80.0],
        "envelope": env,
    }
    if pq:
        inputs["pq"] = {}  # take the project's or the assumed values
    return {
        "intents": [{"intent": i} for i in intents],
        "critical_mw": 34.49,
        "inputs": inputs,
    }


def _wait(client, name, timeout=300.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        r = client.get(URL.format(name))
        if r.json()["status"] != "running":
            return r.json()
        time.sleep(0.2)
    raise AssertionError("the comparison did not finish")


def test_defaults_prefill_from_the_project(client, hub):
    from services.dc_load_profile import read_envelope

    name, d = hub
    got = _defaults(client, name)
    assert got["link"]["value"] == "grid_import"
    ramp = next(
        x
        for x in client.get(f"/api/campus-electrical/{name}/grid-readiness").json()[
            "rows"
        ]
        if x["id"] == "ramp_rate"
    )
    assert got["ramp_mw_per_min"]["value"] == ramp["limit"] == 4.0
    assert got["ramp_mw_per_min"]["source"] == ramp["source"]
    env = read_envelope(d)
    assert got["envelope"]["value"]["max"] == env["max"]
    assert got["envelope"]["value"]["phases"] == env["phases"]
    assert set(got["pq"]) == PQ_FIELDS
    assert all(
        v["source"].startswith("assumed") and v["kind"] == "assumed" and v["value"] > 0
        for v in got["pq"].values()
    )
    assert got["ramp_mw_per_min"]["kind"] == (
        "assumed" if ramp["assumed"] else "project"
    )
    assert (
        got["envelope"]["kind"] == "load_envelope" and got["link"]["kind"] == "project"
    )
    assert got["pq"]["s_n_mva"]["value"] == got["pq"]["p_cap_mw"]["value"] == 40.0


def _island_off(client) -> None:
    """The user turns the template's island limits off in the solver settings; the loop does not reproduce them."""
    r = client.get("/api/simulation/solver_config")
    assert r.status_code == 200, r.text
    r = client.put(
        "/api/simulation/solver_config", json={**r.json(), "island_constraints": False}
    )
    assert r.status_code == 200, r.text


def test_as_shipped_the_island_constraints_refusal_names_the_setting(client, hub):
    name, _ = hub
    r = client.post(URL.format(name), json=_body(_defaults(client, name), "BC"))
    assert r.status_code == 422
    assert (
        "Island limits in the optimisation: they are on" in r.json()["detail"]
        and "island_config.json" not in r.json()["detail"]
    )


def test_the_comparison_runs_on_the_template(client, hub):
    import pypsa

    name, d = hub
    _island_off(client)
    r = client.post(URL.format(name), json=_body(_defaults(client, name), "BC"))
    assert r.status_code == 202, r.text
    done = _wait(client, name)
    assert done["status"] == "done"
    assert [x["intent"] for x in done["rows"]] == ["B", "C"]
    assert all(x["status"] != "failed" for x in done["rows"]), [
        x["verdict"] for x in done["rows"]
    ]
    saved = pypsa.Network(str(d / "network.nc"))
    assert (
        "pq_x_pu" not in saved.links.columns
    )  # the comparison never tags the project's network


def test_intent_a_without_a_backup_battery_fails_in_plain_words(client, hub):
    name, d = hub
    _island_off(client)
    assert (
        client.post(
            URL.format(name), json=_body(_defaults(client, name), "AC")
        ).status_code
        == 202
    )
    row, other = _wait(client, name)["rows"]
    assert (
        other["intent"] == "C" and other["status"] != "failed"
    )  # the other intent still runs
    assert row["intent"] == "A" and row["status"] == "failed"
    assert (
        "backup reserve" in row["verdict"]
        and "EnvelopeFloorsError" not in row["verdict"]
    )


def test_without_point_of_connection_values_the_refusal_names_the_fields(client, hub):
    name, _ = hub
    r = client.post(
        URL.format(name), json=_body(_defaults(client, name), "BC", pq=False)
    )
    assert r.status_code == 422
    text = r.json()["detail"]
    assert "AS-05" not in text and "PQ tags" not in text
    assert (
        text.startswith("Point of connection: ")
        and "power factor" in text
        and "STATCOM cost" in text
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("pf", 1.5),
        ("pf_req", 0.0),
        ("s_n_mva", -1.0),
        ("x_pu", -0.1),
        ("statcom_capital_cost", -5.0),
        ("p_cap_mw", 0.0),
    ],
)
def test_a_bad_point_of_connection_value_is_422(client, hub, field, value):
    from services.compare.intent_inputs import PQ_LABELS

    name, _ = hub
    _island_off(client)  # so the refusal is the value's own, not the island limits'
    body = _body(_defaults(client, name), "BC")
    body["inputs"]["pq"] = {field: value}
    r = client.post(URL.format(name), json=body)
    assert r.status_code == 422
    assert (
        r.json()["detail"].startswith("Point of connection: ")
        and PQ_LABELS[field] in r.json()["detail"]
    )


def test_an_unknown_point_of_connection_field_is_422(client, hub):
    name, _ = hub
    _island_off(client)
    body = _body(_defaults(client, name), "BC")
    body["inputs"]["pq"] = {"surprise": 1.0}
    r = client.post(URL.format(name), json=body)
    assert (
        r.status_code == 422
        and r.json()["detail"] == "Point of connection: unknown value(s) surprise"
    )


def test_a_post_that_leaves_the_project_values_out_runs_exactly_the_defaults_and_records_their_sources(
    client, hub, monkeypatch
):
    """GK P1/P4: GET defaults and POST read one ladder; the job record says what ran and where it came from."""
    name, _ = hub
    _island_off(client)
    seen = {}

    def fake_run(network, config, inp, **kw):
        from services.solver.pcs_statcom import pcs_statcom_tags
        from tests.test_intent_table import _result

        seen["tags"] = pcs_statcom_tags(network)[1].loc[inp.link].to_dict()
        seen["inp"] = inp
        return _result()

    monkeypatch.setattr("services.adequacy.sizing_loop_runner.run_sizing", fake_run)
    d = _defaults(client, name)
    body = {
        "intents": [{"intent": "B"}, {"intent": "C"}],
        "critical_mw": 34.49,
        "inputs": {
            "sk_mva": 250.0,
            "pst_limit": 1.0,
            "transformer_library_mva": [40.0],
            "pq": {},
        },
    }
    assert client.post(URL.format(name), json=body).status_code == 202
    done = _wait(client, name)
    assert seen["inp"].link == d["link"]["value"]
    assert seen["inp"].ramp_mw_per_min == d["ramp_mw_per_min"]["value"]
    assert seen["inp"].envelope == d["envelope"]["value"]
    from services.compare.intent_inputs import PQ_COLUMNS

    assert {f: seen["tags"][c] for f, c in PQ_COLUMNS.items()} == {
        f: v["value"] for f, v in d["pq"].items()
    }
    used = done["inputs"]
    assert used["sources"]["pq"] == {f: v["source"] for f, v in d["pq"].items()}
    assert used["sources"]["ramp_mw_per_min"] == d["ramp_mw_per_min"]["source"]
    assert (
        used["sources"]["link"] == "project" and used["values"]["link"] == "grid_import"
    )
    # A value the request gives is recorded as the request's.
    intent_jobs.clear()
    body["inputs"]["pq"] = {"x_pu": 0.08}
    assert client.post(URL.format(name), json=body).status_code == 202
    used = _wait(client, name)["inputs"]
    assert (
        used["sources"]["pq"]["x_pu"] == "request"
        and used["values"]["pq"]["x_pu"] == 0.08
    )
    assert used["sources"]["pq"]["pf"].startswith("assumed")


def test_a_post_with_point_of_connection_values_writes_nothing(
    client, hub, monkeypatch
):
    """GK P5: the saved network file and the live network stay as they were; only the comparison's copy is tagged."""
    from services.pypsa_service import PyPSAService
    from tests.test_intent_table import _result

    name, d = hub
    _island_off(client)
    monkeypatch.setattr(
        "services.adequacy.sizing_loop_runner.run_sizing", lambda *a, **k: _result()
    )
    before = (d / "network.nc").read_bytes()
    live = PyPSAService.get_network()
    live_cols = list(live.links.columns)
    assert (
        client.post(
            URL.format(name), json=_body(_defaults(client, name), "BC")
        ).status_code
        == 202
    )
    _wait(client, name)
    assert (d / "network.nc").read_bytes() == before
    assert list(PyPSAService.get_network().links.columns) == live_cols
    assert "pq_x_pu" not in PyPSAService.get_network().links.columns


def test_the_defaults_carry_the_grid_code_the_campus_study_applies(client, hub):
    """GK P6: with no saved campus settings the backend applies its default code, and says so."""
    from services import campus_electrical_service as campus

    name, _ = hub
    code = _defaults(client, name)["grid_code"]
    assert (
        code["value"] == campus.DEFAULTS["profile"]
        and code["source"] == "default"
        and code["title"]
    )
    # The applied code's Pst limit comes with it: the published-code route serves only a project's own codes,
    # so the form reads a shipped code's limit here.
    from gridspine.drivers.campus_study import load_grid_code

    want = load_grid_code(code["value"])["pst_limit"]
    pst = _defaults(client, name)["pst_limit"]
    assert pst["value"] == want["value"]
    assert pst["kind"] == ("assumed" if want["source"] == "assumed" else "grid_code")


def test_the_statcom_cost_is_the_pack_unit_annualised_at_the_project_rate():
    """GK P3: 450 k€ per 5 Mvar, 20 years, 3 %/yr O&M at 7 %: 90 000 × (0.07 / (1 − 1.07^−20) + 0.03)."""
    from services.compare.intent_inputs import statcom_cost

    value, note = statcom_cost(0.07)
    assert (
        value
        == pytest.approx(90_000 * (0.07 / (1 - 1.07**-20) + 0.03))
        == pytest.approx(11_195.36, abs=0.01)
    )
    assert "statcom.sc_33_5m.investment.overnight" in note and "7.0%" in note


def test_the_island_refusal_has_no_engine_words(client, hub):
    name, _ = hub
    r = client.post(URL.format(name), json=_body(_defaults(client, name), "BC"))
    assert (
        "sizing loop" not in r.json()["detail"]
        and "island_constraints" not in r.json()["detail"]
    )


def test_a_saved_grid_code_is_reported_as_saved(client, hub):
    import json

    from services import campus_electrical_service as campus

    name, d = hub
    path = d / campus.SUBDIR / campus.SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"profile": campus.DEFAULTS["profile"]}))
    assert _defaults(client, name)["grid_code"]["source"] == "saved"


@pytest.mark.parametrize(
    "code,profile,want",
    [
        (None, None, {"value": None, "reason": "No grid code is set."}),
        ("x", "raise", {"value": None, "reason": "The grid code could not be read."}),
        ("x", {}, {"value": None, "reason": "The grid code sets no Pst limit."}),
        (
            "x",
            {"pst_limit": {"value": 0}},
            {"value": None, "reason": "The grid code sets no Pst limit."},
        ),
        (
            "x",
            {"pst_limit": {"value": "1"}},
            {"value": None, "reason": "The grid code sets no Pst limit."},
        ),
        (
            "x",
            {"pst_limit": 0.9},
            {"value": 0.9, "source": "the grid code", "kind": "grid_code"},
        ),
        (
            "x",
            {"pst_limit": {"value": 1.0, "clause": "§ 5", "source": "code"}},
            {"value": 1.0, "source": "§ 5", "kind": "grid_code"},
        ),
        (
            "x",
            {"pst_limit": {"value": 0.8, "source": "assumed"}},
            {"value": 0.8, "source": "the grid code", "kind": "assumed"},
        ),
    ],
)
def test_pst_limit_reads_the_applied_code(monkeypatch, code, profile, want):
    from fastapi import HTTPException

    from routers import compare

    def load(row, c):
        assert c == code
        if profile == "raise":
            raise HTTPException(422, "bad")
        return profile

    monkeypatch.setattr("services.campus_electrical_service.grid_code_profile", load)
    got = compare._pst_limit(object(), code)
    assert {k: got.get(k) for k in want} == want
