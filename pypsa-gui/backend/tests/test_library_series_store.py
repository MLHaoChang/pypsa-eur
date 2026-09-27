"""
Library series store (Edge Investment Case P1 WP1.1a; spec decision 17).

Price curves, envelopes, meter history and forecast pairs are not component
attributes, so they cannot live in the component-bound `_user_ts` store. The
Library keeps them per ORG, versioned and content-addressed:

  * `put_series` is idempotent on content — the same numbers under the same
    name return the same version; changed numbers bump it;
  * `resolve(ref)` returns exactly what was put (values, index, timezone), and
    refuses a ref whose recorded hash no longer matches (`LibraryRefStale`);
  * files live under `<projects_root>/.library/<org_id>/series/` — a HIDDEN
    directory, because `legacy_migrate._scan_root` treats any non-hidden,
    non-UUID directory under the projects root as a claimable leftover it may
    MOVE (the plan's `_library/` name would have been one); the org id is
    always in the path, whatever `use_org_segment()` says for projects;
  * the hash is over canonical bytes (`%.10g` floats, ISO UTC timestamps), so
    idempotency does not depend on pandas' repr of a float.

Written red first: `services.library` did not exist.
"""
from __future__ import annotations

import gzip
import uuid

import numpy as np
import pandas as pd
import pytest


@pytest.fixture()
def db(_auth_db):
    _engine, session_local = _auth_db
    with session_local() as s:
        yield s
        s.rollback()


@pytest.fixture()
def org(seeded_identity):
    return seeded_identity["org_id"]


def _berlin_prices():
    idx = pd.date_range("2030-03-31 00:00", periods=96, freq="15min", tz="Europe/Berlin")
    return pd.Series(np.round(np.linspace(40.0, 120.0, len(idx)), 3), index=idx, name="da")


def test_put_returns_a_ref_and_resolve_returns_exactly_what_was_put(db, org, tmp_path):
    from services.library import series_store as S

    s = _berlin_prices()
    ref = S.put_series(db, org, "de_day_ahead_2030", s, {"source": "user upload"}, root=tmp_path)
    assert ref.id == "de_day_ahead_2030" and ref.version == 1 and len(ref.hash) == 64
    back = S.resolve(db, org, ref, root=tmp_path)
    pd.testing.assert_series_equal(back, s, check_freq=False, check_names=False)
    assert str(back.index.tz) == "Europe/Berlin"


def test_same_content_is_idempotent_and_changed_content_bumps_the_version(db, org, tmp_path):
    from services.library import series_store as S

    s = _berlin_prices()
    r1 = S.put_series(db, org, "px", s, {"source": "u"}, root=tmp_path)
    r2 = S.put_series(db, org, "px", s.copy(), {"source": "u"}, root=tmp_path)
    assert (r2.version, r2.hash) == (r1.version, r1.hash)
    s2 = s.copy()
    s2.iloc[0] += 1.0
    r3 = S.put_series(db, org, "px", s2, {"source": "u"}, root=tmp_path)
    assert r3.version == 2 and r3.hash != r1.hash
    # Both versions stay resolvable: a project pinned to v1 keeps v1.
    pd.testing.assert_series_equal(S.resolve(db, org, r1, root=tmp_path), s,
                                   check_freq=False, check_names=False)


def test_hash_is_over_canonical_bytes_not_float_repr(db, org, tmp_path):
    from services.library import series_store as S

    s = _berlin_prices()
    noisy = s + 1e-13          # below %.10g resolution → same canonical bytes
    r1 = S.put_series(db, org, "canon", s, {"source": "u"}, root=tmp_path)
    r2 = S.put_series(db, org, "canon", noisy, {"source": "u"}, root=tmp_path)
    assert r2.hash == r1.hash and r2.version == 1


def test_files_live_under_the_hidden_org_scoped_library_dir(db, org, tmp_path):
    from services.library import series_store as S

    ref = S.put_series(db, org, "loc", _berlin_prices(), {"source": "u"}, root=tmp_path)
    files = list((tmp_path / ".library" / str(org) / "series").glob("*.csv.gz"))
    assert len(files) == 1 and ref.hash[:16] in files[0].name
    # gzip written with mtime=0: identical content → identical file bytes.
    raw = files[0].read_bytes()
    assert gzip.decompress(raw).startswith(b"# tz=Europe/Berlin\ntimestamp_utc,value")
    # mtime=0 → a second write of the same content is byte-identical.
    assert gzip.compress(gzip.decompress(raw), mtime=0) == raw


def test_the_legacy_scanner_never_offers_the_library_dir(db, org, tmp_path):
    from services.legacy_migrate import _scan_root
    from services.library import series_store as S

    S.put_series(db, org, "hid", _berlin_prices(), {"source": "u"}, root=tmp_path)
    assert ".library" not in _scan_root(tmp_path, pre_auth_layout=True)


def test_orgs_cannot_resolve_each_others_series(db, org, tmp_path):
    from services.library import series_store as S

    ref = S.put_series(db, org, "private", _berlin_prices(), {"source": "u"}, root=tmp_path)
    with pytest.raises(S.LibraryRefNotFound):
        S.resolve(db, uuid.uuid4(), ref, root=tmp_path)


def test_a_tampered_file_is_refused_as_stale(db, org, tmp_path):
    from services.library import series_store as S

    ref = S.put_series(db, org, "tamper", _berlin_prices(), {"source": "u"}, root=tmp_path)
    f = next((tmp_path / ".library" / str(org) / "series").glob("*.csv.gz"))
    f.write_bytes(gzip.compress(b"timestamp_utc,value\n2030-01-01T00:00:00Z,1\n", mtime=0))
    with pytest.raises(S.LibraryRefStale):
        S.resolve(db, org, ref, root=tmp_path)


def test_a_ref_with_a_wrong_hash_is_stale(db, org, tmp_path):
    from models.commercial import PriceSeriesRef
    from services.library import series_store as S

    ref = S.put_series(db, org, "wronghash", _berlin_prices(), {"source": "u"}, root=tmp_path)
    bad = PriceSeriesRef(id=ref.id, version=ref.version, hash="0" * 64, source=ref.source)
    with pytest.raises(S.LibraryRefStale):
        S.resolve(db, org, bad, root=tmp_path)


def test_naive_series_round_trip_as_naive(db, org, tmp_path):
    from services.library import series_store as S

    idx = pd.date_range("2030-01-01", periods=24, freq="h")
    s = pd.Series(np.arange(24, dtype=float), index=idx)
    ref = S.put_series(db, org, "naive", s, {"source": "u"}, root=tmp_path)
    back = S.resolve(db, org, ref, root=tmp_path)
    assert back.index.tz is None
    pd.testing.assert_series_equal(back, s, check_freq=False, check_names=False)


def test_non_finite_or_non_datetime_series_are_refused(db, org, tmp_path):
    from services.library import series_store as S

    idx = pd.date_range("2030-01-01", periods=3, freq="h")
    with pytest.raises(ValueError):
        S.put_series(db, org, "nan", pd.Series([1.0, np.nan, 2.0], index=idx), {"source": "u"},
                     root=tmp_path)
    with pytest.raises(ValueError):
        S.put_series(db, org, "intidx", pd.Series([1.0, 2.0]), {"source": "u"}, root=tmp_path)
    with pytest.raises(ValueError):
        S.put_series(db, org, "", pd.Series([1.0], index=idx[:1]), {"source": "u"}, root=tmp_path)


def test_list_series_shows_latest_versions(db, org, tmp_path):
    from services.library import series_store as S

    s = _berlin_prices()
    S.put_series(db, org, "a", s, {"source": "u"}, root=tmp_path)
    S.put_series(db, org, "a", s + 1, {"source": "u"}, root=tmp_path)
    S.put_series(db, org, "b", s, {"source": "u"}, root=tmp_path)
    latest = {r.id: r.version for r in S.list_series(db, org)}
    assert latest["a"] == 2 and latest["b"] == 1


# ── review WP1.1a conditions ─────────────────────────────────────────────────


def test_bad_meta_is_refused_before_anything_is_committed(db, org, tmp_path):
    from services.library import series_store as S

    with pytest.raises(ValueError):
        S.put_series(db, org, "badmeta", _berlin_prices(), {"source": 123}, root=tmp_path)
    with pytest.raises(ValueError):
        S.put_series(db, org, "badmeta", _berlin_prices(), {"surprise": "x"}, root=tmp_path)
    # Nothing was written: the org's listing still works and has no such item.
    assert all(r.id != "badmeta" for r in S.list_series(db, org))


def test_concurrent_puts_of_the_same_content_both_get_version_one(_auth_db, org, tmp_path):
    """Two sessions race: both read 'no latest', both try to insert v1."""
    from services.library import series_store as S

    _engine, session_local = _auth_db
    s = _berlin_prices()
    with session_local() as a, session_local() as b:
        real_latest = S._latest_row
        calls = {"a": 0}

        def stale_latest(db_, org_, name_):  # A's FIRST read predates B's commit
            if db_ is a and calls["a"] == 0:
                calls["a"] += 1
                return None
            return real_latest(db_, org_, name_)

        rb = S.put_series(b, org, "race", s, {"source": "u"}, root=tmp_path)
        S._latest_row = stale_latest
        try:
            ra = S.put_series(a, org, "race", s, {"source": "u"}, root=tmp_path)
        finally:
            S._latest_row = real_latest
    assert (ra.version, ra.hash) == (rb.version, rb.hash) == (1, rb.hash)


def test_concurrent_puts_of_different_content_get_consecutive_versions(_auth_db, org, tmp_path):
    from services.library import series_store as S

    _engine, session_local = _auth_db
    s = _berlin_prices()
    with session_local() as a, session_local() as b:
        real_latest = S._latest_row
        rb = S.put_series(b, org, "race2", s, {"source": "u"}, root=tmp_path)
        seen = {"a": 0}

        def stale_once(db_, o, n):
            if db_ is a and seen["a"] == 0:
                seen["a"] += 1
                return None
            return real_latest(db_, o, n)

        S._latest_row = stale_once
        try:
            ra = S.put_series(a, org, "race2", s + 1.0, {"source": "u"}, root=tmp_path)
        finally:
            S._latest_row = real_latest
    assert rb.version == 1 and ra.version == 2


def test_a_corrupt_existing_file_is_rewritten_on_the_next_put(db, org, tmp_path):
    from services.library import series_store as S

    s = _berlin_prices()
    S.put_series(db, org, "heal", s, {"source": "u"}, root=tmp_path)
    f = next((tmp_path / ".library" / str(org) / "series").glob("heal-*.csv.gz"))
    f.write_bytes(b"garbage")
    ref = S.put_series(db, org, "heal", s, {"source": "u"}, root=tmp_path)
    pd.testing.assert_series_equal(S.resolve(db, org, ref, root=tmp_path), s,
                                   check_freq=False, check_names=False)


def test_sub_second_timestamps_are_refused():
    from services.library import series_store as S

    idx = pd.date_range("2030-01-01", periods=3, freq="500ms")
    with pytest.raises(ValueError, match="second"):
        S._validate("subsec", pd.Series([1.0, 2.0, 3.0], index=idx))


def test_timezone_is_part_of_the_hash(db, org, tmp_path):
    from services.library import series_store as S

    s = _berlin_prices()
    r1 = S.put_series(db, org, "tzh", s, {"source": "u"}, root=tmp_path)
    r2 = S.put_series(db, org, "tzh", s.tz_convert("UTC"), {"source": "u"}, root=tmp_path)
    assert r2.version == 2 and r2.hash != r1.hash
    assert str(S.resolve(db, org, r2, root=tmp_path).index.tz) == "UTC"


def test_unrounded_values_round_trip_to_ten_significant_digits(db, org, tmp_path):
    from services.library import series_store as S

    idx = pd.date_range("2030-01-01", periods=5, freq="h", tz="Europe/Berlin")
    s = pd.Series([123456.78901234, -0.0, 1e-7 / 3, 2.0 / 3.0, 9.87654321e8], index=idx)
    ref = S.put_series(db, org, "prec", s, {"source": "u"}, root=tmp_path)
    back = S.resolve(db, org, ref, root=tmp_path)
    np.testing.assert_allclose(back.to_numpy(), s.to_numpy(), rtol=1e-9, atol=0)
    # -0.0 and 0.0 are the same canonical number.
    s2 = s.copy()
    s2.iloc[1] = 0.0
    assert S.put_series(db, org, "prec", s2, {"source": "u"}, root=tmp_path).version == 1


def test_a_row_path_escaping_the_library_is_refused(db, org, tmp_path):
    from db.models import LibraryItem
    from services.library import series_store as S

    ref = S.put_series(db, org, "esc", _berlin_prices(), {"source": "u"}, root=tmp_path)
    row = db.query(LibraryItem).filter_by(org_id=org, name="esc").one()
    row.path = "../../../etc/passwd"
    db.commit()
    with pytest.raises(S.LibraryRefStale, match="outside"):
        S.resolve(db, org, ref, root=tmp_path)


def test_reconcile_never_treats_the_library_as_a_project_dir(tmp_path):
    from services.storage_reconcile import _candidate_dirs

    (tmp_path / ".library" / "org-x" / "series").mkdir(parents=True)
    (tmp_path / "some-org" / "proj").mkdir(parents=True)
    for seg in (True, False):
        dirs = _candidate_dirs(tmp_path, org_segment=seg)
        assert not any(".library" in d.parts for d in dirs), (seg, dirs)


def test_list_series_is_org_isolated_between_two_real_orgs(db, org, second_identity, tmp_path):
    from services.library import series_store as S

    S.put_series(db, org, "mine", _berlin_prices(), {"source": "u"}, root=tmp_path)
    S.put_series(db, second_identity["org_id"], "theirs", _berlin_prices(), {"source": "u"},
                 root=tmp_path)
    assert {r.id for r in S.list_series(db, org)} == {"mine"}
    assert {r.id for r in S.list_series(db, second_identity["org_id"])} == {"theirs"}


def test_items_persist_and_resolve_from_a_fresh_session(_auth_db, org, tmp_path):
    from services.library import series_store as S

    _engine, session_local = _auth_db
    with session_local() as a:
        ref = S.put_series(a, org, "persist", _berlin_prices(), {"source": "u"}, root=tmp_path)
    with session_local() as b:
        pd.testing.assert_series_equal(S.resolve(b, org, ref, root=tmp_path), _berlin_prices(),
                                       check_freq=False, check_names=False)


def test_library_dir_lives_in_storage_paths():
    from services import storage_paths
    from services.library import series_store as S

    assert S.library_dir is storage_paths.library_dir
