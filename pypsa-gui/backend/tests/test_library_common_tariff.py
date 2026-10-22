"""
The shipped (`common`) Library tariffs: EU-C09, TenneT's 2026 transport tariff
(ACM tarievenbesluit TenneT 2026, dictum table 2), as Library items every
organization sees and none can overwrite (GS-EU Part 2, slice P2d).

The bill fixture is the oracle: a direct HS customer with 10 MW contracted, a
full 2026, billed by the native tariff engine. The weighted monthly peak
(Tarievencode elektriciteit 2026 art. 3.9 lid 5) is not modelled, so the bill
names it and has no total; it is never presented as complete.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from models import commercial as M
from services.commercial.tariff_engine import rate
from services.library import common
from services.library import items as I

HS, EHS = "nl-tennet-2026-hs", "nl-tennet-2026-ehs"


@pytest.fixture
def db(_auth_db):
    session = _auth_db[1]()
    yield session
    session.close()


@pytest.fixture
def org_id(second_identity):
    return second_identity["org_id"]


def _tariff(name: str) -> M.Tariff:
    return M.Tariff.model_validate(common.payload("tariff", name))


def _year_2026(peaks_mw: list[float]) -> pd.DataFrame:
    """Hourly 2026 import: 6 MW base, each month's peak in one hour."""
    idx = pd.date_range("2026-01-01", "2027-01-01", freq="h", inclusive="left")
    imp = pd.Series(6.0, index=idx)
    for month, peak in enumerate(peaks_mw, start=1):
        imp[idx[idx.month == month][100]] = peak
    return pd.DataFrame({"import_mw": imp, "export_mw": 0.0})


# ── the bill fixture (the gatekeeper's first test) ─────────────────────────

def test_hs_full_year_bill_fixture():
    dispatch = _year_2026([8, 8.5, 9, 9.5, 10, 10, 9, 8, 8.5, 9, 9.5, 10])
    res = rate(dispatch, _tariff(HS), step_hours=1.0, timezone=None,
               billing_period=(pd.Timestamp("2026-01-01"), pd.Timestamp("2027-01-01")),
               capacity_kw=10_000.0)
    assert res.per_item["vastrecht"] == pytest.approx(2_760.00, abs=0.005)
    assert res.per_item["kw_contracted"] == pytest.approx(65.62 * 10_000, abs=0.005)
    # The weighted monthly peak is not modelled: named, and the bill has no total.
    assert res.total is None
    assert any("kw_max_gewogen_per_maand" in f and "art. 3.9 lid 5" in f for f in res.flags["_tariff"])


def test_ehs_vastrecht_is_the_annual_figure_to_the_cent_at_full_precision():
    payload = common.payload("tariff", EHS)
    monthly = next(i for i in payload["items"] if i["id"] == "vastrecht")["periods"][0]["rate"]
    assert monthly == 12_478.96 / 12            # never rounded to cents
    res = rate(_year_2026([200.0] * 12), _tariff(EHS), step_hours=1.0, timezone=None,
               billing_period=(pd.Timestamp("2026-01-01"), pd.Timestamp("2027-01-01")),
               capacity_kw=150_000.0)
    assert res.per_item["vastrecht"] == pytest.approx(12_478.96, abs=0.005)
    assert res.per_item["kw_contracted"] == pytest.approx(49.28 * 150_000, abs=0.005)


def test_a_part_year_prorates_vastrecht_by_the_engines_hours_rule():
    q1 = _year_2026([9.0] * 12).loc["2026-01-01":"2026-03-31 23:00"]
    res = rate(q1, _tariff(HS), step_hours=1.0, timezone=None, capacity_kw=10_000.0)
    assert res.per_item["vastrecht"] == pytest.approx(3 * 2_760.00 / 12, abs=0.005)
    assert "capacity_prorated_by_represented_hours" in res.notes["kw_contracted"]


def test_no_declared_contracted_capacity_is_not_established_never_billed():
    res = rate(_year_2026([9.0] * 12), _tariff(HS), step_hours=1.0, timezone=None,
               billing_period=(pd.Timestamp("2026-01-01"), pd.Timestamp("2027-01-01")),
               capacity_kw=None)
    assert res.per_item["kw_contracted"] is None
    assert "capacity_not_established" in res.flags["kw_contracted"]


# ── the source cells ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name,fixed,contracted", [(EHS, 12_478.96, 49.28), (HS, 2_760.00, 65.62)])
def test_rates_are_the_acm_dictum_cells(name, fixed, contracted):
    meta = common.meta("tariff", name)
    assert meta["source_cells"]["vastrecht_per_year"]["value"] == fixed
    assert meta["source_cells"]["kw_contracted_per_year"]["value"] == contracted
    items = {i["id"]: i for i in common.payload("tariff", name)["items"]}
    assert items["kw_contracted"]["periods"][0]["rate"] == contracted
    assert items["vastrecht"]["periods"][0]["rate"] == fixed / 12
    assert set(items) == {"vastrecht", "kw_contracted"}


@pytest.mark.parametrize("name", [EHS, HS])
def test_meta_carries_scope_licence_validity_and_what_is_not_modelled(name):
    meta = common.meta("tariff", name)
    assert meta["scope"] == "common" and meta["source_url"].startswith("https://www.acm.nl/")
    assert len(meta["source_sha256"]) == 64 and meta["licence"] and meta["attribution"]
    notes = " ".join(meta["notes"])
    for needle in ("art. 3.9 lid 5", "600", "VAT", "energy tax", "direct", "per_month",
                   "contracted transport capacity"):
        assert needle in notes, needle
    payload = common.payload("tariff", name)
    assert (payload["jurisdiction"], payload["valid_from"], payload["valid_to"]) == ("NL", "2026-01-01", "2026-12-31")


# ── common scope in the Library ────────────────────────────────────────────

def test_payload_hashes_are_pinned():
    for (kind, name), digest in common.PINNED.items():
        assert common.ref(kind, name).hash == digest


def test_common_items_are_listed_and_resolve_in_any_org(db, org_id):
    refs = {r.id: r for r in I.list_items(db, org_id, "tariff")}
    assert {HS, EHS} <= set(refs)
    assert I.resolve(db, org_id, refs[HS]) == common.payload("tariff", HS)
    assert I.item_meta(db, org_id, refs[HS])["scope"] == "common"
    assert I.latest_ref(db, org_id, "tariff", HS) == refs[HS]


def test_common_items_are_read_only_and_never_shadowed(db, org_id):
    with pytest.raises(I.LibraryItemCommonReadonly):
        I.put_item(db, org_id, "tariff", HS, common.payload("tariff", HS))
    payload = dict(common.payload("tariff", HS), name="mine")
    ref = I.put_item(db, org_id, "tariff", "mine", payload)
    assert ref.id == "mine"
    with pytest.raises(ValueError, match="common"):
        I.put_item(db, org_id, "tariff", "mine", payload, {"scope": "common"})


def test_a_pinned_ref_to_a_common_item_stays_resolvable_and_a_wrong_hash_is_stale(db, org_id):
    ref = common.ref("tariff", HS)
    assert I.resolve(db, org_id, ref)["id"] == HS
    with pytest.raises(I.LibraryRefStale):
        I.resolve(db, org_id, ref.model_copy(update={"hash": "0" * 64}))


def test_routes_list_get_and_refuse_a_put(client):
    names = [r["id"] for r in client.get("/api/library/items/tariff").json()]
    assert HS in names and EHS in names
    got = client.get(f"/api/library/items/tariff/{HS}").json()
    assert got["meta"]["scope"] == "common" and got["payload"]["id"] == HS
    r = client.put(f"/api/library/items/tariff/{HS}", json={"payload": got["payload"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "library_item_common_readonly"


# ── user-scoped sources never ship ─────────────────────────────────────────

USER_SCOPED = ("50hertz", "netze bw", "netze-bw", "liander", "turpe", "cnmc", "e-distribuzione",
               "arera", "entso-e", "transparency.entsoe", "openei", "urdb")


def test_no_user_scoped_source_is_shipped_as_common():
    for path in sorted(common.COMMON_DIR.rglob("*.json")):
        text = path.read_text("utf-8").lower()
        hits = [s for s in USER_SCOPED if s in text]
        assert not hits, f"{path.name} names user-scoped source(s) {hits}"
        assert json.loads(text)["meta"]["scope"] == "common"


def test_the_monthly_vastrecht_is_not_a_rounded_cent_value():
    for name in (EHS, HS):
        rate_ = next(i for i in common.payload("tariff", name)["items"] if i["id"] == "vastrecht")["periods"][0]["rate"]
        assert np.isclose(rate_ * 12, common.meta("tariff", name)["source_cells"]["vastrecht_per_year"]["value"],
                          rtol=0, atol=1e-9)


# ── through the site bill on a solved network ─────────────────────────────

def test_the_site_bill_charges_the_tennet_items_and_marks_the_bill_incomplete():
    import queue
    import threading

    from services.commercial import billing as B
    from services.pypsa_service import PyPSAService
    from services.solver_service import SolverConfig, run_simulation
    from tests.fixtures.investment_case import edge_15min as F

    n = F.build_edge_15min()
    n.generators.loc["grid_supply", "marginal_cost"] = 0.0
    commercial = {"poc_link": "import", "import_tariff": common.payload("tariff", HS)}
    PyPSAService.set_network(n)
    status, cond = run_simulation(SolverConfig(commercial=commercial), n, PyPSAService.get_lock(),
                                  threading.Event(), queue.SimpleQueue(), state_update=lambda **kw: None)
    assert status in ("ok", "optimal"), (status, cond)
    bill = B.bill_site(n, commercial)
    res = bill.per_period[None]
    hours = len(n.snapshots) * 0.25
    p_nom_kw = float(n.links.at["import", "p_nom_opt"] or n.links.at["import", "p_nom"]) * 1000.0
    assert res.per_item["kw_contracted"] == pytest.approx(65.62 * p_nom_kw * hours / 8760.0, rel=1e-9)
    assert res.per_item["vastrecht"] > 0
    assert res.total is None
    assert any(f.startswith("tariff_incomplete:kw_max_gewogen_per_maand") for f in bill.flags)
