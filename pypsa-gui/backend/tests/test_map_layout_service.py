"""
`services/map_layout_service.py` — the `map_layout.json` sidecar: document
shape, its validation, atomic I/O and the rename hook.

Plan: docs/superpowers/plans/2026-10-06-visual-layers-2-map-view.md, M1.
Modelled on `test_site_service.py` (the `sites.json` sidecar of plan 3 S0).

Route `points` are the INTERIOR waypoints of a branch in `[lng, lat]` order —
the ends are the two buses' coordinates and are not stored, so a bus drag
keeps the interior intact. One bend is a valid route, which is why the
minimum is one vertex, not two.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from services import map_layout_service as ml

EXAMPLE = {
    "version": 1,
    "routes": {
        "line:L1": {"points": [[6.8321, 53.4396], [6.8340, 53.4401]], "source": "user"},
        "link:HVDC": {"points": [[6.9, 53.5]], "source": "import"},
        "tr:T1": {"points": [[6.83, 53.44]], "source": "osm"},
    },
    "bubbles": {
        "Campus 33kV|Thermal": {"dx": 42.0, "dy": -18},
        "Campus 33kV|Storage": {"dx": 0, "dy": 30.5},
    },
}


def _doc(**top):
    d = copy.deepcopy(EXAMPLE)
    d.update(top)
    return d


# ── read ────────────────────────────────────────────────────────────────────

def test_empty_document_when_missing(tmp_path):
    assert ml.read_map_layout(tmp_path) == {"version": 1, "routes": {}, "bubbles": {}}


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", "42", "", '{"version": 1}', '{"routes": [], "bubbles": {}}'])
def test_corrupt_file_degrades_to_empty(tmp_path, raw):
    (tmp_path / ml.MAP_LAYOUT_FILE).write_text(raw)
    assert ml.read_map_layout(tmp_path) == ml.empty_document()


def test_unreadable_but_not_denied_degrades_to_empty(tmp_path):
    """A directory named map_layout.json is unusable, not denied — same as /layout."""
    (tmp_path / ml.MAP_LAYOUT_FILE).mkdir()
    assert ml.read_map_layout(tmp_path) == ml.empty_document()


def test_permission_error_is_raised_not_swallowed(tmp_path, monkeypatch):
    (tmp_path / ml.MAP_LAYOUT_FILE).write_text(json.dumps(EXAMPLE))
    real = pathlib.Path.read_text

    def deny(self, *a, **k):
        if self.name == ml.MAP_LAYOUT_FILE:
            raise PermissionError("denied")
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", deny)
    with pytest.raises(PermissionError):
        ml.read_map_layout(tmp_path)


def test_read_returns_what_was_written(tmp_path):
    ml.write_map_layout(tmp_path, EXAMPLE)
    assert ml.read_map_layout(tmp_path) == EXAMPLE


# ── validate ────────────────────────────────────────────────────────────────

def test_validate_accepts_example():
    ml.validate_map_layout(EXAMPLE)  # no raise


def test_validate_accepts_the_empty_document():
    ml.validate_map_layout(ml.empty_document())


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (lambda d: d.__setitem__("version", 2), "version"),
        (lambda d: d.pop("version"), "version"),
        (lambda d: d.__setitem__("routes", []), "routes must be an object"),
        (lambda d: d.pop("routes"), "routes must be an object"),
        (lambda d: d.__setitem__("bubbles", []), "bubbles must be an object"),
        (lambda d: d.pop("bubbles"), "bubbles must be an object"),
        # route keys
        (lambda d: d["routes"].__setitem__("L1", {"points": [[1, 1]], "source": "user"}), "routes: key 'L1'"),
        (lambda d: d["routes"].__setitem__("line:", {"points": [[1, 1]], "source": "user"}), "routes: key 'line:'"),
        (lambda d: d["routes"].__setitem__("Line:L1", {"points": [[1, 1]], "source": "user"}), "routes: key 'Line:L1'"),
        (lambda d: d["routes"].__setitem__("bus:B1", {"points": [[1, 1]], "source": "user"}), "routes: key 'bus:B1'"),
        # route values
        (lambda d: d["routes"].__setitem__("line:L1", "not an object"), "routes['line:L1']: must be an object"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", []), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].pop("points"), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[1.0]]), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[1.0, 2.0, 3.0]]), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [["6.8", 53.4]]), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[True, 53.4]]), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[float("nan"), 53.4]]), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[float("inf"), 53.4]]), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[181.0, 53.4]]), "out of range"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", [[6.8, -91.0]]), "out of range"),
        (lambda d: d["routes"]["line:L1"].__setitem__("points", {"lng": 6.8, "lat": 53.4}), "routes['line:L1'].points"),
        (lambda d: d["routes"]["line:L1"].__setitem__("source", "guess"), "routes['line:L1'].source"),
        (lambda d: d["routes"]["line:L1"].pop("source"), "routes['line:L1'].source"),
        # bubbles
        (lambda d: d["bubbles"].__setitem__("Campus 33kV", {"dx": 1, "dy": 1}), "bubbles: key 'Campus 33kV'"),
        (lambda d: d["bubbles"].__setitem__("|Thermal", {"dx": 1, "dy": 1}), "bubbles: key '|Thermal'"),
        (lambda d: d["bubbles"].__setitem__("B1|", {"dx": 1, "dy": 1}), "bubbles: key 'B1|'"),
        (lambda d: d["bubbles"].__setitem__("B1|Thermal", [1, 1]), "bubbles['B1|Thermal']"),
        (lambda d: d["bubbles"].__setitem__("B1|Thermal", {"dx": 1}), "bubbles['B1|Thermal']"),
        (lambda d: d["bubbles"].__setitem__("B1|Thermal", {"dx": "1", "dy": 1}), "bubbles['B1|Thermal']"),
        (lambda d: d["bubbles"].__setitem__("B1|Thermal", {"dx": float("nan"), "dy": 1}), "bubbles['B1|Thermal']"),
        (lambda d: d["bubbles"].__setitem__("B1|Thermal", {"dx": True, "dy": 1}), "bubbles['B1|Thermal']"),
    ],
)
def test_validate_rejects(mutate, needle):
    d = copy.deepcopy(EXAMPLE)
    mutate(d)
    with pytest.raises(ml.MapLayoutInvalid) as exc:
        ml.validate_map_layout(d)
    assert needle in str(exc.value), str(exc.value)


def test_validate_rejects_a_non_object():
    with pytest.raises(ml.MapLayoutInvalid):
        ml.validate_map_layout([1, 2])


def test_a_bus_name_may_itself_contain_the_bubble_separator():
    """`rpartition`: the category is what follows the LAST `|`."""
    d = _doc(bubbles={"North|South|Thermal": {"dx": 1, "dy": 2}})
    ml.validate_map_layout(d)


def test_a_single_interior_waypoint_is_a_valid_route():
    """
    One bend in a line is the most common route; the plan's '≥ 2 vertices'
    counted the ends, which this document does not store.
    """
    d = _doc(routes={"line:L1": {"points": [[6.8, 53.4]], "source": "user"}})
    ml.validate_map_layout(d)


def test_validate_preserves_unknown_keys(tmp_path):
    d = _doc(top_level_extra="kept")
    d["routes"]["line:L1"]["length_source"] = "route"   # M2's field, unknown today
    d["bubbles"]["Campus 33kV|Thermal"]["pinned"] = True
    ml.validate_map_layout(d)
    ml.write_map_layout(tmp_path, d)
    back = ml.read_map_layout(tmp_path)
    assert back["top_level_extra"] == "kept"
    assert back["routes"]["line:L1"]["length_source"] == "route"
    assert back["bubbles"]["Campus 33kV|Thermal"]["pinned"] is True


# ── write ───────────────────────────────────────────────────────────────────

def test_write_is_atomic_and_compact(tmp_path):
    ml.write_map_layout(tmp_path, EXAMPLE)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [ml.MAP_LAYOUT_FILE], f"temp file left behind: {names}"
    raw = (tmp_path / ml.MAP_LAYOUT_FILE).read_text()
    assert "\n" not in raw.strip() and ": " not in raw
    assert json.loads(raw) == EXAMPLE


def test_write_rejects_oversize(tmp_path):
    d = _doc(padding="x" * (ml.MAX_MAP_LAYOUT_BYTES + 1))
    with pytest.raises(ml.MapLayoutTooLarge):
        ml.write_map_layout(tmp_path, d)
    assert not (tmp_path / ml.MAP_LAYOUT_FILE).exists()


def test_write_honours_a_caller_supplied_cap(tmp_path):
    """The route passes the router's `_MAX_LAYOUT_BYTES`; one cap for both sidecars."""
    with pytest.raises(ml.MapLayoutTooLarge):
        ml.write_map_layout(tmp_path, EXAMPLE, max_bytes=16)
    assert not (tmp_path / ml.MAP_LAYOUT_FILE).exists()


# ── rename ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cls, kind", [("Line", "line"), ("Link", "link"), ("Transformer", "tr")])
def test_route_key_maps_the_three_branch_classes(cls, kind):
    assert ml.route_key(cls, "X") == f"{kind}:X"


@pytest.mark.parametrize("cls", ["Bus", "Generator", "Load", "Carrier", ""])
def test_route_key_is_none_for_classes_without_a_route(cls):
    assert ml.route_key(cls, "X") is None


def test_rename_component_moves_the_route_key():
    d = copy.deepcopy(EXAMPLE)
    assert ml.rename_component(d, "Line", "L1", "L1 renamed") is True
    assert "line:L1" not in d["routes"]
    assert d["routes"]["line:L1 renamed"] == EXAMPLE["routes"]["line:L1"]
    # The other kinds with similar names are untouched.
    assert d["routes"]["link:HVDC"] == EXAMPLE["routes"]["link:HVDC"]
    assert d["bubbles"] == EXAMPLE["bubbles"]


def test_rename_component_is_scoped_to_one_kind():
    d = _doc(routes={
        "line:X": {"points": [[1, 1]], "source": "user"},
        "link:X": {"points": [[2, 2]], "source": "user"},
    })
    assert ml.rename_component(d, "Link", "X", "Y") is True
    assert sorted(d["routes"]) == ["line:X", "link:Y"]


def test_rename_component_missing_key_is_a_noop():
    d = copy.deepcopy(EXAMPLE)
    before = copy.deepcopy(d)
    assert ml.rename_component(d, "Line", "nope", "still nope") is False
    assert d == before


def test_rename_component_of_a_class_without_routes_is_a_noop():
    d = copy.deepcopy(EXAMPLE)
    before = copy.deepcopy(d)
    assert ml.rename_component(d, "Generator", "L1", "G") is False
    assert d == before


def test_rename_on_disk_rewrites_the_file(tmp_path):
    ml.write_map_layout(tmp_path, EXAMPLE)
    ml.rename_component_on_disk(tmp_path, "Transformer", "T1", "T2")
    back = ml.read_map_layout(tmp_path)
    assert "tr:T2" in back["routes"] and "tr:T1" not in back["routes"]


def test_rename_on_disk_does_not_write_when_nothing_changed(tmp_path):
    ml.write_map_layout(tmp_path, EXAMPLE)
    before = (tmp_path / ml.MAP_LAYOUT_FILE).stat().st_mtime_ns
    ml.rename_component_on_disk(tmp_path, "Line", "absent", "x")
    ml.rename_component_on_disk(tmp_path, "Generator", "gas", "x")
    assert (tmp_path / ml.MAP_LAYOUT_FILE).stat().st_mtime_ns == before


def test_rename_on_disk_without_a_project_dir_is_a_noop():
    ml.rename_component_on_disk(None, "Line", "a", "b")  # no raise, nothing to touch


def test_rename_on_disk_same_name_does_not_read(tmp_path, monkeypatch):
    monkeypatch.setattr(ml, "read_map_layout", lambda *a, **k: pytest.fail("must not read"))
    ml.rename_component_on_disk(tmp_path, "Line", "same", "same")


def test_rename_on_disk_swallows_and_logs_failures(tmp_path, monkeypatch, caplog):
    ml.write_map_layout(tmp_path, EXAMPLE)

    def boom(*a, **k):
        raise OSError("read-only volume")

    monkeypatch.setattr(ml, "write_map_layout", boom)
    ml.rename_component_on_disk(tmp_path, "Line", "L1", "L2")  # must not raise
    assert any("map_layout.json rename hook failed" in r.getMessage() for r in caplog.records), caplog.text


# ── M2: length provenance beside the route ──────────────────────────────────
# PyPSA has no column for "where did this length come from", so the document
# carries it: `lengths: {"<kind>:<name>": {"source": "typed" | "chord" | "route"}}`.
# A document without the key (the M1 migration, any pre-M2 file) reads as
# "unknown", which is treated as `typed` — nothing was ever derived for it.

def test_length_source_defaults_to_typed_when_absent():
    d = copy.deepcopy(EXAMPLE)
    assert "lengths" not in d
    assert ml.length_source(d, "line:L1") == "typed"
    assert ml.length_source(ml.empty_document(), "link:K") == "typed"


def test_set_length_source_creates_the_table_and_reads_back():
    d = copy.deepcopy(EXAMPLE)
    ml.set_length_source(d, "line:L1", "route")
    ml.set_length_source(d, "link:HVDC", "chord")
    assert d["lengths"] == {"line:L1": {"source": "route"}, "link:HVDC": {"source": "chord"}}
    assert ml.length_source(d, "line:L1") == "route"
    assert ml.length_source(d, "link:HVDC") == "chord"
    ml.validate_map_layout(d)  # what we write must pass what PUT checks


def test_set_length_source_rejects_an_unknown_source():
    with pytest.raises(ml.MapLayoutInvalid):
        ml.set_length_source(copy.deepcopy(EXAMPLE), "line:L1", "guess")


def test_length_source_tolerates_a_malformed_table():
    """A hand-edited or partial table degrades to 'typed', never raises."""
    assert ml.length_source(_doc(lengths="nope"), "line:L1") == "typed"
    assert ml.length_source(_doc(lengths={"line:L1": "route"}), "line:L1") == "typed"
    assert ml.length_source(_doc(lengths={"line:L1": {"source": "magic"}}), "line:L1") == "typed"


def test_validate_accepts_lengths_and_keeps_unknown_keys_inside():
    d = _doc(lengths={"line:L1": {"source": "route", "measured_at": "2026-10-06"}})
    ml.validate_map_layout(d)


@pytest.mark.parametrize(
    "lengths, needle",
    [
        ([], "lengths must be an object"),
        ({"L1": {"source": "route"}}, "lengths: key 'L1'"),
        ({"bus:B1": {"source": "route"}}, "lengths: key 'bus:B1'"),
        ({"line:L1": "route"}, "lengths['line:L1']"),
        ({"line:L1": {"source": "guess"}}, "lengths['line:L1'].source"),
        ({"line:L1": {}}, "lengths['line:L1'].source"),
    ],
)
def test_validate_rejects_a_bad_lengths_table(lengths, needle):
    with pytest.raises(ml.MapLayoutInvalid) as exc:
        ml.validate_map_layout(_doc(lengths=lengths))
    assert needle in str(exc.value), str(exc.value)


def test_rename_component_moves_the_length_provenance_too():
    d = _doc(lengths={"line:L1": {"source": "route"}, "link:HVDC": {"source": "chord"}})
    assert ml.rename_component(d, "Line", "L1", "L9") is True
    assert "line:L1" not in d["lengths"] and d["lengths"]["line:L9"] == {"source": "route"}
    assert d["lengths"]["link:HVDC"] == {"source": "chord"}


def test_rename_component_re_keys_provenance_without_a_route():
    """A typed-then-derived branch may have provenance but no bend."""
    d = _doc(lengths={"link:K": {"source": "chord"}})
    assert ml.rename_component(d, "Link", "K", "K2") is True
    assert d["lengths"] == {"link:K2": {"source": "chord"}}


def test_route_points_returns_interior_lnglat_or_none():
    d = copy.deepcopy(EXAMPLE)
    assert ml.route_points(d, "line:L1") == [[6.8321, 53.4396], [6.8340, 53.4401]]
    assert ml.route_points(d, "line:nope") is None
    assert ml.route_points(_doc(routes={"line:L1": {"points": "bad"}}), "line:L1") is None
