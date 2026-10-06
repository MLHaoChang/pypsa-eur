"""
Route length = the geodesic length of bus0 → interior waypoints → bus1,
haversine per segment. Plan M2: `services/network_geometry.route_length_km`.
"""
from __future__ import annotations

import math

import pytest

from services.network_geometry import _haversine_km, route_length_km

# One degree of latitude on the 6371 km sphere.
ONE_DEGREE_KM = math.pi * 6371.0 / 180.0   # ≈ 111.19


def test_one_degree_of_latitude_is_about_111_km():
    assert route_length_km([[0.0, 0.0], [0.0, 1.0]]) == pytest.approx(ONE_DEGREE_KM, rel=1e-9)
    assert 111.1 < route_length_km([[6.0, 53.0], [6.0, 54.0]]) < 111.3


def test_a_two_leg_route_is_the_sum_of_its_legs():
    a, b, c = [6.8321, 53.4396], [6.8340, 53.4401], [6.8400, 53.4450]
    assert route_length_km([a, b, c]) == pytest.approx(
        _haversine_km(*a, *b) + _haversine_km(*b, *c), rel=1e-12,
    )


def test_a_bend_is_longer_than_the_chord():
    a, c = [6.0, 53.0], [7.0, 53.0]
    bend = [6.5, 53.5]
    assert route_length_km([a, bend, c]) > route_length_km([a, c])


def test_degenerate_inputs_are_zero():
    assert route_length_km([]) == 0.0
    assert route_length_km([[6.0, 53.0]]) == 0.0
    assert route_length_km([[6.0, 53.0], [6.0, 53.0]]) == 0.0


def test_order_is_lng_lat_like_the_document():
    # Cologne → Berlin is ~475 km when read as [lng, lat]; swapped it is not.
    cologne, berlin = [6.960, 50.938], [13.405, 52.520]
    assert 460 < route_length_km([cologne, berlin]) < 490
    assert not 460 < route_length_km([cologne[::-1], berlin[::-1]]) < 490
