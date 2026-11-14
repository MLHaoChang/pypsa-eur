"""
Business case 09: the line table's read-only options route (spec §4, §6.1; G9) and
`models.business_case.allowed_units`, the one "units by source" rule the table's selects
read, pinned to the validator's `unit_not_allowed`.
"""

from __future__ import annotations

import pytest
from typing import get_args

import models.business_case as M
from tests.conftest import build_network
from tests.test_business_case_routes import _bc, _line, _put, _with_ic

OPTIONS_URL = "/api/results/business_case/options"
H, BASE = (2030, 2040), 2030


# ── allowed_units: the select's options equal what the validator accepts ─────


def _case_line(kind: str, source: str, unit: str) -> M.LineItem:
    cat = M.CATEGORIES[kind][0]
    kw: dict = dict(name="x", kind=kind, category=cat, unit=unit, escalation="opex")
    if source == "share_of":
        # The base is a by-product line, so the probe never reads itself (G6).
        kw.update(quantity_source="share_of:revenue/by_product", quantity=0.5)
    else:
        kw.update(quantity=1.0, price=1.0)
    if kind == "capex":
        kw["life_years"] = 10
    return M.LineItem(**kw)


@pytest.mark.parametrize("kind", ["revenue", "opex", "capex"])
@pytest.mark.parametrize("source", ["typed", "share_of"])
def test_allowed_units_equal_the_units_the_validator_accepts(kind, source):
    base = ([_case_line("revenue", "typed", "t").model_copy(update={"category": "by_product"})]
            if source == "share_of" else [])
    accepted = []
    for unit in get_args(M.Unit):
        try:
            ln = _case_line(kind, source, unit)
        except ValueError:
            continue
        lines = base + [ln.model_copy(update={"name": "probe"})]
        found = M.BusinessCaseInputs(lines=lines).refusals(horizon=H, base_year=BASE)
        if not any(r.kind == "unit_not_allowed" for r in found):
            accepted.append(unit)
    assert list(M.allowed_units(kind, source)) == accepted


def test_allowed_units_of_a_linked_line_are_its_outputs_units():
    assert M.allowed_units("revenue", "linked", ("MWh", "t")) == ("MWh", "t")
    assert M.allowed_units("revenue", "linked", ()) == ()


# ── the route ────────────────────────────────────────────────────────────────


def _project(client, install_network, monkeypatch, template):
    import routers.results as R

    install_network(build_network())
    monkeypatch.setattr(R, "_active_template_meta",
                        lambda: {"id": template} if template else None)


def test_no_template_is_a_blank_table_with_the_typed_units(
    client, install_network, monkeypatch
):
    _project(client, install_network, monkeypatch, None)
    r = client.get(OPTIONS_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["template_id"] is None and body["variants"] == {}
    assert body["offered_rows"] == []
    assert body["typed_units"] == {
        k: list(M.allowed_units(k, "typed")) for k in ("revenue", "opex", "capex")
    }
    assert "lump" in body["typed_units"]["capex"]
    assert "lump" not in body["typed_units"]["revenue"]
    assert isinstance(body["outputs"], list) and body["outputs_reason"] is None
    assert body["refusals"] == []          # nothing stored, nothing refused


def test_a_template_lists_its_variants_and_offered_rows(
    client, install_network, monkeypatch
):
    from services.business_case import presets as P

    _project(client, install_network, monkeypatch, "eh_datacenter")
    body = client.get(OPTIONS_URL).json()
    assert set(body["variants"]) == {"gpu_cloud", "colocation"}
    assert body["offered_rows"] == [
        o.model_dump(mode="json") for o in P.offered_rows("eh_datacenter")
    ]


def test_outputs_are_the_catalogue_the_run_reads(client, install_network, monkeypatch):
    from services.business_case import linked_outputs as L

    _project(client, install_network, monkeypatch, None)
    spec = L.OutputSpec("served_product.h2", "Product served on h2", ("MWh_H2", "kg"))
    seen = {}

    def fake(n, solve=None):
        seen["solve"] = solve
        return [spec]

    monkeypatch.setattr(L, "catalogue", fake)
    body = client.get(OPTIONS_URL).json()
    assert body["outputs"] == [{"id": "served_product.h2", "label": "Product served on h2",
                                "units": ["MWh_H2", "kg"], "derived": False}]
    assert isinstance(seen["solve"], L.LinkedSolve)   # bound as the run binds it


def test_unreadable_outputs_are_a_reason_never_a_500(client, install_network, monkeypatch):
    from services.business_case import linked_outputs as L

    _project(client, install_network, monkeypatch, None)

    def boom(n, solve=None):
        raise RuntimeError("bad network")

    monkeypatch.setattr(L, "catalogue", boom)
    body = client.get(OPTIONS_URL).json()
    assert body["outputs"] is None and body["outputs_reason"] == "outputs_not_readable"


def test_a_busy_network_is_a_reason(client, install_network, monkeypatch):
    import routers.results as R
    from services.pypsa_service import PyPSAService

    _project(client, install_network, monkeypatch, None)

    class Busy:
        def acquire(self, timeout=None):
            return False

    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(lambda: Busy()))
    monkeypatch.setattr(R, "_IC_LOCK_WAIT_S", 0.0)
    body = client.get(OPTIONS_URL).json()
    assert body["outputs"] is None and body["outputs_reason"] == "network_busy"


def test_refusals_need_the_investment_case_horizon(client, install_network, monkeypatch):
    _project(client, install_network, monkeypatch, None)
    assert _put(client, _bc(_line(), _line(price=None))).status_code == 200
    body = client.get(OPTIONS_URL).json()
    assert body["refusals"] is None
    assert body["refusals_reason"] == "investment_case_missing"


def test_every_refusal_names_its_line_and_column(client, install_network, monkeypatch):
    """G9: all of them, in the validator's order, not only the first the run raises."""
    _with_ic(client, install_network, monkeypatch)
    lines = (_line(), _line(price=None), _line(name="fee", unit="share"))
    assert _put(client, _bc(*lines)).status_code == 200
    body = client.get(OPTIONS_URL).json()
    assert body["refusals_reason"] is None
    got = [(r["refusal"], r["field"]) for r in body["refusals"]]
    assert ("name_duplicate", "lines[1].name") in got
    assert ("price_not_established", "lines[1].price") in got
    assert ("unit_not_allowed", "lines[2].unit") in got
    assert all(r["code"] == "business_case_refused" and r["message"]
               for r in body["refusals"])
