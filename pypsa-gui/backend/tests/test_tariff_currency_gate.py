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
* Plan verdict GK B (27), 2026-11-06, conditions C1-C8 (issue 01 ## Comments):
  the map is frozen (C1), an API read states the resolved currency (C2), every
  stored or shipped form is byte-identical to master (C3,
  `fixtures/tariff_currency_pins.json`, made on master dc6223c9e), both orders of
  attach and finance change are caught (C5), no template or guided study trips
  the gate (C6), and a refused PUT changes nothing (C7).
* `currency_mismatch` (nothing converted) when the tariff's currency is not the
  project's stated finance currency: at attach (`attach_tariff`, the
  solver-config PUT's `import_tariff_ref`) and in preflight for an inline tariff.
  An unstated currency on either side is not checked.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

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
GENERIC = {
    "id": "g",
    "name": "Generic",
    "jurisdiction": "generic",
    "valid_from": "2030-01-01",
    "items": [
        {
            "id": "e",
            "kind": "energy",
            "unit": "per_kwh",
            "periods": [{"name": "all", "rate": 0.1}],
        }
    ],
}


def _nl(**over):
    return {**GENERIC, "id": "nl", "jurisdiction": "NL", **over}


# ── the model ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "jurisdiction,currency",
    [
        ("NL", "EUR"),
        ("DE", "EUR"),
        ("GB", "GBP"),
        ("US", "USD"),
        ("US-CA", "USD"),
        ("CA_ON", "CAD"),
        ("generic", None),
    ],
)
def test_an_unstated_currency_is_the_jurisdictions(jurisdiction, currency):
    assert M.Tariff.model_validate(_nl(jurisdiction=jurisdiction)).currency == currency


def test_the_jurisdiction_map_is_frozen():
    """C1: a stored tariff that states no currency reads as this map implies; an entry
    change is a hash-recipe change, never an in-place edit."""
    euro = (
        "AT",
        "BE",
        "CY",
        "DE",
        "EE",
        "ES",
        "FI",
        "FR",
        "GR",
        "HR",
        "IE",
        "IT",
        "LT",
        "LU",
        "LV",
        "MT",
        "NL",
        "PT",
        "SI",
        "SK",
        "EU",
    )
    assert M.JURISDICTION_CURRENCY == {
        **{c: "EUR" for c in euro},
        "GB": "GBP",
        "UK": "GBP",
        "US": "USD",
        "CA": "CAD",
        "CH": "CHF",
        "AU": "AUD",
        "NZ": "NZD",
        "DK": "DKK",
        "SE": "SEK",
        "NO": "NOK",
        "PL": "PLN",
        "CZ": "CZK",
        "HU": "HUF",
        "RO": "RON",
        "JP": "JPY",
    }
    assert M.JURISDICTION_CURRENCY_CHANGES == {"BG": (date(2026, 1, 1), "BGN", "EUR")}


@pytest.mark.parametrize(
    "valid_from,currency",
    [("2025-12-31", "BGN"), ("2026-01-01", "EUR"), ("2030-06-01", "EUR")],
)
def test_bulgaria_reads_on_the_tariffs_start(valid_from, currency):
    t = M.Tariff.model_validate(_nl(jurisdiction="BG", valid_from=valid_from))
    assert t.currency == currency
    assert "currency" not in t.model_dump(mode="json")  # implied: outside every dump


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
    bare, stated, usd = (
        M.Tariff.model_validate(p)
        for p in (_nl(), _nl(currency="EUR"), _nl(currency="USD"))
    )
    assert H.library_item_digest(bare) == H.library_item_digest(stated)
    assert H.library_item_digest(usd) != H.library_item_digest(bare)
    for version in (1, 2):
        cfg = lambda t: M.CommercialConfig(poc_link="import", import_tariff=t)  # noqa: E731
        assert H.digest(cfg(bare), version=version) == H.digest(
            cfg(stated), version=version
        )
        assert H.digest(cfg(usd), version=version) != H.digest(
            cfg(bare), version=version
        )
    # C2/C3: every dump leaves the implied one out; an API read states it.
    assert "currency" not in bare.model_dump(mode="json")
    assert (
        bare.model_dump(mode="json", context=M.RESOLVED_CURRENCY)["currency"] == "EUR"
    )
    assert usd.model_dump(mode="json")["currency"] == "USD"
    assert M.tariff_payload_resolved(_nl())["currency"] == "EUR"
    assert "currency" not in M.tariff_payload_resolved(GENERIC)


def _forms(t: M.Tariff) -> dict:
    def h(x):
        return hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()[:16]

    cfg = M.CommercialConfig(poc_link="import", import_tariff=t)
    return {
        "dump": h(t.model_dump(mode="json")),
        "lib": H.library_item_digest(t)[:16],
        "v1": H.digest(cfg, version=1),
        "v2": H.digest(cfg, version=2),
        "stored_cfg": h(cfg.model_dump(mode="json")),
    }


def _shipped_and_fixture_tariffs() -> dict[str, M.Tariff]:
    """Every pack tariff, common item, tariff fixture and URDB oracle import (C3)."""
    from services.library.urdb import urdb_to_tariff

    root, out = Path(__file__).resolve().parents[1], {}

    def take(key, payload):
        try:
            out[key] = M.Tariff.model_validate(payload)
        except (ValueError, TypeError, AttributeError):
            pass

    for p in sorted((root / "services/library").rglob("*.json")):
        raw, rel = json.loads(p.read_text()), p.relative_to(root).as_posix()
        if isinstance(raw, dict) and isinstance(raw.get("tariffs"), list):
            for e in raw["tariffs"]:
                take(f"{rel}:{e['tariff']['id']}", e["tariff"])
        elif isinstance(raw, dict) and "payload" in raw:
            take(rel, raw["payload"])
    for p in sorted((root / "tests/fixtures").rglob("*.json")):
        raw, rel = json.loads(p.read_text()), p.relative_to(root).as_posix()
        if isinstance(raw, dict):
            for k in ("tariff", "import_tariff", None):
                take(f"{rel}:{k}", raw.get(k) if k else raw)
    for p in sorted(
        (root / "tests/fixtures/investment_case/oracles").glob("*.urdb.json")
    ):
        t, _r, _n = urdb_to_tariff(
            json.loads(p.read_text()),
            name="x",
            accept_partial=True,
            valid_from=date(2024, 1, 1),
        )
        out[f"{p.relative_to(root).as_posix()}:urdb"] = t
    return out


def test_every_stored_and_shipped_form_is_byte_identical_to_master():
    """C3: the dump, the Library hash, hash recipes 1 and 2 and the stored commercial
    config of every shipped and fixture tariff equal master's (pins made on dc6223c9e;
    the 2026-11-06 pack added on 482bb6e1, 2026-11-07 with DC-05)."""
    pins = json.loads(
        (Path(__file__).parent / "fixtures/tariff_currency_pins.json").read_text()
    )
    now = {k: _forms(t) for k, t in _shipped_and_fixture_tariffs().items()}
    assert set(now) == set(pins)
    assert {k for k in pins if now[k] != pins[k]} == set()


def test_a_pack_tariff_is_in_its_meta_currency_or_states_none():
    """A `generic` pack tariff keeps no currency: its copies stay byte-identical (C3); the
    gap is issue 03."""
    from services.library.defaults_pack.loader import (
        available_versions,
        load_defaults_pack,
    )

    generic = set()
    for version in available_versions():
        pack = load_defaults_pack(version)
        for tid in pack.tariff_ids():
            t = pack.pack_tariff(tid)
            assert t.currency in (None, pack.tariff_meta(tid).currency), (version, tid)
            if t.currency is None:
                generic.add(tid)
    assert generic == {"tou_reference_illustrative"}


# ── a Library write needs a currency ───────────────────────────────────────


def test_a_library_tariff_with_no_currency_is_refused_until_it_states_one(client):
    r = client.put("/api/library/items/tariff/g", json={"payload": GENERIC})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "tariff_currency_required"
    # C4: under the field the tariff builder can fix, in words naming the currency.
    assert detail["errors"] == [{"loc": ["currency"], "msg": detail["message"]}]
    assert detail["message"] == (
        "\u201cgeneric\u201d names no country, so the tariff's "
        "currency is unknown. Use the country's two-letter code, "
        "such as NL, GB or US, or state the currency."
    )
    r = client.put(
        "/api/library/items/tariff/g", json={"payload": {**GENERIC, "currency": "EUR"}}
    )
    assert r.status_code == 200, r.text
    got = client.get("/api/library/items/tariff/g").json()
    assert got["payload"]["currency"] == "EUR"


def test_the_chat_tool_refusal_is_typed_and_names_the_currency_too(
    client, install_network, tmp_projects_dir
):
    _project(install_network, tmp_projects_dir, None)
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["put_library_item"](
            kind="tariff", name="g", payload=GENERIC
        )
    assert exc.value.status_code == 422
    assert exc.value.detail["error_kind"] == "tariff_currency_required"
    assert "payload.currency" in exc.value.detail["message"]
    out = chat_tools.DISPATCHERS["put_library_item"](
        kind="tariff", name="g", payload={**GENERIC, "currency": "EUR"}
    )
    assert out["id"] == "g"


def test_an_implied_currency_is_not_stored_twice_and_reads_back(client):
    a = client.put("/api/library/items/tariff/nl", json={"payload": _nl()}).json()
    b = client.put(
        "/api/library/items/tariff/nl", json={"payload": _nl(currency="EUR")}
    ).json()
    assert a == b  # the same content: no new version
    assert (
        client.get("/api/library/items/tariff/nl").json()["payload"]["currency"]
        == "EUR"
    )
    assert (
        client.get(f"/api/library/items/tariff/{HS}").json()["payload"]["currency"]
        == "EUR"
    )


# ── the gate ───────────────────────────────────────────────────────────────


def _project(install_network, tmp_projects_dir, currency):
    install_network(build_edge_15min(), name="P")
    (tmp_projects_dir / "P").mkdir(parents=True, exist_ok=True)
    (tmp_projects_dir / "P" / "network.nc").write_bytes(b"")
    PyPSAService.get_active_context().solver_state["solver_config"].finance = (
        {"currency": currency} if currency else None
    )
    chat_tools.DISPATCHERS["update_solver_config"](
        partial={"commercial": {"poc_link": "import", "timezone": "Europe/Amsterdam"}}
    )


def test_a_gbp_project_refuses_the_tennet_tariff_at_attach(
    client, install_network, tmp_projects_dir
):
    _project(install_network, tmp_projects_dir, "GBP")
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["attach_tariff"](name=HS)
    assert exc.value.status_code == 409
    assert exc.value.detail["error_kind"] == "currency_mismatch"
    assert exc.value.detail["message"].startswith(
        f"Tariff \u201c{HS}\u201d is priced in EUR; "
    )
    assert "GBP" in exc.value.detail["message"]
    assert (
        chat_tools.DISPATCHERS["get_solver_config"]()["commercial"].get(
            "import_tariff_ref"
        )
        is None
    )  # nothing stored


def test_the_solver_config_put_refuses_the_ref_and_changes_nothing(
    client, install_network, tmp_projects_dir
):
    """C7: a refused PUT leaves the stored config and the network revision as they were."""
    _project(install_network, tmp_projects_dir, "USD")
    ctx = PyPSAService.get_active_context()
    before = json.dumps(
        chat_tools.DISPATCHERS["get_solver_config"](), sort_keys=True, default=str
    )
    revision = ctx.network_revision
    ref = common.ref("tariff", HS).model_dump(mode="json")
    # The route's own handler, in process: the context the chat tools read.
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["update_solver_config"](
            partial={"commercial": {"poc_link": "import", "import_tariff_ref": ref}}
        )
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "currency_mismatch"
    assert (
        json.dumps(
            chat_tools.DISPATCHERS["get_solver_config"](), sort_keys=True, default=str
        )
        == before
    )
    assert ctx.network_revision == revision


def test_a_finance_currency_change_after_attach_is_caught_in_preflight(
    client, install_network, tmp_projects_dir
):
    """C5, the other order: attach in EUR, then the project moves to GBP."""
    _project(install_network, tmp_projects_dir, "EUR")
    chat_tools.DISPATCHERS["attach_tariff"](name=HS)
    ctx = PyPSAService.get_active_context()
    cfg = ctx.solver_state["solver_config"]
    assert "commercial.currency_mismatch" not in {
        i.code for i in validate_for_run(ctx.network, cfg)
    }
    cfg.finance = {"currency": "GBP"}
    issues = {i.code: i for i in validate_for_run(ctx.network, cfg)}
    assert issues["commercial.currency_mismatch"].severity == "error"
    # GK B: an unrelated commercial edit (saveCommercial echoes the stored ref) still
    # saves; the unchanged ref is left to preflight.
    echo = chat_tools.DISPATCHERS["get_solver_config"]()["commercial"]
    echo.pop("value_flows", None)
    out = chat_tools.DISPATCHERS["update_solver_config"](
        partial={"commercial": {**echo, "timezone": "Europe/Berlin"}}
    )
    assert out["commercial"]["timezone"] == "Europe/Berlin"
    assert (
        out["commercial"]["import_tariff_ref"]["hash"] == common.PINNED[("tariff", HS)]
    )
    cfg = ctx.solver_state["solver_config"]
    assert "commercial.currency_mismatch" in {
        i.code for i in validate_for_run(ctx.network, cfg)
    }
    # A newly attached ref is still refused.
    with pytest.raises(HTTPException) as exc:
        chat_tools.DISPATCHERS["attach_tariff"](name="nl-tennet-2026-ehs")
    assert exc.value.detail["error_kind"] == "currency_mismatch"


def test_a_no_op_echo_of_the_read_config_keeps_the_fingerprint(
    client, install_network, tmp_projects_dir
):
    """C2 x C3: the read states the currency; sending it back stores the same bytes."""
    from services.workflow_tools import _network_input_fingerprint

    _project(install_network, tmp_projects_dir, "EUR")
    chat_tools.DISPATCHERS["update_solver_config"](
        partial={
            "commercial": {
                "poc_link": "import",
                "timezone": "Europe/Amsterdam",
                "import_tariff": _nl(),
            }
        }
    )
    ctx = PyPSAService.get_active_context()
    stored = ctx.solver_state["solver_config"].commercial["import_tariff"]
    assert "currency" not in stored
    fp = _network_input_fingerprint(ctx)
    read = chat_tools.DISPATCHERS["get_solver_config"]()["commercial"]
    assert read["import_tariff"]["currency"] == "EUR"
    # The PUT answers in the read form too.
    assert (
        chat_tools.DISPATCHERS["update_solver_config"](partial={"voll": 10_000.0})[
            "commercial"
        ]["import_tariff"]["currency"]
        == "EUR"
    )
    fp = _network_input_fingerprint(ctx)
    read.pop("value_flows", None)
    chat_tools.DISPATCHERS["update_solver_config"](partial={"commercial": read})
    assert ctx.solver_state["solver_config"].commercial["import_tariff"] == stored
    assert _network_input_fingerprint(ctx) == fp


@pytest.mark.parametrize("currency", ["EUR", None])
def test_an_eur_or_unstated_project_attaches_it_and_the_pin_resolves(
    client, install_network, tmp_projects_dir, currency
):
    _project(install_network, tmp_projects_dir, currency)
    out = chat_tools.DISPATCHERS["attach_tariff"](name=HS)
    assert out["import_tariff_ref"]["hash"] == common.PINNED[("tariff", HS)]
    cfg = chat_tools.DISPATCHERS["get_solver_config"]()["commercial"]
    assert cfg["import_tariff_ref"]["hash"] == common.PINNED[("tariff", HS)]
    assert cfg["import_tariff"]["currency"] == "EUR"


# ── preflight, for an inline tariff ────────────────────────────────────────


def _codes(currency, tariff):
    finance = {"currency": currency} if currency else None
    issues = validate_for_run(
        build_edge_15min(),
        SolverConfig(
            commercial={"poc_link": "import", "import_tariff": tariff}, finance=finance
        ),
    )
    return {i.code: i for i in issues if i.code.startswith("commercial.")}


def test_preflight_refuses_an_inline_eur_tariff_on_a_gbp_project():
    codes = _codes("GBP", _nl())
    assert codes["commercial.currency_mismatch"].severity == "error"
    assert "EUR" in codes["commercial.currency_mismatch"].message
    # The other findings still run beside it.
    late = _codes("GBP", _nl(valid_from="2099-01-01"))
    assert {"commercial.currency_mismatch", "commercial.tariff_out_of_validity"} <= set(
        late
    )


@pytest.mark.parametrize(
    "currency,tariff",
    [("EUR", _nl()), (None, _nl()), ("GBP", _nl(currency="GBP")), ("GBP", GENERIC)],
)
def test_preflight_passes_a_matching_or_unstated_currency(currency, tariff):
    assert "commercial.currency_mismatch" not in _codes(currency, tariff)


# ── no shipped template or guided study trips the gate (C6) ────────────────


def _templates():
    root = Path(__file__).resolve().parents[1] / "project_templates"
    return sorted(p for p in root.glob("*/solver_config.json"))


@pytest.mark.parametrize("path", _templates(), ids=lambda p: p.parent.name)
def test_no_shipped_template_trips_the_currency_gate(path):
    """Each template's tariff and finance currency through preflight (on the edge
    fixture network, so the template's PoC naming is not what is tested)."""
    raw = json.loads(path.read_text())
    tariff = (raw.get("commercial") or {}).get("import_tariff") or _nl(
        jurisdiction="DE"
    )
    assert "commercial.currency_mismatch" not in _codes(
        (raw.get("finance") or {}).get("currency"), tariff
    )


def test_no_guided_study_trips_the_currency_gate():
    """A guided study's finance is `FinanceInputs` dumped whole (currency stated, EUR)
    and its tariff a pack copy (`compile.commercial_from_ledger`)."""
    from models.finance import FinanceInputs
    from services.library.defaults_pack.loader import (
        available_versions,
        load_defaults_pack,
    )

    currency = FinanceInputs.model_fields["currency"].default
    for version in available_versions():
        pack = load_defaults_pack(version)
        for tid in pack.tariff_ids():
            tariff = pack.pack_tariff(tid).model_dump(mode="json")
            assert "commercial.currency_mismatch" not in _codes(currency, tariff), (
                version,
                tid,
            )


def test_the_builders_copy_of_the_jurisdiction_map_matches_the_backend():
    """`tariffModel.ts` shows the implied currency as the currency input's default."""
    import re
    from pathlib import Path

    src = (Path(__file__).parents[2] / "frontend/src/pages/results/investment/tariffModel.ts"
           ).read_text()
    euro = re.search(r"const EURO = \[(.*?)\]", src, re.S).group(1)
    got = {c: "EUR" for c in re.findall(r"'([A-Z]{2})'", euro)}
    body = src.split("export const JURISDICTION_CURRENCY", 1)[1].split("\n}", 1)[0]
    got.update(re.findall(r"\b([A-Z]{2}): '([A-Z]{3})'", body))
    assert got == M.JURISDICTION_CURRENCY
