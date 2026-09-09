"""
`GET /api/results/economics_by_carrier` must not answer with a traceback.

When the per-carrier roll-up raises, `services/results/economics_by_carrier.py`
degrades gracefully rather than 500ing — deliberately, so one bad carrier does not blank the Results tab.
What it degraded INTO was the problem:

    return {"error": str(exc), "trace": traceback.format_exc().splitlines()[-5:]}

so any signed-in caller who can make the roll-up raise gets five frames of
traceback: absolute paths, the module layout, library versions and the
surrounding source lines. CodeQL flags it as `py/stack-trace-exposure`, and it
is right; the response shape is a lie besides, since a caller checking for the
documented `{"by_carrier": …}` payload sees a `200` and a dict.

The graceful degradation is worth keeping. Exporting the traceback is not, and
nothing consumes it: `frontend/src/api/simulation.ts` types the response with
`error?: string` and no `trace` at all, and both readers
(`pages/results/Dispatch.tsx`, `pages/results/CapacityExpansion.tsx`) take
`econByCarrier?.by_carrier ?? null`. So the trace goes to the LOG, where
whoever is debugging can actually find it, and the response says only that it
failed.

`str(exc)` goes too. It is the second flow CodeQL reported on the same line and
it leaks for the same reason — a `FileNotFoundError` renders as its path.
"""
from __future__ import annotations

import logging

import pytest

from tests.conftest import build_network


@pytest.fixture
def solved_network(install_network):
    """A solved network installed as the live singleton, so the gate lets us in."""
    return install_network(build_network(solve=True))


@pytest.fixture
def _exploding_rollup(monkeypatch):
    """
    Make the roll-up raise something whose message is unmistakably sensitive.

    Patches the ENGINE, not the handler, so the handler's own except-branch is
    what runs. The name is imported at module scope by
    `services/results/economics_by_carrier.py`, so the patch has to land THERE
    rather than on `services.compare.economics` — patching the definition site
    would leave the already-bound reference untouched.
    """
    import services.results.economics_by_carrier as rollup

    boom = FileNotFoundError(2, "No such file or directory", "/srv/tenants/acme/secret.nc")

    def _raise(*_a, **_kw):
        raise boom

    monkeypatch.setattr(rollup, "_compute_economics_summary", _raise)
    return boom


def test_the_error_payload_has_no_trace_key(client, solved_network, _exploding_rollup):
    resp = client.get("/api/results/economics_by_carrier")
    assert resp.status_code == 200
    body = resp.json()
    assert "trace" not in body, (
        f"the response still carries a traceback: {body.get('trace')!r}. "
        f"Nothing consumes it and it names absolute paths."
    )


def test_the_error_message_does_not_quote_the_exception(
    client, solved_network, _exploding_rollup
):
    """
    A generic message, not the exception's own text. The exception here renders
    as a path under `/srv/tenants/acme/`, which is exactly the shape of thing a
    tenant must not learn from another tenant's failure.
    """
    body = client.get("/api/results/economics_by_carrier").json()
    blob = repr(body)
    for leaked in ("/srv/tenants", "secret.nc", "No such file or directory"):
        assert leaked not in blob, (
            f"the response quotes the exception ({leaked!r} appears in {blob[:200]}). "
            f"Log the detail; return something the caller can act on."
        )
    assert body.get("error"), (
        "the response no longer says it failed at all — the frontend types this "
        "as `error?: string` and a silent empty payload is indistinguishable "
        "from 'this network has no carriers'"
    )


def test_the_traceback_is_logged_instead(
    client, solved_network, _exploding_rollup, caplog
):
    """
    The trace must not simply vanish. Removing it from the response is only safe
    if it lands somewhere a maintainer looks.
    """
    with caplog.at_level(logging.ERROR):
        client.get("/api/results/economics_by_carrier")
    logged = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "secret.nc" in logged or "Traceback" in logged, (
        "the roll-up failure was neither returned nor logged, so it is now "
        "invisible; log the traceback when you stop returning it"
    )
