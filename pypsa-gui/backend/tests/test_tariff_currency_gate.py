"""
A tariff carries a currency, and a project priced in another refuses it
(gridspine-library-eu issue 01; owner answer Q-GS-1, 2026-11-05: required,
outside the hash).

* `Tariff.currency` is ISO 4217. Unstated, it is the jurisdiction's
  (`NL` -> EUR, `US-CA` -> USD); a `generic` jurisdiction implies none, and a
  Library write then refuses the tariff until it states one.
* The currency stays out of the canonical hash: one the jurisdiction implies is
  left out of every hashed form, so the shipped TenneT pins and stored
  commercial records keep their hashes. One that differs from it is content.
* `currency_mismatch` (nothing converted) when the tariff's currency is not the
  project's stated finance currency: at attach (`attach_tariff`, the
  solver-config PUT's `import_tariff_ref`) and in preflight for an inline tariff.
  An unstated currency on either side is not checked.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from models import commercial as M
from services import chat_tools
from services.commercial import hashing as H
from services.library import common
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig
from services.validation_service import validate_for_run
from tests.fixtures.investment_case.edge_15min import build_edge_15min

HS = "nl-tennet-2026-hs"
GENERIC = {"id": "g", "name": "Generic", "jurisdiction": "generic", "valid_from": "2030-01-01",
           "items": [{"id": "e", "kind": "energy", "unit": "per_kwh",
                      "periods": [{"name": "all", "rate": 0.1}]}]}


def _nl(**over):
    return {**GENERIC, "id": "nl", "jurisdiction": "NL", **over}


# ── the model ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("jurisdiction,currency", [
    ("NL", "EUR"), ("DE", "EUR"), ("GB", "GBP"), ("US", "USD"), ("US-CA", "USD"),
    ("CA_ON", "CAD"), ("generic", None)])
def test_an_unstated_currency_is_the_jurisdictions(jurisdiction, currency):
    assert M.Tariff.model_validate(_nl(jurisdiction=jurisdiction)).currency == currency


def test_a_stated_currency_wins_and_must_be_iso_4217():
    assert M.Tariff.model_validate(_nl(currency="USD")).currency == "USD"
    with pytest.raises(ValueError):
        M.Tariff.model_validate(_nl(currency="eur"))


def test_the_shipped_tennet_tariffs_read_as_eur_and_keep_their_pinned_hashes():
    for name in ("nl-tennet-2026-hs", "nl-tennet-2026-ehs"):
        tariff = M.Tariff.model_validate(common.payload("tariff", name))
        assert tariff.currency == "EUR"
        assert H.library_item_digest(tariff) == common.PINNED[("tariff", name)]
        assert "currency" not in H.library_item_canonical(tariff)


def test_an_implied_currency_is_outside_every_hash_and_a_different_one_is_content():
    bare, stated, usd = (M.Tariff.model_validate(p) for p in (_nl(), _nl(currency="EUR"),
                                                               _nl(currency="USD")))
    assert H.library_item_digest(bare) == H.library_item_digest(stated)
    assert H.library_item_digest(usd) != H.library_item_digest(bare)
    for version in (1, 2):
        cfg = lambda t: M.CommercialConfig(poc_link="import", import_tariff=t)  # noqa: E731
        assert H.digest(cfg(bare), version=version) == H.digest(cfg(stated), version=version)
        assert H.digest(cfg(usd), version=version) != H.digest(cfg(bare), version=version)
    # A full dump (what the API returns) states it.
    assert bare.model_dump(mode="json")["currency"] == "EUR"


@pytest.mark.parametrize("version", ["2026-10-05", None])
def test_the_pack_states_its_tariffs_currency(version):
    from services.library.defaults_pack.loader import load_defaults_pack

    pack = load_defaults_pack(version)
    for tid in pack.tariff_ids():
        t = pack.pack_tariff(tid)
        assert t.currency == pack.tariff_meta(tid).currency == "EUR"
        # A copy made before tariffs carried a currency is still the shipped tariff.
        old = t.model_dump(mode="json")
        old.pop("currency")
        assert pack.tariff_is_unchanged(old)


# ── a Library write needs a currency ───────────────────────────────────────

def test_a_library_tariff_with_no_currency_is_refused_until_it_states_one(client):
    r = client.put("/api/library/items/tariff/g", json={"payload": GENERIC})
    assert r.status_code == 422, r.text
    assert "currency" in r.text
    r = client.put("/api/library/items/tariff/g", json={"payload": {**GENERIC, "currency": "EUR"}})
    assert r.status_code == 200, r.text
    got = client.get("/api/library/items/tariff/g").json()
    assert got["payload"]["currency"] == "EUR"


def test_an_implied_currency_is_not_stored_twice_and_reads_back(client):
    a = client.put("/api/library/items/tariff/nl", json={"payload": _nl()}).json()
    b = client.put("/api/library/items/tariff/nl", json={"payload": _nl(currency="EUR")}).json()
    assert a == b                                   # the same content: no new version
    payload = client.get("/api/library/items/tariff/nl").json()["payload"]
    assert "currency" not in payload                # the stored form leaves it out
    assert M.Tariff.model_validate(payload).currency == "EUR"   # and a read derives it


# ── the gate ───────────────────────────────────────────────────────────────

def _project(install_network, tmp_projects_dir, currency):
    install_network(build_edge_15min(), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    PyPSAService.get_active_context().solver_state["solver_config"].finance = \
        {"currency": currency} if currency else None
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": {
        "poc_link": "import", "timezone": "Europe/Amsterdam"}})


def test_a_gbp_project_refuses_the_tennet_tariff_at_attach(client, install_network,
                                                           tmp_projects_dir):
    _project(install_network, tmp_projects_dir, "GBP")
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["attach_tariff"](name=HS)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "currency_mismatch"
    assert "EUR" in exc.value.detail["message"] and "GBP" in exc.value.detail["message"]
    assert chat_tools.DISPATCHERS["get_solver_config"]()["commercial"].get(
        "import_tariff_ref") is None                # nothing stored


def test_the_solver_config_put_refuses_the_ref_too(client, install_network, tmp_projects_dir):
    _project(install_network, tmp_projects_dir, "USD")
    ref = common.ref("tariff", HS).model_dump(mode="json")
    r = client.put("/api/simulation/solver_config", json={"commercial": {
        "poc_link": "import", "import_tariff_ref": ref}})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "currency_mismatch"


@pytest.mark.parametrize("currency", ["EUR", None])
def test_an_eur_or_unstated_project_attaches_it_and_the_pin_resolves(
        client, install_network, tmp_projects_dir, currency):
    _project(install_network, tmp_projects_dir, currency)
    out = chat_tools.DISPATCHERS["attach_tariff"](name=HS)
    assert out["import_tariff_ref"]["hash"] == common.PINNED[("tariff", HS)]
    cfg = chat_tools.DISPATCHERS["get_solver_config"]()["commercial"]
    assert cfg["import_tariff_ref"]["hash"] == common.PINNED[("tariff", HS)]
    assert cfg["import_tariff"]["currency"] == "EUR"


# ── preflight, for an inline tariff ────────────────────────────────────────

def _codes(currency, tariff):
    finance = {"currency": currency} if currency else None
    issues = validate_for_run(build_edge_15min(), SolverConfig(
        commercial={"poc_link": "import", "import_tariff": tariff}, finance=finance))
    return {i.code: i for i in issues if i.code.startswith("commercial.")}


def test_preflight_refuses_an_inline_eur_tariff_on_a_gbp_project():
    codes = _codes("GBP", _nl())
    assert codes["commercial.currency_mismatch"].severity == "error"
    assert "EUR" in codes["commercial.currency_mismatch"].message


@pytest.mark.parametrize("currency,tariff", [
    ("EUR", _nl()), (None, _nl()), ("GBP", _nl(currency="GBP")), ("GBP", GENERIC)])
def test_preflight_passes_a_matching_or_unstated_currency(currency, tariff):
    assert "commercial.currency_mismatch" not in _codes(currency, tariff)
