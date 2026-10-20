"""Tests for profile presets (profile_gen Task 4).

Plan: docs/superpowers/plans/2026-10-17-renewable-profile-generator.md, Task 4.
Spec: docs/superpowers/specs/2026-10-17-renewable-profile-generator-spec.md §3.3.

Gridspine-templates-v2 discipline: every parameter field carries provenance,
the file is validated at LOAD time, and turbine/panel configs are embedded
dicts — never atlite library names that could trigger a runtime download.

Run with the pixi env python:
    pixi run python -m pytest pypsa-gui/backend/tests/test_profile_gen_presets.py
"""
from __future__ import annotations

import textwrap

import pytest

from services.profile_gen import generate_profile
from services.profile_gen.presets import (
    InvalidPresetError,
    UnknownPresetError,
    get_preset,
    list_presets,
    load_presets,
    resolve_params,
)


# --- shipped file ------------------------------------------------------------


def test_shipped_defaults_exist_per_technology_and_are_synthetic():
    for tech in ("wind", "solar"):
        p = get_preset("default", tech)
        assert p.source_type == "synthetic", "default must never need a download"
        assert p.technology == tech


def test_shipped_atlite_presets_exist_and_are_offline_dicts():
    wind = [p for p in list_presets("wind") if p.source_type == "atlite"]
    solar = [p for p in list_presets("solar") if p.source_type == "atlite"]
    assert len(wind) >= 2 and len(solar) >= 2
    for p in wind:
        t = p.params["turbine"]
        assert isinstance(t, dict), "embedded curve dict, not a library name"
        assert {"V", "POW", "hub_height", "P"} <= set(t)
        assert len(t["V"]) == len(t["POW"])
    for p in solar:
        assert isinstance(p.params["panel"], dict)
        assert "model" in p.params["panel"]
        assert isinstance(p.params["orientation"], dict)


def test_every_params_field_has_provenance():
    for p in list_presets():
        for key in p.params:
            assert key in p.provenance, f"{p.name}/{p.technology}: '{key}' lacks provenance"


# --- load-time validation -----------------------------------------------------


def _write(tmp_path, body):
    f = tmp_path / "presets.yaml"
    f.write_text(textwrap.dedent(body))
    return f


def test_missing_provenance_field_refused_at_load(tmp_path):
    # Mutation target: silently filling provenance turns this test red.
    f = _write(tmp_path, """
        - name: bad
          technology: wind
          source_type: synthetic
          params: {seed: 1}
          provenance: {}   # seed has no provenance -> must refuse
    """)
    with pytest.raises(InvalidPresetError, match="provenance"):
        load_presets(f)


def test_missing_required_key_refused_at_load(tmp_path):
    f = _write(tmp_path, """
        - name: bad
          technology: wind
          source_type: synthetic
          params: {}
    """)
    with pytest.raises(InvalidPresetError, match="provenance"):
        load_presets(f)


def test_unknown_technology_and_source_type_refused(tmp_path):
    f = _write(tmp_path, """
        - name: bad
          technology: hydro
          source_type: synthetic
          params: {}
          provenance: {}
    """)
    with pytest.raises(InvalidPresetError, match="technology"):
        load_presets(f)
    f2 = _write(tmp_path, """
        - name: bad
          technology: wind
          source_type: magic
          params: {}
          provenance: {}
    """)
    with pytest.raises(InvalidPresetError, match="source_type"):
        load_presets(f2)


def test_atlite_preset_without_embedded_config_refused(tmp_path):
    f = _write(tmp_path, """
        - name: bad
          technology: wind
          source_type: atlite
          params: {turbine: "Vestas_V90_3MW"}   # a NAME, not a dict -> refuse
          provenance: {turbine: "x"}
    """)
    with pytest.raises(InvalidPresetError, match="embedded"):
        load_presets(f)


def test_duplicate_name_technology_pair_refused(tmp_path):
    f = _write(tmp_path, """
        - name: dup
          technology: wind
          source_type: synthetic
          params: {}
          provenance: {}
        - name: dup
          technology: wind
          source_type: synthetic
          params: {}
          provenance: {}
    """)
    with pytest.raises(InvalidPresetError, match="duplicate"):
        load_presets(f)


# --- lookup semantics ----------------------------------------------------------


def test_unknown_preset_lists_available():
    with pytest.raises(UnknownPresetError, match="default"):
        get_preset("no-such-preset", "wind")


def test_bare_default_requires_technology():
    with pytest.raises(UnknownPresetError, match="technology"):
        get_preset("default")  # exists for both wind and solar -> ambiguous


def test_resolve_params_applies_overrides_without_mutating_preset():
    p = get_preset("default", "wind")
    out = resolve_params("default", "wind", {"seed": 99})
    assert out["seed"] == 99
    assert p.params.get("seed") != 99, "the shipped preset must stay pristine"


# --- integration: a shipped default drives the engine ---------------------------


def test_default_preset_feeds_the_engine():
    params = resolve_params("default", "solar")
    res = generate_profile(points=[("a", 13.4, 52.5)], technology="solar",
                           params=params, source="synthetic", year=2023,
                           now="2026-10-18T00:00:00+00:00")
    assert len(res.series) == 8760
    assert res.meta["preset"] == "default"
