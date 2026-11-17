"""
Business case 10 (GK B F2): the report serves each P&L stream's escalation class and
source ("line:<kind>" or "energy_model") as display keys only, so the view groups the
streams without a copy of the maps, and every other report field is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

import services.business_case.runner as RN
import services.finance.business_case as BCM
from models.business_case import CATEGORY_STREAM
from services.finance.cashflow import BC_STREAM_CLASS, STREAM_CLASS
from tests.test_business_case_routes import (
    BC_URL,
    INPUTS_URL,
    _bc,
    _bc_poll,
    _put,
    _with_ic,
)


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
        "row_reason",
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
        b["pnl"].pop("row_reason")
        b.pop("created_at", None)  # the build time
    assert body == again
    assert set(body["pnl"]) == {"years", "streams", "rows", "not_finite"}


#: The P&L row keys the view knows (its `ROW_NEEDS` and labels); a vitest pins the view's
#: table to this list, and this test pins the list to what the report serves (GK B W1).
PNL_ROWS = (
    Path(__file__).resolve().parents[2]
    / "frontend/src/pages/results/investment/businessCasePnlRows.json"
)


def test_the_view_s_row_list_is_the_report_s(client, install_network, monkeypatch):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert client.post(BC_URL).status_code == 200
    pnl = _bc_poll(client)["report"]["pnl"]
    rows = pnl["rows"]
    assert sorted(rows) == json.loads(PNL_ROWS.read_text())
    # BC 17: a reason for exactly the rows that read None, each naming its input.
    assert set(pnl["row_reason"]) == {k for k, v in rows.items() if v is None}
    assert sorted(BCM._ROW_INPUTS) == json.loads(PNL_ROWS.read_text())


def test_line_and_ledger_stream_names_are_disjoint():
    """W2: `stream_source` labels a stream by its name, so a name both a line and the IC
    ledger can emit would be labelled wrong for part of its sum. `cashflow` refuses that
    at import; this pins the names, and an unknown stream reads energy_model only when it
    is not a line stream."""
    line_streams = set(RN._STREAM_SOURCE)
    ledger = set(STREAM_CLASS) - set(BC_STREAM_CLASS)
    assert line_streams == set(BC_STREAM_CLASS)
    assert not line_streams & ledger
    for s in ledger | {"some_future_ledger_row"}:
        assert RN._STREAM_SOURCE.get(s, "energy_model") == "energy_model"
    for s in line_streams:
        assert RN._STREAM_SOURCE[s].startswith("line:")


def test_bc17_row_reason_is_a_display_key_that_moves_no_digest_or_staleness(
    client, install_network, monkeypatch
):
    """GK B BC 17 C4: two reports that differ only in `row_reason` agree on every other
    field, the digests among them, and read the same staleness."""
    seen = {}
    real = RN.report_body

    def spy(**kw):
        seen["kw"] = kw
        return real(**kw)

    monkeypatch.setattr(RN, "report_body", spy)
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert client.post(BC_URL).status_code == 200
    _bc_poll(client)
    kw = seen["kw"]
    a = real(**kw)
    monkeypatch.setattr(
        BCM,
        "row_reasons",
        lambda *_a, **_k: {"revenue": [{"section": "operating", "code": "x"}]},
    )
    b = real(**kw)
    assert a["pnl"]["row_reason"] != b["pnl"]["row_reason"]
    # Staleness reads the stored bodies with the key in them.
    current = (a["ic_assumptions_hash"], None)
    for cur in (current, (None, "gone")):
        assert RN.staleness(a, inputs_raw=kw["raw"], ic_current=cur) == RN.staleness(
            b, inputs_raw=kw["raw"], ic_current=cur
        )
    assert RN.staleness(b, inputs_raw=kw["raw"], ic_current=current)["stale"] is False
    for k in (
        "business_case_hash",
        "inputs_digest",
        "ic_assumptions_hash",
        "ic_case_hash",
    ):
        assert a[k] == b[k], k
    for body in (a, b):
        body["pnl"].pop("row_reason")
        body.pop("created_at")
    assert a == b


def test_bc15_a_lease_up_reaches_the_report_and_the_workbook(
    client, install_network, monkeypatch
):
    """The run applies the stored volume factor; the report serves it per P&L year as a
    display key, and the workbook writes it as a percent row of "P&L by year"."""
    from openpyxl import Workbook

    from services.results_workbook.business_case import (
        LEASE_UP_ROW,
        write_business_case_sheet,
    )

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    assert client.post(BC_URL).status_code == 200
    full = _bc_poll(client)
    years = full["report"]["pnl"]["years"]
    first = years[-2]
    bc = _bc()
    bc["inputs"]["volume_factor_by_year"] = {str(first): 0.25, "1990": 0.5}
    assert _put(client, bc).status_code == 200
    assert client.get(INPUTS_URL).json()["business_case"]["inputs"][
        "volume_factor_by_year"
    ] == {str(first): 0.25, "1990": 0.5}
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    # This IC sells under a PPA: a generation-side row refuses, naming it (ruling 6).
    assert body["status"] == "refused"
    assert body["refusal"]["refusal"] == "volume_factor_unclassified"
    assert body["refusal"]["field"] == "ppa_settlement"
    # Read as fixed, the run completes: the rest of the path is what is pinned here.
    for s, cls in BCM.STREAM_VOLUME_CLASS.items():
        if cls == "undetermined":
            monkeypatch.setitem(BCM.STREAM_VOLUME_CLASS, s, "fixed")
    assert client.post(BC_URL).status_code == 200
    body = _bc_poll(client)
    assert body.get("status") == "done", json.dumps(body)[:600]
    rep = body["report"]
    assert rep["pnl"]["volume_factor"] == {str(first): 0.25}
    assert rep["business_case_hash"] != full["report"]["business_case_hash"]
    wb = Workbook()
    ws = wb[
        write_business_case_sheet(
            wb, {**rep, "basis": {"label": "x"}}, {"stale": False}
        )
    ]
    cells = [[c.value for c in row] for row in ws.iter_rows()]
    at = next(i for i, r in enumerate(cells) if r[0] == "P&L by year")
    assert cells[at + 1][0] == LEASE_UP_ROW
    want = [25.0 if y == first else 100.0 for y in years]
    assert cells[at + 1][1 : 1 + len(years)] == want
    # A full-volume report writes no such row.
    ws = wb[
        write_business_case_sheet(
            wb, {**full["report"], "basis": {"label": "x"}}, {"stale": False}
        )
    ]
    assert LEASE_UP_ROW not in {c.value for row in ws.iter_rows() for c in row}


def test_bc15_a_lease_up_outside_zero_to_one_is_a_422(
    client, install_network, monkeypatch
):
    _with_ic(client, install_network, monkeypatch)
    for bad in (1.5, -0.1):
        bc = _bc()
        bc["inputs"]["volume_factor_by_year"] = {"2031": bad}
        assert _put(client, bc).status_code == 422
