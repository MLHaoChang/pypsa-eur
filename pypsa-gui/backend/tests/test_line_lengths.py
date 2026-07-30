"""
Line lengths must never be measured to a bus that has no location.

PyPSA's Bus.x / Bus.y default to 0.0. Before this guard, `_bus_coord` returned
(0.0, 0.0) for a bus nobody had placed, so `recalculate_lengths` computed the
great-circle distance from a real substation to 0°N 0°E in the Gulf of Guinea
and wrote several thousand kilometres into n.lines.length as fact.

`routers/network.py:386-394` documents an earlier instance of the same bug
class — a partial PUT whose Pydantic default made every bus look like it had
moved to the origin. That one was fixed at the request layer. This is the
remaining path: coordinates that genuinely are (0, 0).
"""
from __future__ import annotations


def _place(client, name: str, x: float, y: float):
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 380.0, "x": x, "y": y})
    assert r.status_code == 201, r.text


def _unplaced(client, name: str):
    """A bus created the way the GUI creates one: no coordinates supplied."""
    r = client.post("/api/network/buses", json={"name": name, "v_nom": 380.0})
    assert r.status_code == 201, r.text


def _line(client, name: str, bus0: str, bus1: str, length: float):
    # A positive length is the manual-override path (create_line only
    # auto-fills when the caller passes length <= 0), so this pins a value the
    # recalculation must be seen to leave alone.
    r = client.post("/api/network/lines", json={
        "name": name, "bus0": bus0, "bus1": bus1, "length": length, "s_nom": 100.0,
    })
    assert r.status_code == 201, r.text


def _lengths(client) -> dict[str, float]:
    return {ln["name"]: ln["length"] for ln in client.get("/api/network/lines").json()}


def test_recalculate_skips_a_line_touching_an_unplaced_bus(client):
    _place(client, "COLOGNE", 6.960, 50.938)
    _unplaced(client, "NOWHERE")
    _line(client, "L1", "COLOGNE", "NOWHERE", 42.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "skipped": 1, "total": 1}

    # The pre-existing value survives untouched. Without the guard this became
    # the haversine distance from Cologne to Null Island — about 5,600 km.
    assert _lengths(client)["L1"] == 42.0


def test_recalculate_still_measures_a_line_between_two_placed_buses(client):
    # The guard must not turn into "skip everything": this is the case the
    # feature exists for. Cologne -> Berlin is ~475 km.
    _place(client, "COLOGNE", 6.960, 50.938)
    _place(client, "BERLIN", 13.405, 52.520)
    _line(client, "L1", "COLOGNE", "BERLIN", 1.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": 0, "total": 1}
    assert 460.0 < _lengths(client)["L1"] < 490.0


def test_a_bus_on_the_prime_meridian_is_still_placed(client):
    # D1: the rule is BOTH coordinates zero. Greenwich has x == 0 and must
    # keep working, or the guard has traded one silent corruption for another.
    _place(client, "GREENWICH", 0.0, 51.478)
    _place(client, "COLOGNE", 6.960, 50.938)
    _line(client, "L1", "GREENWICH", "COLOGNE", 1.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 1, "skipped": 0, "total": 1}
    assert 480.0 < _lengths(client)["L1"] < 520.0


def test_recalculate_skips_a_line_touching_an_out_of_range_bus(client):
    # The frontend's busLatLng (utils/geo.ts) rejects |lat| > 90 / |lng| > 180
    # so an out-of-range bus is hidden on the map and counted as "unplaced" in
    # UnplacedBusesPanel. Before this guard, _bus_coord had no range check at
    # all, so recalculate_lengths still measured a real haversine distance to
    # that impossible point and wrote it into n.lines.length as fact — a bus
    # the map shows as MISSING was silently treated as PLACED by the model.
    # y = 91 is reachable: PropertiesPanel's Latitude field is an unbounded
    # NumInput and BusCreate.y (schemas.py) is a plain unbounded float.
    _place(client, "COLOGNE", 6.960, 50.938)
    _place(client, "OUT_OF_RANGE", 6.960, 91.0)
    _line(client, "L1", "COLOGNE", "OUT_OF_RANGE", 42.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 0, "skipped": 1, "total": 1}

    # The pre-existing value survives untouched — same contract as an
    # unplaced (0, 0) bus.
    assert _lengths(client)["L1"] == 42.0
