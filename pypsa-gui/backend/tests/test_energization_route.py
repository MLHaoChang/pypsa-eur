# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""template-e2e 02b (GK B E5): `PUT /api/projects/{name}/flow/energization` behaves
as an asset edit does (undo, edit counter, unsaved mark, change log, save and reload),
refuses with a typed 422 and writes nothing, and the connection row reads it back.
The writer itself is pinned in `test_energization_per_period.py`."""

from __future__ import annotations

import numpy as np
import pytest

from services.adequacy import energization as E

LINK = "grid_import"


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


def test_the_route_sets_one_period_with_undo_revision_log_and_reload(
    client, tmp_projects_dir
):
    from services import change_log_service

    name = _create(client, "eh_datacenter_staged", "staged02b")
    assert _row(client, name)["energization"] == [{"link": LINK, "by_period": AS_BUILT}]
    info = client.get("/api/network/undo/info").json()
    r = _put(client, name, link=LINK, period=2030, mw=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["message"]
        == f"The energized capacity of {LINK} for 2030 is now 30 MW (was 55 MW)."
    )
    assert body["period"] == "2030" and body["note"] is None and "series" not in body
    after = client.get("/api/network/undo/info").json()
    assert after["depth"] == info["depth"] + 1  # one undo step
    assert after["network_revision"] > info["network_revision"]
    assert after["unsaved"] is True
    assert change_log_service.get_all()[0]["description"] == body["message"]
    assert _row(client, name)["energization"][0]["by_period"]["2030"] == 30.0
    # Undo restores the period.
    assert client.post("/api/network/undo").status_code == 200
    assert _row(client, name)["energization"][0]["by_period"] == AS_BUILT
    # Save and reload: the edit survives.
    assert _put(client, name, link=LINK, period="2035", mw=40.0).status_code == 200
    assert (
        client.post(f"/api/projects/{name}", params={"force": True}).status_code == 200
    )
    assert client.get(f"/api/projects/{name}").status_code == 200
    assert _row(client, name)["energization"][0]["by_period"] == {
        **AS_BUILT,
        "2035": 40.0,
    }


def test_the_route_names_the_rating_it_does_not_raise(client, tmp_projects_dir):
    name = _create(client, "eh_datacenter_staged", "staged02b_rating")
    r = _put(client, name, link=LINK, period=2030, mw=500)
    assert r.status_code == 200, r.text
    assert r.json()["message"].endswith(
        f"(was 55 MW). This is above the link's rating ({r.json()['p_nom_mw']:g} MW): "
        "it doesn't raise the link's capacity."
    )


@pytest.mark.parametrize(
    "body, kind",
    [
        ({"link": LINK, "period": 2030, "mw": True}, "energization_invalid_mw"),
        ({"link": LINK, "period": 2030, "mw": 0}, "energization_invalid_mw"),
        ({"link": LINK, "period": 2030, "mw": "30"}, "energization_invalid_mw"),
        (
            {"link": "site_transformer", "period": 2030, "mw": 30},
            "energization_no_schedule",
        ),
        ({"link": LINK, "period": 2031, "mw": 30}, "energization_unknown_period"),
        ({"link": LINK, "mw": 30}, "energization_unknown_period"),
        ({"link": LINK, "period": 2030}, "energization_invalid_request"),
        (
            {"link": LINK, "period": 2030, "mw": 30, "scale": 2},
            "energization_invalid_request",
        ),
        ({"link": 3, "period": 2030, "mw": 30}, "energization_invalid_request"),
    ],
)
def test_each_refusal_is_a_typed_422_and_writes_nothing(
    client, tmp_projects_dir, body, kind
):
    name = _create(client, "eh_datacenter_staged", "staged02b_refused")
    info = client.get("/api/network/undo/info").json()
    r = _put(client, name, **body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == kind
    assert client.get("/api/network/undo/info").json()["depth"] == info["depth"]
    assert _row(client, name)["energization"][0]["by_period"] == AS_BUILT


def test_a_period_that_varies_is_refused_by_the_route_and_shown_as_a_range(
    client, tmp_projects_dir, bound_ctx
):
    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_datacenter_staged", "staged02b_varies")
    with bound_ctx(client), PyPSAService.get_lock():
        frame = E.energization_frame(PyPSAService.get_network())
        rows = np.flatnonzero(frame.index.get_level_values(0) == 2035)
        frame.iloc[rows[:5], 0] = 30.0
        frame.iloc[rows[5:], 0] = 40.0
    r = _put(client, name, link=LINK, period=2035, mw=35)
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "error_kind": "energization_period_not_flat",
        "message": "2035 varies 30–40 MW: set it on the network.",
    }


def test_a_template_without_a_schedule_keeps_the_single_limit(client, tmp_projects_dir):
    """UX condition 5: no energization column, no by_period on the row."""
    name = _create(client, "eh_datacenter", "single02b")
    row = _row(client, name)
    assert "energization" not in row and row["connection_limit"]["mw"] is not None
    r = _put(client, name, link=LINK, period="all", mw=30)
    assert (
        r.status_code == 422
        and r.json()["detail"]["error_kind"] == "energization_no_schedule"
    )
