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
    assert r.json()["detail"]["error_kind"] == "invalid_edit_value"
    assert "Storage 's1'" in r.json()["detail"]["message"]
    if (
        bad.get("inv_energy_overnight") == "inf"
        or bad.get("inv_power_lifetime") == "inf"
    ):
        # The finite check's own reason, not a downstream one ("no lifetime", a non-finite derived cost).
        assert "must be a finite number" in r.json()["detail"]["message"]
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


def test_an_import_derives_at_the_assets_own_rate(client, install_network, session_ctx):
    install_network(_network())
    r = _import(client, _legacy_workbook([{**BATTERY, "discount_rate": 0.05}]))
    assert r.status_code == 200, r.text
    _assert_derived(session_ctx(client).network.storage_units, "s0", rate=0.05)
    assert (
        _expected(BATTERY, 0.05)["capital_cost"] != _expected(BATTERY)["capital_cost"]
    )


def test_a_priced_row_is_checked_at_its_own_rate():
    """The check derives at the rate the re-derive uses: an asset's own rate that makes the annuity fail
    refuses, though the project's would not."""
    from fastapi import HTTPException

    from services.network_crud import rederive_imported_parts

    n = _network()
    n.storage_units["discount_rate"] = n.storage_units["discount_rate"].astype(float)
    n.storage_units.at["bess", "discount_rate"] = float("nan")
    rederive_imported_parts(n)  # blank own rate: the project's
    n.storage_units.at["bess", "discount_rate"] = float("inf")
    with pytest.raises(HTTPException) as exc:
        rederive_imported_parts(n)
    assert "Storage 'bess'" in exc.value.detail["message"]


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


@pytest.mark.parametrize("fmt", ["netcdf", "csv"])
def test_a_native_import_of_a_priced_line_with_an_infinite_length_is_refused(
    client, install_network, session_ctx, fmt
):
    """A scale is checked too: an infinite length makes the derived cost infinite."""
    n = _network()
    n.lines.at["ab", "length"] = float("inf")
    install_network(n)
    data = client.get(f"/api/io/export/{fmt}").content
    good = install_network(_network())
    r = client.post(
        f"/api/io/import/{fmt}",
        files={"file": (FORMATS[fmt][1], data, "application/octet-stream")},
    )
    assert r.status_code == 422, r.text
    assert "Line 'ab'" in r.json()["detail"]["message"]
    assert session_ctx(client).network is good


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
    if value == float("inf"):
        assert "must be a finite number" in r.json()["detail"]["message"]
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
    """Only priced parts refuse: an unpriced row of the same class is priced as before."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _network()
    n.add("StorageUnit", "plain", bus="B1", p_nom=10.0, max_hours=2.0)
    out = price_from_pack(
        n, "storage_units", "plain", "gas-genset", version="2026-10-22"
    )
    assert n.storage_units.at["plain", "overnight_cost"] == out["overnight_cost"] > 0


def test_pack_pricing_of_a_battery_onto_a_priced_row_still_rederives():
    """The battery branch prices the parts themselves (`apply_parts`), so it is not refused."""
    from services.library.defaults_pack.pricing import price_from_pack

    n = _network()
    out = price_from_pack(n, "storage_units", "bess", "battery", version="2026-10-22")
    assert n.storage_units.at["bess", "capital_cost"] == out["capital_cost"]


def test_every_caller_prices_a_class_with_parts_only_as_a_battery():
    """`PartsOwnCosts` is a guard no caller reaches: each prices a row it has just added, and a storage unit
    or line only as a battery. Every call of `price_from_pack` or a `_price` wrapper in the app (the wrappers'
    own bodies forward their arguments) must name its class, and a class with parts must name "battery". A new
    caller that breaks this must map the refusal to a typed kind."""
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    seen = []
    for folder in ("services", "routers", "harness", "project_templates"):
        for path in (root / folder).rglob("*.py"):
            tree = ast.parse(path.read_text())
            wrappers = {
                id(node)
                for fn in ast.walk(tree)
                if isinstance(fn, ast.FunctionDef) and fn.name == "_price"
                for node in ast.walk(fn)
            }
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or id(node) in wrappers:
                    continue
                f = node.func
                fname = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                if fname not in ("price_from_pack", "_price"):
                    continue
                args = node.args
                # (component, name, technology) on a bound wrapper, else (n, component, name, technology).
                at = 0 if args and isinstance(args[0], ast.Constant) else 1
                comp, tech = args[at], args[at + 2]
                where = f"{path.name}:{node.lineno}"
                assert isinstance(comp, ast.Constant), where
                seen.append((where, comp.value))
                if comp.value in ("storage_units", "lines"):
                    assert isinstance(tech, ast.Constant) and tech.value == "battery", (
                        where
                    )
    assert sum(c == "storage_units" for _, c in seen) >= 5 and len(seen) > 20, seen
    assert any(w.startswith("redundancy.py") and c == "storage_units" for w, c in seen)


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
        "Storage 'bess'",
        "Storage 'bess2'",
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
    assert "Storage 'bess'" in rederive_imported_parts(n)


def test_a_nan_part_cell_on_a_priced_row_is_blank():
    from services.network_crud import rederive_imported_parts

    n = _network()
    for col in ("inv_energy_overnight", "inv_energy_lifetime", "inv_energy_fom_share"):
        n.storage_units.at["bess", col] = float("nan")
    assert "Storage 'bess'" in rederive_imported_parts(n)
    power = {k: v for k, v in BATTERY.items() if k.startswith("inv_power")}
    want = _expected(power)
    assert n.storage_units.at["bess", "capital_cost"] == pytest.approx(
        want["capital_cost"], rel=1e-12
    )


def test_the_import_log_note_names_the_rederived_rows():
    from routers.io import _rederived_note

    assert _rederived_note([]) == ""
    ten = [f"StorageUnit 's{i}'" for i in range(10)]
    assert _rederived_note(ten).endswith("10 row(s): " + ", ".join(ten))
    assert _rederived_note([*ten, "Line 'x'"]).endswith(", ".join(ten) + " and 1 more")


# ── project bundle import (TCU 11) ───────────────────────────────────────────


def _bundle_of(client, install_network, n, name="tcu11_src", rate=None):
    install_network(n, name=name)
    if rate is not None:
        r = client.put("/api/simulation/solver_config", json={"discount_rate": rate})
        assert r.status_code == 200, r.text
    r = client.post(f"/api/projects/{name}", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text
    r = client.get(f"/api/projects/{name}/bundle")
    assert r.status_code == 200, r.text
    return r.content


def _import_bundle(client, data, name):
    return client.post(
        f"/api/projects/import_bundle?name={name}",
        files={"file": ("p.pypsaproj.zip", data, "application/zip")},
    )


def test_a_bundle_import_rederives_a_stale_cost_from_the_parts(
    client, install_network, session_ctx
):
    n = _network()
    n.storage_units.at["bess", "capital_cost"] = 1.0  # a raw writer left it stale
    data = _bundle_of(client, install_network, n)
    r = _import_bundle(client, data, "tcu11_stale")
    assert r.status_code in (200, 201), r.text
    got = session_ctx(client).network
    _assert_derived(got.storage_units, "bess", rate=0.07)
    _assert_derived(got.storage_units, "bess2", rate=0.07)
    assert "Storage 'bess'" in r.json()["rederived"]
    from services import dirty_state

    assert dirty_state.is_dirty(
        session_ctx(client)
    )  # memory now differs from the bundle's file


def test_a_bundle_import_of_a_bad_priced_part_is_refused_before_anything_is_written(
    client, install_network, session_ctx, project_row
):
    n = _network()
    n.storage_units.at["bess2", "inv_power_lifetime"] = float("inf")
    data = _bundle_of(client, install_network, n)
    good = install_network(_network())
    r = _import_bundle(client, data, "tcu11_bad")
    assert r.status_code == 422, r.text
    assert "bess2" in r.json()["detail"]["message"]
    assert "must be a finite number" in r.json()["detail"]["message"]
    assert project_row("tcu11_bad") is None  # refused before the project row
    assert session_ctx(client).network is good


def test_a_bundle_import_derives_at_the_bundles_own_rate(
    client, install_network, session_ctx
):
    """The bundle carries its project's solver settings: its rate, not the one open before the import."""
    n = _network()
    n.storage_units.at["bess", "capital_cost"] = 1.0
    data = _bundle_of(client, install_network, n, name="tcu11_rate", rate=0.05)
    install_network(_network())
    r = client.put("/api/simulation/solver_config", json={"discount_rate": 0.09})
    assert r.status_code == 200, r.text
    r = _import_bundle(client, data, "tcu11_rate_in")
    assert r.status_code in (200, 201), r.text
    _assert_derived(session_ctx(client).network.storage_units, "bess", rate=0.05)
    assert "Storage 'bess'" in r.json()["rederived"]
    assert (
        "rederived_at_default_rate" not in r.json()
    )  # the bundle brought its own rate


def test_a_clean_bundle_without_solver_settings_is_not_marked_as_rederived(
    client, install_network
):
    """Nothing re-derived: neither key, even though the default rate was the one in force."""
    data = _with_member(
        _bundle_of(client, install_network, _network()), "solver_config.json", None
    )
    r = _import_bundle(client, data, "tcu11_clean_nocfg")
    assert r.status_code in (200, 201), r.text
    assert "rederived" not in r.json() and "rederived_at_default_rate" not in r.json()


# ── experiment preflight copy (TCU 11) ───────────────────────────────────────


def _bounded():
    """`_network()` with finite extendable bounds, so the copy passes network validation."""
    n = _network()
    n.storage_units["p_nom_max"] = 100.0
    n.lines["s_nom_max"] = 100.0
    return n


def _experiment_candidate(n, attribute, value, name="bess", cls="StorageUnit"):
    from services import assistant_tools
    from services.pypsa_service import PyPSAService

    del n  # installed by the caller; the active context holds it
    ctx = PyPSAService.get_active_context()
    change = {
        "component_class": cls,
        "names": [name],
        "attribute": attribute,
        "value": value,
    }
    return assistant_tools._candidate(ctx, assistant_tools._changes([change]))


@pytest.mark.parametrize(
    "cls,attr,name", [("StorageUnit", "storage_units", "bess"), ("Line", "lines", "ab")]
)
def test_an_experiment_case_setting_a_cost_the_parts_own_is_refused(
    install_network, cls, attr, name
):
    """The staging re-derives that cost away, so the case would solve as the baseline while reported changed."""
    from fastapi import HTTPException

    n = install_network(_bounded())
    frame = getattr(n, attr).copy()
    with pytest.raises(HTTPException) as exc:
        _experiment_candidate(n, "capital_cost", 5.0, name=name, cls=cls)
    assert exc.value.detail["error_kind"] == "derived_field", exc.value.detail
    assert "set by the investment parts" in exc.value.detail["message"]
    assert name in exc.value.detail["message"]
    pd.testing.assert_frame_equal(getattr(n, attr), frame)


def test_an_experiment_case_setting_an_unpriced_rows_cost_is_written(install_network):
    n = _bounded()
    n.add("StorageUnit", "plain", bus="B1", p_nom=5.0, max_hours=2.0, capital_cost=10.0)
    install_network(n)
    candidate, _, _ = _experiment_candidate(n, "capital_cost", 12.0, name="plain")
    assert candidate.network.storage_units.at["plain", "capital_cost"] == 12.0


def test_an_experiment_case_changing_a_priced_scale_rederives_the_copy(install_network):
    from services.asset_schema.derive import apply_parts

    n = install_network(_bounded())
    before = n.storage_units.at["bess", "capital_cost"]
    candidate, _, _ = _experiment_candidate(n, "max_hours", 4.0)
    want = _bounded()
    want.storage_units.at["bess", "max_hours"] = 4.0
    apply_parts(want, "StorageUnit", "bess", {}, discount_rate=0.07)
    got = candidate.network.storage_units
    assert got.at["bess", "capital_cost"] == pytest.approx(
        want.storage_units.at["bess", "capital_cost"], rel=1e-12
    )
    assert got.at["bess", "capital_cost"] != pytest.approx(before)
    assert (
        n.storage_units.at["bess", "capital_cost"] == before
    )  # the open network is untouched


def test_a_sweep_case_setting_a_cost_the_parts_own_is_refused_and_an_unpriced_one_is_not():
    """TCU 09's sweep guard, on the problem it shares with the experiment copy."""
    from fastapi import HTTPException

    from services import assistant_tools, workflow_tools

    n = _bounded()
    n.add("StorageUnit", "plain", bus="B1", p_nom=5.0, max_hours=2.0, capital_cost=10.0)

    def change(name):
        return assistant_tools._changes(
            [
                {
                    "component_class": "StorageUnit",
                    "names": [name],
                    "attribute": "capital_cost",
                    "value": 5.0,
                }
            ]
        )[0]

    with pytest.raises(HTTPException) as exc:
        workflow_tools._refuse_cost_the_parts_own(n, change("bess"))
    assert exc.value.detail["error_kind"] == "invalid_sensitivity_cases"
    assert "StorageUnit bess" in exc.value.detail["message"]
    assert workflow_tools._refuse_cost_the_parts_own(n, change("plain")) is None


# ── GK B (62) conditions on TCU 11 ───────────────────────────────────────────


def _with_member(data: bytes, member: str, body: bytes | None) -> bytes:
    """The bundle with `member` replaced (None: removed)."""
    import zipfile

    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for item in src.namelist():
            if item != member:
                out.writestr(item, src.read(item))
        if body is not None:
            out.writestr(member, body)
    return buf.getvalue()


def test_a_bundle_without_solver_settings_derives_at_the_default_rate_and_says_so(
    client, install_network, session_ctx
):
    """A1: only an absent settings file uses the default rate."""
    n = _network()
    n.storage_units.at["bess", "capital_cost"] = 1.0
    data = _with_member(
        _bundle_of(client, install_network, n, rate=0.05), "solver_config.json", None
    )
    install_network(_network())
    r = client.put("/api/simulation/solver_config", json={"discount_rate": 0.09})
    assert r.status_code == 200, r.text
    r = _import_bundle(client, data, "tcu11_nocfg")
    assert r.status_code in (200, 201), r.text
    assert r.json()["rederived_at_default_rate"] is True
    _assert_derived(session_ctx(client).network.storage_units, "bess", rate=0.07)


@pytest.mark.parametrize(
    "body",
    [
        b"{not json",
        b"[1, 2]",
        b'{"discount_rate": "abc"}',
        # Not an object, though an empty value would otherwise read as "all defaults".
        b"null",
        b"[]",
        b"0",
        b'""',
        # A rate that is not a finite number.
        b'{"discount_rate": NaN}',
        b'{"discount_rate": Infinity}',
        b'{"discount_rate": -Infinity}',
    ],
)
def test_a_bundle_with_unreadable_solver_settings_is_refused_before_anything_is_written(
    client, install_network, session_ctx, project_row, body
):
    """A1: present but unreadable is a 422, never the default rate."""
    n = _network()
    data = _with_member(
        _bundle_of(client, install_network, n), "solver_config.json", body
    )
    good = install_network(_network())
    r = _import_bundle(client, data, "tcu11_badcfg")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "import_unreadable"
    assert project_row("tcu11_badcfg") is None
    assert session_ctx(client).network is good


def test_a_clean_bundle_loads_as_the_old_path_did_and_is_not_marked_unsaved(
    client, install_network, session_ctx, tmp_path
):
    """A2: the candidate swapped in equals a reset-and-load of the same network.nc, frame by frame."""
    import zipfile

    from services import dirty_state
    from services.pypsa_service import PyPSAService

    data = _bundle_of(client, install_network, _network())
    r = _import_bundle(client, data, "tcu11_clean")
    assert r.status_code in (200, 201), r.text
    assert "rederived" not in r.json() and "rederived_at_default_rate" not in r.json()
    ctx = session_ctx(client)
    assert not dirty_state.is_dirty(ctx)
    got = ctx.network
    path = tmp_path / "network.nc"
    path.write_bytes(zipfile.ZipFile(io.BytesIO(data)).read("network.nc"))
    with PyPSAService.get_lock():
        PyPSAService.reset_network()
        old = PyPSAService.get_network()
        PyPSAService.import_network_from_netcdf(old, path)
    for c in ("Bus", "Generator", "Load", "Line", "StorageUnit", "Carrier"):
        pd.testing.assert_frame_equal(
            got.components[c].static, old.components[c].static, obj=c
        )
    pd.testing.assert_index_equal(got.snapshots, old.snapshots)
    pd.testing.assert_frame_equal(got.snapshot_weightings, old.snapshot_weightings)
    pd.testing.assert_frame_equal(got.loads_t.p_set, old.loads_t.p_set)


def test_a_bad_row_in_a_second_class_refuses_with_nothing_left_behind(
    client, install_network, session_ctx, project_row, project_storage_dir
):
    """A3: the storage rows are fine; the Line's infinite length refuses before the project row or a folder."""
    n = _network()
    n.lines.at["ab", "length"] = float("inf")
    data = _bundle_of(client, install_network, n, name="tcu11_src2")
    root = project_storage_dir("tcu11_src2").parent
    before = {p.name for p in root.iterdir()}
    good = install_network(_network())
    r = _import_bundle(client, data, "tcu11_line")
    assert r.status_code == 422, r.text
    assert "Line 'ab'" in r.json()["detail"]["message"]
    assert project_row("tcu11_line") is None
    assert {p.name for p in root.iterdir()} == before
    assert session_ctx(client).network is good


def test_an_experiment_change_cannot_reach_a_rate_or_a_part():
    """B2: the preflight's allowlist holds no discount rate and no investment part, so a re-derive inside the
    copy never runs at a rate the same changes move."""
    from services.asset_schema.schema import COMPOSITE, part_columns
    from services.workflow_tools import _ATTRIBUTES

    for cls in COMPOSITE:
        allowed = _ATTRIBUTES.get(cls, set())
        assert "discount_rate" not in allowed, cls
        assert not allowed & set(part_columns(cls)), cls


def test_every_owned_cost_an_experiment_can_name_is_refused():
    """B3: the refused set is the single edit's (`derive.owned_columns`); of it, the allowlist reaches only
    capital_cost, which the refusal tests above cover on both priced classes."""
    from services.asset_schema.derive import owned_columns
    from services.asset_schema.schema import COMPOSITE
    from services.workflow_tools import _ATTRIBUTES

    reachable = {
        cls: _ATTRIBUTES.get(cls, set()) & set(owned_columns(cls)) for cls in COMPOSITE
    }
    assert reachable == {cls: {"capital_cost"} for cls in COMPOSITE}, reachable
