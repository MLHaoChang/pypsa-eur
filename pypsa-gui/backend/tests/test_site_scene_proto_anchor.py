"""SC-00 N, D01 / V73: the confirmed site anchor (tests/site_scene_proto/anchor.py)."""
from __future__ import annotations

import json
import math

import pytest

from tests.site_scene_proto import anchor
from tests.site_scene_proto.contract import SceneError


def _body(lat, lng, source="manual", confirmed=True):
    return {"lat": lat, "lng": lng, "source": source, "confirmed": confirmed}


@pytest.mark.parametrize("lat,lng", [(None, 5.0), ("52", 5.0), (math.nan, 5.0), (52.0, math.inf),
                                     (-math.inf, 5.0), (True, 5.0)])
def test_missing_or_non_finite_coordinates_are_refused(lat, lng):
    with pytest.raises(SceneError) as e:
        anchor.validate_anchor(_body(lat, lng))
    assert e.value.kind == "invalid_input" and e.value.status == 422


@pytest.mark.parametrize("lat,lng,field", [(90.000001, 0.0, "lat"), (-91, 0.0, "lat"),
                                           (0.0, 180.5, "lng"), (0.0, -181, "lng")])
def test_out_of_range_is_refused_never_clamped(lat, lng, field):
    with pytest.raises(SceneError) as e:
        anchor.validate_anchor(_body(lat, lng))
    assert e.value.detail["field"] == field


def test_a_swapped_pair_is_refused_with_a_hint_not_corrected():
    # Munich written lng-first: lat=11.58 is fine, so this is NOT detectable ...
    assert anchor.validate_anchor(_body(11.58, 48.14))["lat"] == 11.58
    # ... but a longitude in the latitude slot that is out of range is refused, never swapped.
    with pytest.raises(SceneError) as e:
        anchor.validate_anchor(_body(151.21, -33.87))
    assert e.value.detail == {"field": "lat", "hint": "swapped"}


@pytest.mark.parametrize("lat,lng", [(-90, -180), (90, 180), (0.0, 0.0)])
def test_boundaries_and_confirmed_null_island_are_valid(lat, lng):
    assert anchor.validate_anchor(_body(lat, lng)) == {"lat": lat, "lng": lng, "source": "manual", "confirmed": True}


def test_unconfirmed_anchor_is_stored_but_admits_nothing(tmp_path):
    doc = anchor.write_anchor(tmp_path, _body(11.58, 48.14, confirmed=False), expected_revision=0)
    assert doc["revision"] == 1 and not anchor.admits_creation(doc)
    doc = anchor.write_anchor(tmp_path, _body(11.58, 48.14, confirmed=True), expected_revision=1)
    assert anchor.admits_creation(doc)


@pytest.mark.parametrize("bad", [{"source": "bus"}, {"source": None}, {"confirmed": "yes"}])
def test_a_bus_placeholder_or_untyped_confirmation_is_not_an_anchor(bad):
    with pytest.raises(SceneError):
        anchor.validate_anchor({**_body(52.0, 5.0), **bad})


def test_compare_and_swap_refuses_a_stale_revision(tmp_path):
    assert anchor.read_anchor(tmp_path) == {"version": 1, "revision": 0, "anchor": None}
    anchor.write_anchor(tmp_path, _body(52.0, 5.0), expected_revision=0)
    with pytest.raises(SceneError) as e:
        anchor.write_anchor(tmp_path, _body(48.0, 11.0), expected_revision=0)
    assert e.value.kind == "revision_conflict" and e.value.detail == {"current_revision": 1}
    assert anchor.read_anchor(tmp_path)["anchor"]["lat"] == 52.0
    with pytest.raises(SceneError):
        anchor.write_anchor(tmp_path, _body(48.0, 11.0), expected_revision=None)


def test_the_anchor_is_a_sidecar_not_a_sites_json_key(tmp_path):
    anchor.write_anchor(tmp_path, _body(52.0, 5.0), expected_revision=0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["site_anchor.json"]
    assert json.loads((tmp_path / "site_anchor.json").read_text())["anchor"]["source"] == "manual"
    # Clearing is an ordinary revisioned write.
    assert anchor.write_anchor(tmp_path, None, expected_revision=1)["anchor"] is None
