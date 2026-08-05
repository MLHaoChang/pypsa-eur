"""
The `from`/`to` contract on the /results/* series endpoints.

Solves a small network ONCE per module so the dispatch-freshness gate passes;
conftest's autouse `reset_backend` means the network must be re-installed per
test, which `solved_client` does.

Two coverage gaps found in review, both closed here without touching the
shared `tests/golden/fixture.py` (nine other test files depend on that
network's exact shape):

1. `store_dispatch` / `store_energy` / `transformers` have no `Store` /
   `Transformer` components on the golden network, so the four data-dependent
   parametrized tests always hit the 204 branch and skip — a copy-paste bug
   that declares `from_`/`to_` on the endpoint but forgets to forward them to
   `_serve_ts` would go undetected there. `_build_widened_network()` /
   `widened_client` add one of each, LOCALLY, to this file only.
2. Even with real data, a 204 on an endpoint the widened fixture GUARANTEES
   data for must fail the test, not skip it — `_expect_data` enforces that
   via `DATA_GUARANTEED_ENDPOINTS`. Skipping is reserved for the endpoints
   that genuinely have nothing to serve on this fixture (the three AC-PF
   endpoints, which need a `n.pf()` stage this module doesn't run).
3. The three AC-PF endpoints' forwarding still can't be exercised
   behaviourally (no PF stage = always 204), so a static check
   (`test_every_serve_ts_call_site_forwards_from_and_to`) reads
   `routers/results.py`'s AST and asserts every `_serve_ts(...)` call site
   literally contains `from_=from_, to_=to_` — catching the copy-paste bug
   with no dependency on fixture data at all.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.golden import fixture as gf

# Endpoints served by `_serve_ts`. Task 3 extends this to all sixteen.
SERVE_TS_ENDPOINTS = [
    "/api/results/generators",
    "/api/results/storage_dispatch",
    "/api/results/store_dispatch",
    "/api/results/store_energy",
    "/api/results/storage",
    "/api/results/lines",
    "/api/results/links",
    "/api/results/transformers",
    "/api/results/voltages",
    "/api/results/line_reactive",
    "/api/results/transformer_reactive",
]

# The subset of SERVE_TS_ENDPOINTS the WIDENED fixture (below) guarantees
# real dispatch on. A 204 from one of these is a regression (dispatch gate
# or `_result_df` broke), not a legitimate "no data" outcome — see
# `_expect_data`. The three AC-PF endpoints are deliberately excluded: they
# need a `n.pf()` stage this module doesn't run, so 204 stays a skip for them.
DATA_GUARANTEED_ENDPOINTS = [
    "/api/results/generators",
    "/api/results/storage_dispatch",
    "/api/results/store_dispatch",
    "/api/results/store_energy",
    "/api/results/storage",
    "/api/results/lines",
    "/api/results/links",
    "/api/results/transformers",
]

# Bespoke bodies — they call slice_ts directly rather than via _serve_ts.
BESPOKE_ENDPOINTS = [
    "/api/results/prices",
    "/api/results/curtailment",
    "/api/results/lost_load",
    "/api/results/loads",
]

# /unit_commitment is a COMPOSITE: {generators, status_grid, n_committable},
# and only `status_grid` carries index/columns/data. Its range block lives
# inside status_grid, so the shared assertions cannot address it.
COMPOSITE_ENDPOINT = "/api/results/unit_commitment"

ALL_SERIES_ENDPOINTS = SERVE_TS_ENDPOINTS + BESPOKE_ENDPOINTS


@pytest.fixture()
def solved_client(client, reset_backend):
    """
    A client whose backend holds a solved network, so `_dispatch_ready` passes.

    Re-installed per test on purpose: conftest's `reset_backend` is autouse and
    calls `PyPSAService.reset_network()` before AND after every test, so a
    session-scoped solved network cannot stay installed.
    """
    n = gf.solve_golden_network()
    gf.install_golden(n)
    return client


def _build_widened_network():
    """
    The golden network plus one `Store` and one `Transformer` — the two
    component classes the golden network doesn't carry, so
    `/store_dispatch`, `/store_energy` and `/transformers` have nothing to
    serve and every data-dependent test on them skips instead of running.

    Deliberately NOT added to `tests/golden/fixture.py`: that network feeds
    economics/compare/coverage assertions in nine other test files, and
    changing its composition would change the numbers all of them anchor
    against. This widening is LOCAL to this file.

    Isolated on its own bus pair (`store_bus`, `trafo_bus`) with its own
    generator and loads, so it cannot perturb dispatch/sizing on any of the
    golden network's existing assets — the new components only exchange
    power with each other.
    """
    n = gf.build_golden_network()

    n.add("Bus", "store_bus")
    n.add(
        "Generator", "store_feed",
        bus="store_bus", carrier="gas",
        p_nom=20.0, p_nom_extendable=False, marginal_cost=40.0,
    )
    n.add("Load", "store_load", bus="store_bus", p_set=5.0)
    n.add(
        "Store", "widen_store",
        bus="store_bus", e_nom=50.0, e_cyclic=True,
        capital_cost=0.0, marginal_cost=0.0,
    )

    n.add("Bus", "trafo_bus")
    n.add(
        "Transformer", "widen_trafo",
        bus0="store_bus", bus1="trafo_bus",
        s_nom=100.0, x=0.1, r=0.01,
    )
    n.add("Load", "trafo_load", bus="trafo_bus", p_set=5.0)

    return n


_WIDENED_SOLVED = None


def _solve_widened_network():
    """
    Build + solve the widened network once per process — mirrors
    `gf.solve_golden_network()` (fixture.py:155-180) exactly: same
    `SolverConfig`, same `with_periodized_cost_defaults` wrapper, same
    `n.optimize()` call — so this fixture's solved state is produced the
    same way the golden one's is, just on a wider component set.
    """
    global _WIDENED_SOLVED
    if _WIDENED_SOLVED is None:
        from services.solver_service import SolverConfig, with_periodized_cost_defaults

        n = _build_widened_network()
        cfg = SolverConfig(
            discount_rate=gf.GOLDEN_DISCOUNT_RATE,
            multi_investment_periods=True,
            investment_periods=list(gf.GOLDEN_PERIODS),
        )
        with with_periodized_cost_defaults(n, cfg):
            n.optimize(solver_name="highs", multi_investment_periods=True)
        _WIDENED_SOLVED = n
    return _WIDENED_SOLVED


@pytest.fixture()
def widened_client(client, reset_backend):
    """
    Like `solved_client`, but backed by the widened network so
    `DATA_GUARANTEED_ENDPOINTS` all have real dispatch to slice.
    """
    n = _solve_widened_network()
    gf.install_golden(n)
    return client


def _ranged(client, url, **params):
    r = client.get(url, params=params)
    assert r.status_code in (200, 204), f"{url} -> {r.status_code}: {r.text[:400]}"
    return r


def _expect_data(r, url):
    """
    Route a 204 to the right outcome: FAIL for an endpoint the widened
    fixture guarantees data on (a regression in the dispatch gate or
    `_result_df`), SKIP for one that genuinely has nothing to serve here
    (the AC-PF endpoints, absent a `n.pf()` stage).
    """
    if r.status_code != 204:
        return
    if url in DATA_GUARANTEED_ENDPOINTS:
        pytest.fail(
            f"{url} returned 204, but the widened fixture guarantees data for "
            f"this endpoint (Store + Transformer added) — the dispatch gate "
            f"or _result_df regressed"
        )
    pytest.skip(f"{url} has no data on this fixture network")


@pytest.mark.parametrize("url", ALL_SERIES_ENDPOINTS)
def test_endpoint_accepts_from_and_to(solved_client, url):
    """A 422 here means the endpoint never declared the parameters."""
    r = _ranged(solved_client, url, **{"from": 0, "to": 0})

    assert r.status_code != 422


@pytest.mark.parametrize("url", ALL_SERIES_ENDPOINTS)
def test_ranged_response_echoes_what_it_served(widened_client, url):
    r = _ranged(widened_client, url, **{"from": 0, "to": 0})
    _expect_data(r, url)

    body = r.json()
    assert "range" in body, f"{url} returned no range block"
    assert set(body["range"]) == {"from", "to", "total", "complete", "capped"}
    assert body["range"]["from"] == 0
    assert body["range"]["to"] == 0
    assert len(body["data"]) == 1, "one row requested, one row expected"


@pytest.mark.parametrize("url", ALL_SERIES_ENDPOINTS)
def test_unranged_response_carries_no_range_key(widened_client, url):
    """The no-parameter path must stay byte-identical for existing consumers."""
    r = _ranged(widened_client, url)
    _expect_data(r, url)

    assert "range" not in r.json()


@pytest.mark.parametrize("url", ALL_SERIES_ENDPOINTS)
def test_a_server_slice_equals_the_same_slice_taken_client_side(widened_client, url):
    """
    The invariant this whole feature rests on: moving the window from after
    the download to before it must not change a single value. This is also
    what catches an off-by-one in the inclusive bounds.
    """
    full = _ranged(widened_client, url)
    _expect_data(full, url)
    full_body = full.json()
    if len(full_body["data"]) < 3:
        pytest.skip(f"{url} has too few rows to window")

    sliced = _ranged(widened_client, url, **{"from": 1, "to": 2}).json()

    assert sliced["columns"] == full_body["columns"]
    assert sliced["index"] == full_body["index"][1:3]
    assert sliced["data"] == full_body["data"][1:3]


@pytest.mark.parametrize("url", ALL_SERIES_ENDPOINTS)
def test_complete_is_false_for_a_window_and_true_for_the_whole(widened_client, url):
    full = _ranged(widened_client, url)
    _expect_data(full, url)
    total = len(full.json()["data"])
    if total < 2:
        pytest.skip(f"{url} has too few rows to window")

    whole = _ranged(widened_client, url, **{"from": 0, "to": total - 1}).json()
    window = _ranged(widened_client, url, **{"from": 0, "to": 0}).json()

    assert whole["range"]["complete"] is True
    assert window["range"]["complete"] is False


def test_a_one_row_request_is_small(widened_client):
    """The canvas win, as a regression guard rather than a claim."""
    r = _ranged(widened_client, "/api/results/generators", **{"from": 0, "to": 0})
    _expect_data(r, "/api/results/generators")

    assert len(r.content) < 8_192, f"one row serialised to {len(r.content)} bytes"


def test_the_endpoint_list_covers_every_series_endpoint():
    """
    Guards against a seventeenth series endpoint being added and silently
    escaping this contract. Mirrors the SURFACES matrix from the
    trustworthy-numbers work: the list is the test, not a comment.
    """
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routers" / "results.py"
    text = src.read_text()
    declared = set(re.findall(r'@results_router\.get\("([^"]+)"', text))

    ranged = {u.removeprefix("/api/results") for u in ALL_SERIES_ENDPOINTS}
    ranged.add(COMPOSITE_ENDPOINT.removeprefix("/api/results"))

    # The twelve aggregate endpoints: a snapshot range is meaningless for a
    # scalar or a per-asset roll-up, so they are excluded BY NAME. Adding a
    # new endpoint forces a deliberate choice between the two lists.
    aggregates = {
        "/cost_breakdown", "/objective_decomposition", "/economics_by_carrier",
        "/statistics", "/lcoh", "/ac_pf/status", "/losses", "/carrier_kpis",
        "/emissions", "/line_duals", "/price_drivers", "/asset_economics",
    }

    unclassified = declared - ranged - aggregates
    assert not unclassified, (
        f"unclassified /results endpoints: {sorted(unclassified)} — add each to "
        f"ALL_SERIES_ENDPOINTS (if it returns index/columns/data) or to "
        f"`aggregates` (if it does not)"
    )


def test_unit_commitment_carries_its_range_inside_status_grid(solved_client):
    r = solved_client.get(COMPOSITE_ENDPOINT, params={"from": 0, "to": 0})
    assert r.status_code in (200, 204), r.text[:400]
    if r.status_code == 204:
        pytest.skip("no committable units on this fixture network")

    body = r.json()
    grid = body.get("status_grid")
    if grid is None:
        pytest.skip("no status grid on this fixture network")

    assert "range" not in body, "range belongs beside index/columns/data, not at the top"
    assert grid["range"]["from"] == 0
    assert grid["range"]["to"] == 0
    assert len(grid["data"]) == 1


# ── Static forwarding check: closes the AC-PF endpoints' coverage gap ──────
#
# /voltages, /line_reactive and /transformer_reactive default to
# source="ac_pf" and need a PF stage to carry any data — running one is a
# bigger lift than this task warrants. But the exact copy-paste bug Step 6
# manufactured (declare from_/to_ on the endpoint signature, forget to
# forward them to `_serve_ts`) is detectable WITHOUT data: read the source.

def _serve_ts_call_sites():
    """
    Parse `routers/results.py` and return one `(endpoint_function_name,
    ast.Call)` pair per `_serve_ts(...)` call site. AST rather than regex so
    formatting/line-wrapping can't dodge the check, and walking function
    defs lets a failure name the offending endpoint instead of a line number.
    """
    path = Path(__file__).resolve().parents[1] / "routers" / "results.py"
    tree = ast.parse(path.read_text(), filename=str(path))

    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "_serve_ts"
            ):
                sites.append((node.name, inner))
    return sites


def test_every_serve_ts_call_site_forwards_from_and_to():
    """
    Every `_serve_ts(...)` call site in `routers/results.py` must literally
    forward `from_=from_` and `to_=to_`. A site that declares the Query
    params on its own signature but forgets to pass them through would be
    invisible to every behavioural test above whenever the endpoint has no
    data to slice (204 short-circuits before `from_`/`to_` matter) — this
    test doesn't need data at all.
    """
    sites = _serve_ts_call_sites()
    assert len(sites) == 11, f"expected 11 _serve_ts call sites, found {len(sites)}"

    missing = []
    for func_name, call in sites:
        forwarded = {
            kw.arg
            for kw in call.keywords
            if kw.arg in ("from_", "to_")
            and isinstance(kw.value, ast.Name)
            and kw.value.id == kw.arg
        }
        if forwarded != {"from_", "to_"}:
            missing.append(func_name)

    assert not missing, (
        f"these endpoint functions call _serve_ts without forwarding both "
        f"from_=from_ and to_=to_: {missing}"
    )
