# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Harness issue 225: `_exact_frame_json` writes plain numpy columns and
indexes straight to JSON text instead of one `scalar()` list per cell. The
text, and so every exact fingerprint, must be master's byte for byte: each
case here is compared with exact `==` against master's encoder, kept below
verbatim as the reference."""

from __future__ import annotations

import json
import random
import sys
import types
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from services import workflow_tools as wt
from services.provenance_graph.runs import input_fingerprint

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "project_templates"))
import eh_ammonia  # noqa: E402
import eh_concentrator  # noqa: E402
import eh_ipp  # noqa: E402
import eh_site  # noqa: E402
import eh_staged_hubs  # noqa: E402
import eh_templates as T  # noqa: E402


def reference(frame):
    """Master's `_exact_frame_json` (3aba8dead), verbatim but for `_refuse`'s module."""
    _refuse = wt._refuse

    def scalar(value):
        if value is None or value is pd.NA or value is pd.NaT:
            return ["missing"]
        if isinstance(value, tuple):
            return ["tuple", [scalar(item) for item in value]]
        if isinstance(value, (datetime, date)):
            return ["datetime", value.isoformat()]
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, bool):
            return ["bool", value]
        if isinstance(value, int):
            return ["int", str(value)]
        if isinstance(value, float):
            return ["float", value.hex()]
        if isinstance(value, str):
            return ["str", value]
        _refuse(
            "input_capture_unavailable",
            "An input has an unsupported scalar type; exact preflight is unavailable.",
            409,
        )

    return json.dumps(
        {
            "columns": [scalar(v) for v in frame.columns.tolist()],
            "index": [scalar(v) for v in frame.index.tolist()],
            "index_names": [scalar(v) for v in frame.index.names],
            "dtypes": [str(v) for v in frame.dtypes],
            "values_by_column": [
                [scalar(v) for v in frame.iloc[:, position].tolist()]
                for position in range(len(frame.columns))
            ],
        },
        sort_keys=True,
        allow_nan=False,
    )


def same(frame):
    expected = reference(frame)
    assert wt._exact_frame_json(frame) == expected
    return expected


SPECIAL = [
    0.187 / 0.4,
    0.1 + 0.2,
    float("nan"),
    float("inf"),
    -float("inf"),
    -0.0,
    0.0,
    5e-324,
    1.7976931348623157e308,
    1 / 3,
]


def test_special_floats_ints_bools_and_object_columns():
    n = len(SPECIAL)
    with np.errstate(over="ignore"):  # 1.8e308 overflows float16/32 to inf, on purpose
        frame = pd.DataFrame(
            {
                "f64": SPECIAL,
                "f32": np.array(SPECIAL, dtype=np.float32),
                "f16": np.array(SPECIAL, dtype=np.float16),
                "i64": np.array(
                    [np.iinfo(np.int64).min, np.iinfo(np.int64).max, 0, -1, 1] * 2,
                    dtype=np.int64,
                ),
                "u64": np.array([np.iinfo(np.uint64).max, 0] * 5, dtype=np.uint64),
                "i8": np.arange(n, dtype=np.int8) - 5,
                "b": [True, False] * 5,
                "obj": pd.Series(
                    [
                        "a",
                        1,
                        2.5,
                        None,
                        pd.NA,
                        (1, "x"),
                        True,
                        np.float64(0.1),
                        np.int64(7),
                        -0.0,
                    ],
                    dtype=object,
                ),
                "s": [
                    'q"uote',
                    "back\\slash",
                    "ünïcode",
                    "日本",
                    "tab\t",
                    "",
                    "nl\n",
                    "\u2028",
                    "x",
                    "y",
                ],
            },
            index=pd.Index([f"r{i}" for i in range(n)], name="row"),
        )
    text = same(frame)
    assert '"-0x0.0p+0"' in text and '"nan"' in text and '"inf"' in text


@pytest.mark.parametrize("dtype", [np.float16, np.float32])
def test_narrow_floats_hex_their_exact_widening(dtype):
    values = np.array([0.1, 0.187 / 0.4, 1 / 3, 65504.0, 6e-8], dtype=dtype)
    frame = pd.DataFrame({"v": values})
    text = same(frame)
    for v in values:  # the long .tolist() text, never the short one
        assert f'["float", "{float(v).hex()}"]' in text


def test_datetimes_at_and_beyond_the_bounds():
    edge = pd.Series(
        np.array(
            ["0001-01-01T00:00:00", "9999-12-31T23:59:59", "1970-01-01T00:00:00"],
            dtype="datetime64[s]",
        )
    )
    beyond = pd.Series(
        np.array(["0001-01-01T00:00:00", "10000-01-01T00:00:00"], dtype="datetime64[s]")
    )
    before = pd.Series(
        np.array(["-0001-12-31T23:59:59", "2027-01-01T00:00:00"], dtype="datetime64[s]")
    )
    sub = pd.Series(
        pd.to_datetime(
            [
                "2027-01-01 00:00:00",
                "2027-01-01 00:00:00.5",
                "1960-01-01 00:00:00.000001",
            ],
            format="ISO8601",
        )
    )
    nat = pd.Series(pd.to_datetime(["2027-01-01", None]))
    tz = pd.Series(pd.date_range("2027-01-01", periods=3, freq="h", tz="Europe/Berlin"))
    for series in (edge, sub, nat, tz):
        same(series.to_frame("t"))
        same(pd.DataFrame({"x": range(len(series))}, index=pd.Index(series)))
    for series in (
        beyond,
        before,
    ):  # out of pandas' Timestamp range: master refuses or encodes, alike
        frame = series.to_frame("t")
        try:
            expected = reference(frame)
        except Exception as exc:  # noqa: BLE001
            with pytest.raises(type(exc)):
                wt._exact_frame_json(frame)
        else:
            assert wt._exact_frame_json(frame) == expected


def test_multiindex_levels_encode_like_the_tuples_master_iterates():
    snapshots = pd.date_range("2027-01-01", periods=4, freq="h")
    frames = [
        pd.MultiIndex.from_product(
            [[2027, 2030], snapshots], names=["period", "timestep"]
        ),
        pd.MultiIndex.from_arrays(
            [["a", "b", "a"], [0.1, 0.187 / 0.4, -0.0], [True, False, True]]
        ),
        pd.MultiIndex.from_arrays(
            [[1.0, np.nan, 2.0], ["x", "y", "z"]]
        ),  # a NaN code: falls back
        pd.MultiIndex.from_arrays([[np.uint64(2**64 - 1), np.uint64(0)], [None, "q"]]),
        pd.MultiIndex.from_arrays(
            [
                pd.to_datetime(
                    ["2027-01-01 00:00:00.25", "2027-01-02"], format="ISO8601"
                ),
                [1, 2],
            ]
        ),
        pd.MultiIndex.from_arrays([[], []]),
    ]
    for index in frames:
        same(pd.DataFrame({"v": np.arange(len(index), dtype=float) / 7}, index=index))


def test_empty_zero_column_and_named_frames():
    same(pd.DataFrame())
    same(pd.DataFrame(index=pd.Index(["a", "b"], name="Generator")))
    same(
        pd.DataFrame(
            {"a": pd.Series([], dtype=float), "b": pd.Series([], dtype=object)}
        )
    )
    frame = pd.DataFrame(
        [[1.5, 2.5]], columns=pd.MultiIndex.from_tuples([("p", 1), ("q", 2.5)])
    )
    frame.index = pd.MultiIndex.from_tuples([(1, "a")], names=[None, 3])
    same(frame)


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"c": np.array([1 + 2j, 3j])}),  # kind 'c': never the float path
        pd.DataFrame({"c": pd.Series([1.0, 1 + 2j], dtype=object)}),
        pd.DataFrame({"x": [1.0, 2.0]}, index=pd.Index([1 + 2j, 3j])),
        pd.DataFrame({"x": [1.0]}, index=pd.MultiIndex.from_tuples([(1, 2j)])),
        pd.DataFrame({"x": [1.0]}, index=[pd.Timedelta(1)]),
    ],
    ids=[
        "complex-column",
        "complex-object",
        "complex-index",
        "complex-level",
        "timedelta",
    ],
)
def test_refusals_are_unchanged(frame):
    with pytest.raises(HTTPException) as master:
        reference(frame)
    with pytest.raises(HTTPException) as now:
        wt._exact_frame_json(frame)
    assert (now.value.status_code, now.value.detail) == (
        master.value.status_code,
        master.value.detail,
    )


def _column(rng, n):
    kind = rng.choice(
        [
            "f64",
            "f32",
            "f16",
            "int",
            "uint",
            "bool",
            "obj",
            "str",
            "dt",
            "dt-sub",
            "dt-nat",
            "dt-s",
            "tz",
            "nullable",
            "cat",
        ]
    )
    floats = [
        rng.choice(SPECIAL) if rng.random() < 0.3 else rng.uniform(-1e6, 1e6)
        for _ in range(n)
    ]
    stamps = pd.Timestamp("2027-01-01") + pd.to_timedelta(
        [rng.randrange(0, 10**8) for _ in range(n)], unit="s"
    )
    if kind == "f64":
        return pd.Series(floats, dtype=np.float64)
    if kind == "f32":
        return pd.Series(np.array(floats, dtype=np.float32))
    if kind == "f16":
        return pd.Series(np.array([x / 1e3 for x in floats], dtype=np.float16))
    if kind == "int":
        return pd.Series(
            [rng.randrange(-(2**63), 2**63) for _ in range(n)], dtype=np.int64
        )
    if kind == "uint":
        return pd.Series([rng.randrange(0, 2**64) for _ in range(n)], dtype=np.uint64)
    if kind == "bool":
        return pd.Series([rng.random() < 0.5 for _ in range(n)], dtype=bool)
    if kind == "obj":
        pool = [
            "s",
            3,
            2.5,
            None,
            True,
            np.float32(0.1),
            (1, "t"),
            pd.Timestamp("2027-01-01"),
            -0.0,
        ]
        return pd.Series([rng.choice(pool) for _ in range(n)], dtype=object)
    if kind == "str":
        return pd.Series(
            [
                rng.choice(["a", "é", '"', "\\", ""]) * rng.randrange(1, 4)
                for _ in range(n)
            ]
        )
    if kind == "dt":
        return pd.Series(stamps)
    if kind == "dt-sub":
        return pd.Series(
            stamps + pd.to_timedelta([rng.randrange(0, 2) for _ in range(n)], unit="us")
        )
    if kind == "dt-nat":
        return pd.Series(stamps.where([rng.random() < 0.8 for _ in range(n)]))
    if kind == "dt-s":
        return pd.Series(stamps.as_unit("s"))
    if kind == "tz":
        return pd.Series(stamps.tz_localize("UTC"))
    if kind == "nullable":
        return pd.Series(
            [None if rng.random() < 0.2 else rng.randrange(-9, 9) for _ in range(n)],
            dtype="Int64",
        )
    return pd.Series([rng.choice(["x", "y"]) for _ in range(n)], dtype="category")


def _index(rng, n):
    kind = rng.choice(
        ["range", "str", "float", "dt", "multi", "multi-nan", "bool-like"]
    )
    if kind == "range":
        return pd.RangeIndex(n)
    if kind == "str":
        return pd.Index(
            [f"g{i}" for i in range(n)], name=rng.choice([None, "Generator", 0])
        )
    if kind == "float":
        return pd.Index([i / 10 for i in range(n)])
    if kind == "dt":
        return pd.date_range(
            "2027-01-01",
            periods=n,
            freq=rng.choice(["h", "30min", "1s"]),
            name="snapshot",
        )
    if kind == "multi":
        half = (n + 1) // 2
        product = pd.MultiIndex.from_product(
            [[2027, 2030], pd.date_range("2027-01-01", periods=half, freq="h")],
            names=["period", "timestep"],
        )
        return product[:n]
    if kind == "multi-nan":
        return pd.MultiIndex.from_arrays(
            [[rng.choice([1.5, np.nan]) for _ in range(n)], [f"k{i}" for i in range(n)]]
        )
    return pd.Index([0.5] * n, dtype=np.float32)


def test_seeded_random_frames_match_master():
    rng = random.Random(225)
    names = ["p_max_pu", 0, 2.5, ("a", 1), None, "é", True]
    for _ in range(240):
        n = rng.randrange(0, 30)
        columns = {}
        for position in range(rng.randrange(0, 5)):
            columns[position] = _column(rng, n).reset_index(drop=True)
        frame = pd.DataFrame(columns, index=pd.RangeIndex(n))
        frame.columns = [
            rng.choice(names) if rng.random() < 0.5 else f"c{p}"
            for p in range(len(frame.columns))
        ]
        frame.index = _index(rng, n)
        same(frame)


def _builders():
    """Every shipped template's network (the staged builders register in BUILDERS
    once imported), plus the issue's case: the concentrator's first stage, 8760 h."""
    return {
        "eh_site": eh_site.build_eh_site,
        **T.BUILDERS,
        "eh_ammonia_staged": lambda: eh_ammonia.build_eh_ammonia_staged(T),
        "eh_ipp_hybrid": eh_ipp.build_eh_ipp_hybrid,
        "eh_h2_hub_staged": lambda: eh_staged_hubs.build_eh_h2_hub_staged(T),
        "eh_microgrid_staged": lambda: eh_staged_hubs.build_eh_microgrid_staged(T),
        "eh_concentrator_staged": lambda: eh_concentrator.build_eh_concentrator_staged(
            T
        ),
        "eh_concentrator_staged@2027": lambda: (
            eh_concentrator.build_eh_concentrator_staged(T, years=(2027,))
        ),
    }


@pytest.mark.parametrize("tid", list(_builders()))
def test_template_fingerprints_match_master(monkeypatch, tid):
    """Both fingerprints, through the wrapper and the hash itself, as master's
    (sha256 over every frame's text, so every frame matches too)."""
    ctx = types.SimpleNamespace(
        network=_builders()[tid](),
        solver_state={"solver_config": {}},
        user_ts={},
        storage_dir=None,
    )
    now = (
        input_fingerprint(ctx),
        input_fingerprint(ctx, exact=True),
        wt._network_input_fingerprint(ctx, exact=True),
    )
    monkeypatch.setattr(wt, "_exact_frame_json", reference)
    master = (
        input_fingerprint(ctx),
        input_fingerprint(ctx, exact=True),
        wt._network_input_fingerprint(ctx, exact=True),
    )
    assert master[1] is not None
    assert now == master
