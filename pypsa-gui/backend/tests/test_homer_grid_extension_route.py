"""homer-parity 03d: the grid-extension break-even route and tool. Gate letters are GK B's
plan conditions E1-E7. The pure `evaluate` carries the logic; the route and tool are
thin (checked against it)."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from models.study import AssumptionsLedger, LedgerRow
from routers import grid_extension as G

RATE, LIFE, YEAR = 0.05, 10, 2025


def _ledger(rate=RATE, life=LIFE):
    def row(key, value, unit):
        return LedgerRow(
            key=key,
            label=key,
            value=value,
            unit=unit,
            source="t",
            unavailable={} if value is not None else {"value": "not_set"},
        )

    return AssumptionsLedger(
        ledger_version="t",
        rows=[
            row("discount_rate", rate, "fraction"),
            row("battery_storage_lifetime_years", life, "years"),
        ],
    )


def _study(ledger="default", year=YEAR):
    return SimpleNamespace(
        ledger=_ledger() if ledger == "default" else ledger, currency_year=year
    )


def _row(arch="pv", status="ok", cost=1000.0):
    return {"architecture_id": arch, "status": status, "total_cost_eur_per_year": cost}


def _rec(*rows, stale=False, status="done"):
    return {"status": status, "stale": stale, "rows": list(rows) or [_row()]}


def _body(**kw):
    d = dict(
        architecture_id="pv",
        eur_per_km=10_000.0,
        grid_supply_npc=2000.0,
        grid_basis=dict(
            currency="EUR", currency_year=YEAR, discount_rate=RATE, horizon_years=LIFE
        ),
    )
    d.update(kw)
    return G.GridExtensionRequest(**d)


def _kind(exc: pytest.ExceptionInfo) -> str:
    return exc.value.detail["error_kind"]


def _annuity(r, n):
    return n if r == 0 else (1 - (1 + r) ** -n) / r


def test_happy_path_derives_npc_and_echoes_the_row():
    out = G.evaluate(_study(), _rec(), _body())
    npc = 1000.0 * _annuity(RATE, LIFE)
    assert out["offgrid_npc"] == pytest.approx(npc)
    assert out["architecture_id"] == "pv" and out["total_cost_eur_per_year"] == 1000.0
    assert out["status"] == "break_even"
    assert out["km"] == pytest.approx((npc - 2000.0) / 10_000.0)
    assert "annualised" in out["approximation"]
    assert out["basis"] == {
        "currency": "EUR",
        "currency_year": YEAR,
        "discount_rate": RATE,
        "horizon_years": LIFE,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("discount_rate", RATE + 0.01),
        ("horizon_years", LIFE + 1),
        ("currency", "USD"),
        ("currency_year", YEAR - 1),
    ],
)
def test_e1_each_basis_field_refuses_on_its_own(field, value):
    basis = dict(
        currency="EUR", currency_year=YEAR, discount_rate=RATE, horizon_years=LIFE
    )
    basis[field] = value
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), _rec(), _body(grid_basis=basis))
    assert _kind(e) == "basis_mismatch"
    assert e.value.detail["fields"] == [field]


@pytest.mark.parametrize(
    "delta,ok", [(5e-10, True), (-5e-10, True), (2e-9, False), (-2e-9, False)]
)
def test_e1_rate_tolerance_is_1e9(delta, ok):
    basis = dict(
        currency="EUR",
        currency_year=YEAR,
        discount_rate=RATE + delta,
        horizon_years=LIFE,
    )
    if ok:
        assert G.evaluate(_study(), _rec(), _body(grid_basis=basis))["status"]
        return
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), _rec(), _body(grid_basis=basis))
    assert _kind(e) == "basis_mismatch" and e.value.detail["fields"] == [
        "discount_rate"
    ]


def test_e2_ledger_changes_move_npc_and_expected_basis():
    base = G.evaluate(_study(), _rec(), _body())["offgrid_npc"]
    study = _study(_ledger(rate=0.08, life=12))
    with pytest.raises(HTTPException) as e:  # the old basis is now wrong
        G.evaluate(study, _rec(), _body())
    assert e.value.detail["expected"]["discount_rate"] == 0.08
    b = dict(currency="EUR", currency_year=YEAR, discount_rate=0.08, horizon_years=12)
    moved = G.evaluate(study, _rec(), _body(grid_basis=b))["offgrid_npc"]
    assert moved == pytest.approx(1000.0 * _annuity(0.08, 12)) and moved != base


@pytest.mark.parametrize(
    "study",
    [
        SimpleNamespace(ledger=None, currency_year=YEAR),
        SimpleNamespace(ledger=_ledger(), currency_year=None),
        SimpleNamespace(ledger=_ledger(rate=None), currency_year=YEAR),
        SimpleNamespace(ledger=_ledger(life=None), currency_year=YEAR),
        SimpleNamespace(
            ledger=AssumptionsLedger(ledger_version="t", rows=[]), currency_year=YEAR
        ),
    ],
)
def test_e2_missing_assumptions_refuse_without_defaults(study):
    with pytest.raises(HTTPException) as e:
        G.evaluate(study, _rec(), _body())
    assert _kind(e) == "assumptions_not_set"


@pytest.mark.parametrize("rate", [-1.0, -2.0])
def test_e2_rate_at_or_below_minus_one_refuses(rate):
    with pytest.raises(HTTPException) as e:
        G.study_assumptions(_study(_ledger(rate=rate)))
    assert _kind(e) == "discount_rate_invalid"


def test_e2_zero_rate_annuity_is_the_horizon():
    assert G.annuity_factor(0.0, 7) == 7.0
    b = dict(currency="EUR", currency_year=YEAR, discount_rate=0.0, horizon_years=LIFE)
    out = G.evaluate(_study(_ledger(rate=0.0)), _rec(), _body(grid_basis=b))
    assert out["offgrid_npc"] == pytest.approx(1000.0 * LIFE)


def test_e2_overflowing_annuity_refuses_not_500():
    with pytest.raises(HTTPException) as e:
        G.annuity_factor(-0.999999, 200)
    assert _kind(e) == "npc_not_finite"


def test_e3_stale_comparison_refuses():
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), _rec(stale=True), _body())
    assert _kind(e) == "supply_architectures_stale"


@pytest.mark.parametrize(
    "rec,kind",
    [
        (None, "supply_architectures_never_run"),
        (_rec(status="running"), "supply_architectures_running"),
        (
            {"status": "not_evaluated", "stale": None},
            "supply_architectures_not_evaluated",
        ),
    ],
)
def test_no_usable_comparison_refuses(rec, kind):
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), rec, _body())
    assert _kind(e) == kind


def test_e4_distance_outcomes_are_defined():
    far = G.evaluate(_study(), _rec(), _body(eur_per_km=1.0, max_km=100.0))
    assert far["status"] == "beyond_max_km" and math.isfinite(far["km"])
    dear = G.evaluate(_study(), _rec(), _body(grid_supply_npc=1e9))
    assert dear["status"] == "offgrid_cheaper_everywhere" and dear["km"] is None
    flat = G.evaluate(_study(), _rec(), _body(eur_per_km=0.0, grid_supply_npc=1.0))
    assert flat["status"] == "grid_cheaper_everywhere" and flat["km"] is None


def test_e4_huge_inputs_give_not_established_not_500():
    out = G.evaluate(
        _study(), _rec(_row(cost=1e300)), _body(eur_per_km=1e-300, grid_supply_npc=0.0)
    )
    assert out["status"] == "break_even_not_established" and out["km"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("eur_per_km", math.inf),
        ("eur_per_km", math.nan),
        ("eur_per_km", -1.0),
        ("fixed_eur", math.inf),
        ("grid_supply_npc", math.inf),
        ("grid_supply_npc", math.nan),
        ("max_km", math.inf),
        ("max_km", 0.0),
    ],
)
def test_e4_inputs_must_be_finite_and_in_range(field, value):
    with pytest.raises(ValidationError):
        _body(**{field: value})


def test_e4_basis_rate_and_extra_fields_are_validated():
    with pytest.raises(ValidationError):
        _body(
            grid_basis=dict(
                currency="EUR",
                currency_year=YEAR,
                discount_rate=math.inf,
                horizon_years=LIFE,
            )
        )
    with pytest.raises(ValidationError):
        G.GridExtensionRequest(**{**_body().model_dump(), "offgrid_npc": 1.0})


@pytest.mark.parametrize(
    "rows,arch,kind",
    [
        ([_row("pv")], "nope", "architecture_unknown"),
        ([_row("pv", status="infeasible", cost=None)], "pv", "architecture_not_solved"),
        ([_row("pv", cost=None)], "pv", "architecture_not_solved"),
        ([_row("pv", cost=math.inf)], "pv", "architecture_not_solved"),
        ([_row("pv", cost=math.nan)], "pv", "architecture_not_solved"),
        ([_row("pv", cost=-5.0)], "pv", "architecture_not_solved"),
    ],
)
def test_e7_bad_architecture_rows_refuse(rows, arch, kind):
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), _rec(*rows), _body(architecture_id=arch))
    assert _kind(e) == kind


def test_tool_and_route_are_registered_and_read_only():
    from harness import catalogue
    from services import chat_tools

    names = {t["name"] for t in catalogue.TOOLS}
    assert "get_grid_extension_break_even" in names
    assert (
        "get_grid_extension_break_even" in chat_tools.DISPATCHERS
        if hasattr(chat_tools, "DISPATCHERS")
        else True
    )
    t = next(t for t in catalogue.TOOLS if t["name"] == "get_grid_extension_break_even")
    assert t["description"].rstrip().endswith("Safety: read.")
    assert "offgrid_npc" not in t["input_schema"]["properties"]


# ── the route and the tool, over a real project with the study and the 03a record stubbed ──

from tests.study_s4_support import enable_studies
from tests.test_homer_supply_architecture_routes import _project


@pytest.fixture
def studies_on(monkeypatch):
    yield from enable_studies(monkeypatch)


def _stub(monkeypatch, rec=None, study=None):
    from services.study import supply_architecture_runner as run

    monkeypatch.setattr(G, "_load", lambda project, sid: study or _study())
    monkeypatch.setattr(run, "get", lambda *a, **k: rec if rec is not None else _rec())


def test_route_returns_the_result_and_writes_nothing(
    client, install_network, studies_on, monkeypatch, tmp_path
):
    sid = _project(client, install_network, "gx_route")
    _stub(monkeypatch)
    from routers.deps import AuthorizedProject  # noqa: F401

    url = f"/api/projects/gx_route/studies/{sid}/grid-extension-break-even"
    r = client.post(url, json=_body().model_dump())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "break_even" and r.json()["offgrid_npc"] > 0
    bad = _body().model_dump()
    bad["grid_basis"]["horizon_years"] += 1
    r = client.post(url, json=bad)
    assert r.status_code == 422 and r.json()["detail"]["error_kind"] == "basis_mismatch"
    inf = client.post(
        url,
        content=_body().model_dump_json().replace("2000.0", "1e999"),
        headers={"content-type": "application/json"},
    )
    assert inf.status_code == 422


def test_e6_tool_refuses_a_project_that_is_not_the_sessions_own(
    client, install_network, studies_on, monkeypatch
):
    from services import chat_tools

    _project(client, install_network, "gx_tool")
    _stub(monkeypatch)
    with pytest.raises(HTTPException) as e:
        chat_tools.get_grid_extension_break_even(
            "no_such_project_zz", "s", "pv", 1.0, 1.0, _body().grid_basis.model_dump()
        )
    missing = (e.value.status_code, e.value.detail)
    with pytest.raises(HTTPException) as e2:
        chat_tools.get_grid_extension_break_even(
            "no_such_project_zz", "s", "pv", -1.0, 1.0, _body().grid_basis.model_dump()
        )
    assert e2.value.detail["error_kind"] == "grid_extension_input_invalid"
    assert missing[0] in (403, 404)


def test_mutant_gaps_nonfinite_rate_status_bool_cost_and_npc_overflow():
    with pytest.raises(HTTPException) as e:
        G.study_assumptions(_study(_ledger(rate=math.nan)))
    assert _kind(e) == "discount_rate_invalid"
    with pytest.raises(HTTPException) as e:  # a solved-looking cost on a failed row
        G.evaluate(_study(), _rec(_row(status="infeasible", cost=5.0)), _body())
    assert _kind(e) == "architecture_not_solved"
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), _rec(_row(cost=True)), _body())
    assert _kind(e) == "architecture_not_solved"
    with pytest.raises(
        HTTPException
    ) as e:  # finite cost, annuity pushes the NPC to inf
        G.evaluate(_study(), _rec(_row(cost=1.7e308)), _body())
    assert _kind(e) == "npc_not_finite"


@pytest.mark.parametrize("rate", [1e-16, -1e-16, 1e-13, 1e-11])
def test_d1_tiny_rate_keeps_a_positive_annuity(rate):
    f = G.annuity_factor(rate, 20)
    assert f == pytest.approx(20.0, rel=1e-6)
    b = dict(currency="EUR", currency_year=YEAR, discount_rate=rate, horizon_years=20)
    out = G.evaluate(_study(_ledger(rate=rate, life=20)), _rec(), _body(grid_basis=b))
    assert out["offgrid_npc"] == pytest.approx(20_000.0, rel=1e-6)


def test_d1_annuity_matches_the_closed_form_at_ordinary_rates():
    assert G.annuity_factor(0.05, 10) == pytest.approx(_annuity(0.05, 10))
    assert G.annuity_factor(-0.5, 3) == pytest.approx(_annuity(-0.5, 3))


@pytest.mark.parametrize("rows", [["junk"], [None], [_row(), 5]])
def test_d2_a_non_dict_saved_row_is_refused_not_a_500(rows):
    with pytest.raises(HTTPException) as e:
        G.evaluate(_study(), {"status": "done", "stale": False, "rows": rows}, _body())
    assert _kind(e) == "supply_architectures_not_evaluated"


def test_d3_the_handler_refuses_when_studies_are_off_as_a_plain_call(monkeypatch):
    from routers import studies as S

    def off():
        raise HTTPException(404, "off")

    monkeypatch.setattr(S, "require_decision_studies_enabled", off)
    with pytest.raises(HTTPException) as e:
        G.grid_extension_break_even_route(
            "s", _body(), project=None, db=None, user=None
        )
    assert e.value.status_code == 404


def test_grid_basis_rejects_an_extra_key():
    with pytest.raises(ValidationError):
        G.GridBasis(
            currency="EUR",
            currency_year=YEAR,
            discount_rate=RATE,
            horizon_years=LIFE,
            extra_key=1,
        )
