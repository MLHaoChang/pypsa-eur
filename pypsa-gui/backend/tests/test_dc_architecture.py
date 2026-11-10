"""DC grid readiness 05: power-architecture comparison (AC / MV UPS / 800 VDC / SST).

GK A (15) C1–C9 and GK B (28) K1–K9; the issue's ## Comments hold them.
"""

from __future__ import annotations

import csv
import json
import math
import os
import pathlib
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pypsa
import pytest

from services import dc_architecture as A
from services.library.defaults_pack import loader

PACK = pathlib.Path(loader.__file__).parent / "versions"
NEW, PREV = "2026-11-07", "2026-11-06"


# ── the pack (K1, K2, C7) ─────────────────────────────────────────────────


def test_every_previous_row_is_byte_equal_and_the_new_version_is_the_default():
    old = (PACK / PREV / "values.csv").read_bytes()
    cur = (PACK / NEW / "values.csv").read_bytes()
    assert cur.startswith(old)
    added = [line.split(b",", 1)[0].decode() for line in cur[len(old) :].splitlines()]
    assert added and all(k.startswith("dc-arch-") for k in added)
    for f in ("finance.yaml", "tariffs.json"):
        assert (PACK / NEW / f).read_bytes() == (PACK / PREV / f).read_bytes()
    pins = json.loads(
        (
            pathlib.Path(__file__).parent
            / "fixtures"
            / "defaults_pack"
            / "pack_hashes.json"
        ).read_text()
    )["generic_defaults"]
    assert loader.load_defaults_pack(PREV).hash == pins[PREV]
    assert loader.load_defaults_pack(NEW).hash == pins[NEW]
    assert NEW == A.ARCH_PACK_VERSION   # pinned; DC BC 02 (2026-11-10) is the unpinned default now


def test_pinned_callers_are_unaffected():
    from project_templates.eh_templates import TEMPLATE_PACK_VERSION
    from services.adequacy.redundancy import REDUNDANCY_PACK_VERSION

    for v in (TEMPLATE_PACK_VERSION, REDUNDANCY_PACK_VERSION):
        assert v != NEW
        assert not any(
            r.key.startswith("dc-arch-")
            for r in loader.load_defaults_pack(v).cost_values
        )


def test_architecture_rows_are_assumed_ranged_real_per_mw_of_it_without_connection():
    pack = loader.load_defaults_pack(NEW)
    rows = [r for r in pack.cost_values if r.key.startswith("dc-arch-")]
    assert {r.technology for r in rows} == set(A.TECHNOLOGY.values())
    for r in rows:
        assert r.range is not None and r.range.source == "assumed" and r.illustrative
        assert r.includes_grid_connection is False
        if r.part == "investment":
            assert r.basis == "per_MW" and r.capacity_basis == "output_electric"
            assert r.price_basis == "real"
        else:
            assert r.part is None and r.parameter == "efficiency"
            assert r.capacity_basis == "not_applicable"
        if r.parameter == "overnight":
            # K2: EUR/kW in the transcription, EUR/MW in the pack, once.
            assert r.original_unit == "EUR/kW" and r.unit == "EUR/MW"
            assert r.value == pytest.approx(r.original_value * 1000.0)
            assert r.currency == "EUR" and r.currency_year == 2025


def _write_values(tmp_path, rows: list[dict]) -> pathlib.Path:
    src = PACK / NEW / "values.csv"
    with src.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header, base = reader.fieldnames, list(reader)
    p = tmp_path / "values.csv"
    with p.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header, lineterminator="\n")
        w.writeheader()
        w.writerows(base + rows)
    return p


def _row(src_key: str, **kw) -> dict:
    src = next(
        r
        for r in csv.DictReader((PACK / NEW / "values.csv").open(encoding="utf-8"))
        if r["key"] == src_key
    )
    return {**src, **kw}


def test_unchanged_loader_takes_the_stage_rows_and_still_refuses_a_stage_part(tmp_path):
    ok = _write_values(tmp_path, [])
    assert len(loader._parse_values(ok, typed=True)) == len(
        loader.load_defaults_pack(NEW).cost_values
    )
    bad = _write_values(
        tmp_path,
        [
            _row(
                "dc-arch-ac.stage.ups.efficiency",
                key="dc-arch-ac.ups.efficiency",
                part="ups",
                basis="per_MW",
                capacity_basis="output_electric",
            )
        ],
    )
    with pytest.raises(loader.DefaultsPackError):
        loader._parse_values(bad, typed=True)


def test_a_stated_unit_that_disagrees_with_the_catalogue_unit_is_refused(tmp_path):
    bad = _write_values(
        tmp_path,
        [
            _row(
                "dc-arch-ac.investment.overnight",
                key="dc-arch-x.investment.overnight",
                technology="dc-arch-x",
                unit="EUR/kW",
            )
        ],
    )
    with pytest.raises(loader.DefaultsPackError, match="no conversion"):
        loader._parse_values(bad, typed=True)


# ── the chains (C2, C7) ───────────────────────────────────────────────────


def test_chain_is_the_product_of_its_stage_rows_with_the_rack_stage_apart():
    pack = loader.load_defaults_pack(NEW)
    rows = {r.key: r.value for r in pack.cost_values}
    for a, tech in A.TECHNOLOGY.items():
        c = A.chain(a, pack)
        stages = [k for k in rows if k.startswith(f"{tech}.stage.")]
        to_rack = [rows[k] for k in stages if ".stage.rack." not in k]
        assert c["eta_to_rack"] == pytest.approx(math.prod(to_rack))
        assert c["eta_rack"] == rows[f"{tech}.stage.rack.efficiency"]
    assert A.chain("ac")["eta_to_rack"] == pytest.approx(0.985 * 0.955 * 0.99)
    assert A.chain("sst")["eta_to_rack"] > A.chain("dc_800v")["eta_to_rack"]
    lo, hi = A.chain("ac", pick="low"), A.chain("ac", pick="high")
    assert lo["eta_to_rack"] < A.chain("ac")["eta_to_rack"] < hi["eta_to_rack"]
    with pytest.raises(A.DcArchitectureError) as e:
        A.chain("hvdc")
    assert e.value.kind == "dc_architecture_invalid"


# ── the algebra (Q-A1, C3, C9) ────────────────────────────────────────────


def test_variant_facility_is_the_closed_form_and_the_own_load_is_unchanged():
    ac, sst, v800 = A.chain("ac"), A.chain("sst"), A.chain("dc_800v")
    pue = np.array([1.3, 1.4])
    own = np.array([130.0, 140.0])  # IT 100 MW × PUE
    assert np.array_equal(A.variant_facility(own, pue, ac, ac), own)
    got = A.variant_facility(own, pue, ac, sst)
    it_v = 100.0 * ac["eta_rack"] / sst["eta_rack"]  # the in-rack line
    want = it_v * (pue - 1 / ac["eta_to_rack"] + 1 / sst["eta_to_rack"])
    assert got == pytest.approx(want, rel=1e-12)
    # The sign: a more efficient chain draws less (own and variant not swapped).
    assert (got < own).all()
    assert A.variant_facility(
        got, pue - 1 / ac["eta_to_rack"] + 1 / sst["eta_to_rack"], sst, ac
    ) == pytest.approx(own)
    # The in-rack stage alone (no to-rack change) still shifts the IT energy.
    same = dict(v800, eta_to_rack=ac["eta_to_rack"])
    assert A.variant_facility(own, pue, ac, same) == pytest.approx(
        own * ac["eta_rack"] / v800["eta_rack"]
    )


def test_a_pue_below_the_own_chain_is_refused_never_clipped():
    eta = A.chain("ac")["eta_to_rack"]
    A.check_pue([1.3, 1.5], eta)
    A.check_pue([1.0 / eta], eta)  # zero overhead holds
    with pytest.raises(A.DcArchitectureError) as e:
        A.check_pue([1.3, 1.05], eta)
    assert e.value.kind == "dc_architecture_pue_infeasible"


def test_create_refuses_an_infeasible_pue_only_for_a_set_architecture():
    from project_templates import eh_templates as T
    from services.dc_load_profile import DcLoadError

    with pytest.raises(DcLoadError) as e:
        T.dc_load_spec("eh_datacenter", {"architecture": "ac", "pue_at_design": 1.05})
    assert e.value.field == "dc_load.pue_at_design"
    assert T.dc_load_spec("eh_datacenter", {"pue_at_design": 1.05}).architecture == "ac"
    with pytest.raises(DcLoadError) as e:
        T.dc_load_spec("eh_datacenter", {"architecture": "hvdc"})
    assert e.value.field == "dc_load.architecture"


# ── the ledger and stored spec (C1, C2) ───────────────────────────────────

UPS_LINE = "ups_distribution_loss_share 0.03 is not added: the losses are inside PUE"


def test_unset_architecture_keeps_the_ledger_and_stored_spec_verbatim():
    from project_templates import eh_templates as T
    from services.dc_load_profile import meta_block

    block = meta_block(T.dc_load_spec("eh_datacenter", {}), None)
    assert "architecture" not in block["spec"]
    items = {r["item"]: r["value"] for r in block["ledger"]}
    assert items["UPS and distribution losses"] == UPS_LINE
    assert "power architecture" not in items and "PUE boundary" not in items


def test_a_set_architecture_carries_its_chain_and_boundary_in_the_ledger():
    from project_templates import eh_templates as T
    from services.dc_load_profile import meta_block

    block = meta_block(
        T.dc_load_spec("eh_datacenter", {"architecture": "dc_800v"}), None
    )
    assert block["spec"]["architecture"] == "dc_800v"
    items = {r["item"]: r["value"] for r in block["ledger"]}
    assert "UPS and distribution losses" not in items
    assert "Central AC/800 VDC rectifier 0.975" in items["power architecture"]
    assert "category 3" in items["PUE boundary"]


def test_sidecars_of_every_template_are_unchanged():
    from project_templates import eh_templates as T

    root = pathlib.Path(T.__file__).parent
    for tid in T.TEMPLATE_META:
        shipped = root / tid
        if not shipped.is_dir():
            continue
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            T.write_sidecars(pathlib.Path(tmp), tid)
            for f in pathlib.Path(tmp).rglob("*"):
                if f.is_file():
                    rel = f.relative_to(tmp)
                    assert f.read_bytes() == (shipped / rel).read_bytes(), (tid, rel)


# ── services (C6) ─────────────────────────────────────────────────────────


def test_services_are_gridspine_words_and_storage_gates_the_conditional_ones():
    from services.adequacy.island_config import SERVICES

    for a in A.ARCHITECTURES:
        assert set(A.ARCH_SERVICES[a]) <= set(SERVICES)
    assert A.services("ac", True) == {"counted": [], "conditional": []}
    no = A.services("sst", False)
    assert no["counted"] == ["voltage_control"]
    assert {"grid_forming", "synthetic_inertia"} <= set(no["conditional"])
    yes = A.services("sst", True)
    assert {"grid_forming", "synthetic_inertia"} <= set(yes["counted"]) and not yes[
        "conditional"
    ]
    assert "fast_frequency_response" in A.services("dc_800v", False)["conditional"]


def test_an_architecture_service_alone_never_passes():
    from services.adequacy.grid_readiness import service_verdict

    assert service_verdict(None)["verdict"] == "unknown"
    assert service_verdict([])["verdict"] == "warn"
    assert service_verdict(["grid_forming"])["verdict"] == "pass"
    got = service_verdict([], ["fast_frequency_response"], architecture="MV UPS")
    assert got["verdict"] == "warn" and "assumed, MV UPS" in got["project_value"]
    got = service_verdict(None, [], conditional=["grid_forming"])
    assert got["verdict"] == "warn" and got["conditional"] == ["grid_forming"]
    assert (
        service_verdict(["voltage_control"], wanted=("voltage_control",))["verdict"]
        == "pass"
    )


# ── cost legs (K4, K6, K9) ────────────────────────────────────────────────


def _ann(r, life):
    return r / (1 - (1 + r) ** -life)


def _pvf(r, n):
    return (1 - (1 + r) ** -n) / r


def test_leg_npv_by_hand_lifetime_beyond_the_horizon():
    r, c = 0.05, 1000.0
    got = A.leg_npv(
        overnight=c, fom=0.0, life=15, it_mw=1.0, energy_cost=0.0, years=10, r=r
    )
    residual = c * _ann(r, 15) * _pvf(r, 5)
    assert got["total"] == pytest.approx(c - residual / (1 + r) ** 10)


def test_leg_npv_by_hand_one_repurchase_plus_residual():
    r, c, e = 0.05, 1000.0, 10.0
    got = A.leg_npv(
        overnight=c, fom=0.02, life=15, it_mw=2.0, energy_cost=e, years=20, r=r
    )
    cap = 2 * c
    residual = cap * _ann(r, 15) * _pvf(r, 10)
    want = (
        cap
        + (cap - 0) / (1 + r) ** 15
        - residual / (1 + r) ** 20
        + (0.02 * cap + e) * _pvf(r, 20)
    )
    assert got["total"] == pytest.approx(want)


def _leg(total_v, total_lo, total_hi):
    return {
        "npv": {
            "value": {"total": total_v},
            "low": {"total": total_lo},
            "high": {"total": total_hi},
        }
    }


def test_an_interval_ending_exactly_at_zero_is_not_distinguishable():
    """K9: an interval end at exactly 0 still spans zero."""
    own = _leg(100.0, 90.0, 110.0)
    low_at_zero = A.delta_interval(_leg(120.0, 110.0, 130.0), own)
    assert (low_at_zero["low"], low_at_zero["high"]) == (0.0, 40.0)
    assert low_at_zero["verdict"] == "not_distinguishable"
    high_at_zero = A.delta_interval(_leg(80.0, 70.0, 90.0), own)
    assert (high_at_zero["low"], high_at_zero["high"]) == (-40.0, 0.0)
    assert high_at_zero["verdict"] == "not_distinguishable"


def test_an_interval_that_spans_zero_names_no_cheaper_architecture():
    own = _leg(100.0, 90.0, 110.0)
    assert (
        A.delta_interval(_leg(95.0, 85.0, 105.0), own)["verdict"]
        == "not_distinguishable"
    )
    assert A.delta_interval(_leg(50.0, 40.0, 60.0), own)["verdict"] == "cheaper"
    assert A.delta_interval(_leg(200.0, 190.0, 210.0), own)["verdict"] == "dearer"
    vs = [
        dict(
            _leg(100.0, 90.0, 110.0),
            architecture="ac",
            label="AC",
            services={"counted": []},
        ),
        dict(
            _leg(95.0, 85.0, 105.0),
            architecture="sst",
            label="SST",
            services={"counted": ["voltage_control"]},
        ),
    ]
    s = A.summary(vs)
    assert s["cheapest"] is None and "not distinguishable" in s["cheapest_text"]
    vs[1]["npv"] = _leg(10.0, 5.0, 20.0)["npv"]
    assert A.summary(vs)["cheapest"] == "sst"


class _Pack:
    def __init__(self, drop=None, **change):
        p = loader.load_defaults_pack(NEW)
        self.version, self.finance = p.version, p.finance
        rows = []
        for r in p.cost_values:
            if r.key == drop:
                continue
            upd = change.get(r.key)
            rows.append(r.model_copy(update=upd) if upd else r)
        self.cost_values = rows


@pytest.mark.parametrize(
    "bad",
    [
        {"drop": "dc-arch-sst.investment.lifetime"},
        {"dc-arch-sst.investment.fom_share": {"value": None}},
        {"dc-arch-sst.investment.overnight": {"value": float("nan")}},
        {"dc-arch-sst.investment.overnight": {"price_basis": "nominal"}},
        {"dc-arch-sst.investment.overnight": {"range": None}},
    ],
)
def test_capex_rows_fail_closed(bad):
    with pytest.raises(A.DcArchitectureError) as e:
        A.capex_rows("sst", _Pack(**bad))
    assert e.value.kind == "dc_architecture_pack_row"


def test_mixed_money_years_are_refused():
    legs = {a: A.capex_rows(a) for a in A.ARCHITECTURES}
    A._check_money_years(legs)
    legs["sst"] = {**legs["sst"], "money_year": 2020}
    with pytest.raises(A.DcArchitectureError) as e:
        A._check_money_years(legs)
    assert e.value.kind == "dc_architecture_pack_row"


# ── the screen on a small network (C5, K5) ────────────────────────────────


def _net(weight=2.0, hours=24):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-07-01", periods=hours, freq="h"))
    n.snapshot_weightings.loc[:, ["objective", "generators"]] = weight
    n.add("Bus", "b")
    p = np.linspace(120.0, 140.0, hours)
    n.add("Load", "it_load", bus="b", p_set=0.75 * p)
    n.add("Load", "cooling", bus="b", p_set=0.25 * p)
    return n, p


def _spec(**kw):
    from project_templates import eh_templates as T

    return T.dc_load_spec("eh_datacenter", kw)


def _compare(n, spec, **kw):
    kw.setdefault("energy_price_per_mwh", 80.0)
    return A.compare(
        n, spec, own_set=False, timezone=None, years=10, it_mw=100.0, island=None, **kw
    )


def test_energy_uses_the_generator_weighting_and_the_peak_does_not():
    spec = _spec()
    n1, p = _net(1.0)
    n2, _ = _net(2.5)
    a, b = _compare(n1, spec), _compare(n2, spec)
    own_a = next(v for v in a["variants"] if v["own"])
    own_b = next(v for v in b["variants"] if v["own"])
    assert own_a["facility_mwh"] == pytest.approx(p.sum())
    assert own_b["facility_mwh"] == pytest.approx(2.5 * p.sum())
    assert own_a["peak_mw"] == own_b["peak_mw"] == pytest.approx(p.max())
    assert a["weighting"] == "generators"
    assert own_b["effective_pue"] == pytest.approx(own_a["effective_pue"])


def test_the_screen_orders_own_first_and_states_its_assumptions_once():
    out = _compare(_net()[0], _spec())
    assert [v["architecture"] for v in out["variants"]][0] == "ac"
    assert (
        out["variants"][0]["own"] and out["variants"][0]["npv_delta"]["central"] == 0.0
    )
    text = json.dumps(out)
    assert text.count(A.RMS_CAVEAT) == 1
    assert (
        "category 3" in out["boundary"] and "flat over part load" in out["second_order"]
    )
    assert out["price"]["source"] == "request (assumed)" and out["price"]["assumed"]
    assert "not valued" in out["price"]["not_valued"]
    assert out["range_note"] == A.RANGE_NOTE
    sst = next(v for v in out["variants"] if v["architecture"] == "sst")
    assert sst["facility_mwh"] < out["variants"][0]["facility_mwh"]
    assert sst["verdicts"]["frequency_support"]["verdict"] == "warn"


def test_a_genset_only_backup_leaves_every_conditional_service_unpassed():
    spec = _spec()
    assert spec.backup.mode == "genset" and not A.dc_bus_storage(spec)
    out = _compare(_net()[0], spec)
    assert out["dc_bus_storage"] is False
    gated = {
        a: {s for s, how in A.ARCH_SERVICES[a].items() if how == "needs_storage"}
        for a in A.ARCHITECTURES
    }
    assert any(gated.values())
    for v in out["variants"]:
        a = v["architecture"]
        assert set(v["services"]["conditional"]) == gated[a]
        assert not gated[a] & set(v["services"]["counted"])
        for verdict in v["verdicts"].values():
            assert verdict["verdict"] != "pass"
            assert set(verdict.get("conditional") or []) <= gated[a]
        assert gated[a] <= set(A.services(a, True)["counted"])


def test_the_own_leg_keeps_the_saved_load_at_every_cost_end():
    n, p = _net()
    out = _compare(n, _spec())
    own = next(v for v in out["variants"] if v["own"])
    assert own["npv"]["low"]["energy"] == own["npv"]["value"]["energy"]
    assert own["npv"]["high"]["energy"] == own["npv"]["value"]["energy"]
    other = next(v for v in out["variants"] if v["architecture"] == "dc_800v")
    assert other["npv"]["low"]["energy"] < other["npv"]["high"]["energy"]


def test_a_multi_period_network_counts_the_final_period_once():
    n, p = _net(weight=365.0)
    one = _compare(n, _spec())
    m = pypsa.Network()
    m.set_snapshots(n.snapshots)
    m.set_investment_periods([2027, 2030])
    m.snapshot_weightings.loc[:, ["objective", "generators"]] = 365.0
    m.add("Bus", "b")
    early = np.r_[0.5 * p, p]
    m.add("Load", "it_load", bus="b", p_set=0.75 * early)
    m.add("Load", "cooling", bus="b", p_set=0.25 * early)
    two = _compare(m, _spec())
    assert "2030" in two["energy_basis"] and one["energy_basis"] == "the modelled year"
    for a, b in zip(one["variants"], two["variants"]):
        assert b["facility_mwh"] == pytest.approx(a["facility_mwh"])
        assert b["npv"]["value"]["energy"] == pytest.approx(a["npv"]["value"]["energy"])


def test_the_price_follows_the_projects_finance_currency():
    with pytest.raises(A.DcArchitectureError) as e:
        _compare(_net()[0], _spec(), finance_currency="USD")
    assert e.value.kind == "dc_architecture_currency_mismatch"
    assert (
        _compare(_net()[0], _spec(), finance_currency="EUR")["price"]["currency"]
        == "EUR"
    )


def test_an_unset_architecture_leaves_the_scorecard_as_before(tmp_path, monkeypatch):
    from project_templates import eh_templates as T
    from services.adequacy import grid_readiness as G

    def card(name, dc_load):
        d = tmp_path / name
        d.mkdir()
        spec = T.dc_load_spec("eh_datacenter", dc_load)
        n = T.BUILDERS["eh_datacenter"](load_spec=spec)
        T.write_sidecars(d, "eh_datacenter")
        meta = json.loads((d / "eh_template.json").read_text())
        meta["dc_load"] = T.dc_load_meta("eh_datacenter", spec)
        (d / "eh_template.json").write_text(json.dumps(meta))
        return d, n

    d, n = card("unset", {})
    unset = json.dumps(
        G.grid_readiness(d, n, "eu_rfg_dcc_ce"), sort_keys=True, default=str
    )
    with monkeypatch.context() as mp:
        mp.setattr(G, "_arch_offer", lambda inp: None)
        before = json.dumps(
            G.grid_readiness(d, n, "eu_rfg_dcc_ce"), sort_keys=True, default=str
        )
    assert unset == before
    d, n = card("sst", {"architecture": "sst"})
    rows = {r["id"]: r for r in G.grid_readiness(d, n, "eu_rfg_dcc_ce")["rows"]}
    assert A.RMS_CAVEAT in rows["frequency_support"]["note"]


def test_without_a_tariff_or_a_price_the_screen_refuses():
    with pytest.raises(A.DcArchitectureError) as e:
        _compare(_net()[0], _spec(), energy_price_per_mwh=None)
    assert e.value.kind == "dc_architecture_no_energy_price"
    assert str(e.value).startswith("Energy price: ") and "Enter a price per MWh" in str(
        e.value
    )


def test_the_screen_says_what_it_counts_and_why_a_peak_is_unknown():
    out = _compare(_net()[0], _spec())
    assert out["summary"]["most_services_text"].startswith(
        "Most grid services without added storage: "
    )
    for v in out["variants"]:
        assert v["peak"]["verdict"] == "unknown"
        assert v["peak"]["note"] == "the network has no import connection to check"


def _tou(currency=None):
    items = [
        {
            "id": "energy",
            "kind": "energy",
            "unit": "per_kwh",
            "periods": [
                {"name": "peak", "rate": 0.2, "start_hour": 8, "end_hour": 20},
                {"name": "off", "rate": 0.05},
            ],
        }
    ]
    return {
        "poc_link": "poc",
        "import_tariff": {
            "id": "tou",
            "name": "TOU",
            "jurisdiction": "XX",
            "valid_from": "2030-01-01",
            "items": items,
        },
    }


def _energy_items(*hour_spans):
    return [
        {
            "id": f"e{k}",
            "kind": "energy",
            "unit": "per_kwh",
            "periods": [{"name": f"p{k}", "rate": 0.1, "start_hour": a, "end_hour": b}],
        }
        for k, (a, b) in enumerate(hour_spans)
    ]


def _tariff(items):
    c = _tou()
    c["import_tariff"]["items"] = items
    return c


def test_an_hour_no_energy_item_rates_is_refused_never_priced_at_zero():
    n, _ = _net(1.0)
    with pytest.raises(A.DcArchitectureError) as e:
        _compare(
            n,
            _spec(),
            energy_price_per_mwh=None,
            commercial=_tariff(_energy_items((8, 20))),
        )
    assert e.value.kind == "dc_architecture_no_energy_price"
    assert "for 12 of the 24 modelled hours" in str(e.value)


def test_split_items_that_cover_every_hour_still_price_the_energy():
    n, _ = _net(1.0)
    out = _compare(
        n,
        _spec(),
        energy_price_per_mwh=None,
        commercial=_tariff(_energy_items((0, 12), (12, 24))),
    )
    assert out["price"]["time_mean_per_mwh"] == pytest.approx(100.0)


def test_own_set_is_read_from_the_stored_spec():
    assert A.is_set({}) is False and A.is_set({"pue_at_design": 1.3}) is False
    assert A.is_set({"architecture": "ac"}) is True


def test_the_range_note_names_what_stays_central():
    assert "this project's own chain stays central" in A.RANGE_NOTE
    assert "lifetime and fixed O&M stay at their central values" in A.RANGE_NOTE


def test_a_tou_tariff_is_weighted_by_the_load_not_the_clock():
    n, p = _net(1.0)
    out = _compare(n, _spec(), energy_price_per_mwh=None, commercial=_tou())
    rate = np.where((np.arange(24) >= 8) & (np.arange(24) < 20), 200.0, 50.0)
    assert out["price"]["time_mean_per_mwh"] == pytest.approx(rate.mean())
    assert out["price"]["weighted_mean_per_mwh"] == pytest.approx(
        (p * rate).sum() / p.sum()
    )
    assert not out["price"]["assumed"] and out["price"]["source"].startswith(
        "tariff tou"
    )


def test_a_tariff_in_another_currency_is_refused(monkeypatch):
    from models import commercial

    tariff = SimpleNamespace(currency="USD", items=[], id="t")
    monkeypatch.setattr(
        commercial.CommercialConfig,
        "model_validate",
        classmethod(
            lambda cls, raw: SimpleNamespace(import_tariff=tariff, timezone=None)
        ),
    )
    with pytest.raises(A.DcArchitectureError) as e:
        A.tariff_energy_rate(
            {"x": 1}, pd.date_range("2030", periods=2, freq="h"), "EUR"
        )
    assert e.value.kind == "dc_architecture_currency_mismatch"


def test_the_screen_refuses_an_infeasible_own_pue():
    with pytest.raises(A.DcArchitectureError) as e:
        _compare(_net()[0], _spec(pue_at_design=1.05))
    assert e.value.kind == "dc_architecture_pue_infeasible"


# ── the route (C8, K8) ────────────────────────────────────────────────────


def _stage_dc(tmp_path, monkeypatch, tid="eh_datacenter"):
    from project_templates import eh_templates as T
    from routers import projects as projects_router

    src = tmp_path / tid
    src.mkdir()
    T.BUILDERS[tid]().export_to_netcdf(str(src / "network.nc"))
    T.write_sidecars(src, tid)
    monkeypatch.setattr(projects_router, "_PROJECT_TEMPLATES_DIR", tmp_path)
    (tmp_path / "eh_templates.py").write_text(pathlib.Path(T.__file__).read_text())


def _snapshot(d: pathlib.Path) -> dict:
    return {
        str(p.relative_to(d)): (p.read_bytes(), os.stat(p).st_mtime_ns)
        for p in sorted(d.rglob("*"))
        if p.is_file()
    }


def test_compare_route_writes_nothing_and_answers_every_architecture(
    client, tmp_path, monkeypatch, tmp_projects_dir, project_row, session_ctx
):
    from services import project_registry
    from services.workflow_tools import _network_input_fingerprint

    _stage_dc(tmp_path, monkeypatch)
    r = client.post(
        "/api/projects/from_template/eh_datacenter",
        params={"name": "dc_arch"},
        json={"dc_load": {"architecture": "mv_ups"}},
    )
    assert r.status_code == 200, r.text
    name = r.json().get("name", "dc_arch")
    d = pathlib.Path(project_registry.project_dir(project_row(name)))
    ctx = session_ctx(client)
    before, fp = _snapshot(d), _network_input_fingerprint(ctx)
    r = client.post(
        f"/api/campus-electrical/{name}/dc-architecture/compare",
        json={"energy_price_per_mwh": 90.0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["own"] == "mv_ups" and body["own_set"]
    assert {v["architecture"] for v in body["variants"]} == set(A.ARCHITECTURES)
    assert body["pack_version"] == NEW
    assert all(
        v["peak"]["verdict"] in ("pass", "warn", "fail", "unknown")
        for v in body["variants"]
    )
    assert _snapshot(d) == before and _network_input_fingerprint(ctx) == fp
    r = client.post(f"/api/campus-electrical/{name}/dc-architecture/compare", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["error_kind"] == "dc_architecture_no_energy_price"
    assert _snapshot(d) == before


def test_compare_route_refuses_a_project_without_a_dc_spec(
    client, tmp_path, monkeypatch, tmp_projects_dir
):
    _stage_dc(tmp_path, monkeypatch)
    r = client.post(
        "/api/projects/from_template/eh_datacenter", params={"name": "dc_plain"}
    )
    name = r.json().get("name", "dc_plain")
    r = client.post(
        f"/api/campus-electrical/{name}/dc-architecture/compare",
        json={"energy_price_per_mwh": 90.0},
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error_kind"] == "dc_architecture_no_spec"


def test_every_refusal_kind_is_declared():
    frag = json.loads(
        (
            pathlib.Path(__file__).parents[2]
            / "tool-error-kinds.d"
            / "claude-dc-05-architecture-compare.json"
        ).read_text()
    )["kinds"]
    import ast

    tree = ast.parse(pathlib.Path(A.__file__).read_text())
    used = {
        f"dc_architecture_{c.args[1].value}"
        for c in ast.walk(tree)
        if isinstance(c, ast.Call)
        and getattr(c.func, "id", None) == "_err"
        and isinstance(c.args[1], ast.Constant)
    }
    assert len(used) == 6 and used == set(frag)
