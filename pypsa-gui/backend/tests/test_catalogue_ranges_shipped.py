"""Every network the app ships passes the hard ranges (expert-catalogue 01a-3a, GK B2 R3).

A new refusal must never break a template: each template at each build that
changes its component set (the D2 builds of 01a-2b), the grid benchmarks and
the test fixture networks have no stored value a write would refuse.
"""

from __future__ import annotations

import pathlib

import pypsa
import pytest

import project_templates._build as classic
from services.asset_schema import ranges as R

from tests.test_template_display_names_builds import _BUILDS

FIXTURES = sorted((pathlib.Path(__file__).parent / "fixtures").rglob("*.nc"))


@pytest.mark.parametrize("label", [label for label, _, _ in _BUILDS])
def test_every_template_build_passes_the_hard_ranges(label):
    make = {lb: m for lb, _, m in _BUILDS}[label]
    assert R.problems(make()) == []


@pytest.mark.parametrize("build", [classic.build_3bus, classic.build_ieee14])
def test_the_grid_benchmarks_pass_the_hard_ranges(build):
    assert R.problems(build()) == []


def test_the_fixture_networks_are_found():
    assert len(FIXTURES) >= 2


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.parent.name)
def test_every_fixture_network_passes_the_hard_ranges(path):
    assert R.problems(pypsa.Network(str(path))) == []


def test_the_sweep_finds_a_stored_value_out_of_range():
    n = classic.build_3bus()
    bus = n.buses.index[0]
    n.buses.at[bus, "v_nom"] = 0.0
    assert R.problems(n) == [{
        "component": "Bus", "id": str(bus), "field": "v_nom", "value": "0.0",
        "message": "Nominal voltage must be more than 0; got 0.0.",
    }]
