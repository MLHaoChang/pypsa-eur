"""
The `from`/`to` contract on the /results/* series endpoints.

Solves a small network ONCE per module so the dispatch-freshness gate passes;
conftest's autouse `reset_backend` means the network must be re-installed per
test, which `solved_client` does.
"""
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


def _ranged(client, url, **params):
    r = client.get(url, params=params)
    assert r.status_code in (200, 204), f"{url} -> {r.status_code}: {r.text[:400]}"
    return r


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_endpoint_accepts_from_and_to(solved_client, url):
    """A 422 here means the endpoint never declared the parameters."""
    r = _ranged(solved_client, url, **{"from": 0, "to": 0})

    assert r.status_code != 422


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_ranged_response_echoes_what_it_served(solved_client, url):
    r = _ranged(solved_client, url, **{"from": 0, "to": 0})
    if r.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")

    body = r.json()
    assert "range" in body, f"{url} returned no range block"
    assert set(body["range"]) == {"from", "to", "total", "complete", "capped"}
    assert body["range"]["from"] == 0
    assert body["range"]["to"] == 0
    assert len(body["data"]) == 1, "one row requested, one row expected"


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_unranged_response_carries_no_range_key(solved_client, url):
    """The no-parameter path must stay byte-identical for existing consumers."""
    r = _ranged(solved_client, url)
    if r.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")

    assert "range" not in r.json()


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_a_server_slice_equals_the_same_slice_taken_client_side(solved_client, url):
    """
    The invariant this whole feature rests on: moving the window from after
    the download to before it must not change a single value. This is also
    what catches an off-by-one in the inclusive bounds.
    """
    full = _ranged(solved_client, url)
    if full.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")
    full_body = full.json()
    if len(full_body["data"]) < 3:
        pytest.skip(f"{url} has too few rows to window")

    sliced = _ranged(solved_client, url, **{"from": 1, "to": 2}).json()

    assert sliced["columns"] == full_body["columns"]
    assert sliced["index"] == full_body["index"][1:3]
    assert sliced["data"] == full_body["data"][1:3]


@pytest.mark.parametrize("url", SERVE_TS_ENDPOINTS)
def test_complete_is_false_for_a_window_and_true_for_the_whole(solved_client, url):
    full = _ranged(solved_client, url)
    if full.status_code == 204:
        pytest.skip(f"{url} has no data on this fixture network")
    total = len(full.json()["data"])
    if total < 2:
        pytest.skip(f"{url} has too few rows to window")

    whole = _ranged(solved_client, url, **{"from": 0, "to": total - 1}).json()
    window = _ranged(solved_client, url, **{"from": 0, "to": 0}).json()

    assert whole["range"]["complete"] is True
    assert window["range"]["complete"] is False


def test_a_one_row_request_is_small(solved_client):
    """The canvas win, as a regression guard rather than a claim."""
    r = _ranged(solved_client, "/api/results/generators", **{"from": 0, "to": 0})
    if r.status_code == 204:
        pytest.skip("no generator dispatch on this fixture network")

    assert len(r.content) < 8_192, f"one row serialised to {len(r.content)} bytes"
