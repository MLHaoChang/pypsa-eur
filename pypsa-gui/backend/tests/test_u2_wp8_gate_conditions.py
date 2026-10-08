"""
U2 WP8 part A gate (`docs/superpowers/notes/2026-10-08-u2-wp8a-gate.md`),
binding conditions Y1 and Y2, taken in part B.

* Y1 — a refused finance is disclosed, never a 500. A tariff in a second
  currency year (reachable through the API or the assistant) runs to `done`
  with `details.<option>.finance_unavailable`; the findings, the tornado POST,
  the report POST and the case route then answer 422 with the compile's code
  and message (`findings.load_inputs` reads the run record). An `EngineRefused`
  of a centre case is a 422 too (`findings._centre`), never an uncaught 500.
* Y2 — the GS fallback is a refusal on a study context. A network the tornado
  solves itself (the PV-only reference, a price-bound variant) that comes back
  without the engine's solve record is `VariantFailed("variant_not_engine_solved")`:
  the reference or the bar is not established with that code, and neither GS's
  bill nor the pro forma is ever taken (`findings._require_engine`).

Mutations: drop `load_inputs`' finance check → the route test red; drop the
centre guard → the centre unit test red; drop `_require_engine` from
`_price_bound` / the reference / `_bill_of` / `_case` → the matching Y2 test red.
"""
from __future__ import annotations

import types

import pytest

from tests.test_study_tornado_routes import NO_PV_TOU, TOU_DC, _run

STUDY_YEAR_TARIFF = {**TOU_DC, "currency_year": 2024}     # the study is 2020


# ── Y1 ────────────────────────────────────────────────────────────────────

def test_a_finance_the_run_could_not_compile_is_refused_422_everywhere(
        client, api_project, studies_on, fake):
    name = "y1-mixed-years"
    sid = _run(client, api_project, name,
               intake={**NO_PV_TOU, "tariff": {"custom": STUDY_YEAR_TARIFF}})
    base = f"/api/projects/{name}/studies/{sid}"
    run = client.get(f"{base}/run").json()
    refused = {oid: d.get("finance_unavailable") for oid, d in run["details"].items()
               if oid != "none"}
    assert refused and set(refused.values()) == {"currency_year_mixed"}, refused
    for method, path in (("get", "/findings"), ("post", "/findings/tornado"),
                         ("post", "/report"), ("get", "/options/bess_1h/case")):
        r = getattr(client, method)(f"{base}{path}", **({"json": {}} if method == "post" else {}))
        assert r.status_code == 422, (path, r.status_code, r.text)
        detail = r.json()["detail"]
        assert detail["error_kind"] == "currency_year_mixed", (path, detail)
        assert "currency year" in detail["message"], (path, detail)


def _centre_ctx(**kw):
    from services.study import questions as Q

    opt = types.SimpleNamespace(network=types.SimpleNamespace(), bill=object(),
                                asset_economics=None)
    return types.SimpleNamespace(
        question=Q.BESS_AT_SITE, ledger=None, tariff=types.SimpleNamespace(currency_year=2020),
        baseline_network=types.SimpleNamespace(snapshots=None), baseline_bill=object(),
        options={"bess_2h": opt}, fidelity=None, export_series={"ref": "x"}, bundles={}, **kw)


def test_an_engine_refusal_at_the_centre_is_a_422_never_a_500(monkeypatch):
    from services.study import engine_adapter as A
    from services.study import findings as F

    def refuse(*a, **kw):
        raise A.EngineRefused("lifetime_not_whole_years", "a lifetime must be whole years")

    monkeypatch.setattr(F, "_solver_config", lambda ctx, ledger, snapshots=None: "cfg")
    monkeypatch.setattr(F, "battery_size", lambda n: 0.5)
    monkeypatch.setattr(F, "_case", refuse)
    with pytest.raises(F.FindingsRefused) as exc:
        F.centre_attributions(_centre_ctx())
    assert (exc.value.status, exc.value.code) == (422, "lifetime_not_whole_years")
    assert "whole years" in exc.value.message


# ── Y2 ────────────────────────────────────────────────────────────────────

def _no_fallback(monkeypatch):
    from services.study import findings as F

    def boom(*a, **kw):
        raise AssertionError("the GS fallback was taken on a study context")

    monkeypatch.setattr(F, "_gs_bill_of", boom)
    monkeypatch.setattr(F.proforma, "build_investment_case", boom)
    monkeypatch.setattr(F.study_engine, "engine_ready", lambda n: False)


def test_a_price_bound_not_solved_on_the_engine_is_refused(monkeypatch):
    from services.study import findings as F

    _no_fallback(monkeypatch)
    monkeypatch.setattr(F, "_with_value", lambda ledger, key, value: ledger)
    monkeypatch.setattr(F, "_solver_config", lambda ctx, ledger, snapshots=None: "cfg")
    monkeypatch.setattr(F, "fixed_size_network", lambda n, drop_battery=False: n)
    ctx = types.SimpleNamespace(ledger=None, export_series={"ref": "x"})
    n = types.SimpleNamespace(snapshots=None)
    with pytest.raises(F.VariantFailed) as exc:
        F._price_bound(ctx, n, "bess_2h", "demand_charge_price", 1.0,
                       lambda net, cfg, vid: net, "t0l", reference=False)
    assert exc.value.code == "variant_not_engine_solved"


def test_a_pv_only_reference_not_solved_on_the_engine_is_not_established(monkeypatch):
    from services.study import findings as F

    _no_fallback(monkeypatch)
    centre = types.SimpleNamespace(status="ok", kpis=types.SimpleNamespace(npv=100.0),
                                   currency_year=2020, engine="finance_engine")
    seen = []

    def case(ctx, n, *a, **kw):
        seen.append(n)
        return centre

    monkeypatch.setattr(F, "_solver_config", lambda ctx, ledger, snapshots=None: "cfg")
    monkeypatch.setattr(F, "battery_size", lambda n: 0.5)
    monkeypatch.setattr(F, "pv_size", lambda n: 1.0)
    monkeypatch.setattr(F, "fixed_size_network", lambda n, drop_battery=False: n)
    monkeypatch.setattr(F, "_case", case)
    monkeypatch.setattr(F, "attribute", lambda oid, case, p_bat, **kw: kw)
    ctx = _centre_ctx()
    ctx.options = {"bess_pv_2h": ctx.options["bess_2h"]}
    solved = types.SimpleNamespace()
    _cases, refs, _nets, [kw], _stopped = F._centre(ctx, lambda net, cfg, vid: solved)
    assert kw["reference_missing"] == "reference_variant_not_engine_solved"
    assert "reference_case" not in kw and refs == {}
    assert not any(x is solved for x in seen)    # never valued


@pytest.mark.parametrize("seam", ["bill", "case"])
def test_the_fallback_seams_refuse_on_a_study_context(monkeypatch, seam):
    from services.study import findings as F

    _no_fallback(monkeypatch)
    ctx = types.SimpleNamespace(ledger=None, export_series={"ref": "x"}, tariff=None,
                                fidelity=None)
    n = types.SimpleNamespace(snapshots=None)
    with pytest.raises(F.VariantFailed) as exc:
        if seam == "bill":
            F._bill_of(ctx, n)
        else:
            F._case(ctx, n, "cfg", None, None, {}, "bess_2h")
    assert exc.value.code == "variant_not_engine_solved"


@pytest.fixture
def studies_on(monkeypatch):
    from tests.study_s4_support import enable_studies

    yield from enable_studies(monkeypatch)


@pytest.fixture
def fake(monkeypatch):
    from services import solver_service
    from tests.test_study_runner import FakeSolver

    solver = FakeSolver()
    monkeypatch.setattr(solver_service, "run_simulation", solver)
    return solver
