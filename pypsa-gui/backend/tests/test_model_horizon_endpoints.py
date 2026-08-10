"""
Model Horizon page — HTTP-level coverage for `PATCH /api/network/snapshots/weightings`.

Task 1 (B1, B2) fixed the frontend to send `period|iso` keys on multi-period
networks instead of a bare ISO, because the backend registers a bare ISO once
per period and resolves it last-write-wins — a bare key on a 3-period network
always ends up writing the LAST period's row. Nothing under
`pypsa-gui/backend/tests` exercised `update_snapshot_weightings` over HTTP, so
a future edit that inverted the `is_multi and "|" not in str(key)` check (or
dropped the `ambiguous_bare_keys` increment) would pass the whole suite in
silence. These two tests pin both directions of that check.

NOTE for future tasks: this file is meant to be extended, not duplicated —
add further Model Horizon endpoint coverage here rather than in a new file.
"""
from __future__ import annotations

import pandas as pd
import pypsa

from routers.network import _infer_snapshot_freq
from tests.conftest import build_network


def _multi_period_network() -> pypsa.Network:
    """
    2-period MultiIndex network (2030, 2050), 2 hourly timesteps each. Built
    the way the endpoints themselves expect a MultiIndex: `mi.name = "snapshot"`
    set after `pd.MultiIndex.from_arrays(...)`, then `n.investment_periods`.
    """
    n = build_network(solve=False)
    periods = [2030, 2050]
    base_idx = pd.date_range("2024-01-01", periods=2, freq="h")
    mi = pd.MultiIndex.from_arrays(
        [[p for p in periods for _ in range(len(base_idx))],
         list(base_idx) * len(periods)],
        names=["period", "timestep"],
    )
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = periods
    return n


def _weightings_changelog_entries(client, baseline_id: int) -> list[dict]:
    entries = client.get("/api/changelog/").json()
    return [
        e for e in entries
        if e["id"] > baseline_id
        and e["component_type"] == "Network"
        and e["name"] == "snapshot_weightings"
    ]


def test_bare_iso_key_on_multi_period_writes_the_last_period_and_warns(client, install_network):
    """
    Positive case: a bare ISO key on a multi-period network is the ambiguous
    form. The PATCH must still succeed (documented tolerant fallback) AND
    resolve to the LAST period's row — 2050, not 2030 — with a changelog
    WARNING naming the count of ambiguous keys.
    """
    install_network(_multi_period_network())
    baseline_id = max((e["id"] for e in client.get("/api/changelog/").json()), default=0)

    bare_iso = "2024-01-01T00:00:00"
    r = client.patch("/api/network/snapshots/weightings", json={
        "updates": {bare_iso: {"objective": 7.0}},
    })
    assert r.status_code == 200, r.text

    rows = {(row["period"], row["timestep"]): row for row in r.json()["weightings"]}
    assert rows[(2050, bare_iso)]["objective"] == 7.0, \
        "bare key must resolve to the LAST period's row"
    assert rows[(2030, bare_iso)]["objective"] != 7.0, \
        "bare key must NOT also (or instead) write the first period's row"

    new_entries = _weightings_changelog_entries(client, baseline_id)
    assert any(
        "WARNING" in e["description"] and "1 bare-ISO key" in e["description"]
        for e in new_entries
    ), f"expected a bare-ISO WARNING changelog entry, got: {new_entries}"


def test_period_qualified_key_on_multi_period_writes_that_period_with_no_warning(client, install_network):
    """
    Negative case: the period-qualified `period|iso` key the GUI now sends is
    unambiguous. It must write exactly the named period's row and the
    changelog entry must carry no WARNING clause.
    """
    install_network(_multi_period_network())
    baseline_id = max((e["id"] for e in client.get("/api/changelog/").json()), default=0)

    iso = "2024-01-01T00:00:00"
    r = client.patch("/api/network/snapshots/weightings", json={
        "updates": {f"2030|{iso}": {"objective": 9.0}},
    })
    assert r.status_code == 200, r.text

    rows = {(row["period"], row["timestep"]): row for row in r.json()["weightings"]}
    assert rows[(2030, iso)]["objective"] == 9.0
    assert rows[(2050, iso)]["objective"] != 9.0, \
        "period-qualified key must not leak into the other period's row"

    new_entries = _weightings_changelog_entries(client, baseline_id)
    assert new_entries, "expected a snapshot_weightings changelog entry"
    assert all("WARNING" not in e["description"] for e in new_entries), \
        f"period-qualified key must not trigger the ambiguous-bare-key warning, got: {new_entries}"


def _multi_index(periods, block):
    import numpy as np
    period_level = np.concatenate([np.full(len(block), p) for p in periods])
    timestep_level = pd.DatetimeIndex(np.concatenate([block.values for _ in periods]))
    mi = pd.MultiIndex.from_arrays(
        [period_level, timestep_level], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    return mi


def test_infer_freq_flat_hourly():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=24, freq="h"))
    assert _infer_snapshot_freq(n) == "h"


def test_infer_freq_flat_daily():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=10, freq="D"))
    assert _infer_snapshot_freq(n) == "D"


def test_infer_freq_multi_period_reads_one_period_not_the_seam():
    # Naively inferring over the flattened timestep level sees the jump from
    # period 2030's last hour back to period 2040's first — an irregular index.
    # Only period 0's slice is meaningful.
    block = pd.date_range("2024-01-01", periods=8, freq="3h")
    n = pypsa.Network()
    n.set_snapshots(_multi_index([2030, 2040, 2050], block))
    assert _infer_snapshot_freq(n) == "3h"


def test_infer_freq_falls_back_to_modal_delta_for_representative_weeks():
    # Two disjoint 168-hour blocks: pd.infer_freq gives None, but the resolution
    # is hourly and the card should say so.
    a = pd.date_range("2024-01-01", periods=168, freq="h")
    b = pd.date_range("2024-03-04", periods=168, freq="h")
    n = pypsa.Network()
    n.set_snapshots(pd.DatetimeIndex(a.append(b)))
    assert _infer_snapshot_freq(n) == "h"


def test_infer_freq_returns_none_for_a_single_snapshot():
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=1, freq="h"))
    assert _infer_snapshot_freq(n) is None


def test_snapshots_endpoint_reports_freq(client, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=12, freq="6h"))
    n.add("Bus", "B1")
    install_network(n)

    body = client.get("/api/network/snapshots").json()
    assert body["freq"] == "6h"
