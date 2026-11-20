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


@pytest.mark.parametrize("mw", [True, False])
def test_d3_a_bool_mw_is_refused_on_the_bool_path(client, tmp_projects_dir, mw):
    """GK B D3: a bool is refused before the number checks. True would read as
    1 MW (accepted) and False as 0 (NOT_ABOVE_ZERO); both read NOT_A_NUMBER."""
    name = _create(client, "eh_datacenter_staged", "staged02b_bool")
    r = _put(client, name, link=LINK, period=2030, mw=mw)
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == {
        "error_kind": "energization_invalid_mw",
        "message": E.NOT_A_NUMBER,
    }
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


# ── GK A 32 G2: the network is read under the lock, seen at lock entry ──────


class _SwapOnRouteEntry:
    """The project lock, with the network replaced (as a reload or an undo
    does) just as the route enters it: a route that read the network before
    taking the lock writes the edit to a network that is no longer the
    project's (GK A 32, mutant M23)."""

    def __init__(self, lock, state):
        self._lock, self._state = lock, state

    def acquire(self, *a, **kw):
        return self._lock.acquire(*a, **kw)

    def release(self):
        self._lock.release()

    def __enter__(self):
        import sys

        from services.pypsa_service import PyPSAService

        caller = sys._getframe(1).f_code.co_name
        if caller == "put_flow_energization" and not self._state["swapped"]:
            ctx = PyPSAService.get_active_context()
            old = ctx.network
            new = old.copy()
            new.links_t[E.ATTR] = E.energization_frame(old).copy()
            ctx.network = new
            self._state["swapped"] = new
        entered = self._lock.__enter__()
        self._state["depth"] += 1
        return entered

    def __exit__(self, *exc):
        self._state["depth"] -= 1
        return self._lock.__exit__(*exc)


def test_g2_the_route_reads_the_network_under_its_lock(
    client, tmp_projects_dir, monkeypatch
):
    import sys

    from services.pypsa_service import PyPSAService

    name = _create(client, "eh_datacenter_staged", "staged02b_lock")
    state = {"swapped": None, "depth": 0}
    real_lock, real_network = PyPSAService.get_lock, PyPSAService.get_network
    reads = []

    def get_network():
        if sys._getframe(1).f_code.co_name == "put_flow_energization":
            reads.append(state["depth"])
        return real_network()

    monkeypatch.setattr(
        PyPSAService,
        "get_lock",
        staticmethod(lambda: _SwapOnRouteEntry(real_lock(), state)),
    )
    monkeypatch.setattr(PyPSAService, "get_network", staticmethod(get_network))
    r = _put(client, name, link=LINK, period=2030, mw=30)
    assert r.status_code == 200, r.text
    assert state["swapped"] is not None, "the route never entered the lock"
    assert reads and all(d > 0 for d in reads), (
        "the route read the network outside the lock"
    )
    # The edit is on the network that is the project's after the lock was taken.
    assert E.by_period(state["swapped"], LINK)["by_period"]["2030"] == 30.0
    monkeypatch.undo()
    assert _row(client, name)["energization"][0]["by_period"]["2030"] == 30.0
