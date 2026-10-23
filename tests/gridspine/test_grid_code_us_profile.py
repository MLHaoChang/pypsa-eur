"""The US grid-code profile and the loader's optional fields (E2E demo plan
2026-10-14 §7.3, gatekeeper review §7.4).

``us_ieee2800_nerc`` is honest about what binds a data centre: the steady-state
voltage band at transmission level is NERC PRC-029-1's continuous operation
range (aligned with IEEE 2800-2022); the site's reactive range is an assumed
placeholder, since no US code sets one for a load; IEEE 2800's reactive
capability applies to the on-site inverter-based resources (``q_range_ibr``);
and what a steady-state study cannot show (ride-through, RoCoF, primary
frequency response, the pending large-load rules) is listed as cited
``dynamic_requirements``, reported and never passed.

The loader stays tolerant of unknown keys (a project or extracted profile may
carry extras) but warns, and the shipped profiles use only known keys, so a
typo cannot switch a check off silently.
"""
import logging

import pytest
import yaml

from gridspine.schema.contracts import ContractError
from gridspine.templates.grid_codes import (
    BAND_KEYS, PROFILE_KEYS, band_for, list_grid_codes, load_grid_code, reported_requirements,
    unconfirmed,
)

US = "us_ieee2800_nerc"


def _write(tmp_path, profile, name="p"):
    path = tmp_path / "codes.yaml"
    path.write_text(yaml.safe_dump({"profiles": {name: profile}}))
    return path


def test_the_us_profile_is_shipped_and_listed():
    codes = list_grid_codes()
    assert US in codes and "IEEE 2800" in codes[US] and "NERC" in codes[US]


def test_every_shipped_profile_uses_only_known_keys():
    for name in list_grid_codes():
        p = load_grid_code(name, raw=True)
        assert set(p) <= PROFILE_KEYS, (name, set(p) - PROFILE_KEYS)
        for b in p["voltage_bands"]:
            assert set(b) <= BAND_KEYS, (name, set(b) - BAND_KEYS)


def test_an_unknown_key_is_loaded_with_a_warning(tmp_path, caplog):
    p = load_grid_code("generic_assumed", raw=True)
    p["q_range_ibrr"] = {"value": 0.33, "clause": "typo", "source": "assumed"}
    with caplog.at_level(logging.WARNING, logger="gridspine.templates.grid_codes"):
        got = load_grid_code("p", path=_write(tmp_path, p))
    assert got["q_range_ibrr"]["value"] == 0.33
    assert any("q_range_ibrr" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("kv, band, source, v30", [
    (138.0, (0.90, 1.05), "code", 1.10),
    (345.0, (0.90, 1.05), "code", 1.10),
    (33.0, (0.90, 1.10), "assumed", None),
])
def test_the_us_voltage_bands(kv, band, source, v30):
    b = band_for(load_grid_code(US), kv)
    assert (b["v_min"], b["v_max"]) == band and b["source"] == source
    assert b.get("v_max_30min") == v30
    if source == "code":
        assert "PRC-029" in b["clause"]


def test_the_us_reactive_ranges_say_what_binds_whom():
    p = load_grid_code(US)
    site = p["q_range_demand"]
    assert site["value"] == pytest.approx(0.329) and site["source"] == "assumed"
    assert "not" in site["clause"] and "load" in site["clause"]
    ibr = p["q_range_ibr"]
    assert ibr["value"] == pytest.approx(0.3287) and ibr["source"] == "code"
    assert "IEEE 2800" in ibr["clause"] and "ICR" in ibr["clause"]
    assert unconfirmed(p) == []


def test_the_us_notes_carry_the_unverified_scope():
    notes = load_grid_code(US)["notes"]
    assert "PRC-029" in notes and "unverified" in notes and "behind-the-meter" in notes


def test_dynamic_requirements_are_reported_never_passed():
    for name in (US, "eu_rfg_dcc_ce"):
        rows = reported_requirements(load_grid_code(name))
        assert rows, name
        for r in rows:
            assert r["status"] == "reported", r
            assert r["clause"] and r["source"] in ("code", "assumed") and r["requirement"]
            assert r["applies_to"] in ("ibr", "load", "facility")
            # a dynamic requirement needs an RMS study; the 30-min voltage tier a dynamic one
            assert ("dynamic study needed" if r["id"] == "v_max_30min" else "(needs an RMS study)") in r["detail"]
    us = {r["id"]: r for r in reported_requirements(load_grid_code(US))}
    assert {"frt", "vrt", "rocof", "pfr", "large_load_ride_through", "v_max_30min"} <= set(us)
    assert us["rocof"]["value"] == "5 Hz/s" and us["large_load_ride_through"]["applies_to"] == "load"
    assert "1.10" in us["v_max_30min"]["value"]          # the 30-min tier, reported, not enforced


def test_the_generic_profile_reports_nothing():
    assert reported_requirements(load_grid_code("generic_assumed")) == []


@pytest.mark.parametrize("mutate, match", [
    (lambda p: p["q_range_ibr"].update(value=0.0), "q_range_ibr value"),
    (lambda p: p["q_range_ibr"].pop("clause"), "q_range_ibr has no clause"),
    (lambda p: p["voltage_bands"][1].update(v_max_30min=1.0), "v_max_30min"),
    (lambda p: p["dynamic_requirements"][0].pop("clause"), "dynamic_requirements\\[0\\]"),
    (lambda p: p["dynamic_requirements"][0].update(applies_to="everyone"), "applies_to"),
    (lambda p: p["dynamic_requirements"][0].update(source="extracted"), "source"),
    (lambda p: p.update(dynamic_requirements={"frt": 1}), "dynamic_requirements must be a list"),
    (lambda p: p["dynamic_requirements"].append(dict(p["dynamic_requirements"][0])), "duplicate id"),
    (lambda p: p.update(notes=3), "notes must be text"),
])
def test_impossible_optional_fields_are_refused(tmp_path, mutate, match):
    p = load_grid_code(US, raw=True)
    mutate(p)
    with pytest.raises(ContractError, match=match):
        load_grid_code("p", path=_write(tmp_path, p))
