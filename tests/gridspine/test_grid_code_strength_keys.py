# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The optional grid-strength minima of a grid-code profile (plan GC-03): ``scr_min``,
``escr_min`` and ``wscr_min``, each {value, clause, source}. No shipped profile carries one."""

import pytest

from gridspine.schema.contracts import ContractError
from gridspine.templates.grid_codes import (
    PROFILE_KEYS,
    STRENGTH_KEYS,
    list_grid_codes,
    load_grid_code,
    validate_profile,
)

KEYS = ("scr_min", "escr_min", "wscr_min")


def _raw():
    return load_grid_code("generic_assumed", raw=True)


def test_the_keys_are_known_and_no_shipped_profile_states_one():
    assert STRENGTH_KEYS == KEYS
    assert set(KEYS) <= PROFILE_KEYS
    for name in list_grid_codes():
        p = load_grid_code(name)
        assert not set(KEYS) & set(p), name


@pytest.mark.parametrize("key", KEYS)
def test_a_stated_minimum_loads_with_its_clause(key):
    p = _raw()
    p[key] = {"value": 3.0, "clause": "demo clause 1.2", "source": "code"}
    out = validate_profile("p", p)
    assert out[key] == {"value": 3.0, "clause": "demo clause 1.2", "source": "code"}


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("value", [0.0, -1.0, "3", True, float("nan")])
def test_a_minimum_that_is_not_a_positive_number_is_refused(key, value):
    p = _raw()
    p[key] = {"value": value, "clause": "demo", "source": "code"}
    with pytest.raises(ContractError, match=f"{key} value must be a positive number"):
        validate_profile("p", p)


@pytest.mark.parametrize("key", KEYS)
def test_a_minimum_without_a_clause_is_refused(key):
    p = _raw()
    p[key] = {"value": 3.0, "source": "code"}
    with pytest.raises(ContractError, match=f"{key} has no clause"):
        validate_profile("p", p)


def test_absent_minima_load_as_absent():
    out = validate_profile("p", _raw())
    assert not set(KEYS) & set(out)
