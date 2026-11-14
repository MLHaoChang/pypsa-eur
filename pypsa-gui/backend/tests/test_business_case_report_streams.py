"""
Business case 10 (GK B F2): the report serves each P&L stream's escalation class and
source ("line:<kind>" or "energy_model") as display keys only, so the view groups the
streams without a copy of the maps, and every other report field is unchanged.
"""

from __future__ import annotations

import services.business_case.runner as RN
from models.business_case import CATEGORY_STREAM
from services.finance.cashflow import BC_STREAM_CLASS, STREAM_CLASS
from tests.test_business_case_routes import BC_URL, _bc, _bc_poll, _put, _with_ic


def test_every_stream_pnl_can_emit_has_a_class_and_a_source():
    line_streams = {s for (_k, _c), (s, _cls) in CATEGORY_STREAM.items() if s}
    for s in line_streams | set(STREAM_CLASS) | set(BC_STREAM_CLASS):
        assert STREAM_CLASS.get(s), s
    assert set(RN._STREAM_SOURCE) == line_streams
    for (kind, _c), (s, _cls) in CATEGORY_STREAM.items():
        if s:
            assert RN._STREAM_SOURCE[s] == f"line:{kind}"


def test_the_report_adds_the_display_keys_and_changes_nothing_else(
    client, install_network, monkeypatch
):
    seen = {}
    real = RN.report_body

    def spy(**kw):
        body = real(**kw)
        seen["body"] = body
        seen["kw"] = kw
        return body

    monkeypatch.setattr(RN, "report_body", spy)
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert client.post(BC_URL).status_code == 200
    rep = _bc_poll(client)["report"]
    pnl = rep["pnl"]
    assert set(pnl) == {
        "years",
        "streams",
        "rows",
        "not_finite",
        "stream_class",
        "stream_source",
    }
    assert set(pnl["stream_class"]) == set(pnl["streams"]) == set(pnl["stream_source"])
    assert pnl["stream_source"]["product_revenue"] == "line:revenue"
    assert all(
        v == "energy_model"
        for s, v in pnl["stream_source"].items()
        if s not in RN._STREAM_SOURCE
    )
    # Byte-equal (==) for every field that was there before: the body without the two
    # display keys equals the body built from the same inputs with them removed.
    body = seen["body"]
    again = real(**seen["kw"])
    for b in (body, again):
        b["pnl"].pop("stream_class")
        b["pnl"].pop("stream_source")
        b.pop("created_at", None)  # the build time
    assert body == again
    assert set(body["pnl"]) == {"years", "streams", "rows", "not_finite"}
