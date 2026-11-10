"""gridspine-library-eu 05: an API read of a tariff shows the currency the gate reads.

A copied `generic` pack tariff states no currency; the currency gate reads EUR from its
`pack_hash` stamp (`binding.resolved_tariff_currency`, issue 03). The resolved payload
shows that currency and says it came from the stamp. Nothing is written onto the
tariff: the stored payload and every hash stay as they are.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict

import pytest

from models import commercial as M
from services.commercial import binding as B
from services.commercial import hashing as H
from services.commercial import stamp_currency  # noqa: F401  (the app registers it)
from tests.fixtures.investment_case.edge_15min import build_edge_15min

GEN = "tou_reference_illustrative"


def _generic_copy_payload() -> dict:
    from services.library.defaults_pack.loader import load_defaults_pack

    tariff = load_defaults_pack().pack_tariff(GEN)
    assert tariff.currency is None and tariff.pack_hash  # a stamped copy stating none
    return tariff.model_dump(mode="json")


def _hashes(payload: dict) -> dict:
    t = M.Tariff.model_validate(payload)
    cfg = M.CommercialConfig(poc_link="import", import_tariff=t)
    return {
        "lib": H.library_item_digest(t),
        "v1": H.digest(cfg, version=1),
        "v2": H.digest(cfg, version=2),
        "dump": t.model_dump(mode="json"),
    }


def test_a_copied_generic_tariff_reads_eur_from_its_stamp_and_nothing_is_written():
    stored = _generic_copy_payload()
    before, hashes = copy.deepcopy(stored), _hashes(stored)

    out = B.tariff_payload_read(stored)

    assert out["currency"] == "EUR"
    assert out["currency_source"] == "stamp"
    # Nothing is written onto the tariff: the stored payload and its hashes are unchanged.
    assert stored == before and "currency" not in stored
    assert _hashes(stored) == hashes


def test_a_tariff_that_states_a_currency_shows_that_one():
    stored = {**_generic_copy_payload(), "currency": "USD"}
    out = B.tariff_payload_read(stored)
    assert out["currency"] == "USD"
    assert "currency_source" not in out


def test_an_implied_currency_and_an_unstamped_generic_tariff_are_as_before():
    stored = _generic_copy_payload()
    nl = {**stored, "jurisdiction": "NL", "pack_hash": None}
    assert B.tariff_payload_read(nl) == {**nl, "currency": "EUR"}
    bare = {**stored, "pack_hash": None}
    assert B.tariff_payload_read(bare) == bare


def _break_pack(monkeypatch, exc_name):
    from services.library.defaults_pack import loader

    exc = RuntimeError if exc_name == "RuntimeError" else loader.DefaultsPackError

    def boom(*a, **k):
        raise exc("broken pack")

    monkeypatch.setattr(loader, "load_defaults_pack", boom)
    return exc


@pytest.mark.parametrize("exc_name", ["RuntimeError", "DefaultsPackError"])
def test_a_broken_pack_degrades_the_read_and_anything_else_propagates(
    monkeypatch, exc_name
):
    """GK A G5-2: a display read never 500s on the pack's own error; it shows no
    currency and says why. Any other error propagates (#1290 F1: inputs first)."""
    stored = _generic_copy_payload()
    exc = _break_pack(monkeypatch, exc_name)
    if exc_name == "RuntimeError":
        with pytest.raises(exc):
            B.tariff_payload_read(stored)
        return
    out = B.tariff_payload_read(stored)
    assert out == {**stored, "currency_source": "pack_unreadable"}
    assert "currency" not in out


# ── the write side: a read sent back writes nothing (GK A G5-1) ───────────────


def test_a_stamp_read_sent_back_drops_the_echo_and_a_stated_currency_stays():
    stored = _generic_copy_payload()
    read = B.tariff_payload_read(stored)
    # The echo: same bytes and hashes as the stored tariff.
    assert _hashes(read) == _hashes(stored)
    assert M.Tariff.model_validate(read).currency is None
    # A currency that differs from the stamp's is stated content.
    usd = {**read, "currency": "USD"}
    assert M.Tariff.model_validate(usd).currency == "USD"
    assert _hashes(usd)["lib"] != _hashes(stored)["lib"]
    # No marker: an explicit EUR states it (content, as before).
    explicit = {**stored, "currency": "EUR"}
    assert M.Tariff.model_validate(explicit).currency == "EUR"
    assert _hashes(explicit)["lib"] != _hashes(stored)["lib"]
    # Any other marker is dropped and states nothing.
    unread = {**stored, "currency_source": "pack_unreadable"}
    assert _hashes(unread) == _hashes(stored)


@pytest.mark.parametrize("exc_name", ["RuntimeError", "DefaultsPackError"])
def test_a_marked_write_with_a_broken_pack_is_refused_typed(monkeypatch, exc_name):
    from pydantic import ValidationError

    read = B.tariff_payload_read(_generic_copy_payload())
    exc = _break_pack(monkeypatch, exc_name)
    if exc_name == "RuntimeError":
        with pytest.raises(exc):
            M.Tariff.model_validate(read)
        return
    with pytest.raises(ValidationError) as info:
        M.Tariff.model_validate(read)
    err = info.value.errors()[0]
    assert err["type"] == "tariff_stamp_unreadable"
    assert read["pack_hash"] in err["msg"]


def test_the_app_registers_the_stamp_lookup_and_none_refuses(monkeypatch):
    """Models hold no service imports, so the app registers the lookup; a marked write
    with none registered is refused, never stored with the currency."""
    import importlib

    from pydantic import ValidationError

    import main  # noqa: F401

    assert M._STAMP_CURRENCY == (
        stamp_currency.stamp_currency,
        importlib.import_module(
            "services.library.defaults_pack.loader"
        ).DefaultsPackError,
    )
    read = B.tariff_payload_read(_generic_copy_payload())
    monkeypatch.setattr(M, "_STAMP_CURRENCY", None)
    with pytest.raises(ValidationError) as info:
        M.Tariff.model_validate(read)
    assert info.value.errors()[0]["type"] == "tariff_stamp_unreadable"


# ── the routes (GK A G5-1, G5-3) ──────────────────────────────────────────────


def _install(client, install_network):
    install_network(build_edge_15min(), name="gle05")
    r = client.post("/api/projects/gle05", params={"force": True, "rebind": True})
    assert r.status_code == 200, r.text


def _put_tariff(client, tariff: dict):
    return client.put(
        "/api/simulation/solver_config",
        json={
            "commercial": {
                "poc_link": "import",
                "timezone": "UTC",
                "import_tariff": tariff,
            }
        },
    )


def _stored(session_ctx, client) -> tuple[str, str, str, str]:
    from services.workflow_tools import _network_input_fingerprint

    ctx = session_ctx(client)
    commercial = ctx.solver_state["solver_config"].commercial
    commercial = commercial if isinstance(commercial, dict) else asdict(commercial)
    cfg = M.CommercialConfig.model_validate(commercial)
    return (
        json.dumps(commercial, sort_keys=True, default=str),
        _network_input_fingerprint(ctx),
        H.digest(cfg, version=1),
        H.digest(cfg, version=2),
    )


def _save_commercial(client):
    """The frontend's `saveCommercial`: GET the config, PUT its commercial back."""
    commercial = dict(client.get("/api/simulation/solver_config").json()["commercial"])
    commercial.pop("value_flows", None)
    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})
    assert r.status_code == 200, r.text
    return commercial


def test_get_then_put_solver_config_stores_the_same_bytes(
    client, install_network, session_ctx
):
    _install(client, install_network)
    assert _put_tariff(client, _generic_copy_payload()).status_code == 200
    before = _stored(session_ctx, client)

    sent = _save_commercial(client)

    assert sent["import_tariff"]["currency"] == "EUR"
    assert sent["import_tariff"]["currency_source"] == "stamp"
    assert _stored(session_ctx, client) == before


@pytest.mark.parametrize("case", ["stamp", "stated", "implied", "generic_no_stamp"])
def test_both_route_readers_show_the_resolved_currency(client, install_network, case):
    """G5-3: GET /solver_config and GET /library item read alike, and each case
    round-trips to the same Library hash."""
    base = _generic_copy_payload()
    tariff = {
        "stamp": base,
        "stated": {**base, "currency": "USD"},
        "implied": {**base, "jurisdiction": "NL", "pack_hash": None},
        "generic_no_stamp": {**base, "pack_hash": None},
    }[case]
    want = {
        "stamp": ("EUR", "stamp"),
        "stated": ("USD", None),
        "implied": ("EUR", None),
        "generic_no_stamp": (None, None),
    }[case]

    _install(client, install_network)
    assert _put_tariff(client, tariff).status_code == 200
    got = client.get("/api/simulation/solver_config").json()["commercial"][
        "import_tariff"
    ]
    assert (got.get("currency"), got.get("currency_source")) == want

    if case in ("stamp", "generic_no_stamp"):
        return  # a Library write refuses a tariff with no currency (rule 01): below
    ref = client.put("/api/library/items/tariff/gle05", json={"payload": tariff}).json()
    item = client.get("/api/library/items/tariff/gle05").json()["payload"]
    assert (item.get("currency"), item.get("currency_source")) == want
    again = client.put("/api/library/items/tariff/gle05", json={"payload": item}).json()
    assert (again["version"], again["hash"]) == (ref["version"], ref["hash"])


def test_a_stored_stamped_library_item_read_and_put_back_stores_nothing(
    client, monkeypatch
):
    """A generic pack copy stored before rule 01 (no currency): its read shows EUR from
    the stamp, and sending that read back neither states EUR nor stores a version."""
    stored = _generic_copy_payload()
    with monkeypatch.context() as m:
        # Seed it as it was stored before the currency rule: currency left out.
        m.setattr(M, "jurisdiction_currency", lambda *a, **k: "EUR")
        ref = client.put("/api/library/items/tariff/legacy", json={"payload": stored})
        assert ref.status_code == 200, ref.text
        ref = ref.json()
    item = client.get("/api/library/items/tariff/legacy").json()["payload"]
    assert (item["currency"], item["currency_source"]) == ("EUR", "stamp")

    r = client.put("/api/library/items/tariff/legacy", json={"payload": item})

    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "tariff_currency_required"
    latest = client.get("/api/library/items/tariff").json()
    assert {
        (x["id"], x["version"], x["hash"]) for x in latest if x["id"] == "legacy"
    } == {("legacy", ref["version"], ref["hash"])}


def test_a_marked_solver_config_put_with_a_broken_pack_is_a_typed_422(
    client, install_network, session_ctx, monkeypatch
):
    _install(client, install_network)
    assert _put_tariff(client, _generic_copy_payload()).status_code == 200
    commercial = dict(client.get("/api/simulation/solver_config").json()["commercial"])
    commercial.pop("value_flows", None)
    before = _stored(session_ctx, client)
    _break_pack(monkeypatch, "DefaultsPackError")

    r = client.put("/api/simulation/solver_config", json={"commercial": commercial})

    assert r.status_code == 422, r.text
    assert "tariff_stamp_unreadable" in r.text
    monkeypatch.undo()
    assert _stored(session_ctx, client) == before
