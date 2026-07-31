"""
Changing a line's length must not silently change its per-km impedance.

r/x/b are stored absolute; the properties form presents them per-km (it
divides by length to display and multiplies back to save). Both backend paths
that change length — a bus move and the Ruler button — rewrote ONLY length, so
the physical per-km value silently changed by the length ratio. Click-to-place
makes that easy to hit: LineCreate.length defaults to 1.0 km, so real geography
rescales a hand-built network by orders of magnitude.

Length stays automatic — it follows from coordinates. Impedance is a modelling
choice and is never written without consent, so these endpoints PREVIEW and a
separate one applies.
"""
from __future__ import annotations


def _bus(client, name, x, y):
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 380.0, "x": x, "y": y})
    assert r.status_code == 201, r.text


def _line(client, name, bus0, bus1, length, r_ohm, x_ohm, b_s):
    resp = client.post("/api/network/lines", json={
        "name": name, "bus0": bus0, "bus1": bus1, "length": length,
        "r": r_ohm, "x": x_ohm, "b": b_s, "s_nom": 500.0,
    })
    assert resp.status_code == 201, resp.text


def _lines(client):
    return {ln["name"]: ln for ln in client.get("/api/network/lines").json()}


def test_recalculate_previews_the_rescale_without_writing_it(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 3.0, 17.5, 0.00015)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1

    (prev,) = body["rescale"]
    assert prev["name"] == "L1"
    assert prev["old_length"] == 1.0
    assert 460.0 < prev["new_length"] < 490.0
    assert prev["skipped_reason"] is None
    # Per-km preserved: new/old == length ratio, identically for r, x and b.
    ratio = prev["new_length"] / prev["old_length"]
    assert abs(prev["new"]["r"] - 3.0 * ratio) < 1e-6
    assert abs(prev["new"]["x"] - 17.5 * ratio) < 1e-6
    assert abs(prev["new"]["b"] - 0.00015 * ratio) < 1e-9
    assert abs(prev["rel_change"] - (ratio - 1.0)) < 1e-6

    # PREVIEW ONLY. The length is rewritten (geometry); the impedance is not.
    after = _lines(client)["L1"]
    assert after["r"] == 3.0 and after["x"] == 17.5 and after["b"] == 0.00015
    assert 460.0 < after["length"] < 490.0


def test_apply_writes_only_the_named_lines(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 3.0, 17.5, 0.00015)
    _line(client, "L2", "COLOGNE", "BERLIN", 1.0, 9.0, 21.0, 0.00030)

    r = client.post("/api/network/lines/rescale_impedances", json={
        "lines": [{"name": "L1", "r": 100.0, "x": 200.0, "b": 0.5}],
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": []}

    after = _lines(client)
    assert (after["L1"]["r"], after["L1"]["x"], after["L1"]["b"]) == (100.0, 200.0, 0.5)
    assert (after["L2"]["r"], after["L2"]["x"], after["L2"]["b"]) == (9.0, 21.0, 0.00030)


def test_apply_reports_an_unknown_line_instead_of_creating_it(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    r = client.post("/api/network/lines/rescale_impedances", json={
        "lines": [{"name": "GHOST", "r": 1.0, "x": 2.0, "b": 3.0}],
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "skipped": [{"name": "GHOST", "reason": "unknown-line"}]}
    assert "GHOST" not in _lines(client)


def test_a_zero_length_line_is_reported_not_guessed(client):
    # Per-km is undefined when the old length is 0, so there is nothing to
    # preserve. Reporting beats inventing an impedance.
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 0.0, 3.0, 17.5, 0.00015)

    body = client.post("/api/network/lines/recalculate_lengths").json()
    (prev,) = body["rescale"]
    assert prev["skipped_reason"] == "old_length<=0"
    assert prev["new"] == prev["old"]


def test_an_all_zero_impedance_line_produces_no_preview(client):
    # Scaling zero by anything is zero — there is no choice to offer.
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 0.0, 0.0, 0.0)

    body = client.post("/api/network/lines/recalculate_lengths").json()
    assert body["rescale"] == []


def test_moving_a_bus_previews_its_connected_lines(client):
    _bus(client, "COLOGNE", 6.960, 50.938)
    _bus(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0, 3.0, 17.5, 0.00015)

    r = client.put("/api/network/buses/BERLIN", json={
        "name": "BERLIN", "v_nom": 380.0, "x": 2.35, "y": 48.86,   # -> Paris
    })
    assert r.status_code == 200, r.text
    (prev,) = r.json()["rescale"]
    assert prev["name"] == "L1"
    assert prev["skipped_reason"] is None
    assert prev["new"]["r"] > 3.0
    # Still preview-only.
    assert _lines(client)["L1"]["r"] == 3.0
