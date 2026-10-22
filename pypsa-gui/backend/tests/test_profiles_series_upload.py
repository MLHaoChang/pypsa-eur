"""A measured file for one field, through the editors' "Measured file" tab.

`POST /profiles/series/upload` reads the row's template layout (the model's
snapshots, a column named after the component or a single column) into a
pending artifact on the snapshot clock, like `from-current`; `/profiles/apply`
writes it with the same old → new receipt and provenance "measured". A file
that does not fit its field is a `series_upload` refusal saying what to change.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pypsa
import pytest

IDX = pd.date_range("2025-01-01", periods=8760, freq="h")


def _network() -> pypsa.Network:
    n = pypsa.Network()
    IDX.name = "snapshot"
    n.set_snapshots(IDX)
    n.add("Bus", "site")
    n.add("Generator", "gt1", bus="site", p_nom=50.0, marginal_cost=60.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    assert client.post("/api/projects/Upload Site",
                       params={"force": True, "rebind": True}).status_code == 200
    return "Upload Site"


def _csv(values, *, column="gt1", index=IDX) -> bytes:
    frame = pd.DataFrame({column: values}, index=index)
    frame.index.name = "timestamp"
    return frame.to_csv().encode()


def _upload(client, project, blob, attribute="p_max_pu"):
    return client.post(
        f"/api/projects/{project}/profiles/series/upload",
        data={"component_type": "generators", "attribute": attribute, "component": "gt1"},
        files={"file": ("gt1.csv", io.BytesIO(blob), "text/csv")})


def test_a_measured_file_previews_and_applies_old_to_new(client, project):
    values = np.where(IDX.month.isin([6, 7]), 0.5, 1.0)
    resp = _upload(client, project, _csv(values))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provenance"] == "measured" and body["source"] == "upload"
    assert body["stats"]["gt1"]["min"] == pytest.approx(0.5)
    applied = client.post(f"/api/projects/{project}/profiles/apply",
                          json={"job_id": body["job_id"], "component": "gt1",
                                "component_type": "generators", "attribute": "p_max_pu"})
    assert applied.status_code == 200, applied.text
    assert applied.json()["before"]["min"] == pytest.approx(1.0)
    assert applied.json()["min"] == pytest.approx(0.5)


def test_a_single_unnamed_column_is_read_as_the_component(client, project):
    resp = _upload(client, project, _csv(np.full(len(IDX), 70.0), column="price"),
                   attribute="marginal_cost")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stats"]["gt1"]["max"] == pytest.approx(70.0)


@pytest.mark.parametrize("blob, says", [
    (_csv(np.full(len(IDX), 80.0)), "looks like a percentage: divide by 100"),
    (_csv(np.ones(100), index=IDX[:100]), "no value for 8660 of the model's 8760 hours"),
    (pd.DataFrame({"a": np.ones(len(IDX)), "b": np.ones(len(IDX))}, index=IDX).to_csv().encode(),
     "none is named 'gt1'"),
])
def test_a_file_that_does_not_fit_says_what_to_change(client, project, blob, says):
    resp = _upload(client, project, blob)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error_kind"] == "series_upload"
    assert says in detail["message"]


@pytest.mark.parametrize("blob, says", [
    (pd.concat([pd.DataFrame({"gt1": np.ones(len(IDX))}, index=IDX),
                pd.DataFrame({"gt1": [1.0]}, index=IDX[:1])]).to_csv().encode(), "twice"),
    (_csv(np.where(np.arange(len(IDX)) == 5, np.inf, 1.0)), "not a finite number"),
])
def test_a_repeated_hour_or_an_infinite_value_is_refused(client, project, blob, says):
    resp = _upload(client, project, blob)
    assert resp.status_code == 422, resp.text
    assert says in resp.json()["detail"]["message"]
