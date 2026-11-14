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
from tests.test_business_case_routes import (
    _bc,
    _line,
    _put,
    _put_fin,
    _s2_inputs,
    _with_ic,
)

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


def test_typed_and_share_lines_are_not_checked_against_the_catalogue(
    client, install_network, monkeypatch
):
    """Only a linked line is read against the catalogue in hand: a typed line in a
    unit no output offers, and a share of it, are clean (mutant survivor, #1507)."""
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    _fresh(monkeypatch)
    spec = L.OutputSpec("served_product.conc", "Product served on conc", ("t",))
    monkeypatch.setattr(L, "catalogue", lambda n, solve=None: [spec])
    lines = (
        _line(name="typed", category="by_product", unit="kg"),
        _line(
            name="share",
            unit="share",
            quantity=0.5,
            price=None,
            quantity_source="share_of:revenue/by_product",
        ),
    )
    assert _put(client, _bc(*lines)).status_code == 200
    r = client.get(OPTIONS_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outputs"] is not None
    assert body["refusals"] == []


def test_refusals_wait_for_a_current_investment_case(
    client, install_network, monkeypatch
):
    import routers.results as R

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(_line(price=None))).status_code == 200
    monkeypatch.setattr(R, "_bc_ic_current", lambda: "investment_case_out_of_date")
    body = client.get(OPTIONS_URL).json()
    assert body["refusals"] is None
    assert body["refusals_reason"] == "investment_case_out_of_date"


# ── the stored investment case the refusals read (horizon, currency) ─────────


def _ic_payload(stored: dict) -> dict:
    return stored["sections"]["project"]["payload"]


def _store_ic(session_state, client, edit) -> None:
    """Replace the stored IC report with a deep copy that `edit` changes in place."""
    import copy

    st = session_state(client)
    rep = copy.deepcopy(dict(st["investment_case_report"]))
    edit(rep)
    st["investment_case_report"] = rep


def test_a_stale_investment_case_is_not_checked_against(
    client, install_network, monkeypatch
):
    """The real staleness reader, not a stub: the finance inputs moved after the IC run."""
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(_line(price=None))).status_code == 200
    assert client.get(OPTIONS_URL).json()["refusals"] is not None
    assert _put_fin(client, {**_s2_inputs(), "analysis_years": 3}).status_code == 200
    body = client.get(OPTIONS_URL).json()
    assert body["refusals"] is None
    assert body["refusals_reason"] == "investment_case_out_of_date"


@pytest.mark.parametrize("years", [0, -1])
def test_no_analysis_years_is_no_horizon(
    client, install_network, monkeypatch, session_state, years
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    _store_ic(
        session_state, client, lambda r: _ic_payload(r).update(analysis_years=years)
    )
    body = client.get(OPTIONS_URL).json()
    assert body["horizon"] is None and body["base_year"] is None
    assert body["refusals"] is None
    assert body["refusals_reason"] == "investment_case_missing"


def test_one_analysis_year_is_a_one_year_horizon(
    client, install_network, monkeypatch, session_state
):
    _with_ic(client, install_network, monkeypatch)
    pay = _ic_payload(dict(session_state(client)["investment_case_report"]))
    _store_ic(session_state, client, lambda r: _ic_payload(r).update(analysis_years=1))
    body = client.get(OPTIONS_URL).json()
    assert body["horizon"] == [pay["cod_year"], pay["cod_year"]]
    assert body["base_year"] == pay["base_year"]


def test_a_stored_report_model_reads_as_its_json(
    client, install_network, monkeypatch, session_state
):
    """The session may hold the report as a model object: horizon and currency read
    through its `model_dump`, the same as the stored dict."""
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(_line(price=None))).status_code == 200
    as_dict = client.get(OPTIONS_URL).json()
    st = session_state(client)
    rep = dict(st["investment_case_report"])

    class Stored:
        def model_dump(self, mode="python"):
            assert mode == "json"
            return rep

    st["investment_case_report"] = Stored()
    body = client.get(OPTIONS_URL).json()
    assert body["horizon"] == as_dict["horizon"] is not None
    assert body["base_year"] == as_dict["base_year"]
    assert body["refusals"] == as_dict["refusals"] is not None
    assert body["refusals_reason"] == as_dict["refusals_reason"] is None


@pytest.mark.parametrize("drop", ["assumptions_hash", "finance_case_hash"])
def test_a_report_without_its_key_is_out_of_date(
    client, install_network, monkeypatch, session_state, drop
):
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200

    def edit(r):
        if drop == "assumptions_hash":
            r.pop("assumptions_hash")
        else:
            _ic_payload(r)["provenance"].pop("finance_case_hash")

    _store_ic(session_state, client, edit)
    body = client.get(OPTIONS_URL).json()
    assert body["refusals"] is None
    assert body["refusals_reason"] == "investment_case_out_of_date"


# ── GK B NOT OK @64a3e5e8 (F1–F4) ────────────────────────────────────────────


def test_one_cell_is_refused_once(client, install_network, monkeypatch):
    """F1: a linked revenue line in `lump` is refused by the validator (lump is capex
    only) and by the catalogue check (its output offers t): one refusal on the cell."""
    from services.business_case import linked_outputs as L

    _with_ic(client, install_network, monkeypatch)
    _fresh(monkeypatch)
    spec = L.OutputSpec("served_product.conc", "Product served on conc", ("t",))
    monkeypatch.setattr(L, "catalogue", lambda n, solve=None: [spec])
    v = L.CATALOGUE_VERSION
    ln = _line(
        unit="lump", quantity=None, quantity_source=f"linked:served_product.conc@{v}"
    )
    assert _put(client, _bc(ln)).status_code == 200
    got = [
        (r["refusal"], r["field"]) for r in client.get(OPTIONS_URL).json()["refusals"]
    ]
    assert got.count(("unit_not_allowed", "lines[0].unit")) == 1


def test_without_a_network_outputs_say_so(client, monkeypatch):
    """F2: no network loaded: outputs are None with `no_network`, never []."""
    import routers.results as R
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    monkeypatch.setattr(ctx, "network", None)
    monkeypatch.setattr(R, "_solver_in_flight", lambda: False, raising=False)
    body = client.get(OPTIONS_URL).json()
    assert body["outputs"] is None
    assert body["outputs_reason"] == "no_network"


@pytest.mark.parametrize("key", ["cod_year", "analysis_years", "base_year"])
@pytest.mark.parametrize("bad", [True, 2030.0, "2030"])
def test_a_year_or_count_of_the_wrong_type_is_no_horizon(
    client, install_network, monkeypatch, session_state, key, bad
):
    """F3: a bool is an int to isinstance; it, a float and a string are no year."""
    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc()).status_code == 200
    _store_ic(session_state, client, lambda r: _ic_payload(r).update({key: bad}))
    body = client.get(OPTIONS_URL).json()
    assert body["horizon"] is None and body["base_year"] is None
    assert body["refusals_reason"] == "investment_case_missing"


def test_a_busy_network_is_not_read_as_out_of_date(
    client, install_network, monkeypatch
):
    """F4 (ruling PG-09 Y1): the lock held by someone else while the current IC key is
    read: the refusals say the network is busy and nothing is compared."""
    import threading

    import routers.results as R
    from services.pypsa_service import PyPSAService

    _with_ic(client, install_network, monkeypatch)
    assert _put(client, _bc(_line(price=None))).status_code == 200
    monkeypatch.setattr(R, "_IC_LOCK_WAIT_S", 0.05)
    held, done = threading.Event(), threading.Event()

    def holder(lock):
        with lock:
            held.set()
            done.wait(10)

    real_raw = R._bc_raw

    def raw_then_hold():
        # After the options read has let the lock go, another thread takes it.
        # The lock the route will ask for, read in the route's own context.
        t = threading.Thread(
            target=holder, args=(PyPSAService.get_lock(),), daemon=True
        )
        t.start()
        held.wait(5)
        return real_raw()

    compared = []
    monkeypatch.setattr(R, "_bc_raw", raw_then_hold)
    import services.finance.investment_case_runner as IR

    real_staleness = IR.staleness
    monkeypatch.setattr(
        IR, "staleness", lambda *a, **k: compared.append(1) or real_staleness(*a, **k)
    )
    try:
        body = client.get(OPTIONS_URL).json()
    finally:
        done.set()
    assert body["refusals"] is None
    assert body["refusals_reason"] == "network_busy"
    assert compared == []
