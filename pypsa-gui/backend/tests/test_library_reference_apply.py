"""
Apply one shipped cost reference to one asset (GS-EU Part 2, slice P2b): the plan.

The gatekeeper's conditions are the tests: an input-priced electrolyser on a Link
is never scaled by efficiency; the pack is unchanged; a non-EUR pack refuses;
AC PV, a double-counted connection and a different money year each need an
explicit confirmation; fixed O&M follows the pack's own Generator/Link
convention; only the chosen asset's fields are named.
"""
from __future__ import annotations

import pypsa
import pytest
from fastapi import HTTPException

from services.asset_schema import provenance as P
from services.library import europe_reference as R
from services.library import reference_apply as A
from services.library.defaults_pack.loader import load_defaults_pack
from services.library.defaults_pack.pricing import price_from_pack


def _net() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd_index())
    for bus in ("el", "h2", "grid"):
        n.add("Bus", bus)
    n.add("Link", "electrolyser", bus0="el", bus1="h2", p_nom=10.0, efficiency=0.55)
    n.add("Generator", "wind", bus="el", p_nom=20.0)
    n.add("Generator", "pv", bus="el", p_nom=5.0)
    n.add("Link", "poc", bus0="grid", bus1="el", p_nom=50.0)
    return n


def pd_index():
    import pandas as pd

    return pd.date_range("2030-01-01", periods=2, freq="h")


def _apply(n, p):
    frame = getattr(n, {"Generator": "generators", "Link": "links"}[p["component_class"]])
    for column, value in p["attrs"].items():
        frame.at[p["name"], column] = value
    A.stamp(n, p, approved_by="tester@example.com", approved_at="2026-10-21T10:00:00+00:00")


def _codes(exc) -> tuple[str, list]:
    return exc.value.detail["error_kind"], exc.value.detail.get("missing", [])


# ── 1. the electrolyser on a Link is never scaled by efficiency ─────────────

@pytest.mark.parametrize("efficiency", [0.4, 0.55, 0.8])
def test_input_priced_electrolyser_overnight_is_written_unscaled(efficiency):
    n = _net()
    n.links.at["electrolyser", "efficiency"] = efficiency
    ref = R.load_europe_technology_reference("electrolysis-aec-100mw", 2030)
    p = A.plan(n, {}, "Link", "electrolyser", "electrolysis-aec-100mw", 2030, confirm=())
    assert p["attrs"]["overnight_cost"] == ref.cost_part.overnight.value  # exactly: no factor at all
    assert p["attrs"]["efficiency"] == ref.cost_part.efficiency.value


def test_changing_efficiency_after_apply_does_not_move_the_cost():
    n = _net()
    p = A.plan(n, {}, "Link", "electrolyser", "electrolysis-aec-100mw", 2030)
    A.require_confirmed(p)
    _apply(n, p)
    cost = n.links.at["electrolyser", "overnight_cost"]
    n.links.at["electrolyser", "efficiency"] = 0.7
    assert n.links.at["electrolyser", "overnight_cost"] == cost
    again = A.plan(n, {}, "Link", "electrolyser", "electrolysis-aec-100mw", 2030)
    assert again["attrs"]["overnight_cost"] == cost


# ── fixed O&M: the pack's Generator/Link convention ────────────────────────

def test_wind_fom_cost_is_overnight_times_share_and_capital_cost_is_untouched():
    n = _net()
    n.generators.at["wind", "capital_cost"] = 123.0
    p = A.plan(n, {}, "Generator", "wind", "onwind", 2030)
    assert set(p["attrs"]) == {"overnight_cost", "lifetime", "fom_cost"}
    assert p["attrs"]["overnight_cost"] == pytest.approx(1_146_644.89966, rel=1e-9)
    # = the source's absolute fixed O&M, DEA 20 Onshore turbines!E25, EUR/MW/year
    assert p["attrs"]["fom_cost"] == pytest.approx(16_662.66796409161, rel=1e-9)
    _apply(n, p)
    assert n.generators.at["wind", "capital_cost"] == 123.0


# ── 2. the pack is unchanged; 7. only the chosen asset ────────────────────

def test_pack_and_other_assets_are_unchanged():
    n = _net()
    before_pack = load_defaults_pack().model_dump_json()
    before = (n.generators.copy(), n.links.drop(index="electrolyser").copy())
    p = A.plan(n, {}, "Link", "electrolyser", "electrolysis-aec-100mw", 2030)
    _apply(n, p)
    assert load_defaults_pack().model_dump_json() == before_pack
    assert n.generators.equals(before[0])
    assert n.links.drop(index="electrolyser").equals(before[1])


# ── 3. a non-EUR pack refuses ──────────────────────────────────────────────

def test_a_non_eur_pack_refuses_without_converting(monkeypatch):
    n = _net()
    real = A.C.reference_detail

    def gbp(technology, year, provenance=None):
        detail = real(technology, year, provenance)
        rows = [row.model_copy(update={"pack": row.pack.model_copy(update={"currency": "GBP"})})
                if row.pack is not None and row.parameter == "overnight" else row
                for row in detail.comparison]
        return detail.model_copy(update={"comparison": rows})

    monkeypatch.setattr(A.C, "reference_detail", gbp)
    with pytest.raises(HTTPException) as exc:
        A.plan(n, {}, "Generator", "wind", "onwind", 2030)
    assert _codes(exc)[0] == "currency_mismatch"


# ── 4. PV on AC needs the DC/AC confirmation ──────────────────────────────

def test_ac_pv_on_an_undeclared_generator_needs_confirmation():
    n = _net()
    p = A.plan(n, {}, "Generator", "pv", "solar-utility", 2030)
    assert "dc_ac" in p["missing"]
    with pytest.raises(HTTPException) as exc:
        A.require_confirmed(p)
    assert _codes(exc) == ("reference_confirmation_needed", p["missing"])
    ok = A.plan(n, {}, "Generator", "pv", "solar-utility", 2030, confirm=p["missing"])
    assert ok["missing"] == []
    A.require_confirmed(ok)


# ── 5. grid connection counted twice ──────────────────────────────────────

def test_grid_connection_is_flagged_only_when_the_project_prices_its_connection():
    n = _net()
    commercial = {"poc_link": "poc"}
    free = A.plan(n, commercial, "Generator", "wind", "onwind", 2030)
    assert "grid_connection" not in free["missing"]
    n.links.at["poc", "overnight_cost"] = 200_000.0
    priced = A.plan(n, commercial, "Generator", "wind", "onwind", 2030)
    assert "grid_connection" in priced["missing"]
    fee = A.plan(_net(), {"poc_link": "poc", "connection": {"capacity_fee": {"kind": "capacity"}}},
                 "Generator", "wind", "onwind", 2030)
    assert "grid_connection" in fee["missing"]
    unknown = A.plan(n, commercial, "Link", "electrolyser", "electrolysis-aec-100mw", 2030)
    assert "grid_connection" not in unknown["missing"]  # "unknown" is not flagged as double


# ── money year: copied as is, confirmed when it differs ───────────────────

def test_a_different_money_year_needs_confirmation_and_is_never_adjusted():
    n = _net()
    price_from_pack(n, "generators", "pv", "solar-utility", version=load_defaults_pack().version)
    p = A.plan(n, {}, "Generator", "pv", "solar-utility", 2030, confirm=["dc_ac"])
    assert p["missing"] == ["money_year"]
    assert p["attrs"]["overnight_cost"] == R.load_europe_technology_reference(
        "solar-utility", 2030).cost_part.overnight.value
    assert p["currency_year"] == 2025
    overnight = next(r for r in p["rows"] if r["field"] == "overnight_cost")
    assert overnight["pack"]["currency_year"] == 2020 and overnight["reference"]["currency_year"] == 2025


# ── provenance per field, and the projection-year note ────────────────────

def test_stamp_records_source_money_year_currency_and_approval_per_field():
    n = _net()
    p = A.plan(n, {}, "Link", "electrolyser", "electrolysis-aec-100mw", 2030)
    _apply(n, p)
    stamps = P.get(n, "Link", "electrolyser")
    ref = R.load_europe_technology_reference("electrolysis-aec-100mw", 2030)
    assert set(stamps) == {"overnight_cost", "lifetime", "fom_cost", "efficiency"}
    over = stamps["overnight_cost"]
    assert over["source"].startswith(f"eu-dea-2026-08#{ref.reference_hash[:16]}")
    assert (over["status"], over["currency"], over["currency_year"]) == ("customised", "EUR", 2020)
    assert over["approved_by"] == "tester@example.com"
    assert stamps["lifetime"].get("currency") is None
    P.mark_customised(n, "Link", "electrolyser", ["overnight_cost"])
    assert "approved_by" not in P.get(n, "Link", "electrolyser")["overnight_cost"]


def test_projection_year_mismatch_is_a_note_not_a_refusal():
    n = _net()
    p = A.plan(n, {}, "Generator", "wind", "onwind", 2040)
    assert p["notes"] and "2040" in p["notes"][0] and "2030" in p["notes"][0]


# ── refusals ───────────────────────────────────────────────────────────────

def test_wrong_class_unknown_asset_and_storage_are_refused():
    n = _net()
    n.add("StorageUnit", "bat", bus="el", p_nom=1.0)
    for args, kind in [(("Link", "electrolyser", "onwind", 2030), "reference_target_unsupported"),
                       (("Generator", "wind", "electrolysis-aec-100mw", 2030), "reference_target_unsupported"),
                       (("StorageUnit", "bat", "onwind", 2030), "reference_target_unsupported"),
                       (("Generator", "nope", "onwind", 2030), "component_not_found"),
                       (("Generator", "wind", "onwind", 2035), "library_reference_unknown")]:
        with pytest.raises(HTTPException) as exc:
            A.plan(n, {}, *args)
        assert exc.value.detail["error_kind"] == kind, args
    with pytest.raises(HTTPException) as exc:
        A.plan(n, {}, "Generator", "wind", "onwind", 2030, confirm=["everything"])
    assert exc.value.detail["error_kind"] == "invalid_edit_value"


# ── routes and the assistant tool, on the active network ──────────────────

ROUTE = "/api/network/library_reference"
PREVIEW = "/api/library_reference/preview"
AEC = {"component_class": "Link", "name": "electrolyser",
       "technology": "electrolysis-aec-100mw", "projection_year": 2030}


def test_preview_card_then_apply_writes_and_stamps(client, install_network):
    n = install_network(_net())
    r = client.post(PREVIEW, json=AEC)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["missing"] == [] and body["card"]["tool"] == "apply_library_reference"
    typed = {row["field"]: row for row in body["card"]["rows"] if row["role"] == "typed"}
    assert set(typed) == {"overnight_cost", "lifetime", "fom_cost", "efficiency"}
    assert typed["overnight_cost"]["reference"]["cell"] and typed["efficiency"]["pack"]["key"] == \
        "electrolysis.investment.efficiency"
    assert any(row["field"] == "licence" for row in body["card"]["rows"])
    r = client.post(f"{ROUTE}/apply", json={**AEC, "digest": body["card"]["digest"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["receipt"]["outcome"] == "verified"
    ref = R.load_europe_technology_reference("electrolysis-aec-100mw", 2030)
    assert n.links.at["electrolyser", "overnight_cost"] == ref.cost_part.overnight.value
    assert P.get(n, "Link", "electrolyser")["overnight_cost"]["source"] == out["source"]


def test_preview_without_a_needed_confirmation_returns_needs_and_no_card(client, install_network):
    install_network(_net())
    pv = {"component_class": "Generator", "name": "pv", "technology": "solar-utility", "projection_year": 2030}
    body = client.post(PREVIEW, json=pv).json()
    # AC basis undeclared, and 2025 EUR beside the generic pack's 2020 EUR
    assert body["card"] is None and body["missing"] == ["dc_ac", "money_year"]
    assert "AC output" in body["needs"][0]["text"] and "not adjusted" in body["needs"][1]["text"]
    r = client.post(f"{ROUTE}/apply", json={**pv, "digest": "never-shown"})
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "reference_confirmation_needed"
    card = client.post(PREVIEW, json={**pv, "confirm": ["dc_ac", "money_year"]}).json()["card"]
    confirmed = [row for row in card["rows"] if row.get("confirmed")]
    assert {row["field"] for row in confirmed} == {"dc_ac", "money_year"}
    assert all(row["note"].startswith("Confirmed: ") and "unticked" not in row["note"] for row in confirmed)
    money = [row["note"] for row in card["rows"] if row["role"] == "note" and row["field"] == "overnight_cost"]
    assert len(money) == 1 and "annualises this cost over the 40-year lifetime" in money[0]
    assert "Price data: 2025 EUR" in money[0] and "×" not in money[0] and "fom_cost" not in money[0]


def test_a_stale_digest_refuses_and_writes_nothing(client, install_network):
    n = install_network(_net())
    card = client.post(PREVIEW, json=AEC).json()["card"]
    n.links.at["electrolyser", "lifetime"] = 7.0  # the asset moved after the card was shown
    r = client.post(f"{ROUTE}/apply", json={**AEC, "digest": card["digest"]})
    assert r.status_code == 409 and r.json()["detail"]["error_kind"] == "edit_preview_stale"
    assert n.links.at["electrolyser", "lifetime"] == 7.0


def test_the_tool_is_a_network_edit_with_a_card(install_network):
    from harness import edit_card
    from harness.catalogue import safety_tier_for, tool_edits_network
    from services import chat_tools

    assert safety_tier_for("apply_library_reference") == "write"
    assert tool_edits_network("apply_library_reference") and edit_card.is_edit("apply_library_reference")
    assert "apply_library_reference" in chat_tools.UNDO_CAPTURED_TOOLS
    install_network(_net())
    card = edit_card.preview("apply_library_reference", dict(AEC))
    assert card["kind"] == "parameter_edit" and card["complete"]


# ── money year against the investment case's base year (gatekeeper, option 1) ──

def test_onwind_2020_eur_on_a_case_with_base_year_2026_needs_money_year():
    from types import SimpleNamespace

    import pandas as pd

    n = _net()
    n.set_snapshots(pd.date_range("2026-01-01", periods=2, freq="h"))
    base = A.case_base_year(n, SimpleNamespace(finance={"currency": "EUR"}))
    assert base == 2026
    assert A.case_base_year(n, SimpleNamespace(finance=None)) is None
    p = A.plan(n, {}, "Generator", "wind", "onwind", 2030, base_year=base)
    need = next(x for x in p["needs"] if x["confirmation"] == "money_year")
    assert (need["reference_year"], need["read_as_year"]) == (2020, 2026)
    assert need["disclosure"] == "2020 EUR price data, read as 2026 money: not adjusted for inflation."
    assert need["read_as_source"] == "the investment case's base year (2026)"
    with pytest.raises(HTTPException) as exc:
        A.require_confirmed(p)
    assert _codes(exc) == ("reference_confirmation_needed", ["money_year"])
    ok = A.plan(n, {}, "Generator", "wind", "onwind", 2030, confirm=["money_year"], base_year=base)
    A.require_confirmed(ok)
    _apply(n, ok)
    assert P.get(n, "Generator", "wind")["overnight_cost"]["currency_year"] == 2020
    # without a case, the same 2020 EUR beside the pack's 2020 EUR needs nothing
    assert "money_year" not in A.plan(_net(), {}, "Generator", "wind", "onwind", 2030)["missing"]


def test_money_years_in_use_lists_each_recorded_cost_year():
    n = _net()
    assert A.money_years_in_use(n) == {}
    price_from_pack(n, "generators", "wind", "onwind", version=load_defaults_pack().version)
    p = A.plan(n, {}, "Generator", "pv", "solar-utility", 2030, confirm=["dc_ac", "money_year"])
    _apply(n, p)
    years = A.money_years_in_use(n)
    assert sorted(years) == [2020, 2025]
    assert ("Generator", "pv", "overnight_cost") in years[2025]
    assert ("Generator", "wind", "overnight_cost") in years[2020]
    # A cost typed over the applied reference is not that reference's year (B9).
    from services.asset_schema import provenance
    provenance.mark_customised(n, "Generator", "pv", ["overnight_cost"])
    assert ("Generator", "pv", "overnight_cost") not in A.money_years_in_use(n).get(2025, [])
    assert A.hand_typed_cost_fields(n) == [("Generator", "pv", "overnight_cost")]


def test_the_http_apply_needs_the_digest_of_a_card_the_user_saw(client, install_network):
    n = install_network(_net())
    r = client.post(f"{ROUTE}/apply", json=AEC)
    assert r.status_code == 422
    ref = R.load_europe_technology_reference("electrolysis-aec-100mw", 2030)
    assert n.links.at["electrolyser", "overnight_cost"] != ref.cost_part.overnight.value
    assert P.get(n, "Link", "electrolyser") == {}


def test_re_applying_compares_with_the_pack_the_asset_had_before_and_is_idempotent():
    n = _net()
    version = load_defaults_pack().version
    price_from_pack(n, "generators", "wind", "onwind", version=version)
    first = A.plan(n, {}, "Generator", "wind", "onwind", 2030)
    _apply(n, first)
    again = A.plan(n, {}, "Generator", "wind", "onwind", 2030)
    assert again["pack"]["chosen_by"] == "asset_provenance" and again["pack"]["version"] == version
    assert again["attrs"] == first["attrs"] and again["missing"] == first["missing"]
    _apply(n, again)
    assert P.get(n, "Generator", "wind")["overnight_cost"]["replaces"].startswith(f"generic_defaults@{version}")


def test_no_default_and_no_case_is_a_note_not_a_silent_skip():
    p = A.plan(_net(), {}, "Link", "electrolyser", "electrolysis-pem-100mw", 2030)
    assert "money_year" not in p["missing"]
    assert any("No investment case or default value" in note for note in p["notes"])


def test_a_regionally_priced_asset_refuses_on_its_recorded_currency():
    """GK: a `<date>+<region>` pack this build may not load must not fall back to EUR and pass."""
    n = _net()
    P.record(n, "Generator", "wind", "overnight_cost", status="default",
             source="generic_defaults@2026-10-21+uk#0123456789abcdef", source_year=2025,
             currency_year=2025, currency="GBP")
    before = n.generators.copy()
    with pytest.raises(HTTPException) as exc:
        A.plan(n, {}, "Generator", "wind", "onwind", 2030)
    assert exc.value.detail["error_kind"] == "currency_mismatch"
    assert n.generators.equals(before)


def test_a_regional_version_this_build_cannot_load_is_named_not_silently_eur():
    detail = A.C.reference_detail("onwind", 2030, {"overnight_cost": {
        "source": "generic_defaults@2026-10-21+uk#0123456789abcdef"}})
    assert detail.pack.chosen_by == "latest_generic"
    assert "not available" in detail.pack.note


def test_an_asset_priced_from_a_real_regional_pack_is_compared_with_it_and_refused():
    """With #499's regional packs: a UK-priced onwind asset names its UK pack, and an EUR
    reference is refused on the recorded GBP, nothing converted or written."""
    from services.library.defaults_pack.loader import available_regions

    if "uk" not in available_regions():
        pytest.skip("no UK regional pack in this build")
    n = _net()
    price_from_pack(n, "generators", "wind", "onwind", version="2026-10-22", region="uk")
    stamps = P.get(n, "Generator", "wind")
    assert stamps["overnight_cost"]["currency"] == "GBP"
    detail = A.C.reference_detail("onwind", 2030, stamps)
    assert detail.pack.chosen_by == "asset_provenance" and detail.pack.version == "2026-10-22+uk"
    before = n.generators.copy()
    with pytest.raises(HTTPException) as exc:
        A.plan(n, {}, "Generator", "wind", "onwind", 2030)
    assert exc.value.detail["error_kind"] == "currency_mismatch"
    assert n.generators.equals(before)


def _solved_net():
    import pandas as pd

    n = _net()
    n.links.at["electrolyser", "overnight_cost"] = 123.0
    n.generators_t.p = pd.DataFrame({"wind": [1.0, 1.0], "pv": [1.0, 1.0]}, index=n.snapshots)
    n.links_t.p0 = pd.DataFrame({"electrolyser": [1.0, 1.0], "poc": [1.0, 1.0]}, index=n.snapshots)
    return n


def test_the_preview_is_a_read_outside_the_network_prefixes(
    client, anon_client, install_network, session_ctx, monkeypatch,
):
    import pandas as pd

    import main
    from services import undo_service
    from services.dispatch_status import dispatch_status

    monkeypatch.setattr(undo_service, "claim_push_slot", lambda *a, **k: True)
    for prefixes in (main._UNDO_PREFIXES, main._DISPATCH_INVALIDATE_PREFIXES, main._STUDY_EDIT_PREFIXES):
        assert not any(PREVIEW.startswith(p) for p in prefixes)
    n = install_network(_solved_net())
    assert dispatch_status(n) == "fresh"
    dispatch = n.generators_t.p.copy(deep=True)
    cost = n.links.at["electrolyser", "overnight_cost"]
    depth = client.get("/api/network/undo/info").json()["depth"]
    revision = session_ctx(client).network_revision
    r = client.post(PREVIEW, json=AEC)
    assert r.status_code == 200, r.text
    assert r.json()["card"]["tool"] == "apply_library_reference"
    pv = {"component_class": "Generator", "name": "pv", "technology": "solar-utility", "projection_year": 2030}
    r = client.post(PREVIEW, json=pv)
    assert r.status_code == 200, r.text
    assert r.json()["needs"] and r.json()["card"] is None
    assert client.get("/api/network/undo/info").json()["depth"] == depth
    assert session_ctx(client).network_revision == revision
    assert dispatch_status(n) == "fresh"
    pd.testing.assert_frame_equal(n.generators_t.p, dispatch)
    assert n.links.at["electrolyser", "overnight_cost"] == cost
    assert anon_client.post(PREVIEW, json=AEC).status_code == 401
    # Last: even an absent route under /api/network still traverses write middleware.
    assert client.post(f"{ROUTE}/preview", json=AEC).status_code in (404, 405)


def test_the_apply_is_still_an_edit_that_clears_results(client, install_network, session_ctx, monkeypatch):
    from services import undo_service
    from services.dispatch_status import dispatch_status

    monkeypatch.setattr(undo_service, "claim_push_slot", lambda *a, **k: True)
    n = install_network(_solved_net())
    assert dispatch_status(n) == "fresh"
    depth = client.get("/api/network/undo/info").json()["depth"]
    revision = session_ctx(client).network_revision
    r = client.post(PREVIEW, json=AEC)
    assert r.status_code == 200, r.text
    r = client.post(f"{ROUTE}/apply", json={**AEC, "digest": r.json()["card"]["digest"]})
    assert r.status_code == 200, r.text
    assert r.json()["receipt"]["outcome"] == "verified"
    assert client.get("/api/network/undo/info").json()["depth"] == depth + 1
    assert session_ctx(client).network_revision > revision
    assert dispatch_status(n) == "none"
