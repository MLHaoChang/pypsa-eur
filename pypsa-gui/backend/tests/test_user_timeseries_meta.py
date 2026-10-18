"""The user-TS metadata channel (profile_gen Task 5; spec D6, plan Task 5).

`assign_profile` records WHERE a profile came from — source, preset, params,
weather year, bbox, leap-day handling, tiling flag, generated-at, atlite
version — in a store parallel to `_user_ts`, keyed identically
``(component, attribute, column)``. The channel must:

  * ride the SAME persistence + undo payload as the series (`user_ts.json`,
    undo snapshots) under a reserved ``"$meta"`` top-level key — a second
    sidecar would drift;
  * replace wholesale on restore, exactly like the series store, so
    ``_restore_user_ts({})`` (project/template reset) clears it too;
  * follow renames and deletes so it never orphans;
  * stay per-project — the tenancy property the series store already pins
    (``tests/test_user_ts_tenancy.py``).
"""
from __future__ import annotations

import json

import pandas as pd
import pypsa

from services.project_context import ProjectContext
from services.pypsa_service import PyPSAService
from services.user_timeseries import (
    _restore_user_ts,
    _serialize_user_ts,
    _user_ts,
    _user_ts_delete_asset,
    _user_ts_meta,
    _user_ts_rename_asset,
)

SNAPS = pd.date_range("2025-01-01", periods=4, freq="h")
KEY = ("generators", "p_max_pu", "Solar")
META = {
    "source": "synthetic",
    "technology": "solar",
    "preset": "default",
    "params": {"preset": "default"},
    "weather_year": 2024,
    "bbox": [13.0, 52.0, 13.5, 52.6],
    "leap_day": "dropped",
    "tiled": False,
    "generated_at": "2026-10-18T00:00:00+00:00",
    "atlite_version": None,
}


def _series() -> pd.Series:
    return pd.Series([0.1, 0.2, 0.3, 0.4], index=SNAPS, name="Solar", dtype=float)


def _put(*, series: bool = True, meta: bool = True) -> None:
    if series:
        _user_ts[KEY] = _series()
    if meta:
        _user_ts_meta[KEY] = dict(META)


def _load_payload(col: str = "L1") -> dict:
    idx = [t.isoformat() for t in SNAPS]
    return {"loads": {"p_set": {col: {"index": idx, "values": [1.0, 2.0, 3.0, 4.0]}}}}


def test_serialize_emits_meta_for_keys_in_the_store():
    _put()
    payload = _serialize_user_ts()
    assert "$meta" in payload
    assert payload["$meta"]["generators"]["p_max_pu"]["Solar"] == META
    # The series payload keeps its existing shape untouched.
    assert payload["generators"]["p_max_pu"]["Solar"]["values"] == [0.1, 0.2, 0.3, 0.4]


def test_serialize_without_meta_emits_no_meta_key():
    _put(meta=False)
    assert "$meta" not in _serialize_user_ts()


def test_serialize_skips_orphan_meta_without_a_series():
    _put(series=False, meta=True)
    assert "$meta" not in _serialize_user_ts()


def test_round_trip_preserves_meta():
    _put()
    payload = _serialize_user_ts()
    _restore_user_ts({})
    assert dict(_user_ts_meta) == {}
    _restore_user_ts(payload)
    assert dict(_user_ts_meta) == {KEY: META}
    assert KEY in _user_ts


def test_serialized_meta_is_json_round_trippable():
    # The save path does json.dumps(user_ts_data) — meta must survive it.
    _put()
    payload = json.loads(json.dumps(_serialize_user_ts()))
    _restore_user_ts({})
    _restore_user_ts(payload)
    assert _user_ts_meta[KEY]["weather_year"] == 2024
    assert _user_ts_meta[KEY]["bbox"] == [13.0, 52.0, 13.5, 52.6]


def test_restore_without_meta_key_clears_existing_meta():
    _put()
    _restore_user_ts(_load_payload())
    assert dict(_user_ts_meta) == {}
    assert ("loads", "p_set", "L1") in _user_ts


def test_restore_empty_clears_meta_and_series():
    _put()
    _restore_user_ts({})
    assert dict(_user_ts_meta) == {}
    assert KEY not in _user_ts


def test_legacy_pipe_format_restore_clears_meta():
    _put()
    idx = [t.isoformat() for t in SNAPS]
    _restore_user_ts({"loads|p_set|L1": {"index": idx, "values": [1.0, 2.0, 3.0, 4.0]}})
    assert dict(_user_ts_meta) == {}
    assert ("loads", "p_set", "L1") in _user_ts


def test_rename_moves_the_meta_entry_with_the_series():
    _put()
    moved = _user_ts_rename_asset("generators", "Solar", "Solar2")
    assert moved == 1
    new_key = ("generators", "p_max_pu", "Solar2")
    assert _user_ts_meta[new_key] == META
    assert KEY not in _user_ts_meta
    assert new_key in _user_ts


def test_delete_drops_the_meta_entry_with_the_series():
    _put()
    dropped = _user_ts_delete_asset("generators", "Solar")
    assert dropped == 1
    assert KEY not in _user_ts_meta
    assert KEY not in _user_ts


def test_meta_store_is_per_context():
    a = ProjectContext(network=pypsa.Network())
    b = ProjectContext(network=pypsa.Network())
    a.user_ts[KEY] = _series()
    a.user_ts_meta[KEY] = dict(META)
    payload_a = _serialize_user_ts(store=a.user_ts, meta_store=a.user_ts_meta)
    payload_b = _serialize_user_ts(store=b.user_ts, meta_store=b.user_ts_meta)
    assert "$meta" in payload_a
    assert "$meta" not in payload_b
    # Restoring a's payload into b moves the meta across — and only across.
    _restore_user_ts(payload_a, store=b.user_ts, meta_store=b.user_ts_meta)
    assert b.user_ts_meta[KEY] == META
    assert a.user_ts_meta[KEY] == META  # a untouched by the restore into b


def test_explicit_store_pairs_with_its_owner_by_identity():
    # The save / hydrate paths pass `ctx.user_ts` WITHOUT a meta store; the
    # owner is resolved by dict identity (`_meta_store_for`), so no call-site
    # change was needed in routers/projects.py.
    _put()
    ctx = PyPSAService.get_active_context()
    payload = _serialize_user_ts(store=ctx.user_ts)  # no meta_store passed
    assert payload["$meta"]["generators"]["p_max_pu"]["Solar"] == META
    _restore_user_ts({})
    assert dict(_user_ts_meta) == {}
    _restore_user_ts(payload, store=ctx.user_ts)     # no meta_store passed
    assert dict(_user_ts_meta) == {KEY: META}


def test_unowned_store_touches_no_meta_store():
    _put()
    orphan: dict = {}
    _restore_user_ts(_load_payload(), store=orphan)  # no resident owner
    # The active project's provenance survives a restore aimed at an
    # unowned dict.
    assert dict(_user_ts_meta) == {KEY: META}
    assert ("loads", "p_set", "L1") in orphan
