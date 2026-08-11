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

from routers.network import _infer_snapshot_freq, _user_ts, _user_ts_lock
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


# ── Finding 2: the CSV upload path has the same bare-ISO ambiguity hazard as
# the PATCH path above, but until now carried none of the counter/warning
# instrumentation — a CSV round-trip on a multi-period network that lost its
# `period|` prefix (Excel, a hand-edited file) would silently write every row
# into the LAST period, and `applied`/`skipped` alone couldn't distinguish
# that from a correct write. These two tests mirror the PATCH-path pair above,
# over `POST /snapshots/weightings.csv`.

def test_csv_upload_bare_iso_key_on_multi_period_warns_and_reports_count(client, install_network):
    install_network(_multi_period_network())
    baseline_id = max((e["id"] for e in client.get("/api/changelog/").json()), default=0)

    bare_iso = "2024-01-01T00:00:00"
    csv_bytes = f"snapshot,objective\n{bare_iso},7.0\n".encode()
    r = client.post(
        "/api/network/snapshots/weightings.csv",
        files={"file": ("weights.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 1
    assert body["ambiguous_bare_keys"] == 1

    new_entries = _weightings_changelog_entries(client, baseline_id)
    assert any(
        "WARNING" in e["description"] and "1 bare-ISO key" in e["description"]
        for e in new_entries
    ), f"expected a bare-ISO WARNING changelog entry, got: {new_entries}"


def test_csv_upload_period_qualified_key_on_multi_period_does_not_warn(client, install_network):
    install_network(_multi_period_network())
    baseline_id = max((e["id"] for e in client.get("/api/changelog/").json()), default=0)

    iso = "2024-01-01T00:00:00"
    csv_bytes = f"snapshot,objective\n2030|{iso},9.0\n".encode()
    r = client.post(
        "/api/network/snapshots/weightings.csv",
        files={"file": ("weights.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] == 1
    assert body["ambiguous_bare_keys"] == 0

    new_entries = _weightings_changelog_entries(client, baseline_id)
    assert new_entries, "expected a snapshot_weightings changelog entry"
    assert all("WARNING" not in e["description"] for e in new_entries), \
        f"period-qualified upload must not trigger the ambiguous-bare-key warning, got: {new_entries}"


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


def test_infer_freq_degrades_to_none_instead_of_raising_on_a_non_parseable_index():
    # Finding 7: only `pd.infer_freq` was wrapped in try/except. But
    # `pd.DatetimeIndex(sns)` itself raises (a `DateParseError`, a `ValueError`
    # subclass) when the snapshot index holds strings pandas can't parse as
    # dates — and `_infer_snapshot_freq` is called unconditionally at the top
    # of `get_snapshots`, the page's primary endpoint. No current GUI path
    # produces such an index, but the previous (pre-freq) code degraded
    # gracefully here; this guards that the freq feature didn't regress it
    # into a 500.
    n = pypsa.Network()
    n.set_snapshots(pd.Index(["alpha", "beta", "gamma"], name="snapshot"))
    assert _infer_snapshot_freq(n) is None


def test_snapshots_endpoint_reports_freq(client, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=12, freq="6h"))
    n.add("Bus", "B1")
    install_network(n)

    body = client.get("/api/network/snapshots").json()
    assert body["freq"] == "6h"


def test_snapshots_endpoint_reports_freq_on_multi_period_network(client, install_network):
    # Both return branches of get_snapshots carry a "freq" line; the flat-network
    # test above only exercises the non-MultiIndex branch. 3-hourly is chosen to be
    # distinguishable from both the flat test's 6-hourly and the default "h".
    n = pypsa.Network()
    n.add("Bus", "B1")
    block = pd.date_range("2024-01-01", periods=8, freq="3h")
    periods = [2030, 2050]
    n.set_snapshots(_multi_index(periods, block))
    n.investment_periods = periods
    install_network(n)

    body = client.get("/api/network/snapshots").json()
    assert body["freq"] == "3h"


def _period_blocks(n):
    """{period: [iso, ...]} for the current MultiIndex snapshots."""
    sns = n.snapshots
    out: dict[int, list[str]] = {}
    for p, ts in sns:
        out.setdefault(int(p), []).append(ts.isoformat())
    return out


def _multi_index_per_period(pairs):
    """pairs: [(period, DatetimeIndex), ...] — one distinct block per period."""
    import numpy as np
    period_level = np.concatenate([np.full(len(blk), p) for p, blk in pairs])
    timestep_level = pd.DatetimeIndex(np.concatenate([blk.values for _, blk in pairs]))
    mi = pd.MultiIndex.from_arrays(
        [period_level, timestep_level], names=["period", "timestep"],
    )
    mi.name = "snapshot"
    return mi


def _distinct_range_network():
    """3 periods, each on a DIFFERENT operational year — the multi-year
    weather-data workflow the 'Different year per period' mode exists for."""
    pairs = [
        (2030, pd.date_range("2019-01-01", periods=4, freq="h")),
        (2040, pd.date_range("2020-01-01", periods=4, freq="h")),
        (2050, pd.date_range("2021-01-01", periods=4, freq="h")),
    ]
    n = pypsa.Network()
    n.set_snapshots(_multi_index_per_period(pairs))
    n.investment_periods = [2030, 2040, 2050]
    n.add("Bus", "B1")
    return n


def test_adding_a_period_preserves_each_existing_periods_own_range(client, install_network, session_ctx):
    live = install_network(_distinct_range_network())
    before = _period_blocks(live)
    assert before[2030][0].startswith("2019")
    assert before[2040][0].startswith("2020")
    assert before[2050][0].startswith("2021")

    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2040, 2050, 2060]},
    )
    assert resp.status_code == 200, resp.text

    # `PyPSAService.get_network()` called from the test body (outside a
    # request) resolves the process foreground, not this client's session — see
    # the `session_ctx` fixture's docstring. `session_ctx(client).network` is
    # the established way this suite reads back a session's post-request state
    # (see test_activate.py).
    after = _period_blocks(session_ctx(client).network)
    # Each pre-existing period keeps ITS OWN operational year. Before this fix
    # all three collapsed onto 2019 — period 0's range.
    assert after[2030] == before[2030]
    assert after[2040] == before[2040]
    assert after[2050] == before[2050]


def test_a_newly_added_period_inherits_the_first_periods_range_as_a_template(client, install_network, session_ctx):
    install_network(_distinct_range_network())
    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2040, 2050, 2060]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(session_ctx(client).network)
    assert 2060 in after
    assert after[2060][0].startswith("2019")
    assert len(after[2060]) == 4


def test_removing_a_period_drops_only_its_block(client, install_network, session_ctx):
    live = install_network(_distinct_range_network())
    before = _period_blocks(live)

    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2050]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(session_ctx(client).network)
    assert sorted(after) == [2030, 2050]
    assert after[2030] == before[2030]
    assert after[2050] == before[2050]


def test_flat_to_multi_promotion_still_gives_every_period_the_flat_range(client, install_network, session_ctx):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=4, freq="h"))
    n.add("Bus", "B1")
    install_network(n)

    resp = client.post(
        "/api/network/investment_periods",
        json={"periods": [2030, 2040]},
    )
    assert resp.status_code == 200, resp.text

    after = _period_blocks(session_ctx(client).network)
    assert after[2030] == after[2040]
    assert after[2030][0].startswith("2024")


# ── B10: had_custom_weights on POST /network/snapshots/sample_weeks ────────
# Representative-week sampling replaces snapshot_weightings wholesale — the
# prior weights have no mapping onto the new sparse index, so they are always
# overwritten with the sampler-derived rep-week scaling. Until now, whether
# that overwrite actually discarded something the user had configured was
# recorded only in the audit-log changelog entry, never in the HTTP response,
# so the frontend had no way to warn the user. These two tests pin both
# directions of the `had_custom_weights` flag.

HOURS_IN_2030 = 8760


def _annual_hourly_network() -> pypsa.Network:
    """
    A full non-leap-year hourly flat network — the shape
    `_annual_hourly_reference` (and therefore `can_sample_weeks`) requires
    before `/snapshots/sample_weeks` will accept a request at all.
    """
    n = pypsa.Network()
    idx = pd.date_range("2030-01-01", periods=HOURS_IN_2030, freq="h")
    idx.name = "snapshot"
    n.set_snapshots(idx)
    n.add("Bus", "B")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="B", carrier="gas", p_nom=100.0, marginal_cost=10.0)
    n.add("Load", "L", bus="B", p_set=50.0)
    return n


def test_sample_weeks_reports_had_custom_weights_true_when_prior_weights_were_customized(
    client, install_network,
):
    live = install_network(_annual_hourly_network())
    # Non-default BEFORE sampling — this is what the flag must catch.
    live.snapshot_weightings["objective"] = 2.0

    with _user_ts_lock:
        _user_ts[("loads", "p_set", "L")] = pd.Series(50.0, index=live.snapshots)
    try:
        r = client.post(
            "/api/network/snapshots/sample_weeks", json={"n_weeks": 1, "seed": 42},
        )
        assert r.status_code == 200, r.text
        assert r.json()["had_custom_weights"] is True
    finally:
        with _user_ts_lock:
            _user_ts.clear()


def test_sample_weeks_reports_had_custom_weights_false_for_untouched_default_weights(
    client, install_network,
):
    live = install_network(_annual_hourly_network())
    # snapshot_weightings is left at the PyPSA default (1.0 everywhere).

    with _user_ts_lock:
        _user_ts[("loads", "p_set", "L")] = pd.Series(50.0, index=live.snapshots)
    try:
        r = client.post(
            "/api/network/snapshots/sample_weeks", json={"n_weeks": 1, "seed": 42},
        )
        assert r.status_code == 200, r.text
        assert r.json()["had_custom_weights"] is False
    finally:
        with _user_ts_lock:
            _user_ts.clear()


# ── CAPEX budget: stale investment periods must not constrain the LP ───────
# `capex_budget_fn` iterated cfg.capex_budget_per_period (the CONFIG dict) and
# constrained every extendable asset with build_year == period, without ever
# checking the period still exists in n.investment_periods. Remove 2040 from the
# horizon and its cap kept binding. Per-period load scaling already iterates the
# NETWORK's periods, so the two config maps disagreed; this aligns them.


def _budget_network(periods, build_year, p_nom_max=1000.0):
    """
    One extendable generator with an explicit build_year, on a MultiIndex
    network spanning `periods`. There is only ONE Load (fixed at 500 MW) and
    ONE Generator on the bus, so an UNCONSTRAINED solve's optimum is set by
    the LOAD, not by p_nom_max: nothing rewards building past what's needed
    to serve 500 MW, so p_nom_opt settles at 500.0 regardless of p_nom_max
    (here 1000.0 — a generous ceiling that never actually binds). A budget
    BELOW 500 (in €, at coef=1 EUR/MW here) shows up as a smaller p_nom_opt
    — but only because the caller also enables a high-VOLL slack generator
    (see `_solve_and_get_p_nom_opt`). Without that slack, capping this
    Generator below the fixed 500 MW load leaves nothing to cover the
    shortfall — PyPSA's nodal balance is an equality with no lost-load path
    by default — so the LP goes genuinely INFEASIBLE rather than settling at
    a smaller optimum. In that failure mode `p_nom_opt` never gets assigned
    and silently reads back its pre-solve default of 0.0, which would make a
    `p_nom_opt <= budget` assertion pass for the wrong reason regardless of
    whether the budget guard under test is even correct.
    """
    n = pypsa.Network()
    block = pd.date_range("2024-01-01", periods=2, freq="h")
    n.set_snapshots(_multi_index(periods, block))
    n.investment_periods = periods
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=500.0)
    n.add(
        "Generator", "G1", bus="B1",
        p_nom_extendable=True, p_nom_max=p_nom_max,
        build_year=build_year, lifetime=100,
        capital_cost=1.0, marginal_cost=1.0,
    )
    n.generators.loc["G1", "overnight_cost"] = 1.0
    return n


def _wait_for_solve(client, timeout_s: float = 120.0) -> None:
    """
    Block until `/api/simulation/status` leaves the running state.

    The solve runs on a worker thread (`n.optimize()` is blocking, so the
    backend never runs it inline). Poll rather than sleeping a fixed interval.
    """
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = client.get("/api/simulation/status").json()
        if st.get("status") not in ("running", "queued"):
            return
        time.sleep(0.25)
    raise AssertionError("solve did not finish within the timeout")


def _solve_and_get_p_nom_opt(client, session_ctx, budgets):
    """PUT the budget map, run a solve, return G1's optimised capacity.

    `session_ctx` is a pytest fixture factory (see conftest.py), not a
    module global — it has to be threaded through from the calling test
    rather than referenced as a free variable, or this raises NameError
    before the solve result can even be read.

    `voll` (value of lost load) is set here too, and is load-bearing, not
    cosmetic. The fixtures pin Load at 500 MW against a budget of only
    EUR 10 (coef 1 EUR/MW => 10 MW cap). With NO other supply, capping G1
    below the fixed load makes the LP genuinely INFEASIBLE rather than
    "constrained to a smaller p_nom_opt" — confirmed empirically: without
    voll, `p_nom_opt` silently stays at its pre-solve default of 0.0 (the
    solve fails with condition="infeasible"), and a naive `<= 10.0` assertion
    passes for the wrong reason regardless of whether the budget guard is
    correct. A high voll adds a per-bus slack generator (build_year=0,
    unbounded lifetime — active in every period, including 2030 for the
    build_year=2040 fixture, which would otherwise raise PyPSA's "Empty LHS
    with non-zero RHS" — the generator literally cannot supply demand in a
    period before its own build_year) that absorbs any load G1's cap can't
    cover, at a cost far above G1's marginal/capital cost — so the LP always
    prefers building G1 up to whatever cap actually applies, and p_nom_opt
    genuinely reports that cap instead of a stale zero.
    """
    r = client.put("/api/simulation/solver_config",
                   json={"capex_budget_per_period": budgets, "voll": 10000.0})
    assert r.status_code == 200, r.text
    r = client.post("/api/simulation/run")
    assert r.status_code in (200, 202), r.text
    _wait_for_solve(client)
    return float(session_ctx(client).network.generators.at["G1", "p_nom_opt"])


def test_a_budget_for_a_period_not_in_the_horizon_does_not_constrain(
    client, install_network, session_ctx,
):
    # 2040 is NOT an investment period, but the generator still carries
    # build_year=2040 and the config still holds 2040's budget.
    install_network(_budget_network([2030, 2050], build_year=2040))
    p_nom = _solve_and_get_p_nom_opt(client, session_ctx, {"2040": 10.0})
    assert p_nom > 10.0, (
        "a budget for a period outside n.investment_periods must not bind; "
        f"got p_nom_opt={p_nom}"
    )


def test_a_budget_for_a_live_period_still_binds(client, install_network, session_ctx):
    # The gate must not be over-broad: 2030 IS a period, so its cap applies.
    install_network(_budget_network([2030, 2050], build_year=2030))
    p_nom = _solve_and_get_p_nom_opt(client, session_ctx, {"2030": 10.0})
    assert p_nom <= 10.0 + 1e-6, (
        f"a live period's budget must still constrain; got p_nom_opt={p_nom}"
    )


def test_a_budget_still_binds_on_a_flat_network(client, install_network, session_ctx):
    # REGRESSION GUARD. _wrap_with_capex_budget is not multi-period gated, and
    # build_year is meaningful without investment periods. A naive membership
    # check against an empty n.investment_periods would silently disable every
    # budget on every flat network.
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2024-01-01", periods=2, freq="h"))
    n.add("Bus", "B1")
    n.add("Load", "L1", bus="B1", p_set=500.0)
    n.add(
        "Generator", "G1", bus="B1",
        p_nom_extendable=True, p_nom_max=1000.0,
        build_year=2030, lifetime=100,
        capital_cost=1.0, marginal_cost=1.0,
    )
    n.generators.loc["G1", "overnight_cost"] = 1.0
    install_network(n)

    p_nom = _solve_and_get_p_nom_opt(client, session_ctx, {"2030": 10.0})
    assert p_nom <= 10.0 + 1e-6, (
        f"flat-network budgets must still apply by build_year; got p_nom_opt={p_nom}"
    )


# ── Per-period snapshot weightings across a period edit ───────────────────
# _capture_snapshot_weights_per_timestep captured ONLY period 0, keyed by
# timestep; _reapply_snapshot_weights reindexed every period against it and
# .fillna(1.0)'d the misses. Once periods carry DISTINCT calendars — which the
# per-period range fix made survivable — periods 2+ matched nothing and their
# weights were silently reset to 1.0, under-weighting their OPEX in the LP.


def _weights_by_period(n) -> dict[int, list[float]]:
    """{period: [objective weight per timestep]} for a MultiIndex network."""
    sw = n.snapshot_weightings["objective"]
    out: dict[int, list[float]] = {}
    for (p, _ts), v in sw.items():
        out.setdefault(int(p), []).append(float(v))
    return out


def test_each_period_keeps_its_own_weights_when_calendars_differ(
    client, install_network, session_ctx,
):
    # 2030→2019, 2040→2020, 2050→2021 — three distinct operational years.
    n = _distinct_range_network()
    # Give every period a DIFFERENT, non-default weight so a reset to 1.0 and a
    # broadcast from period 0 are both distinguishable from correct behaviour.
    marks = {2030: 2.0, 2040: 3.0, 2050: 4.0}
    for (p, ts) in n.snapshots:
        n.snapshot_weightings.loc[(p, ts), "objective"] = marks[int(p)]
    live = install_network(n)
    before = _weights_by_period(live)

    r = client.post("/api/network/investment_periods",
                    json={"periods": [2030, 2040, 2050, 2060]})
    assert r.status_code == 200, r.text

    after = _weights_by_period(session_ctx(client).network)
    assert after[2030] == before[2030]
    assert after[2040] == before[2040], (
        "period 2040 must keep its OWN weights, not period 2030's and not 1.0; "
        f"got {after[2040]}, expected {before[2040]}"
    )
    assert after[2050] == before[2050]


def test_a_new_period_inherits_the_template_rather_than_defaulting(
    client, install_network, session_ctx,
):
    n = _distinct_range_network()
    for (p, ts) in n.snapshots:
        n.snapshot_weightings.loc[(p, ts), "objective"] = 2.0
    install_network(n)

    r = client.post("/api/network/investment_periods",
                    json={"periods": [2030, 2040, 2050, 2060]})
    assert r.status_code == 200, r.text

    after = _weights_by_period(session_ctx(client).network)
    assert 2060 in after
    assert all(v == 2.0 for v in after[2060]), (
        "a genuinely new period inherits the first captured period's weights as "
        f"a template, since its range is templated from it too; got {after[2060]}"
    )


def test_same_calendar_broadcast_is_unchanged(client, install_network, session_ctx):
    # The common workflow: one operational year replicated under every period.
    # Behaviour here must not change.
    block = pd.date_range("2024-01-01", periods=2, freq="h")
    n = pypsa.Network()
    n.set_snapshots(_multi_index([2030, 2040], block))
    n.investment_periods = [2030, 2040]
    n.add("Bus", "B1")
    for (p, ts) in n.snapshots:
        n.snapshot_weightings.loc[(p, ts), "objective"] = 7.0
    install_network(n)

    r = client.post("/api/network/investment_periods",
                    json={"periods": [2030, 2040, 2050]})
    assert r.status_code == 200, r.text

    after = _weights_by_period(session_ctx(client).network)
    for period in (2030, 2040, 2050):
        assert all(v == 7.0 for v in after[period]), (
            f"same-calendar broadcast regressed for {period}: {after[period]}"
        )
