"""
A flat export price series (IC U1 follow-up, item e; plan "one engine, two faces" §4 rule C3).

IC prices export only through `CommercialConfig.export_price_ref`, a Library series. A guided
study with one constant export price (the defaults pack's `export_price_eur_per_mwh`) mints a
flat series on the project's snapshot axis through `series_store.put_series` and passes the
returned ref. Pinned here:

  * the ref is what `export_price_ref` takes, and resolves back to the same flat values;
  * minting is idempotent on content (same price, same axis → same version);
  * a non-finite price, an empty axis or a bad name are refused before anything is written;
  * `commercial.binding.bind_commercial` resolves the ref and writes the flat price on the
    export link (`ic_export_price`), so the series really is bindable.

Written red first: `services.library.export_series` did not exist.
"""
from __future__ import annotations

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


def _snapshots(n: int = 48) -> pd.DatetimeIndex:
    return pd.date_range("2030-01-01", periods=n, freq="h")


def test_mints_a_flat_series_and_returns_an_export_price_ref(db, org, tmp_path):
    from models.commercial import CommercialConfig, PriceSeriesRef
    from services.library import series_store as S
    from services.library.export_series import put_flat_export_series

    snaps = _snapshots()
    ref = put_flat_export_series(db, org, "study_x_export_flat", 40.0, snapshots=snaps,
                                 source="generic_defaults@2026-10-05 de_industrial_illustrative",
                                 root=tmp_path)
    assert isinstance(ref, PriceSeriesRef) and ref.version == 1 and len(ref.hash) == 64
    back = S.resolve(db, org, ref, root=tmp_path)
    assert back.index.equals(snaps) and (back.to_numpy() == 40.0).all()
    meta = S.series_meta(db, org, ref)
    assert meta["unit"] == "EUR/MWh" and "flat_export_price" in meta["notes"]
    assert ref.source.startswith("generic_defaults@2026-10-05")
    cfg = CommercialConfig.model_validate({"poc_link": "import", "export_link": "export",
                                           "export_price_ref": ref.model_dump()})
    assert cfg.export_price_ref == ref


def test_minting_is_idempotent_and_a_new_price_is_a_new_version(db, org, tmp_path):
    from services.library.export_series import put_flat_export_series

    snaps = _snapshots()
    r1 = put_flat_export_series(db, org, "flat_idem", 30.0, snapshots=snaps, root=tmp_path)
    r2 = put_flat_export_series(db, org, "flat_idem", 30.0, snapshots=snaps.copy(),
                                root=tmp_path)
    assert (r1.version, r1.hash) == (r2.version, r2.hash)
    r3 = put_flat_export_series(db, org, "flat_idem", 35.0, snapshots=snaps, root=tmp_path)
    assert r3.version == 2 and r3.hash != r1.hash


def test_a_multiindex_snapshot_axis_uses_its_timestamps(db, org, tmp_path):
    from services.library import series_store as S
    from services.library.export_series import put_flat_export_series

    t = pd.DatetimeIndex(list(pd.date_range("2030-01-01", periods=3, freq="h"))
                         + list(pd.date_range("2040-01-01", periods=3, freq="h")))
    mi = pd.MultiIndex.from_arrays([[2030] * 3 + [2040] * 3, t], names=["period", "timestep"])
    ref = put_flat_export_series(db, org, "flat_multi", 12.5, snapshots=mi, root=tmp_path)
    back = S.resolve(db, org, ref, root=tmp_path)
    assert back.index.equals(t) and (back == 12.5).all()


@pytest.mark.parametrize("price", [float("nan"), float("inf"), True, "40"])
def test_a_bad_price_is_refused_before_anything_is_written(db, org, tmp_path, price):
    from services.library import series_store as S
    from services.library.export_series import put_flat_export_series

    with pytest.raises((ValueError, TypeError)):
        put_flat_export_series(db, org, "flat_bad", price, snapshots=_snapshots(),
                               root=tmp_path)
    assert S.latest_ref(db, org, "flat_bad") is None


def test_an_empty_or_unordered_axis_is_refused(db, org, tmp_path):
    from services.library.export_series import put_flat_export_series

    with pytest.raises(ValueError):
        put_flat_export_series(db, org, "flat_empty", 1.0,
                               snapshots=pd.DatetimeIndex([]), root=tmp_path)
    with pytest.raises(ValueError):
        put_flat_export_series(db, org, "flat_unordered", 1.0,
                               snapshots=_snapshots(3)[::-1], root=tmp_path)


def test_a_negative_export_price_is_allowed(db, org, tmp_path):
    """A negative export price is a real tariff outcome (paying to export), not an error."""
    from services.library import series_store as S
    from services.library.export_series import put_flat_export_series

    ref = put_flat_export_series(db, org, "flat_negative", -5.0, snapshots=_snapshots(4),
                                 root=tmp_path)
    assert (S.resolve(db, org, ref, root=tmp_path) == -5.0).all()


def test_the_pack_export_price_binds_through_the_commercial_binding(db, org, tmp_path):
    """End to end: the pack's export price → a flat Library series → `bind_commercial`
    resolves the ref and writes it on the export link."""
    import pypsa

    from models.commercial import CommercialConfig
    from services.commercial.binding import bind_commercial
    from services.library import series_store as S
    from services.library.defaults_pack import load_defaults_pack
    from services.library.export_series import put_flat_export_series

    pack = load_defaults_pack("2026-10-05")
    price = pack.export_price_eur_per_mwh("de_industrial_illustrative")
    n = pypsa.Network()
    n.set_snapshots(_snapshots(24))
    n.add("Bus", "grid")
    n.add("Bus", "site")
    n.add("Generator", "market", bus="grid", p_nom=100, marginal_cost=50)
    n.add("Load", "load", bus="site", p_set=1.0)
    n.add("Link", "import", bus0="grid", bus1="site", p_nom=10)
    n.add("Link", "export", bus0="site", bus1="grid", p_nom=10)
    ref = put_flat_export_series(db, org, "pack_export_flat", price, snapshots=n.snapshots,
                                 source=f"{pack.stamp} de_industrial_illustrative",
                                 root=tmp_path)
    commercial = CommercialConfig.model_validate({
        "poc_link": "import", "export_link": "export", "export_price_ref": ref.model_dump(),
        "import_tariff": pack.pack_tariff("de_industrial_illustrative").model_dump(mode="json")})
    stored = bind_commercial(n, commercial, project_dir=None,
                             resolve_ref=lambda r: S.resolve(db, org, r, root=tmp_path))
    assert stored["export_price_ref"]["hash"] == ref.hash
    assert stored["import_tariff"]["pack_hash"] == pack.stamp
    written = n.links_t["ic_export_price"]["export"].to_numpy()
    np.testing.assert_array_equal(written, np.full(24, 40.0))
