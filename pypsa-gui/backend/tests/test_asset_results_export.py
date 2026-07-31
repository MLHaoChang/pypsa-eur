"""The workbook: provenance, sheet layout and the two scopes."""
import io

import openpyxl

from tests.conftest import build_network

URL = "/api/results/asset/Generator/gas/export.xlsx"


def _book(resp):
    assert resp.status_code == 200
    return openpyxl.load_workbook(io.BytesIO(resp.content))


def test_configured_scope_writes_about_summary_and_the_category(
        client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={
        "scope": "view", "category": "dispatch", "metrics": "p,energy_mwh"}))
    assert wb.sheetnames == ["About", "Summary", "Dispatch"]


def test_about_sheet_carries_every_provenance_field(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "view", "category": "dispatch",
                                       "metrics": "p"}))
    keys = {row[0] for row in wb["About"].iter_rows(min_col=1, max_col=1,
                                                    values_only=True) if row[0]}
    for expected in ("Asset", "Component class", "Category", "View mode",
                     "Result source", "Horizon from", "Horizon to", "Period",
                     "Objective", "PyPSA version", "Generated at"):
        assert expected in keys, f"About sheet is missing '{expected}'"


def test_data_sheet_header_matches_the_columns_contract(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "view", "category": "dispatch",
                                       "metrics": "p"}))
    header = [c.value for c in wb["Dispatch"][1]]
    assert header[0] == "snapshot"
    assert "Active power (MW)" in header


def test_duration_mode_writes_rank_and_percentile_columns(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "view", "category": "dispatch",
                                       "metrics": "p", "mode": "duration"}))
    header = [c.value for c in wb["Dispatch"][1]]
    assert header[0] == "rank"
    assert header[1] == "pct_of_hours"


def test_full_scope_writes_every_applicable_category(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "full"}))
    assert "Dispatch" in wb.sheetnames
    assert "Capacity" in wb.sheetnames
    assert "Storage" not in wb.sheetnames, "n/a categories must be omitted"


def test_full_scope_lists_the_omitted_categories_in_about(client, install_network):
    install_network(build_network(solve=True))
    wb = _book(client.get(URL, params={"scope": "full"}))
    text = "\n".join(
        str(row[0]) + "|" + str(row[1])
        for row in wb["About"].iter_rows(min_col=1, max_col=2, values_only=True)
    )
    assert "Storage" in text and "store energy" in text


def test_unsolved_network_still_exports_the_summary(client, install_network):
    install_network(build_network(solve=False))
    wb = _book(client.get(URL, params={"scope": "full"}))
    assert "Summary" in wb.sheetnames
    assert "Dispatch" not in wb.sheetnames


def test_bad_scope_is_422(client, install_network):
    install_network(build_network(solve=True))
    assert client.get(URL, params={"scope": "nope"}).status_code == 422
