"""
The shipped (common) cost references as Library entries: read-only listing and
the per-asset comparison (GS-EU Part 2, slice P2a).

The comparison stands beside the pack the asset was priced from (its
`overnight_cost` provenance), else the latest generic pack, and says which.
Nothing is written: not the project, the pack or the Library.
"""
from __future__ import annotations

import pypsa
import pytest
from fastapi import HTTPException

from services import chat_tools
from services.asset_schema import provenance as P
from services.chat_tools_schema import safety_tier_for
from services.library import europe_reference as R
from services.library import reference_catalogue as C
from services.library.defaults_pack.loader import available_versions, load_defaults_pack
from services.library.defaults_pack.pricing import price_from_pack

TOOLS = ("list_library_references", "get_library_reference")


def _priced(version: str) -> pypsa.Network:
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "wind", bus="b", p_nom=10.0)
    price_from_pack(n, "generators", "wind", "onwind", version=version)
    return n


def test_list_is_every_selector_with_scope_licence_and_typed_units():
    rows = C.list_references()
    assert [(r.technology, r.projection_year) for r in rows] == list(
        R.available_europe_technology_references())
    assert {r.scope for r in rows} == {"common"}
    assert {r.licence for r in rows} == {"CC-BY-4.0"}
    wind = next(r for r in rows if (r.technology, r.projection_year) == ("onwind", 2030))
    assert (wind.currency_year, wind.capacity_basis_code, wind.includes_grid_connection) == (
        2020, "output_electric", True)


def test_an_asset_priced_from_an_older_pack_is_compared_with_that_pack():
    oldest_with_wind = next(v for v in available_versions()
                            if any(row.key == "onwind.investment.overnight"
                                   for row in load_defaults_pack(v).cost_values))
    n = _priced(oldest_with_wind)
    detail = C.reference_detail("onwind", 2030, P.get(n, "Generator", "wind"))
    assert detail.pack.chosen_by == "asset_provenance"
    assert detail.pack.version == oldest_with_wind
    overnight = next(r for r in detail.comparison if r.parameter == "overnight")
    assert overnight.pack.value == pytest.approx(n.generators.at["wind", "overnight_cost"])
    assert overnight.reference.value == pytest.approx(1_146_644.89966)


def test_no_or_unknown_provenance_falls_back_to_the_latest_generic_pack_and_says_so():
    latest = load_defaults_pack()
    plain = C.reference_detail("solar-utility", 2030)
    assert (plain.pack.chosen_by, plain.pack.version, plain.pack.note) == (
        "latest_generic", latest.version, None)
    gone = C.reference_detail("solar-utility", 2030, {"overnight_cost": {
        "source": "generic_defaults@1999-01-01#0123456789abcdef"}})
    assert gone.pack.chosen_by == "latest_generic"
    assert "not available" in gone.pack.note
    customised = C.reference_detail("solar-utility", 2030, {"overnight_cost": {"source": "my quote"}})
    assert customised.pack.chosen_by == "latest_generic" and customised.pack.note is None


def test_reading_writes_nothing():
    n = _priced(load_defaults_pack().version)
    before = (n.generators.copy(), P.get(n, "Generator", "wind"), load_defaults_pack().hash)
    for technology, year in R.available_europe_technology_references():
        C.reference_detail(technology, year, P.get(n, "Generator", "wind"))
    assert n.generators.equals(before[0])
    assert P.get(n, "Generator", "wind") == before[1]
    assert load_defaults_pack().hash == before[2]


# ── HTTP ────────────────────────────────────────────────────────────────────

def test_routes_list_and_detail(client):
    rows = client.get("/api/library/references").json()
    assert len(rows) == 24 and rows[0]["scope"] == "common"
    r = client.get("/api/library/references/electrolysis-aec-100mw/2030")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reference"]["capacity_basis_code"] == "input_electric"
    assert {row["parameter"] for row in body["comparison"]} == {
        "overnight", "fom_share", "lifetime", "efficiency"}
    assert all(row["pack"]["key"].startswith("electrolysis.") for row in body["comparison"])


def test_unknown_selector_is_404(client):
    r = client.get("/api/library/references/onwind/2035")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "library_reference_unknown"


# ── chat tools ──────────────────────────────────────────────────────────────

def test_the_tools_are_registered_read_only():
    for name in TOOLS:
        assert name in chat_tools.DISPATCHERS
    assert [safety_tier_for(n) for n in TOOLS] == ["read", "read"]


def test_tools_list_and_read_with_the_assets_pack(client, install_network):
    page = chat_tools.DISPATCHERS["list_library_references"](limit=5)
    assert page["total_count"] == 24 and 1 <= page["returned"] <= 5 and page["has_more"]
    n = install_network(_priced(load_defaults_pack().version))
    got = chat_tools.DISPATCHERS["get_library_reference"](
        technology="onwind", projection_year=2030, component="Generator", asset="wind")
    assert got["pack"]["chosen_by"] == "asset_provenance"
    overnight = next(r for r in got["comparison"] if r["parameter"] == "overnight")
    assert overnight["differs"] and overnight["pack"]["currency_year"] == 2020
    assert n.generators.at["wind", "overnight_cost"] == pytest.approx(overnight["pack"]["value"])


def test_tool_refuses_half_an_asset_and_unknown_selectors():
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["get_library_reference"](
            technology="onwind", projection_year=2030, component="Generator")
    assert exc.value.detail["error_kind"] == "library_reference_unknown"
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["get_library_reference"](technology="onwind", projection_year=2035)
    assert exc.value.status_code == 404
    assert exc.value.detail["error_kind"] == "library_reference_unknown"


@pytest.mark.parametrize("source", [
    "!" * 5000, "!@" + "!@!" * 3000, "a@b#" + "0" * 15, "a@b#" + "g" * 16, "@v#" + "0" * 16,
    "a b@v#" + "0" * 16, "id@v#" + "0" * 16 + "x", None,
])
def test_a_malformed_pack_source_falls_back_quickly(source):
    detail = C.reference_detail("onwind", 2030, {"overnight_cost": {"source": source}})
    assert detail.pack.chosen_by == "latest_generic"


def test_a_regional_version_loads_through_the_loaders_own_region_list(monkeypatch):
    """Once the loader takes `region=` (#499), a `<date>+<region>` stamp loads that pack;
    the region and date handed over are the loader's / rebuilt, never the stamp's text."""
    real = load_defaults_pack()
    calls = []

    def fake(version=None, *, region=None):
        calls.append((version, region))
        return real

    monkeypatch.setattr(C, "load_defaults_pack", fake)
    monkeypatch.setattr(C.loader, "available_regions", lambda: ("uk",), raising=False)
    source = f"{real.pack_id}@2026-10-21+uk#{real.hash[:16]}"
    _pack, choice = C.pack_for_asset({"overnight_cost": {"source": source}})
    assert calls == [("2026-10-21", "uk")] and choice.chosen_by == "asset_provenance"
    calls.clear()
    _pack, choice = C.pack_for_asset({"overnight_cost": {"source": source.replace("+uk", "+fr")}})
    assert ("2026-10-21", "fr") not in calls and choice.chosen_by == "latest_generic"
