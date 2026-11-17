"""TCU 10: the writers that set the component frame directly run the owned-cost guard, or cannot reach a row
whose investment parts own its costs (`derive.owned_columns`).

- Excel import (N5): a priced part without a lifetime is refused and the open network is unchanged; a sheet
  cost the parts own is re-derived from the parts, as the create path does; an unpriced row keeps its cost.
- Pack pricing (`price_from_pack`): the plain branch refuses a row with priced parts and writes nothing.
- Library reference apply: writes through the single edit, and only prices classes without parts.
"""

from __future__ import annotations

import io

import openpyxl
import pandas as pd
import pytest

from tests.test_bulk_part_rederive import BATTERY, _network

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _legacy_workbook(rows: list[dict]) -> bytes:
    """A legacy-sheet workbook (no `network` sheet): one bus and a storage_units sheet."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    buses = wb.create_sheet("buses")
    buses.append(["name", "v_nom"])
    buses.append(["B1", 110.0])
    sheet = wb.create_sheet("storage_units")
    headers = ["name", "bus", "p_nom", "max_hours", *sorted({k for r in rows for k in r})]
    sheet.append(headers)
    for i, r in enumerate(rows):
        sheet.append([f"s{i}", "B1", 10.0, 2.0, *[r.get(h) for h in headers[4:]]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(client, data: bytes):
    return client.post("/api/io/import/excel", files={"file": ("x.xlsx", data, MIME)})


def _expected(parts: dict) -> dict:
    """What the create path derives for a StorageUnit with these parts, p_nom 10, max_hours 2."""
    import pypsa

    from services.asset_schema.derive import apply_parts
    from services.network_crud import _global_discount_rate

    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("StorageUnit", "s", bus="B1", p_nom=10.0, max_hours=2.0)
    apply_parts(n, "StorageUnit", "s", parts, discount_rate=_global_discount_rate())
    return {c: float(n.storage_units.at["s", c]) for c in ("capital_cost", "fom_cost", "lifetime")}


def test_an_excel_sheet_cost_the_parts_own_is_rederived_from_the_parts(client, install_network, session_ctx):
    install_network(_network())
    stale = {**BATTERY, "capital_cost": 1.0, "fom_cost": 2.0, "lifetime": 3.0}
    plain = {"capital_cost": 5000.0}
    r = _import(client, _legacy_workbook([stale, plain]))
    assert r.status_code == 200, r.text
    su = session_ctx(client).network.storage_units
    want = _expected(BATTERY)
    for col, v in want.items():
        assert su.at["s0", col] == pytest.approx(v, rel=1e-12), col
    assert want["capital_cost"] != 1.0
    assert su.at["s1", "capital_cost"] == 5000.0  # no priced part: the sheet's own cost stands


def test_an_excel_priced_part_without_a_lifetime_is_refused_and_the_network_is_unchanged(
        client, install_network, session_ctx):
    install_network(_network())
    before = session_ctx(client).network.storage_units.copy()
    bad = {k: v for k, v in BATTERY.items() if k != "inv_power_lifetime"}
    r = _import(client, _legacy_workbook([bad]))
    assert r.status_code == 422, r.text
    assert "s0" in r.text
    after = session_ctx(client).network.storage_units
    assert "s0" not in after.index
    pd.testing.assert_frame_equal(after, before)


def test_a_native_excel_round_trip_keeps_the_derived_costs(client, install_network, session_ctx):
    install_network(_network())
    data = client.get("/api/io/export/excel").content
    r = _import(client, data)
    assert r.status_code == 200, r.text
    su = session_ctx(client).network.storage_units
    want = _expected(BATTERY)
    for col, v in want.items():
        assert su.at["bess", col] == pytest.approx(v, rel=1e-12), col


def test_a_native_excel_stale_cost_is_rederived(client, install_network, session_ctx):
    n = _network()
    n.storage_units.at["bess", "capital_cost"] = 1.0  # a raw writer left it stale
    install_network(n)
    r = _import(client, client.get("/api/io/export/excel").content)
    assert r.status_code == 200, r.text
    su = session_ctx(client).network.storage_units
    assert su.at["bess", "capital_cost"] == pytest.approx(_expected(BATTERY)["capital_cost"], rel=1e-12)


def test_pack_pricing_refuses_a_row_whose_parts_own_its_costs_and_writes_nothing():
    from services.library.defaults_pack.pricing import PartsOwnCosts, price_from_pack

    n = _network()
    before = n.storage_units.copy()
    with pytest.raises(PartsOwnCosts, match="bess"):
        price_from_pack(n, "storage_units", "bess", "gas-genset", version="2026-10-22")
    pd.testing.assert_frame_equal(n.storage_units, before)


def test_pack_pricing_of_a_battery_onto_a_priced_row_still_rederives():
    """The battery branch prices the parts themselves (`apply_parts`), so it is not refused."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _network()
    out = price_from_pack(n, "storage_units", "bess", "battery", version="2026-10-22")
    assert n.storage_units.at["bess", "capital_cost"] == out["capital_cost"]


def test_the_library_reference_apply_prices_no_class_with_parts():
    """It writes through `chat_tools.update_component` (the single edit, both validators), and its target
    classes have no investment parts, so it cannot reach a row whose parts own its costs."""
    from services.asset_schema.schema import COMPOSITE
    from services.library.reference_apply import _TARGET_CLASS

    assert not set(_TARGET_CLASS.values()) & set(COMPOSITE)
