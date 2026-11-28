"""TE2E-16: the reference-design export carries the review's staleness."""

from __future__ import annotations

from tests.test_eh_study_edited_since import (
    _both,
    _done_study,
    _edit_bus,
)


def test_the_export_says_stale_and_edited_like_the_review(
    client, install_network, monkeypatch, session_state, session_ctx
):
    """TE2E-16: the export route carries the review's stale and
    edited_since_study, so a saved file says it is from an earlier run."""
    from services.adequacy.eh_report import EH_REPORT_STORE_KEY

    export_url = "/api/results/eh_reference_design"
    _done_study(client, install_network, monkeypatch, session_state, "export-flags")
    first = client.get(export_url).json()
    assert first["stale"] is False  # the stored report
    session_ctx(client).solver_state.pop(EH_REPORT_STORE_KEY, None)
    r = client.get(export_url)
    assert r.status_code == 200, r.text
    assert r.json()["stale"] is True
    assert r.json()["edited_since_study"] is False
    _edit_bus(client)
    body = client.get(export_url).json()
    assert body["stale"] is True
    assert body["edited_since_study"] is True
    _, review = _both(client)
    assert (body["stale"], body["edited_since_study"]) == (
        review["stale"],
        review["edited_since_study"],
    )
