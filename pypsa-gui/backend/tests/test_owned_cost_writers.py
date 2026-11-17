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
    headers = [
        "name",
        "bus",
        "p_nom",
        "max_hours",
        *sorted({k for r in rows for k in r}),
    ]
    sheet.append(headers)
    for i, r in enumerate(rows):
        sheet.append([f"s{i}", "B1", 10.0, 2.0, *[r.get(h) for h in headers[4:]]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(client, data: bytes):
    return client.post("/api/io/import/excel", files={"file": ("x.xlsx", data, MIME)})


def _expected(parts: dict, rate: float | None = None) -> dict:
    """What the create path derives for a StorageUnit with these parts, p_nom 10, max_hours 2."""
    import pypsa

    from services.asset_schema.derive import apply_parts
    from services.network_crud import _global_discount_rate

    n = pypsa.Network()
    n.add("Bus", "B1")
    n.add("StorageUnit", "s", bus="B1", p_nom=10.0, max_hours=2.0)
    apply_parts(
        n,
        "StorageUnit",
        "s",
        parts,
        discount_rate=_global_discount_rate() if rate is None else rate,
    )
    return {
        c: float(n.storage_units.at["s", c])
        for c in ("capital_cost", "fom_cost", "lifetime")
    }


def _assert_derived(su, name, rate=None):
    for col, v in _expected(BATTERY, rate).items():
        assert su.at[name, col] == pytest.approx(v, rel=1e-12), col


# ── Excel, legacy sheets ─────────────────────────────────────────────────────


def test_an_excel_sheet_cost_the_parts_own_is_rederived_from_the_parts(
    client, install_network, session_ctx
):
    install_network(_network())
    stale = {**BATTERY, "capital_cost": 1.0, "fom_cost": 2.0, "lifetime": 3.0}
    plain = {"capital_cost": 5000.0}
    r = _import(client, _legacy_workbook([stale, plain]))
    assert r.status_code == 200, r.text
    su = session_ctx(client).network.storage_units
    _assert_derived(su, "s0")
    assert su.at["s0", "capital_cost"] != 1.0
    assert (
        su.at["s1", "capital_cost"] == 5000.0
    )  # no priced part: the sheet's own cost stands


@pytest.mark.parametrize(
    "bad",
    [
        {
            k: v for k, v in BATTERY.items() if k != "inv_power_lifetime"
        },  # priced, no lifetime
        # Not finite: a sheet cell cannot hold a float inf (openpyxl writes a blank), so the typed text.
        {**BATTERY, "inv_energy_overnight": "inf"},
        {**BATTERY, "inv_power_lifetime": "inf"},
    ],
    ids=["no-lifetime", "inf-overnight", "inf-lifetime"],
)
def test_an_excel_bad_priced_row_after_a_good_one_refuses_and_leaves_the_network(
    client, install_network, session_ctx, bad
):
    n = install_network(_network())
    before = n.copy()
    r = _import(client, _legacy_workbook([BATTERY, bad]))
    assert r.status_code == 422, r.text
    assert "s1" in r.text
    after = session_ctx(client).network
    assert after is n
    pd.testing.assert_frame_equal(after.storage_units, before.storage_units)
    pd.testing.assert_frame_equal(after.lines, before.lines)


def test_an_import_derives_at_the_rate_in_force(client, install_network, session_ctx):
    """A workbook carries no solver settings: the open project's rate (or the asset's own) is the one."""
    from tests.test_bulk_part_rederive import _set_rate

    install_network(_network())
    _set_rate(0.10)
    r = _import(client, _legacy_workbook([BATTERY]))
    assert r.status_code == 200, r.text
    _assert_derived(session_ctx(client).network.storage_units, "s0", rate=0.10)
    assert (
        _expected(BATTERY, 0.10)["capital_cost"]
        != _expected(BATTERY, 0.07)["capital_cost"]
    )


# ── the native exports: Excel, netCDF, CSV bundle ────────────────────────────

FORMATS = {
    "excel": ("excel", "x.xlsx"),
    "netcdf": ("netcdf", "x.nc"),
    "csv": ("csv", "x.zip"),
}


def _round_trip(client, fmt):
    route, filename = FORMATS[fmt]
    data = client.get(f"/api/io/export/{route}").content
    return client.post(
        f"/api/io/import/{route}",
        files={"file": (filename, data, "application/octet-stream")},
    )


@pytest.mark.parametrize("fmt", FORMATS)
def test_a_native_import_rederives_a_stale_cost_and_moves_the_fingerprint(
    client, install_network, session_ctx, fmt
):
    from tests.test_bulk_part_rederive import _fingerprint

    n = _network()
    n.storage_units.at["bess", "capital_cost"] = 1.0  # a raw writer left it stale
    n.storage_units.at["bess", "inv_power_overnight"] = (
        400_000.0  # and moved a part raw
    )
    install_network(n)
    stale_print = _fingerprint(n)
    r = _round_trip(client, fmt)
    assert r.status_code == 200, r.text
    got = session_ctx(client).network
    want = _expected({**BATTERY, "inv_power_overnight": 400_000.0})
    assert got.storage_units.at["bess", "capital_cost"] == pytest.approx(
        want["capital_cost"], rel=1e-12
    )
    _assert_derived(got.storage_units, "bess2")
    assert _fingerprint(got) != stale_print


@pytest.mark.parametrize(
    "fmt,col,value",
    [
        *[(f, "inv_power_lifetime", float("nan")) for f in FORMATS],
        # Excel cannot carry a float inf (it is written blank); netCDF and CSV can.
        ("netcdf", "inv_energy_overnight", float("inf")),
        ("csv", "inv_energy_overnight", float("inf")),
        ("netcdf", "inv_power_lifetime", float("inf")),
    ],
)
def test_a_native_import_of_a_bad_priced_part_is_refused(
    client, install_network, session_ctx, fmt, col, value
):
    n = _network()
    n.storage_units.at["bess2", col] = value  # written raw, never validated
    install_network(n)
    data = client.get(f"/api/io/export/{FORMATS[fmt][0]}").content
    good = install_network(_network())
    before = good.copy()
    r = client.post(
        f"/api/io/import/{FORMATS[fmt][0]}",
        files={"file": (FORMATS[fmt][1], data, "application/octet-stream")},
    )
    assert r.status_code == 422, r.text
    assert "bess2" in r.text
    assert session_ctx(client).network is good
    pd.testing.assert_frame_equal(good.storage_units, before.storage_units)


# ── pack pricing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("attr,name", [("storage_units", "bess"), ("lines", "ab")])
def test_pack_pricing_refuses_a_row_whose_parts_own_its_costs_and_writes_nothing(
    attr, name
):
    from services.library.defaults_pack.pricing import PartsOwnCosts, price_from_pack

    n = _network()
    before = getattr(n, attr).copy()
    with pytest.raises(PartsOwnCosts, match=name):
        price_from_pack(n, attr, name, "gas-genset", version="2026-10-22")
    pd.testing.assert_frame_equal(getattr(n, attr), before)


def test_pack_pricing_does_not_refuse_an_unpriced_storage_unit():
    """Only priced parts refuse: an unpriced row of the same class goes on to be priced (or refused on its
    capacity basis, as before), never `PartsOwnCosts`."""
    from services.library.defaults_pack.pricing import PartsOwnCosts, price_from_pack

    n = _network()
    n.add("StorageUnit", "plain", bus="B1", p_nom=10.0, max_hours=2.0)
    try:
        price_from_pack(n, "storage_units", "plain", "gas-genset", version="2026-10-22")
    except PartsOwnCosts:
        pytest.fail("an unpriced row was refused as priced")
    except Exception:  # noqa: BLE001 - any other refusal is the pricing's own, unchanged
        pass


def test_pack_pricing_of_a_battery_onto_a_priced_row_still_rederives():
    """The battery branch prices the parts themselves (`apply_parts`), so it is not refused."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _network()
    out = price_from_pack(n, "storage_units", "bess", "battery", version="2026-10-22")
    assert n.storage_units.at["bess", "capital_cost"] == out["capital_cost"]


def test_every_caller_prices_a_class_with_parts_only_as_a_battery():
    """`PartsOwnCosts` is a guard no caller reaches: each prices a row it has just added, and a storage unit
    or line only as a battery. A new caller that breaks this must map the refusal to a typed kind."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    call = re.compile(
        r'_?price(?:_from_pack)?\(\s*\w+,\s*"(\w+)",\s*[^,]+,\s*"([\w-]+)"'
    )
    seen = []
    for path in [
        *root.glob("project_templates/*.py"),
        root / "services/adequacy/redundancy.py",
    ]:
        for attr, tech in call.findall(path.read_text()):
            seen.append((path.name, attr, tech))
            if attr in ("storage_units", "lines"):
                assert tech == "battery", (path.name, attr, tech)
    assert any(a == "storage_units" for _, a, _ in seen) and len(seen) > 10


# ── library reference apply ──────────────────────────────────────────────────


@pytest.mark.parametrize("cls,name", [("StorageUnit", "bess"), ("Line", "ab")])
def test_the_library_reference_routes_refuse_a_class_with_parts(
    client, install_network, cls, name
):
    """It writes through the single edit (both validators); its targets are Generator or Link, so a priced
    storage unit or line is refused with its own kind and left as it was."""
    n = install_network(_network())
    before = (n.storage_units.copy(), n.lines.copy())
    body = {
        "component_class": cls,
        "name": name,
        "technology": "onwind",
        "projection_year": 2030,
    }
    r = client.post("/api/library_reference/preview", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "reference_target_unsupported"
    r = client.post(
        "/api/network/library_reference/apply", json={**body, "digest": "x"}
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "reference_target_unsupported"
    pd.testing.assert_frame_equal(n.storage_units, before[0])
    pd.testing.assert_frame_equal(n.lines, before[1])


# ── the import helper and its log note ──────────────────────────────────────


def test_the_import_lists_only_the_priced_rows_it_rederived():
    from services.network_crud import rederive_imported_parts

    n = _network()
    n.add("StorageUnit", "plain", bus="B1", p_nom=1.0, capital_cost=5.0)
    assert rederive_imported_parts(n) == [
        "StorageUnit 'bess'",
        "StorageUnit 'bess2'",
        "Line 'ab'",
    ]
    assert n.storage_units.at["plain", "capital_cost"] == 5.0


def test_a_blank_part_cell_on_a_priced_row_is_not_a_refusal():
    """A sheet's empty cell reads as None in an object column; only the priced parts are checked."""
    from services.network_crud import rederive_imported_parts

    n = _network()
    su = n.storage_units
    for col in ("inv_energy_overnight", "inv_energy_lifetime", "inv_energy_fom_share"):
        su[col] = su[col].astype(object)
        su.at["bess", col] = None
    assert "StorageUnit 'bess'" in rederive_imported_parts(n)


def test_the_import_log_note_names_the_rederived_rows():
    from routers.io import _rederived_note

    assert _rederived_note([]) == ""
    ten = [f"StorageUnit 's{i}'" for i in range(10)]
    assert _rederived_note(ten).endswith("10 row(s): " + ", ".join(ten))
    assert _rederived_note([*ten, "Line 'x'"]).endswith(", ".join(ten) + " and 1 more")
