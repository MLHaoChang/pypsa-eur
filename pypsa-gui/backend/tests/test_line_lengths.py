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
    body = r.json()
    assert body["updated"] == 0 and body["skipped"] == 1 and body["total"] == 1
    # Task 3: recalculate_lengths also gains "rescale" (impedance-preview
    # entries). This line was skipped (unplaced bus), so it never reaches the
    # preview step — the field exists but is empty. Not this file's concern;
    # see test_line_rescale.py for the preview's own behaviour.
    assert body["rescale"] == []

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
    body = r.json()
    assert body["updated"] == 1 and body["skipped"] == 0 and body["total"] == 1
    # Task 3: the length actually changed (1.0 -> ~475 km) and L1 carries
    # LineCreate's non-zero default x (0.01), so a rescale preview IS offered.
    # Its content is test_line_rescale.py's concern, not this file's — just
    # confirm the shape.
    assert len(body["rescale"]) == 1 and body["rescale"][0]["name"] == "L1"
    assert 460.0 < _lengths(client)["L1"] < 490.0


def test_a_bus_on_the_prime_meridian_is_still_placed(client):
    # D1: the rule is BOTH coordinates zero. Greenwich has x == 0 and must
    # keep working, or the guard has traded one silent corruption for another.
    _place(client, "GREENWICH", 0.0, 51.478)
    _place(client, "COLOGNE", 6.960, 50.938)
    _line(client, "L1", "GREENWICH", "COLOGNE", 1.0)

    r = client.post("/api/network/lines/recalculate_lengths")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == 1 and body["skipped"] == 0 and body["total"] == 1
    # Task 3: same as above — length changed and default x is non-zero.
    assert len(body["rescale"]) == 1 and body["rescale"][0]["name"] == "L1"
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
    body = r.json()
    assert body["updated"] == 0 and body["skipped"] == 1 and body["total"] == 1
    # Task 3: skipped line never reaches the preview step.
    assert body["rescale"] == []

    # The pre-existing value survives untouched — same contract as an
    # unplaced (0, 0) bus.
    assert _lengths(client)["L1"] == 42.0


# ── Bus renames must take their dependents with them ─────────────────────────
# A bus has two rename paths and they did not agree. `POST /buses/{name}/rename`
# uses PyPSA's `rename_component_names`, which re-points every referencing
# component. `PUT /buses/{name}` with a changed `name` goes through
# `_update_component`, which renames by remove+add — and remove+add does NOT
# touch `loads.bus` / `generators.bus` / `lines.bus0` at all. The Properties
# panel's Bus edit card uses the PUT, so renaming a bus there silently orphaned
# everything attached to it: the components kept pointing at a name no bus had
# any more. It surfaces later, at the preflight, as
#   bus_ref_unknown: bus='Bus 5' does not match any bus
# and the orphaned load contributes nothing to the solve in the meantime.

def _load(client, name: str, bus: str):
    r = client.post("/api/network/loads", json={"name": name, "bus": bus, "p_set": 10.0})
    assert r.status_code == 201, r.text


def _gen(client, name: str, bus: str):
    r = client.post("/api/network/generators", json={"name": name, "bus": bus, "p_nom": 5.0})
    assert r.status_code == 201, r.text


def test_bus_rename_via_put_repoints_every_dependent(client):
    _place(client, "Bus 5", 6.960, 50.938)
    _place(client, "Bus 6", 7.100, 51.000)
    _load(client, "H5 Heat", "Bus 5")
    _gen(client, "G5", "Bus 5")
    _line(client, "L56", "Bus 5", "Bus 6", 42.0)

    # Exactly what PropertiesPanel's Bus card sends: the full current row with
    # `name` changed.
    r = client.put("/api/network/buses/Bus 5", json={
        "name": "Bus 5 (Heat)", "v_nom": 380.0, "x": 6.960, "y": 50.938,
    })
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Bus 5 (Heat)"

    buses = {b["name"] for b in client.get("/api/network/buses").json()}
    assert buses == {"Bus 5 (Heat)", "Bus 6"}

    loads = {ld["name"]: ld["bus"] for ld in client.get("/api/network/loads").json()}
    gens = {g["name"]: g["bus"] for g in client.get("/api/network/generators").json()}
    lines = {ln["name"]: (ln["bus0"], ln["bus1"]) for ln in client.get("/api/network/lines").json()}

    assert loads["H5 Heat"] == "Bus 5 (Heat)"
    assert gens["G5"] == "Bus 5 (Heat)"
    assert lines["L56"] == ("Bus 5 (Heat)", "Bus 6")

    # The preflight is where the user actually met this, so assert the symptom
    # itself is gone rather than only the DataFrame state.
    pre = client.post("/api/simulation/preflight")
    if pre.status_code == 200:
        codes = {i["code"] for i in pre.json().get("issues", [])}
        assert "bus_ref_unknown" not in codes


def test_bus_rename_via_put_refuses_to_collide_with_an_existing_bus(client):
    _place(client, "Bus 5", 6.960, 50.938)
    _place(client, "Bus 6", 7.100, 51.000)
    _load(client, "H5 Heat", "Bus 5")

    r = client.put("/api/network/buses/Bus 5", json={
        "name": "Bus 6", "v_nom": 380.0, "x": 6.960, "y": 50.938,
    })
    # Renaming onto a name that already exists must not silently merge the two
    # buses (and re-point Bus 5's dependents onto Bus 6).
    assert r.status_code == 409, r.text
    buses = {b["name"] for b in client.get("/api/network/buses").json()}
    assert buses == {"Bus 5", "Bus 6"}
    loads = {ld["name"]: ld["bus"] for ld in client.get("/api/network/loads").json()}
    assert loads["H5 Heat"] == "Bus 5"
