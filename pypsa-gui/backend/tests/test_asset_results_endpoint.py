"""The per-asset endpoint: contract, gating, filters and view modes."""
import pytest

from tests.conftest import build_network

BASE = "/api/results/asset"


def _get(client, path, **params):
    return client.get(f"{BASE}{path}", params=params)


def test_asset_list_is_transient_filtered(client, install_network):
    n = build_network(solve=True)
    n.add("Generator", "__voll_B1", bus="B1", p_nom=1e6, marginal_cost=1e5)
    n.add("Generator", "gas@2030", bus="B1", carrier="gas", p_nom=0.0)
    from services.pypsa_service import PyPSAService
    install_network(n)
    PyPSAService.mark_transient("Generator", "__voll_B1")
    PyPSAService.mark_transient("Generator", "gas@2030")

    names = [a["name"] for a in _get(client, "/assets").json()["assets"]]
    assert "gas" in names
    assert "__voll_B1" not in names
    assert "gas@2030" not in names


def test_every_category_is_returned_with_a_status(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch").json()
    ids = [c["id"] for c in body["categories"]]
    assert ids == ["summary", "capacity", "dispatch", "storage",
                   "loadflow", "prices", "economics", "emissions"]
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["dispatch"]["status"] == "ok"
    assert by_id["storage"]["status"] == "na"
    assert "store energy" in by_id["storage"]["reason"]
    assert by_id["loadflow"]["status"] in ("blocked", "na")


def test_metrics_list_includes_blocked_entries_because_it_is_the_checklist(
        client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch").json()
    by_id = {m["id"]: m for m in body["metrics"]}
    assert by_id["p"]["status"] == "ok"
    assert by_id["status"]["status"] == "blocked"
    assert by_id["status"]["remedy"]["action"] == "open_properties"
    assert "unit" in by_id["p"] and by_id["p"]["unit"] == "MW"
    assert by_id["curtailment"]["origin"] == "derived"
    assert by_id["curtailment"]["formula"]


def test_requested_series_match_a_direct_frame_read(client, install_network):
    n = build_network(solve=True)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p").json()
    assert body["series"]["p"] == pytest.approx(list(n.generators_t.p["gas"].values))
    assert len(body["index"]) == len(n.snapshots)


def test_blocked_metrics_are_never_served_even_if_requested(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch",
                metrics="p,status").json()
    assert "p" in body["series"]
    assert "status" not in body["series"]


def test_summary_stays_live_on_an_unsolved_network(client, install_network):
    install_network(build_network(solve=False))
    body = _get(client, "/Generator/gas", category="summary").json()
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["summary"]["status"] == "ok"
    assert by_id["dispatch"]["status"] == "blocked"
    assert by_id["dispatch"]["remedy"]["action"] == "run_simulation"
    assert body["scalars"]["params"]["p_nom"] == pytest.approx(200.0)


def test_stale_dispatch_blocks_every_result_category(client, install_network):
    n = build_network(solve=True)
    n.add("Generator", "added_after_solve", bus="B1", p_nom=1.0)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch").json()
    by_id = {c["id"]: c for c in body["categories"]}
    assert by_id["summary"]["status"] == "ok"
    assert by_id["dispatch"]["status"] == "blocked"
    assert body["series"] == {}


def test_unknown_asset_is_404(client, install_network):
    install_network(build_network(solve=True))
    assert _get(client, "/Generator/nope", category="summary").status_code == 404


def test_unknown_class_is_404(client, install_network):
    install_network(build_network(solve=True))
    assert _get(client, "/Nonsense/gas", category="summary").status_code == 404


def test_unknown_category_is_422(client, install_network):
    install_network(build_network(solve=True))
    assert _get(client, "/Generator/gas", category="nope").status_code == 422


def test_horizon_filter_narrows_the_index(client, install_network):
    n = build_network(solve=True)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                **{"from": "2025-01-01T01:00:00", "to": "2025-01-01T02:00:00"}).json()
    assert len(body["index"]) == 2
    assert len(body["series"]["p"]) == 2


def test_non_finite_values_serialise_to_null(client, install_network):
    n = build_network(solve=True)
    n.generators_t.p.iloc[0, n.generators_t.p.columns.get_loc("gas")] = float("nan")
    install_network(n)
    r = _get(client, "/Generator/gas", category="dispatch", metrics="p")
    assert r.status_code == 200          # not a 21-byte plain-text 500
    assert r.json()["series"]["p"][0] is None


def test_duration_mode_sorts_each_series_and_reports_percentiles(
        client, install_network):
    n = build_network(solve=True)
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                mode="duration").json()
    vals = body["series"]["p"]
    assert vals == sorted(vals, reverse=True)
    assert body["pct_of_hours"][0] == pytest.approx(1 / len(vals))
    assert body["columns"][0]["metric_id"] == "p"


def test_monthly_mode_emits_one_column_triple_per_metric(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                mode="monthly").json()
    ids = [c["id"] for c in body["columns"]]
    assert ids == ["p__mean", "p__max", "p__energy"]
    assert [c["agg"] for c in body["columns"]] == ["mean", "max", "energy"]
    assert body["index"] == ["2025-01"]


def test_chronological_columns_carry_no_aggregation(client, install_network):
    install_network(build_network(solve=True))
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p").json()
    assert body["columns"] == [
        {"id": "p", "label": "Active power", "unit": "MW",
         "metric_id": "p", "agg": None}
    ]


def _multi_period_network():
    """
    2-period MultiIndex network, solved. `mi.name = "snapshot"` is
    load-bearing: this repo has a documented failure class where a MultiIndex
    loses its overall `.name` and xarray then reports a `dim_0` error.
    """
    import pandas as pd
    n = build_network(solve=False)
    base = n.snapshots
    mi = pd.MultiIndex.from_product([[2026, 2031], base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.set_snapshots(mi)
    n.investment_periods = [2026, 2031]
    n.investment_period_weightings["years"] = 5.0
    n.optimize(solver_name="highs")
    return n


def test_multi_period_series_align_and_do_not_come_back_all_null(
        client, install_network):
    """
    `series_for` reindexes a `_t` frame to ctx.sns. On a MultiIndex the
    reindex aligns by tuple; if it ever silently misaligned, every value would
    be null and the tab would look empty rather than broken.
    """
    install_network(_multi_period_network())
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p").json()
    assert len(body["index"]) == 8            # 4 timesteps x 2 periods
    assert body["periods"] == [2026] * 4 + [2031] * 4
    assert all(v is not None for v in body["series"]["p"])


def test_period_filter_narrows_to_one_investment_period(client, install_network):
    install_network(_multi_period_network())
    body = _get(client, "/Generator/gas", category="dispatch", metrics="p",
                period="2031").json()
    assert len(body["index"]) == 4
    assert set(body["periods"]) == {2031}


def test_multi_period_energy_applies_years_exactly_once(client, install_network):
    """
    Guards the same double-multiplication that shipped in Task 2: with
    years=5 and 8 snapshots, energy must be 5x the raw dispatch sum, not 25x.
    """
    n = _multi_period_network()
    install_network(n)
    body = _get(client, "/Generator/gas", category="dispatch",
                metrics="energy_mwh").json()
    raw = float(n.generators_t.p["gas"].sum())
    assert body["scalars"]["energy_mwh"] == pytest.approx(raw * 5.0)


def test_horizon_filter_on_a_multi_period_network_keeps_the_index_name():
    """
    Positional slicing drops a MultiIndex's overall `.name`. Losing it is a
    documented failure class here — it surfaces later as an xarray `dim_0`
    error, far from the code that caused it.
    """
    from services.asset_results import service as svc
    n = _multi_period_network()
    sns = svc.slice_snapshots(n, "2025-01-01T01:00:00", "2025-01-01T02:00:00", None)
    assert sns.name == "snapshot"
    assert len(sns) == 4          # 2 timesteps x 2 periods


def test_an_unmatched_period_yields_no_rows_not_every_row(client, install_network):
    """
    Falling back to the unfiltered set would report the whole horizon as
    belonging to a period the network does not have.
    """
    from services.asset_results import service as svc
    n = _multi_period_network()
    assert len(svc.slice_snapshots(n, None, None, 9999)) == 0
