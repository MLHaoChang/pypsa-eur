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
    r = client.post("/api/projects/from_template/eh_datacenter", params={"name": "intent_hub"})
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
    inputs = {"link": d["link"]["value"], "ramp_mw_per_min": d["ramp_mw_per_min"]["value"], "sk_mva": 250.0,
              "pst_limit": 1.0, "transformer_library_mva": [40.0, 63.0, 80.0], "envelope": env}
    if pq:
        inputs["pq"] = {k: v["value"] for k, v in d["pq"].items()}
    return {"intents": [{"intent": i} for i in intents], "critical_mw": 34.49, "inputs": inputs}


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
    ramp = next(x for x in client.get(f"/api/campus-electrical/{name}/grid-readiness").json()["rows"]
                if x["id"] == "ramp_rate")
    assert got["ramp_mw_per_min"]["value"] == ramp["limit"] == 4.0
    assert got["ramp_mw_per_min"]["source"] == ramp["source"]
    env = read_envelope(d)
    assert got["envelope"]["value"]["max"] == env["max"]
    assert got["envelope"]["value"]["phases"] == env["phases"]
    assert set(got["pq"]) == PQ_FIELDS
    assert all(v["source"].startswith("assumed") and v["value"] > 0 for v in got["pq"].values())
    assert got["pq"]["s_n_mva"]["value"] == got["pq"]["p_cap_mw"]["value"] == 40.0


def test_the_comparison_runs_on_the_shipped_template(client, hub):
    import pypsa

    name, d = hub
    r = client.post(URL.format(name), json=_body(_defaults(client, name), "BC"))
    assert r.status_code == 202, r.text
    done = _wait(client, name)
    assert done["status"] == "done"
    assert [x["intent"] for x in done["rows"]] == ["B", "C"]
    assert all(x["status"] != "failed" for x in done["rows"]), [x["verdict"] for x in done["rows"]]
    saved = pypsa.Network(str(d / "network.nc"))
    assert "pq_x_pu" not in saved.links.columns          # the comparison never tags the project's network


def test_intent_a_without_a_backup_battery_fails_in_plain_words(client, hub):
    name, _ = hub
    assert client.post(URL.format(name), json=_body(_defaults(client, name), "AC")).status_code == 202
    row = _wait(client, name)["rows"][0]
    assert row["intent"] == "A" and row["status"] == "failed"
    assert "backup reserve" in row["verdict"] and "EnvelopeFloorsError" not in row["verdict"]


def test_without_point_of_connection_values_the_refusal_names_the_fields(client, hub):
    name, _ = hub
    r = client.post(URL.format(name), json=_body(_defaults(client, name), "BC", pq=False))
    assert r.status_code == 422
    text = r.json()["detail"]
    assert "AS-05" not in text and "PQ tags" not in text
    assert "point of connection" in text and "power factor" in text and "STATCOM cost" in text


@pytest.mark.parametrize("field,value", [("pf", 1.5), ("pf_req", 0.0), ("s_n_mva", -1.0), ("x_pu", -0.1),
                                         ("statcom_capital_cost", -5.0), ("p_cap_mw", 0.0)])
def test_a_bad_point_of_connection_value_is_422(client, hub, field, value):
    name, _ = hub
    body = _body(_defaults(client, name), "BC")
    body["inputs"]["pq"][field] = value
    assert client.post(URL.format(name), json=body).status_code == 422


def test_an_unknown_point_of_connection_field_is_422(client, hub):
    name, _ = hub
    body = _body(_defaults(client, name), "BC")
    body["inputs"]["pq"]["surprise"] = 1.0
    assert client.post(URL.format(name), json=body).status_code == 422
