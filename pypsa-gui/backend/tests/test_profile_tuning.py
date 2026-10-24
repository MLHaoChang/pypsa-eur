"""G-a: preset tuning at the schema, request and native engine boundaries."""

from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pandas as pd
import pytest

from services.profile_gen.engine import generate_profile
from services.profile_gen.presets import resolve_params


def _generate(technology="wind", params=None, **kw):
    return generate_profile(
        [("g", 8.0, 50.0)],
        technology,
        params=resolve_params("default", technology, params),
        year=2023,
        now="2026-01-01T00:00:00+00:00",
        **kw,
    )


def test_synthetic_losses_scale_the_mean_exactly():
    baseline = _generate().series
    tuned = _generate(params={"losses": 0.1}).series
    np.testing.assert_allclose(tuned, baseline * 0.9, rtol=1e-12, atol=0)
    assert tuned["g"].mean() == pytest.approx(0.9 * baseline["g"].mean(), rel=1e-12)


@pytest.mark.parametrize("technology", ["wind", "solar"])
def test_untuned_generation_matches_master(technology):
    # Full-year float64 values captured on unchanged master1871073. Comparing
    # numbers avoids treating platform-level math/CSV last bits as a regression;
    # atol=0 keeps the baseline's exact night/clipping zeros exact.
    result = _generate(technology)
    fixture = Path(__file__).parent / "fixtures/profile_gen/untuned_master_1871073.npz"
    with np.load(fixture, allow_pickle=False) as baseline:
        actual = result.series.to_numpy()
        expected = baseline[technology]
        assert actual.dtype == expected.dtype
        np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))
        np.testing.assert_allclose(
            actual, expected,
            rtol=1e-12, atol=0, equal_nan=False,
        )
    pd.testing.assert_index_equal(
        result.series.index, pd.date_range("2023-01-01", periods=8760, freq="h"),
    )
    pd.testing.assert_index_equal(result.series.columns, pd.Index(["g"], dtype=object))
    assert result.meta == {
        "source": "synthetic",
        "technology": technology,
        "preset": "default",
        "params": {"preset": "default"},
        "weather_year": 2023,
        "resolution": "hourly",
        "bbox": [8.0, 50.0, 8.0, 50.0],
        "leap_day": "kept",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "atlite_version": None,
    }


@pytest.mark.parametrize(
    "technology,preset,expected",
    [
        (
            "wind",
            "vestas-v90-3mw",
            {"hub_height": 105, "cut_out": 25.0, "derate": 0, "losses": 0},
        ),
        (
            "solar",
            "csi-fixed-35deg-south",
            {
                "tilt": 35.0,
                "azimuth": 180.0,
                "tracking": None,
                "inverter_efficiency": 0.9,
                "losses": 0,
            },
        ),
    ],
)
def test_the_schema_lists_every_field_with_unit_bounds_and_preset_value(
    client, technology, preset, expected, record_property
):
    res = client.get(
        "/api/profiles/schema", params={"technology": technology, "preset": preset}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    record_property("schema_response", json.dumps(body))
    assert body["technology"] == technology and body["preset"] == preset
    fields = {f["key"]: f for f in body["fields"]}
    assert set(fields) == set(expected)
    for key, value in expected.items():
        f = fields[key]
        assert f["preset_value"] == value
        assert f["label"] and f["type"] and f["provenance"]
        assert "unit" in f and "sources" in f
        assert ("min" in f and "max" in f) or f["options"] == ["none", "horizontal"]
    default = client.get(
        "/api/profiles/schema", params={"technology": technology}
    ).json()
    for f in default["fields"]:
        if f["key"] not in ("derate", "losses"):
            assert f["preset_value"] is None
            assert f["provenance"] == "not used by the synthetic source"
        else:
            assert f["preset_value"] == 0
            assert f["provenance"] == "tool default: none applied"


@pytest.mark.parametrize(
    "params", [{"technology": "gas"}, {"technology": "wind", "preset": "missing"}]
)
def test_unknown_technology_or_preset_is_a_422(client, params):
    res = client.get("/api/profiles/schema", params=params)
    assert res.status_code == 422
    assert res.json()["detail"]["error_kind"]
    assert res.json()["detail"]["message"]


def test_derate_caps_the_series():
    base = _generate().series
    got = _generate(params={"derate": 0.2}).series
    np.testing.assert_array_equal(got, np.minimum(base, 0.8))
    assert float(got.max().max()) <= 0.8


def test_atlite_fields_map_into_the_preset_params():
    from copy import deepcopy
    from services.profile_gen.presets import get_preset

    original = deepcopy(get_preset("vestas-v90-3mw", "wind").params)
    for cut_out in (22, 30):
        got = resolve_params(
            "vestas-v90-3mw", "wind", {"hub_height": 120, "cut_out": cut_out}
        )
        assert got["turbine"]["hub_height"] == 120
        assert got["turbine"]["V"][-2:] == [cut_out, cut_out + 0.01]
        assert got["turbine"]["POW"][-2:] == [3.0, 0.0]
        assert all(v < cut_out and v <= 25 for v in got["turbine"]["V"][:-2])
    assert get_preset("vestas-v90-3mw", "wind").params == original
    solar = resolve_params(
        "csi-fixed-35deg-south",
        "solar",
        {"tilt": 20, "tracking": "horizontal", "inverter_efficiency": 0.95},
    )
    assert solar["orientation"] == {"slope": 20, "azimuth": 180.0}
    assert solar["tracking"] == "horizontal"
    assert solar["panel"]["inverter_efficiency"] == 0.95
    # atlite's native no-tracking value is None, not the UI's 'none'.
    assert (
        resolve_params("cdte-horizontal-tracking", "solar", {"tracking": "none"})[
            "tracking"
        ]
        is None
    )


def test_meta_records_tuning_and_what_the_source_ignored():
    got = _generate(params={"hub_height": 120, "losses": 0.1})
    assert got.meta["tuning"] == {"hub_height": 120, "losses": 0.1}
    assert got.meta["tuning_not_applied"] == ["hub_height"]


@pytest.mark.parametrize(
    "technology,params",
    [
        ("wind", {"losses": 0.9}),
        ("solar", {"tilt": 120}),
        ("solar", {"tracking": "dual"}),
        ("wind", {"derate": True}),
        ("wind", {"hub_height": "120"}),
        ("solar", {"losses": None}),
        ("wind", {"cut_out": float("nan")}),
        ("solar", {"tilt": float("inf")}),
    ],
)
def test_resolve_params_rejects_wrong_types_and_bounds(technology, params):
    from services.profile_gen.engine import ProfileGenError

    with pytest.raises(ProfileGenError, match=next(iter(params))):
        resolve_params("default", technology, params)


@pytest.mark.parametrize(
    "technology,params,key",
    [
        ("wind", {"losses": 0.9}, "losses"),
        ("solar", {"tilt": 120}, "tilt"),
        ("solar", {"tracking": "dual"}, "tracking"),
    ],
)
def test_out_of_range_tuning_is_refused_as_invalid_params(
    client, install_network, technology, params, key
):
    import pandas as pd
    import pypsa
    from services import solve_queue as sq

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2025-01-01", periods=168, freq="h"))
    n.add("Bus", "B", x=8, y=50)
    install_network(n)
    assert (
        client.post(
            "/api/projects/Tuning Farm", params={"force": True, "rebind": True}
        ).status_code
        == 200
    )
    before = {j["id"] for j in sq.solve_queue.list_jobs()}
    body = {
        "points": [{"id": "g", "lon": 8, "lat": 50}],
        "technology": technology,
        "source": "synthetic",
        "year": 2025,
        "params": params,
    }
    for url in (
        "/api/profiles/estimate",
        "/api/projects/Tuning Farm/profiles/generate",
    ):
        res = client.post(url, json=body)
        assert res.status_code == 422, res.text
        assert res.json()["detail"]["error_kind"] == "invalid_params"
        assert key in res.json()["detail"]["message"]
        assert (
            "[0" in res.json()["detail"]["message"]
            or "horizontal" in res.json()["detail"]["message"]
        )
    assert {j["id"] for j in sq.solve_queue.list_jobs()} == before


def test_the_cap_and_losses_apply_after_any_source_before_aggregation():
    import pandas as pd
    from services.profile_gen import engine

    def weather(points, technology, params, year):
        values = np.tile(np.repeat([0.0, 1.0], 12), 365)
        return pd.DataFrame(
            {"g": values}, index=pd.date_range("2023-01-01", periods=8760, freq="h")
        ), {}

    engine.register_source("tuning-test-weather", weather)
    try:
        got = _generate(
            params={"losses": 0.1, "derate": 0.2},
            source="tuning-test-weather",
            resolution="daily",
        )
        np.testing.assert_allclose(got.series, 0.36, rtol=1e-12, atol=0)
        assert "tuning_not_applied" not in got.meta
    finally:
        engine._SOURCES.pop("tuning-test-weather")


def test_an_untuned_native_tracking_preset_does_not_claim_user_tuning():
    from services.profile_gen.presets import get_preset

    for technology, preset in [
        ("solar", "cdte-horizontal-tracking"),
        ("wind", "vestas-v90-3mw"),
    ]:
        assert resolve_params(preset, technology) == {
            **get_preset(preset, technology).params,
            "preset": preset,
        }
    tuned = resolve_params("cdte-horizontal-tracking", "solar", {"losses": 0.1})
    assert tuned["tuning"] == {"losses": 0.1}


def test_tuning_receipts_come_from_flat_settings_not_caller_metadata():
    for fabricated in (None, {"losses": 0.9}):
        got = _generate(
            params={
                "tuning": fabricated,
                "tuning_not_applied": ["losses"],
                "custom_source_param": 42,
            }
        )
        assert "tuning" not in got.meta and "tuning_not_applied" not in got.meta
        assert got.meta["params"]["custom_source_param"] == 42
        assert got.series.equals(_generate().series)
    got = _generate(
        params={
            "losses": 0.1,
            "tuning": {"losses": 0.9},
            "tuning_not_applied": ["losses"],
        }
    )
    assert got.meta["tuning"] == {"losses": 0.1}
    assert "tuning_not_applied" not in got.meta
