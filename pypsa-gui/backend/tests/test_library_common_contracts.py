"""
The shipped (`common`) Library contract templates: five illustrative EU PPA/CfD defaults
(SCU06 S2a; design `.scratch/project-workflow/integration-sensitivities.md` §2; owner
Q11 = A). Each is labelled so it cannot be mistaken for market data, cites a public
range for every number it holds, never changes the dispatch, and names no party or
asset: its placeholders are refused by the server where a contract is saved and where
it is settled.

Every expected settlement amount is written out by hand from the item's values.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from models import commercial as M
from services.commercial import contracts as K
from services.commercial import placeholders as P
from services.library import common
from services.library import items as I
from tests.fixtures.investment_case.edge_15min import build_edge_15min

NAMES = (
    "eu-cfd",
    "eu-ppa-as-consumed-btm",
    "eu-ppa-baseload",
    "eu-ppa-pay-as-produced",
    "eu-ppa-sleeved",
)
PPA_FILLED = {"seller": "Wind BV", "buyer": "site", "asset_ids": ["pv"]}
CFD_FILLED = {"generator_owner": "Wind BV", "counterparty": "site", "asset_ids": ["pv"]}
RANGE = re.compile(
    r"^range:(?P<field>\w+)=(?P<lo>\d+(?:\.\d+)?)\.\.(?P<hi>\d+(?:\.\d+)?) "
    r"(?P<unit>[^|]+)\|(?P<source>[^|]+)\|(?P<url>[^|]+)\|(?P<year>\d{4})$"
)


@pytest.fixture
def db(_auth_db):
    session = _auth_db[1]()
    yield session
    session.close()


def _raw(name: str) -> dict:
    return json.loads(
        (common.COMMON_DIR / "contract" / f"{name}.json").read_text("utf-8")
    )


def _filled(name: str, **extra) -> dict:
    payload = common.payload("contract", name)
    return {
        **payload,
        **(CFD_FILLED if payload["type"] == "cfd" else PPA_FILLED),
        **extra,
    }


def _model(name: str, **extra):
    body = _filled(name, **extra)
    return I.CONTRACT_TYPES[body["type"]].model_validate(
        {k: v for k, v in body.items() if k != "type"}
    )


# ── the five items ─────────────────────────────────────────────────────────


def test_exactly_the_five_templates_ship():
    assert tuple(common.names("contract")) == NAMES
    assert {k for k in common.PINNED if k[0] == "contract"} == {
        ("contract", n) for n in NAMES
    }


@pytest.mark.parametrize("name", NAMES)
def test_each_validates_and_its_hash_is_pinned(name):
    raw = _raw(name)
    model = I.validate_payload("contract", raw["payload"])
    digest = (
        __import__("hashlib")
        .sha256(I._canonical_bytes("contract", model, raw["payload"]))
        .hexdigest()
    )
    assert (
        digest == common.PINNED[("contract", name)] == common.ref("contract", name).hash
    )
    assert common.ref("contract", name).version == 1


@pytest.mark.parametrize("name", NAMES)
def test_a_one_byte_change_fails_the_pin(name, tmp_path, monkeypatch):
    """A shipped item is never edited in place: a changed payload no longer loads."""
    raw = _raw(name)
    key = "strike" if raw["payload"]["type"] == "cfd" else "price"
    text = json.dumps(raw).replace(
        f'"{key}": {raw["payload"][key]}', f'"{key}": {raw["payload"][key] + 1}'
    )
    assert text != json.dumps(raw)
    (tmp_path / "contract").mkdir()
    (tmp_path / "contract" / f"{name}.json").write_text(text, "utf-8")
    monkeypatch.setattr(common, "COMMON_DIR", tmp_path)
    common._items.cache_clear()
    try:
        with pytest.raises(common.CommonItemError, match="pinned hash"):
            common.names("contract")
    finally:
        monkeypatch.undo()
        common._items.cache_clear()


@pytest.mark.parametrize("name", NAMES)
def test_labelled_illustrative_never_market_data(name):
    meta = common.meta("contract", name)
    assert meta["source"] == "illustrative" and meta["scope"] == "common"
    assert meta["description"].startswith("Illustrative EU default, not market data")
    notes = meta["notes"]
    for needle in (
        "region:EU",
        "currency:EUR",
        "price_basis:nominal",
        "illustrative:true",
    ):
        assert needle in notes, needle
    assert any(re.fullmatch(r"currency_year:20\d\d", n) for n in notes)


@pytest.mark.parametrize("name", NAMES)
def test_every_numeric_value_has_a_cited_range_and_lies_inside_it(name):
    """Every number in the shipped file (the canonical payload drops a default 0.0
    indexation, so the file is read) has one range note naming its source, URL and
    year, and sits inside it."""
    ranges = {}
    for note in common.meta("contract", name)["notes"]:
        if note.startswith("range:"):
            m = RANGE.match(note)
            assert m, f"unparseable range note {note!r}"
            assert m["field"] not in ranges, f"two ranges for {m['field']}"
            assert m["source"].strip() and 2000 <= int(m["year"]) <= 2026
            assert all(u.startswith("https://") for u in m["url"].split())
            ranges[m["field"]] = (float(m["lo"]), float(m["hi"]))
    numbers = {
        k: v
        for k, v in _raw(name)["payload"].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    assert numbers and set(numbers) == set(ranges)
    canonical = {
        k
        for k, v in common.payload("contract", name).items()
        if isinstance(v, (int, float))
    }
    assert canonical <= set(numbers)
    for field, value in numbers.items():
        lo, hi = ranges[field]
        assert lo < hi and lo <= value <= hi, (field, value, lo, hi)


@pytest.mark.parametrize("name", NAMES)
def test_never_changes_dispatch(name):
    assert _raw(name)["payload"].get("changes_dispatch", False) is False
    assert getattr(_model(name), "changes_dispatch", False) is False


def test_the_forms_are_the_designs_five():
    kinds = {n: common.payload("contract", n).get("kind") for n in NAMES}
    assert kinds == {
        "eu-cfd": None,
        "eu-ppa-as-consumed-btm": "as_consumed_btm",
        "eu-ppa-baseload": "baseload",
        "eu-ppa-pay-as-produced": "pay_as_produced",
        "eu-ppa-sleeved": "sleeved",
    }
    assert common.payload("contract", "eu-ppa-baseload")["baseload_mw"] > 0
    assert common.payload("contract", "eu-ppa-sleeved")["sleeving_fee_eur_per_mwh"] > 0


# ── common scope in the Library ────────────────────────────────────────────


def test_visible_to_another_org_and_read_only(db, second_identity):
    org = second_identity["org_id"]
    refs = {r.id: r for r in I.list_items(db, org, "contract")}
    assert set(NAMES) <= set(refs)
    for name in NAMES:
        assert I.resolve(db, org, refs[name]) == common.payload("contract", name)
        assert I.item_meta(db, org, refs[name])["source"] == "illustrative"
        with pytest.raises(I.LibraryItemCommonReadonly):
            I.put_item(db, org, "contract", name, _filled(name))


def test_a_copied_contract_keeps_its_library_ref(db, second_identity):
    """The project's copy carries `library_ref`, so its illustrative source is traceable."""
    ref = common.ref("contract", "eu-ppa-pay-as-produced")
    cfg = M.CommercialConfig.model_validate(
        {
            "poc_link": "import",
            "contracts": [
                _filled(
                    "eu-ppa-pay-as-produced", library_ref=ref.model_dump(mode="json")
                )
            ],
        }
    )
    (c,) = cfg.contracts
    assert c.library_ref == ref
    assert (
        I.item_meta(db, second_identity["org_id"], c.library_ref)["source"]
        == "illustrative"
    )


# ── placeholders fail closed on the server ─────────────────────────────────


def test_the_marker_can_never_be_a_component_name():
    from services.component_names import name_problem

    for name in NAMES:
        for field in P.unfilled(
            I.validate_payload("contract", common.payload("contract", name))
        ):
            values = common.payload("contract", name)[field]
            for v in values if isinstance(values, list) else [values]:
                assert v.startswith(P.MARK) and name_problem(v) is not None


@pytest.mark.parametrize(
    "name,fields",
    [
        ("eu-ppa-pay-as-produced", ["seller", "buyer", "asset_ids"]),
        ("eu-cfd", ["asset_ids", "generator_owner", "counterparty"]),
    ],
)
def test_each_template_lists_its_placeholders(name, fields):
    model = I.validate_payload("contract", common.payload("contract", name))
    assert P.unfilled(model) == fields
    assert P.unfilled(_model(name)) == [] and P.problem(_model(name)) is None


def test_a_visible_only_marker_is_still_a_placeholder():
    model = _model("eu-ppa-pay-as-produced", seller="  Fill-In:seller")
    assert P.unfilled(model) == ["seller"]


def _put(client, contract):
    return client.put(
        "/api/simulation/solver_config",
        json={
            "commercial": {
                "poc_link": "import",
                "timezone": "UTC",
                "contracts": [contract],
            }
        },
    )


@pytest.mark.parametrize("left", ["seller", "buyer", "asset_ids"])
def test_save_is_refused_with_any_placeholder_left(left, client, install_network):
    install_network(build_edge_15min(), name="templated")
    template = common.payload("contract", "eu-ppa-pay-as-produced")
    contract = {**_filled("eu-ppa-pay-as-produced"), left: template[left]}
    r = _put(client, contract)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "contract_placeholder"
    word = P.FIELD_WORDS[left]
    assert detail["message"] == (
        f"Contract {contract['id']} still holds the Library template's fill-in in "
        f"{word}. Fill in your own, then save again."
    )


def test_save_is_accepted_once_all_three_are_replaced(
    client, install_network, session_ctx
):
    install_network(build_edge_15min(), name="templated")
    r = _put(client, _filled("eu-ppa-pay-as-produced"))
    assert r.status_code == 200, r.text
    stored = session_ctx(client).solver_state["solver_config"].commercial
    assert stored["contracts"][0]["seller"] == "Wind BV"


def test_the_binder_refuses_before_any_network_check():
    """Every route that stores a commercial config binds it here."""
    from services.commercial import binding

    cfg = M.CommercialConfig.model_validate(
        {"poc_link": "import", "contracts": [common.payload("contract", "eu-cfd")]}
    )
    with pytest.raises(binding.BindingRefusal) as exc:
        binding.bind_commercial(
            build_edge_15min(), cfg, project_dir=None, resolve_ref=lambda ref: None
        )
    assert (exc.value.status, exc.value.code) == (422, "contract_placeholder")
    assert "in Generator owner, Counterparty, Assets (generators). Fill in your own" in exc.value.message


# ── settlement with the items' values ──────────────────────────────────────

IDX = pd.date_range("2030-06-01", periods=4, freq="h")


def _inputs(cid: str, gen, ref=None) -> K.SettlementInputs:
    refs = {} if ref is None else {cid: (pd.Series(ref, index=IDX, dtype=float), [])}
    return K.SettlementInputs(
        period=None,
        index=IDX,
        weights=np.ones(len(IDX)),
        generators=pd.DataFrame({"pv": gen}, index=IDX, dtype=float),
        export_mw=pd.Series(0.0, index=IDX),
        references=refs,
        site_generators=["pv"],
    )


def test_settle_refuses_a_placeholder():
    model = _model("eu-ppa-pay-as-produced", buyer=P.MARK + "buyer")
    with pytest.raises(K.ContractError, match="the Library template's fill-in in Buyer\\."):
        K.settle(model, _inputs(model.id, [1.0] * 4))


@pytest.mark.parametrize("ref,amount", [([100.0] * 4, -60.0), ([70.0] * 4, 60.0)])
def test_the_cfd_is_two_way(ref, amount):
    """Strike 85, 1 MW for 4 h: ref 100 → the generator owner pays back 15 × 4 = 60;
    ref 70 → the counterparty pays 15 × 4 = 60."""
    cfd = _model("eu-cfd")
    (line,) = K.settle(cfd, _inputs(cfd.id, [1.0] * 4, ref))
    assert (line.payer, line.payee, line.value_stream) == (
        "site",
        "Wind BV",
        "cfd_difference",
    )
    assert line.quantity_mwh == 4.0
    assert line.amount == pytest.approx(amount, abs=1e-9)


@pytest.mark.parametrize("gen", [[10.0, 30.0, 15.0, 25.0], [0.0, 0.0, 0.0, 0.0]])
def test_baseload_settles_baseload_mw_whatever_the_profile(gen):
    """20 MW at 78 against ref 70, 90, 50, 80: (8 − 12 + 28 − 2) × 20 = 440, the same
    whether the asset under- or over-produces the block (the shape risk stays with the
    holder of the physical output)."""
    ppa = _model("eu-ppa-baseload")
    (line,) = K.settle(ppa, _inputs(ppa.id, gen, [70.0, 90.0, 50.0, 80.0]))
    assert (line.payer, line.payee) == ("site", "Wind BV")
    assert line.quantity_mwh == 80.0
    assert line.amount == pytest.approx(440.0, abs=1e-9)


def test_the_sleeving_fee_is_a_cost_to_the_buyer():
    """55 €/MWh and a 3 €/MWh fee on 10 + 20 + 0 + 5 = 35 MWh: 1925 to the seller and
    105 to the sleeving party, both paid by the buyer (the site)."""
    ppa = _model("eu-ppa-sleeved", sleeving_party="Utility")
    lines = {
        ln.value_stream: ln
        for ln in K.settle(ppa, _inputs(ppa.id, [10.0, 20.0, 0.0, 5.0]))
    }
    energy, fee = lines["ppa_energy"], lines["ppa_sleeving_fee"]
    assert (energy.payer, energy.payee, energy.amount) == (
        "site",
        "Wind BV",
        pytest.approx(1925.0),
    )
    assert (fee.payer, fee.payee, fee.quantity_mwh) == ("site", "Utility", 35.0)
    assert fee.amount == pytest.approx(105.0, abs=1e-9) and fee.flags == []
