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
    base = (
        [
            _case_line("revenue", "typed", "t").model_copy(
                update={"category": "by_product"}
            )
        ]
        if source == "share_of"
        else []
    )
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


#: Spec §4 "Units by source", written out by hand so a change to `allowed_units` cannot
#: move the expected side with it (the parity test above reads the function on both sides).
_EVERY = tuple(u for u in get_args(M.Unit) if u not in ("share", "lump", "MW"))
_SPEC_UNITS = {
    ("revenue", "typed"): _EVERY,
    ("opex", "typed"): _EVERY,
    ("capex", "typed"): (*_EVERY, "MW", "lump"),
    ("revenue", "share_of"): ("share",),
    ("opex", "share_of"): ("share",),
    ("capex", "share_of"): ("share",),
}


@pytest.mark.parametrize(("kind", "source"), sorted(_SPEC_UNITS))
def test_allowed_units_match_the_spec_table(kind, source):
    assert set(M.allowed_units(kind, source)) == set(_SPEC_UNITS[kind, source])


@pytest.mark.parametrize("kind", ["revenue", "opex"])
def test_lump_is_capex_only(kind):
    assert "lump" not in M.allowed_units(kind, "typed")
    assert "lump" not in M.allowed_units(kind, "linked", ("lump", "MWh"))
    assert "lump" in M.allowed_units("capex", "typed")


@pytest.mark.parametrize("kind", ["revenue", "opex", "capex"])
def test_mw_is_typed_capex_only(kind):
    assert "MW" not in M.allowed_units(kind, "linked", ("MW", "MWh"))
    assert ("MW" in M.allowed_units(kind, "typed")) is (kind == "capex")


@pytest.mark.parametrize("kind", ["revenue", "opex", "capex"])
def test_share_only_with_share_of(kind):
    assert "share" not in M.allowed_units(kind, "typed")
    assert "share" not in M.allowed_units(kind, "linked", ("share", "MWh"))
    assert M.allowed_units(kind, "share_of") == ("share",)


def test_allowed_units_of_a_linked_line_are_its_outputs_units():
    assert M.allowed_units("revenue", "linked", ("MWh", "t")) == ("MWh", "t")
    assert M.allowed_units("revenue", "linked", ()) == ()


# ── the route ────────────────────────────────────────────────────────────────


def _project(client, install_network, monkeypatch, template):
    import routers.results as R

    install_network(build_network())
    monkeypatch.setattr(
        R, "_active_template_meta", lambda: {"id": template} if template else None
    )


def _fresh(monkeypatch):
    """The run's bind, with the solve current (as `solve_matches_inputs` says after a run)."""
    import dataclasses

    from services.business_case import linked_outputs as L

    real = L.bind
    monkeypatch.setattr(
        L, "bind", lambda ctx, **kw: dataclasses.replace(real(ctx, **kw), fresh=True)
    )


def test_no_template_is_a_blank_table_with_the_units_by_source(
    client, install_network, monkeypatch
):
    _project(client, install_network, monkeypatch, None)
    r = client.get(OPTIONS_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["template_id"] is None and body["variants"] == {}
    assert body["offered_rows"] == []
    kinds = ("revenue", "opex", "capex")
    assert body["units_by_source"]["typed"] == {
        k: list(M.allowed_units(k, "typed")) for k in kinds
    }
    assert body["units_by_source"]["share_of"] == {k: ["share"] for k in kinds}
    assert body["categories"] == {k: list(v) for k, v in M.CATEGORIES.items()}
    assert body["escalations"] == list(get_args(M.Escalation))
    assert body["refusals"] == []  # nothing stored, nothing refused


def test_without_a_current_solve_outputs_are_null_never_empty(
    client, install_network, monkeypatch
):
    """O2: an empty list would read as "nothing to link"."""
    _project(client, install_network, monkeypatch, None)
    body = client.get(OPTIONS_URL).json()
    assert body["outputs"] is None and body["outputs_reason"] == "no_current_solve"
    assert body["units_by_source"]["linked"] == {}


@pytest.mark.parametrize("template", ["eh_site", "eh_datacenter"])
def test_a_template_lists_its_variants_and_offered_rows(
    client, install_network, monkeypatch, template
):
    from services.business_case import presets as P

    _project(client, install_network, monkeypatch, template)
    body = client.get(OPTIONS_URL).json()
    assert body["template_id"] == template
    assert body["variants"] == P.variants(template)
    assert body["offered_rows"] == [
        o.model_dump(mode="json") for o in P.offered_rows(template)
    ]


def test_outputs_and_their_units_are_the_catalogue_s_own(
    client, install_network, monkeypatch
):
    """O4: pinned against `OutputSpec.units` of the real catalogue, not a copied list."""
    from services.business_case import linked_outputs as L
    from tests.test_business_case_linked_outputs import _plant

    import routers.results as R

    n = _plant()
    install_network(n)
    monkeypatch.setattr(R, "_active_template_meta", lambda: None)
    _fresh(monkeypatch)
    body = client.get(OPTIONS_URL).json()
    # The default solver config names no export link: the same solve the route binds.
    specs = L.catalogue(n, L.LinkedSolve(network=n, fresh=True))
    assert specs  # the plant offers outputs
    assert body["outputs"] == [
        {"id": s.id, "label": s.label, "units": list(s.units), "derived": s.derived}
        for s in specs
    ]
    assert body["catalogue_version"] == L.CATALOGUE_VERSION
    linked = body["units_by_source"]["linked"]
    for s in specs:
        for k in ("revenue", "opex", "capex"):
            assert linked[s.id][k] == list(M.allowed_units(k, "linked", s.units))
            assert set(linked[s.id][k]) <= set(s.units)


def test_unreadable_outputs_are_a_reason_never_a_500(
    client, install_network, monkeypatch
):
    from services.business_case import linked_outputs as L

    _project(client, install_network, monkeypatch, None)
    _fresh(monkeypatch)

    def boom(n, solve=None):
        raise RuntimeError("bad network")

    monkeypatch.setattr(L, "catalogue", boom)
    body = client.get(OPTIONS_URL).json()
    assert body["outputs"] is None and body["outputs_reason"] == "outputs_not_readable"


def test_a_busy_network_is_409_after_a_bounded_wait(
    client, install_network, monkeypatch
):
    """O2: the lock is tried with a timeout, never a blocking acquire."""
    import routers.results as R
    from services.pypsa_service import PyPSAService

    _project(client, install_network, monkeypatch, None)
    seen = {}

    class Busy:
        def acquire(self, blocking=True, timeout=-1):
            seen["timeout"] = timeout
            return False

    monkeypatch.setattr(PyPSAService, "get_lock", staticmethod(lambda: Busy()))
    r = client.get(OPTIONS_URL)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "network_busy"
    assert seen["timeout"] == R._IC_LOCK_WAIT_S > 0


def test_a_running_solve_is_409(client, install_network, monkeypatch):
    import routers.results as R

    _project(client, install_network, monkeypatch, None)
    monkeypatch.setattr(R, "_solver_in_flight", lambda: True)
    r = client.get(OPTIONS_URL)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "solver_in_flight"


def test_refusals_need_the_investment_case_horizon(
    client, install_network, monkeypatch
):
    _project(client, install_network, monkeypatch, None)
    assert _put(client, _bc(_line(), _line(price=None))).status_code == 200
    body = client.get(OPTIONS_URL).json()
    assert body["refusals"] is None and body["horizon"] is None
    assert body["refusals_reason"] == "investment_case_missing"


def test_every_refusal_names_its_line_and_column(client, install_network, monkeypatch):
    """G9: all of them, in the validator's order, not only the first the run raises."""
    _with_ic(client, install_network, monkeypatch)
    lines = (_line(), _line(price=None), _line(name="fee", unit="share"))
    assert _put(client, _bc(*lines)).status_code == 200
    body = client.get(OPTIONS_URL).json()
    assert body["refusals_reason"] is None
    first, last = body["horizon"]
    assert last >= first and isinstance(body["base_year"], int)
    got = [(r["refusal"], r["field"]) for r in body["refusals"]]
    assert ("name_duplicate", "lines[1].name") in got
    assert ("price_not_established", "lines[1].price") in got
    assert ("unit_not_allowed", "lines[2].unit") in got
    assert all(
        r["code"] == "business_case_refused" and r["message"] for r in body["refusals"]
    )


def test_the_table_has_words_for_every_refusal_kind():
    """The line table places each refusal with plain words (G9): one entry per kind."""
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "pages"
        / "results"
        / "investment"
        / "businessCaseModel.ts"
    ).read_text()
    block = src.split("export const REFUSAL_WORDS", 1)[1].split("\n}\n", 1)[0]
    keys = set(re.findall(r"^\s+([a-z_]+):", block, re.M))
    assert keys == set(M.REFUSAL_KINDS)


def test_o1_offered_units_save_and_run_clean_and_others_are_refused_on_the_unit(
    client, install_network, monkeypatch
):
    """GK B O1, both directions through the routes: every typed unit the options route
    offers for a kind is saved and draws no `unit_not_allowed`; every unit it does not
    offer is saved (partial) and refused on `lines[i].unit`, the cell the table marks.
    The expected units come from the route (the backend), not from the table."""
    _with_ic(client, install_network, monkeypatch)
    offered = client.get(OPTIONS_URL).json()["units_by_source"]["typed"]
    cats = {"revenue": "product_sale", "opex": "staff", "capex": "facility"}
    lines, expect_refused = [], set()
    for kind, cat in cats.items():
        for unit in get_args(M.Unit):
            i = len(lines)
            ln = _line(
                name=f"{kind}-{unit}",
                kind=kind,
                category=cat,
                unit=unit,
                escalation="capex" if kind == "capex" else "opex",
            )
            if kind == "capex":
                ln["life_years"] = 10
            lines.append(ln)
            if unit not in offered[kind]:
                expect_refused.add(f"lines[{i}].unit")
    assert _put(client, _bc(*lines)).status_code == 200
    body = client.get(OPTIONS_URL).json()
    refused = {
        r["field"] for r in body["refusals"] if r["refusal"] == "unit_not_allowed"
    }
    assert refused == expect_refused and expect_refused


def test_o1_a_linked_line_is_checked_against_the_catalogue_in_hand(
    client, install_network, monkeypatch
):
    """O1 for linked lines, both directions: a unit its output offers is clean, one it
    does not is refused on `lines[i].unit`, and an output the model lacks on
    `lines[i].quantity_source` (the cells the table marks)."""
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    _fresh(monkeypatch)
    spec = L.OutputSpec("served_product.conc", "Product served on conc", ("t",))
    monkeypatch.setattr(L, "catalogue", lambda n, solve=None: [spec])
    v = L.CATALOGUE_VERSION
    lines = (
        _line(
            name="ok",
            unit="t",
            quantity=None,
            quantity_source=f"linked:served_product.conc@{v}",
        ),
        _line(
            name="kg",
            unit="kg",
            quantity=None,
            quantity_source=f"linked:served_product.conc@{v}",
        ),
        _line(
            name="gone",
            unit="t",
            quantity=None,
            quantity_source=f"linked:served_product.nope@{v}",
        ),
    )
    assert _put(client, _bc(*lines)).status_code == 200
    body = client.get(OPTIONS_URL).json()
    got = {(r["refusal"], r["field"]) for r in body["refusals"]}
    assert ("unit_not_allowed", "lines[1].unit") in got
    assert ("linked_output_missing", "lines[2].quantity_source") in got
    assert not {f for _, f in got if f.startswith("lines[0].")}


def test_refusals_wait_for_a_current_investment_case(
    client, install_network, monkeypatch
):
    import routers.results as R

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(_line(price=None))).status_code == 200
    monkeypatch.setattr(R, "_bc_ic_current", lambda: False)
    body = client.get(OPTIONS_URL).json()
    assert body["refusals"] is None
    assert body["refusals_reason"] == "investment_case_out_of_date"
