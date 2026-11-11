# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""The four bus-flag readers answer exactly as ``process_chain.truthy`` does, pinned per value."""

import math

import pytest

from gridspine.drivers.process_chain import truthy
from services.adequacy import archetypes, eh_stages, grid_readiness, scr_gate

PINNED = {
    "yes": True,
    " Yes ": True,
    "TRUE": True,
    "true": True,
    "1": True,
    "1.0": True,
    " 1 ": True,
    1: True,
    1.0: True,
    True: True,
    "0": False,
    "": False,
    "no": False,
    "false": False,
    "2": False,
    None: False,
    0: False,
    False: False,
    math.nan: False,
}
READERS = {
    "eh_stages": eh_stages._truthy,
    "grid_readiness": grid_readiness._truthy,
    "archetypes": archetypes._truthy,
    "scr_gate": scr_gate._truthy,
}


@pytest.mark.parametrize("value", list(PINNED), ids=repr)
def test_shared_rule_pinned(value):
    assert truthy(value) is PINNED[value]


@pytest.mark.parametrize("name", list(READERS))
@pytest.mark.parametrize("value", list(PINNED), ids=repr)
def test_reader_matches_shared_rule(name, value):
    assert READERS[name](value) is truthy(value)


@pytest.mark.parametrize("name", list(READERS))
def test_no_private_copy(name):
    assert READERS[name] is truthy
