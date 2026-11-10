"""RP-18: a series with no provenance record (a project saved before RP-14, or
a raw upload) is `unrecorded`, not `uploaded` and never native.

Read-time only: reading changes nothing (R2). Every reader treats it as
measured, so a default never replaces it (R1). The user confirms a series is the
network's own through one route that writes only the provenance meta (R3).
"""
from __future__ import annotations

import pandas as pd
import pypsa
import pytest

from services.profile_gen import defaults as dflt
from services.profile_gen import readiness as rd
from services.profile_gen.apply import current_digest

SNAPS = pd.date_range("2025-01-01", periods=8760, freq="h", name="snapshot")
KEY = ("generators", "p_max_pu", "g_a")
BASE = "/api/projects/Legacy Farm"


def _network() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(SNAPS)
    n.add("Bus", "B1", x=8.0, y=50.0)
    n.add("Bus", "B2")  # never placed
    n.add("Carrier", ["wind"])
    n.add("Generator", "g_a", bus="B1", carrier="wind", p_nom=10.0)
    n.add("Generator", "g_b", bus="B2", carrier="wind", p_nom=10.0)
    return n


@pytest.fixture
def project(client, install_network):
    install_network(_network())
    resp = client.post("/api/projects/Legacy Farm", params={"force": True, "rebind": True})
    assert resp.status_code == 200, resp.text
    return "Legacy Farm"


@pytest.fixture
def ctx(session_ctx, client, project):
    c = session_ctx(client)
    c.user_ts[KEY] = pd.Series(0.9, index=SNAPS)
    c.user_ts.pop(("generators", "p_max_pu", "g_b"), None)
    c.user_ts_meta.pop(KEY, None)  # the legacy shape: a series, no record
    return c


def _row(client, name="g_a"):
    rows = client.get(f"{BASE}/profiles/readiness").json()["generators"]
    return {r["generator"]: r for r in rows}[name]


# --- R1: every reader ---------------------------------------------------

def test_profile_kind_says_unrecorded(ctx):
    assert rd.profile_kind(ctx, "g_a") == "unrecorded"


def test_unrecorded_is_never_native_and_stays_measured(ctx):
    meta = ctx.user_ts_meta.get(KEY)
    assert dflt.is_measured(meta) and not dflt.is_network_native(meta)


def test_supplied_profiles_counts_unrecorded(ctx):
    assert rd.supplied_profiles(ctx, ["g_a"]) == {"g_a"}


def test_location_missing_skips_unrecorded(ctx):
    ctx.user_ts[("generators", "p_max_pu", "g_b")] = pd.Series(0.5, index=SNAPS)
    supplied = rd.supplied_profiles(ctx, ["g_a", "g_b"])
    assert rd.unplaced_renewables(ctx.network, supplied) == []


def test_readiness_row_names_it_and_carries_a_digest(client, ctx):
    row = _row(client)
    assert row["profile"] == "unrecorded"
    assert row["digest"] == current_digest(ctx.user_ts[KEY])


def test_current_reader_treats_it_as_an_upload_not_network(ctx):
    from services.profile_gen.current import describe_current
    d = describe_current(ctx, *KEY)
    assert d["provenance"] == "uploaded" and d["source"] == "upload"


def test_batch_apply_refuses_it_as_measured(client, ctx):
    # the batch route's guard is `is_measured`: an unrecorded series is kept
    assert dflt.is_measured(ctx.user_ts_meta.get(KEY))


# --- R2: no write on read -------------------------------------------------

def test_a_readiness_read_changes_nothing(client, ctx):
    before_series = ctx.user_ts[KEY].copy()
    before_meta = dict(ctx.user_ts_meta)
    _row(client)
    assert ctx.user_ts_meta.get(KEY) is None and dict(ctx.user_ts_meta) == before_meta
    pd.testing.assert_series_equal(ctx.user_ts[KEY], before_series)


# --- R3: the confirmation write ---------------------------------------------

def _confirm(client, digest, name="g_a"):
    return client.post(f"{BASE}/profiles/unrecorded/confirm",
                       json={"items": [{"component": name, "digest": digest}]})


def test_confirm_writes_only_the_meta(client, ctx):
    before = ctx.user_ts[KEY].copy()
    r = _confirm(client, current_digest(before))
    assert r.status_code == 200, r.text
    assert ctx.user_ts_meta[KEY]["provenance"] == "network_native"
    pd.testing.assert_series_equal(ctx.user_ts[KEY], before)
    assert _row(client)["profile"] == "network_native"


def test_a_changed_series_is_refused_stale_and_nothing_is_stamped(client, ctx):
    digest = current_digest(ctx.user_ts[KEY])
    ctx.user_ts[KEY] = pd.Series(0.1, index=SNAPS)
    r = _confirm(client, digest)
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "unrecorded_stale"
    assert ctx.user_ts_meta.get(KEY) is None


def test_one_stale_item_refuses_the_whole_batch(client, ctx):
    ctx.user_ts[("generators", "p_max_pu", "g_b")] = pd.Series(0.5, index=SNAPS)
    good = current_digest(ctx.user_ts[KEY])
    r = client.post(f"{BASE}/profiles/unrecorded/confirm", json={"items": [
        {"component": "g_a", "digest": good}, {"component": "g_b", "digest": "0" * 16}]})
    assert r.status_code == 409
    assert ctx.user_ts_meta.get(KEY) is None


def test_a_recorded_series_is_not_restamped(client, ctx):
    ctx.user_ts_meta[KEY] = {"provenance": "measured", "source": "upload"}
    r = _confirm(client, current_digest(ctx.user_ts[KEY]))
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "not_unrecorded"
    assert ctx.user_ts_meta[KEY]["provenance"] == "measured"


def test_one_undo_restores_the_empty_meta(client, ctx, session_ctx):
    assert _confirm(client, current_digest(ctx.user_ts[KEY])).status_code == 200
    assert client.post("/api/network/undo").status_code == 200
    assert session_ctx(client).user_ts_meta.get(KEY) is None
    assert _row(client)["profile"] == "unrecorded"


def test_confirm_is_not_a_chat_tool():
    from harness.catalogue import TOOL_ROUTES, TOOLS
    assert not [t for t in TOOLS if "unrecorded" in str(t.get("name", t)).lower()
                and "readiness" not in str(t.get("name", t))]
    assert not [r for r in TOOL_ROUTES.values() if "unrecorded" in str(r)]


def test_a_component_with_no_series_is_refused_not_stamped(client, ctx):
    r = _confirm(client, "0" * 16, name="g_b")
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "not_unrecorded"
    assert ("generators", "p_max_pu", "g_b") not in ctx.user_ts_meta
