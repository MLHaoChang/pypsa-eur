# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""One name for the island-capability break-even (UX ruling): the constant
`services.finance.breakeven.LABEL` is what every display uses, and the label is
display only (no digest or fingerprint hashes it)."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib

import openpyxl
import pytest

from project_templates import eh_templates as T
from services.finance.breakeven import LABEL
from services.reports import chapters

TID = "eh_microgrid"
COMMITTED = pathlib.Path(T.__file__).parent / TID / T.META_FILE
OLD_ITEM = "island break-even"


def _ledger_items(meta: dict) -> list[str]:
    return [r["item"] for r in meta["ledger"]]


def test_the_label_is_the_owner_approved_name():
    assert LABEL == "Island-capability break-even"


def test_the_template_ledger_row_is_the_label_lowercased(tmp_path):
    T.write_sidecars(tmp_path, TID)
    for meta in (
        json.loads((tmp_path / T.META_FILE).read_text()),
        json.loads(COMMITTED.read_text()),
    ):
        items = _ledger_items(meta)
        assert LABEL.lower() in items
        assert OLD_ITEM not in items


def test_the_report_chapter_uses_the_label():
    payload = {
        "island_breakeven_text": "The island capability must be worth at least EUR 1 a year"
    }
    assert any(
        LABEL.lower() in line for line in chapters.investment_disclosures(payload)
    )
    assert LABEL in dict(
        chapters.investment_table(payload, "/sections/investment").rows
    )


def test_the_xlsx_summary_uses_the_label():
    from tests.test_finance_export_xlsx import _report, _values
    from services.finance.export_xlsx import build_workbook

    rep = _report()
    rep.sections["project"].payload["island_breakeven"] = {
        "eur_per_year": 1.0,
        "text": "x",
    }
    wb = openpyxl.load_workbook(io.BytesIO(build_workbook(rep)))
    assert LABEL in {r[0] for r in _values(wb["Summary"])}


def _hashes(client, session_ctx):
    from routers import results
    from services import workflow_tools

    ctx = session_ctx(client)
    cfg = ctx.solver_state["solver_config"]
    assumptions = results._ic_assumptions(
        cfg, ctx.network, lock=contextlib.nullcontext()
    )
    return assumptions, workflow_tools._network_input_fingerprint(ctx)


def test_no_digest_or_fingerprint_hashes_the_ledger_label(
    client, tmp_projects_dir, bound_ctx, session_ctx
):
    """The assumptions digest of the investment case and the network input
    fingerprint do not read the template's ledger: rewriting the row's item
    text (old wording against new) changes neither."""
    from services import network_lengths

    r = client.post(f"/api/projects/from_template/{TID}", params={"name": "mg_label"})
    assert r.status_code == 200, r.text
    with bound_ctx(client):
        marker = network_lengths.active_project_dir() / T.META_FILE
    meta = json.loads(marker.read_text())
    assert LABEL.lower() in _ledger_items(meta)

    with bound_ctx(client):
        new = _hashes(client, session_ctx)
    for row in meta["ledger"]:
        if row["item"] == LABEL.lower():
            row["item"] = OLD_ITEM
    marker.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    with bound_ctx(client):
        old = _hashes(client, session_ctx)
    assert new == old


@pytest.mark.parametrize("item", ["island break-even", "island-capability break-even"])
def test_a_project_stored_before_the_rename_shows_the_same_name(item):
    """UX 2 on #1692: a project created before the rename keeps its stored
    ledger text; the Assumptions card's flow message reads the current label
    for both."""
    from services.flow_service import _assumed_items

    template = {
        "ledger": [
            {
                "item": item,
                "value": "not applicable",
                "source": "assumed",
                "note": "the island rows are off",
            }
        ]
    }
    (row,) = _assumed_items(template)
    assert row["message"].startswith(f"{LABEL}: not applicable (assumed)")
