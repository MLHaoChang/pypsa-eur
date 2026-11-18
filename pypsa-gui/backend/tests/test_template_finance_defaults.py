# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Finance defaults on every template (WALK-IMPROVE-1 b; GK B2 plan D1-D5).

Every template ships the inputs its investment case needs, tax included, each
recorded as an assumption with its source (`finance_assumed`). The record is a
label only: no fingerprint, digest or case reads it.
"""

from __future__ import annotations

import json
import math
import queue
import threading

import pytest

from project_templates import eh_templates as T

FINANCE_TEMPLATES = sorted(
    t for t in T.TEMPLATE_META if T.solver_config(t).get("finance")
)


def test_every_template_ships_finance_inputs():
    # The data-centre campus was the one without (walkthrough 2026-11-17, issue 02).
    assert set(FINANCE_TEMPLATES) == set(T.TEMPLATE_META)


@pytest.mark.parametrize("tid", FINANCE_TEMPLATES)
def test_the_tax_rule_resolves_for_every_owned_asset(tid):
    """`tax` is established on the shipped inputs: the pack resolves a
    depreciation class for each asset the owner holds, and needs no input the
    template leaves out (no `tax_input_missing`)."""
    from models.finance import FinanceInputs
    from services.finance.packs.base import load_pack
    from services.finance.tax_layers import OwnerAsset, resolve_tax_layers

    cfg = T.solver_config(tid)
    fin = FinanceInputs.model_validate(cfg["finance"])
    assert fin.tax_pack_id == "eu_de" and fin.hebesatz_pct is not None
    n = T.BUILDERS[tid]()
    owners = cfg["commercial"]["value_flows"]["asset_owners"]
    assets = [
        OwnerAsset(
            o["asset_id"],
            str(n.components[o["component"]].static.at[o["asset_id"], "carrier"]),
            1.0,
        )
        for o in owners
    ]
    cod = min(fin.cod_by_asset.values())
    res = resolve_tax_layers(
        load_pack("eu_de", as_of=fin.financial_close), fin, assets, cod
    )
    assert res.missing == []
    # Straight line throughout: a later stage cannot take the degressive election (P5).
    assert "degressive_afa_elected" not in res.flags


def test_depreciation_classes_follow_the_afa_table_where_it_lists_the_asset():
    fin = T.solver_config("eh_datacenter")["finance"]["depreciation_class_by_asset"]
    assert fin["genset_1"] == "afa_19"  # Fundstelle 3.1.2, generating sets
    assert fin["ups_battery"] == "afa_10"  # assumed, as the IPP's battery
    mg = T.solver_config("eh_microgrid")["finance"]["depreciation_class_by_asset"]
    assert mg["wind_turbines"] == "afa_16" and mg["pv_plant"] == "afa_20"


def test_a_template_value_wins_over_the_default():
    ipp = T.solver_config("eh_ipp_hybrid")["finance"]
    assert ipp["depreciation_class_by_asset"] == {
        "pv_plant": "afa_20",
        "wind_farm": "afa_16",
        "bess": "afa_10",
    }
    assert ipp["hebesatz_pct"] == 400.0 and ipp["acquisition_date"] == "2030-01-01"


@pytest.mark.parametrize("tid", FINANCE_TEMPLATES)
def test_each_assumed_record_names_a_finance_input_and_its_shipped_value(tid):
    from models.finance import FinanceInputs, parse_finance_assumed

    cfg = T.solver_config(tid)
    records = parse_finance_assumed(cfg["finance_assumed"])
    assert {
        "wacc_nominal",
        "tax_pack_id",
        "hebesatz_pct",
        "depreciation_class_by_asset",
        "escalation",
    } <= set(records)
    for k, r in records.items():
        assert k in FinanceInputs.model_fields
        assert r.value == cfg["finance"][k], k
        assert r.source
    assert records["hebesatz_pct"].source_year == 2023


def test_a_record_for_no_finance_input_is_refused():
    from models.finance import parse_finance_assumed

    with pytest.raises(ValueError, match="wacc_nominall"):
        parse_finance_assumed({"wacc_nominall": {"value": 0.07, "source": "assumed"}})
    with pytest.raises(ValueError):
        parse_finance_assumed(
            {"wacc_nominal": {"value": 0.07, "source": "x", "typo": 1}}
        )
    with pytest.raises(ValueError):
        parse_finance_assumed({"wacc_nominal": {"value": 0.07, "source": ""}})


def test_the_record_is_in_no_fingerprint_or_case_digest():
    """D2: changing only `finance_assumed` leaves the config every fingerprint
    and digest hashes (`goal_free_config`) unchanged."""
    from routers.projects import _solver_config_from_dict
    from services.reliability_goal import goal_free_config

    cfg = T.solver_config("eh_datacenter")
    other = {**cfg, "finance_assumed": {"wacc_nominal": {"value": 0.09, "source": "x"}}}
    bare = {k: v for k, v in cfg.items() if k != "finance_assumed"}
    a, b, c = (
        goal_free_config(_solver_config_from_dict(d)) for d in (cfg, other, bare)
    )
    assert a == b == c
    assert "finance_assumed" not in a


def test_the_record_is_not_in_the_investment_case_assumptions_digest():
    """review-gk (inert SolverConfig fields, both digests): the case's staleness
    key is the same with any record or none."""
    from dataclasses import asdict

    from routers.projects import _solver_config_from_dict
    from services.finance.investment_case_runner import assumptions_digest

    cfg = T.solver_config("eh_datacenter")
    keys = []
    for rec in (
        cfg["finance_assumed"],
        {"wacc_nominal": {"value": 0.5, "source": "x"}},
        None,
    ):
        solver = asdict(_solver_config_from_dict({**cfg, "finance_assumed": rec}))
        keys.append(
            assumptions_digest(
                finance=cfg["finance"],
                commercial=cfg["commercial"],
                dispatch="d",
                packs={},
                solver=solver,
            )[0]
        )
    assert keys[0] == keys[1] == keys[2]


def test_a_sunk_asset_with_a_zero_basis_is_depreciated_by_nothing():
    """The existing campus carries a class (the pack needs one per owned
    asset) but no basis, so its AfA is 0: only the candidates are written off."""
    from models.finance import FinanceInputs
    from services.finance.packs.base import load_pack
    from services.finance.tax_layers import OwnerAsset, resolve_tax_layers

    fin = FinanceInputs.model_validate(T.solver_config("eh_datacenter")["finance"])
    assets = [
        OwnerAsset("genset_1", "gas", 0.0),
        OwnerAsset("genset_new", "gas", 1.0e6),
    ]
    res = resolve_tax_layers(
        load_pack("eu_de", as_of=fin.financial_close),
        fin,
        assets,
        min(fin.cod_by_asset.values()),
    )
    assert res.missing == []
    for layer in res.layers:
        shares = {c.name.rpartition(":")[0]: c.share for c in layer.depreciation}
        assert shares == {"genset_1": 0.0, "genset_new": 1.0}


def test_a_project_saved_before_the_defaults_loads_unchanged():
    """D1: a stored config without the record loads as it was, with no record."""
    from routers.projects import _solver_config_from_dict

    old = json.loads(json.dumps(T.solver_config("eh_h2_hub")))
    old.pop("finance_assumed")
    old["finance"].update(tax_pack_id=None, hebesatz_pct=None)
    cfg = _solver_config_from_dict(old)
    assert cfg.finance_assumed is None
    assert cfg.finance["tax_pack_id"] is None


def test_the_finance_read_marks_each_shipped_value_until_it_is_changed(reset_backend):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from routers.simulation import get_finance

    cfg = T.solver_config("eh_datacenter")
    sim_router._state["solver_config"] = _solver_config_from_dict(cfg)
    out = get_finance()
    wacc = out["assumed"]["wacc_nominal"]
    assert (
        wacc["value"] == 0.07
        and wacc["current"] is True
        and wacc["source"].startswith("assumed")
    )
    assert out["digest"] == get_finance()["digest"]

    edited = {**cfg, "finance": {**cfg["finance"], "wacc_nominal": 0.08}}
    sim_router._state["solver_config"] = _solver_config_from_dict(edited)
    out2 = get_finance()
    assert out2["assumed"]["wacc_nominal"]["current"] is False
    assert out2["assumed"]["tax_pack_id"]["current"] is True

    sim_router._state["solver_config"] = _solver_config_from_dict(
        {k: v for k, v in cfg.items() if k != "finance_assumed"}
    )
    assert get_finance()["assumed"] == {}


def test_the_finance_read_labels_nothing_without_inputs_or_with_a_refused_record(
    reset_backend,
):
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from routers.simulation import get_finance

    cfg = T.solver_config("eh_datacenter")
    sim_router._state["solver_config"] = _solver_config_from_dict(
        {**cfg, "finance": None}
    )
    out = get_finance()
    assert out["status"] == "not_set" and out["assumed"] == {}
    typo = {"wacc_nominall": {"value": 0.07, "source": "assumed"}}
    sim_router._state["solver_config"] = _solver_config_from_dict(
        {**cfg, "finance_assumed": typo}
    )
    out = get_finance()
    assert out["status"] == "ok" and out["assumed"] == {}


def _bind(tmp_path, data):
    """`_bind_template_solver_config` on ``data`` (no commercial block, so no
    network): the issues, the stored config and the file it rewrote."""
    import routers.simulation as sim_router
    from routers.projects import _bind_template_solver_config, _solver_config_from_dict

    path = tmp_path / "solver_config.json"
    path.write_text(json.dumps(data))
    sim_router._state["solver_config"] = _solver_config_from_dict(data)
    issues = _bind_template_solver_config(None, path)
    return issues, sim_router._state["solver_config"], json.loads(path.read_text())


def test_a_new_project_keeps_a_valid_record_and_drops_a_refused_one(
    reset_backend, tmp_path
):
    cfg = {k: v for k, v in T.solver_config("eh_h2_hub").items() if k != "commercial"}
    issues, stored, _ = _bind(tmp_path, cfg)
    assert issues == [] and stored.finance_assumed == cfg["finance_assumed"]

    typo = {
        **cfg["finance_assumed"],
        "wacc_nominall": {"value": 0.07, "source": "assumed"},
    }
    issues, stored, data = _bind(tmp_path, {**cfg, "finance_assumed": typo})
    assert [i["code"] for i in issues] == ["finance_assumed_invalid"]
    assert stored.finance_assumed is None and "finance_assumed" not in data
    assert stored.finance == cfg["finance"]


def test_a_new_project_without_finance_inputs_keeps_no_record(reset_backend, tmp_path):
    cfg = {k: v for k, v in T.solver_config("eh_h2_hub").items() if k != "commercial"}
    issues, stored, _ = _bind(tmp_path, {**cfg, "finance": None})
    assert issues == [] and stored.finance is None and stored.finance_assumed is None


# ── The defaults, unit by unit ──────────────────────────────────────────────


def test_an_unlisted_asset_takes_its_network_life_else_twenty_years():
    import pypsa

    n = pypsa.Network()
    n.add("Bus", "b")
    for name, carrier, life in (
        ("pv", "solar", 30.0),
        ("el_25", "h2", 25.4),
        ("el_1", "h2", 1.0),
        ("el_short", "h2", 0.4),
        ("el_inf", "h2", math.inf),
    ):
        n.add("Generator", name, bus="b", carrier=carrier, lifetime=life)
    got = {
        a: T._afa_class(n, "Generator", a)
        for a in ("pv", "el_25", "el_1", "el_short", "el_inf", "not_in_the_network")
    }
    assert got == {
        "pv": "afa_20",
        "el_25": "afa_25",
        "el_1": "afa_1",
        "el_short": "afa_20",
        "el_inf": "afa_20",
        "not_in_the_network": "afa_20",
    }


_FIN = {"wacc_nominal": 0.07, "financial_close": "2029-01-01"}


def test_the_defaults_fill_only_what_a_template_leaves_unset():
    for built in ({"commercial": {}}, {"finance": None}):
        assert T._with_finance_defaults("eh_h2_hub", built) == built
    fin = {**_FIN, "cod_by_asset": {"a": "2031-01-01", "b": "2030-01-01"}}
    built = {"finance": fin}
    out = T._with_finance_defaults("no_builder", built)
    assert built == {
        "finance": {**_FIN, "cod_by_asset": {"a": "2031-01-01", "b": "2030-01-01"}}
    }
    # Read through the model: its defaults are written out, as the screens store them.
    assert out["finance"]["acquisition_date"] == "2030-01-01"
    assert (
        out["finance"]["price_basis"] == "nominal"
        and out["finance"]["tax_pack_id"] == "eu_de"
    )
    stated = T._with_finance_defaults(
        "no_builder", {"finance": {**fin, "acquisition_date": "2032-01-01"}}
    )
    assert stated["finance"]["acquisition_date"] == "2032-01-01"
    unset = T._with_finance_defaults("no_builder", {"finance": _FIN})
    assert unset["finance"]["acquisition_date"] is None


def test_classes_are_filled_for_owned_assets_under_the_german_pack_only():
    owners = {
        "value_flows": {
            "asset_owners": [
                {"asset_id": "genset_1", "component": "Generator"},
                {"asset_id": "ups_battery", "component": "StorageUnit"},
            ]
        }
    }
    de = T._with_finance_defaults(
        "eh_datacenter",
        {
            "commercial": owners,
            "finance": {**_FIN, "depreciation_class_by_asset": {"genset_1": "afa_5"}},
        },
    )
    assert de["finance"]["depreciation_class_by_asset"] == {
        "genset_1": "afa_5",
        "ups_battery": "afa_10",
    }
    nl = T._with_finance_defaults(
        "eh_datacenter",
        {"commercial": owners, "finance": {**_FIN, "tax_pack_id": "eu_nl"}},
    )
    assert nl["finance"]["depreciation_class_by_asset"] == {}
    # No owners: nothing to class and no network built ("no_builder" has none).
    for commercial in (
        None,
        {},
        {"value_flows": None},
        {"value_flows": {"asset_owners": []}},
    ):
        out = T._with_finance_defaults(
            "no_builder", {"commercial": commercial, "finance": _FIN}
        )
        assert out["finance"]["depreciation_class_by_asset"] == {}


def test_a_planned_campus_case_runs_on_the_entered_years():
    p = T.Planning((2033,), 2051)
    fin = T.solver_config("eh_datacenter", p)["finance"]
    assert fin["financial_close"] == "2032-01-01" and fin["analysis_years"] == 18
    assert set(fin["cod_by_asset"].values()) == {"2033-01-01"}
    assert (
        T.solver_config("eh_datacenter")["finance"]["financial_close"] == "2029-01-01"
    )


def test_a_planned_template_ledger_states_its_grid_readiness_rows_once():
    from collections import Counter

    meta = T.template_meta("eh_datacenter", T.Planning((2033,), 2051))
    items = Counter(r["item"] for r in meta["ledger"])
    assert all(items[r["item"]] == 1 for r in T.grid_readiness_ledger("eh_datacenter"))


def test_the_finance_ledger_states_the_tax_rule_only_where_it_is_the_default(
    monkeypatch,
):
    def rows(fin, ledger=()):
        monkeypatch.setattr(
            T,
            "solver_config",
            lambda tid, p=None: {} if fin is None else {"finance": fin},
        )
        return [
            r["item"]
            for r in T._finance_ledger("eh_h2_hub", None, {"ledger": list(ledger)})
        ]

    assert rows(None) == []
    assert rows({"tax_pack_id": "eu_de", "hebesatz_pct": 400.0}) == ["tax"]
    assert (
        rows({"tax_pack_id": "eu_de", "hebesatz_pct": 400.0}, [{"item": "tax"}]) == []
    )
    assert rows({"tax_pack_id": "eu_nl"}) == []


def test_the_campus_ledger_states_its_shipped_wacc_in_percent():
    ledger = T.template_meta("eh_datacenter")["ledger"]
    row = next(r for r in ledger if r["item"] == "finance inputs")
    assert "all equity at 7 %" in row["value"]
    assert [r["item"] for r in ledger].count("existing campus") == 1
    assert all(
        r["item"] != "existing campus" for r in T.template_meta("eh_h2_hub")["ledger"]
    )


# ── The case on the shipped inputs (D4) ─────────────────────────────────────


def _case(tid):
    """Solve the template with its shipped settings and run the investment
    case as the route does (`_ic_build_case`, the pack by the inputs)."""
    import pathlib
    import tempfile

    import routers.results as R
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.finance.engine import run_case
    from services.finance.packs.base import load_pack
    from services.pypsa_service import PyPSAService
    from services.solver import island as I
    from services.solver_service import run_simulation
    from tests.conftest import install_network_into_backend

    isl = T.island_config(tid)
    if isl is not None:
        d = pathlib.Path(tempfile.mkdtemp())
        (d / SIDECAR_NAME).write_text(json.dumps(isl))
        isl = load_island_config(d / SIDECAR_NAME)
        isl["cuts"] = []
    I_load = I.load_project_island
    I.load_project_island = lambda: isl
    try:
        install_network_into_backend(T.BUILDERS[tid]())
        n = PyPSAService.get_network()
        cfg = _solver_config_from_dict(T.solver_config(tid))
        sim_router._state["solver_config"] = cfg
        state: dict = {}
        status, cond = run_simulation(
            cfg,
            n,
            PyPSAService.get_lock(),
            threading.Event(),
            queue.SimpleQueue(),
            state_update=lambda **k: state.update(k),
        )
        assert status in ("ok", "optimal"), (status, cond)
        case = R._ic_build_case(
            n, cfg, owner=None, lost_load=state.get("last_lost_load")
        )()
        return case, run_case(
            case, load_pack(case.inputs.tax_pack_id, as_of=case.inputs.financial_close)
        )
    finally:
        I.load_project_island = I_load


@pytest.mark.live_solve
def test_the_data_centre_case_runs_on_its_shipped_inputs(reset_backend):
    """The walkthrough's walls (no PoC, no COD, no tax pack, no capex) are gone:
    the case completes with every section established."""
    case, res = _case("eh_datacenter")
    assert res.reasons == {}
    assert res.metrics["project_post_tax_npv"] == pytest.approx(
        -17_784_674.57, rel=1e-6
    )
    # Hand check: the one capex the owner pays is the genset candidate at its
    # overnight cost (the existing campus is sunk at 0; no contingency; the
    # first stage's capex is not escalated, `cashflow` C6), written off
    # straight line over the AfA table's 19 years from the 2030 COD:
    #   21 317 813 EUR / 19 = 1 121 990.16 EUR a year, 2030-2043 (14 years);
    #   2044, the last case year, also writes off the 4 years left:
    #   5 × 1 121 990.16 = 5 609 950.79; Σ = 19 × 1 121 990.16 = 21 317 813.
    genset = next(a for a in case.assets if a.name == "genset_new")
    assert sum(a.overnight_cost or 0.0 for a in case.assets) == genset.overnight_cost
    basis = genset.overnight_cost
    assert float(res.op.capex.sum()) == pytest.approx(basis, rel=1e-9)
    dep = res.tax.depreciation["kst"]
    cod = res.tl.index(2030)
    assert dep[cod] == pytest.approx(basis / 19, rel=1e-9)
    # 2030-2044 is 15 of the 19 years: the last case year also writes off the
    # four years left (5/19), so the whole basis is depreciated.
    assert dep[-1] == pytest.approx(basis * 5 / 19, rel=1e-9)
    assert math.isclose(float(dep.sum()), basis, rel_tol=1e-9)


@pytest.mark.live_solve
@pytest.mark.parametrize(
    "tid,npv", [("eh_h2_hub", -1_837_032.50), ("eh_microgrid", 2_867_262.22)]
)
def test_the_single_year_hub_and_island_cases_establish_tax(reset_backend, tid, npv):
    """The hub and the island were pre-tax (no pack): now every section is
    established, and the post-tax NPV is pinned (a regression pin; the data
    centre above carries the hand check of the tax layer)."""
    _case_, res = _case(tid)
    assert res.reasons == {}
    assert res.metrics["project_post_tax_npv"] == pytest.approx(npv, rel=1e-6)


def test_the_data_centre_network_differs_from_master_only_in_the_sunk_typing(
    monkeypatch,
):
    """GK B2 condition 2 (a): typing the campus as sunk changes `overnight_cost`
    and `discount_rate` on the 7 existing rows and nothing else in any `n.*`
    frame; `capital_cost` stays 0, so the LP and the system cost are master's.

    Without the typing (`DC_EXISTING` empty) the build is master's network:
    checked once against a1ce55d89, where a digest of every static and
    time-varying frame matched with those 14 cells masked."""
    import pandas as pd

    typed = T.build_eh_datacenter()
    monkeypatch.setattr(T, "DC_EXISTING", ())
    untyped = {c.name: c for c in T.build_eh_datacenter().components}
    changed = set()
    for c in typed.components:
        u = untyped.pop(c.name)
        a, b = c.static, u.static
        assert list(a.index) == list(b.index) and list(a.columns) == list(b.columns), (
            c.name
        )
        for col in a.columns:
            same = a[col].eq(b[col]) | (a[col].isna() & b[col].isna())
            changed |= {(c.list_name, row, col) for row in a.index[~same]}
        assert sorted(k for k, v in c.dynamic.items() if not v.empty) == sorted(
            k for k, v in u.dynamic.items() if not v.empty
        ), c.name
        for k, ts in c.dynamic.items():
            if not ts.empty:
                pd.testing.assert_frame_equal(
                    ts, u.dynamic[k], obj=f"{c.list_name}_t.{k}"
                )
    assert not untyped
    existing = {
        "genset_1",
        "genset_2",
        "genset_3",
        "genset_4",
        "rooftop_pv",
        "ups_battery",
        "site_transformer",
    }
    assert {col for *_, col in changed} == {"overnight_cost", "discount_rate"}
    assert {r for _, r, _ in changed} == existing and len(changed) == 14
    for f, r in {(f, r) for f, r, _ in changed}:
        df = getattr(typed, f)
        assert (df.at[r, "overnight_cost"], df.at[r, "discount_rate"]) == (0.0, 0.07)
        assert df.at[r, "capital_cost"] == 0.0
        assert not df.at[r, "p_nom_extendable"]


def test_a_new_site_ships_the_tax_default_with_every_owned_asset_classed():
    """The generic site (eh_site) takes the templates' tax default: German
    corporate tax at the average Hebesatz, bought on the first stage's day one,
    and an AfA class per owned asset, so the tax section is established."""
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(T.__file__).parent))
    import eh_site as S

    p = S.site_params({"site_type": "campus"})
    cfg = S.solver_config(p)
    fin = cfg["finance"]
    assert {k: fin[k] for k in T.TAX_DEFAULT} == T.TAX_DEFAULT
    assert fin["acquisition_date"] == f"{p['stages'][0]['year']}-01-01"
    owners = [o["asset_id"] for o in cfg["commercial"]["value_flows"]["asset_owners"]]
    assert owners and set(fin["depreciation_class_by_asset"]) == set(owners)
    assert fin["depreciation_class_by_asset"]["site_battery"] == "afa_10"
    assert fin["depreciation_class_by_asset"]["genset_1"] == "afa_19"


def test_a_new_sites_shipped_finance_reads_as_assumed_with_its_source(reset_backend):
    """GK B2 F1: the site records what it ships, so GET /finance names each
    value assumed (current) with its source, not as entered."""
    import pathlib
    import sys

    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict
    from routers.simulation import get_finance

    sys.path.insert(0, str(pathlib.Path(T.__file__).parent))
    import eh_site as S

    cfg = S.solver_config(S.site_params({"site_type": "campus"}))
    from models.finance import FinanceInputs

    # Stored as validated and dumped, every field present, as the routes store it.
    assert cfg["finance"] == FinanceInputs.model_validate(cfg["finance"]).model_dump(
        mode="json"
    )
    assert set(cfg["finance"]) == set(FinanceInputs.model_fields)
    assert set(cfg["finance_assumed"]) == {
        k for k in T.FINANCE_SOURCES if cfg["finance"].get(k) not in (None, {}, [])
    }
    sim_router._state["solver_config"] = _solver_config_from_dict(cfg)
    out = get_finance()["assumed"]
    for k in (
        "tax_pack_id",
        "hebesatz_pct",
        "depreciation_class_by_asset",
        "acquisition_date",
        "wacc_nominal",
        "escalation",
    ):
        assert (
            out[k]["current"] is True
            and out[k]["source"] == T.FINANCE_SOURCES[k]["source"]
        )
        assert out[k]["value"] == cfg["finance"][k]


@pytest.mark.parametrize("tid", FINANCE_TEMPLATES)
def test_the_acquisition_date_is_the_first_cod(tid):
    """GK B2 F2: bought at the first commercial operation date, on every
    template (the concentrator's own CODs, not the generic site's year)."""
    fin = T.solver_config(tid)["finance"]
    if fin.get("cod_by_asset"):
        assert fin["acquisition_date"] == min(fin["cod_by_asset"].values())


def test_a_settings_put_keeps_the_record_and_cannot_forge_it(client):
    """`finance_assumed` is off the settings schema: a PUT of other settings
    keeps the stored record, and a record in the body is ignored."""
    import routers.simulation as sim_router
    from routers.projects import _solver_config_from_dict

    client.get("/api/simulation/solver_config")  # the request's own state first
    cfg = T.solver_config("eh_h2_hub")
    sim_router._state["solver_config"] = _solver_config_from_dict(cfg)
    forged = {"wacc_nominal": {"value": 0.01, "source": "forged"}}
    r = client.put(
        "/api/simulation/solver_config",
        json={"solver_name": "highs", "finance_assumed": forged},
    )
    assert r.status_code == 200, r.text
    st = sim_router._state["solver_config"]
    assert st.finance_assumed == cfg["finance_assumed"]
