"""
The whole-branch review's MINOR findings, closed.

`notes/2026-09-08-whole-branch-e2e-review.md` §2.2 — the engine-side and
payload-side minors, each a small asymmetry between surfaces that the
review reproduced and none a silent user-reachable defect:

* M2  — a NEGATIVE static `p_max_pu` credited the engines at nameplate
        while the margin credited 0. The fold now clamps to 0 (the margin's
        own rule), so all three agree at 0 MW.
* M3  — an all-NaN `p_max_pu` COLUMN read as "not informative" and the
        unit was credited at nameplate; by rule 1 every unknown hour is
        unavailable, which is what the margin's window nets. The profile
        is now the zero series.
* N1  — LOLE read −1.8e-15 when capacity exactly covered load. Clamped.
* M4  — a typed `outage_rate_value = 0` on a profiled unit carried the
        Phase 12h note asserting "(p_max_pu_includes_outages)". The note
        now says which reason applies, and `deterministic_units` claims the
        flag only when it is set.
* M5  — a FLAGGED unit with a folded static (no column) was in
        `folded_units` but not `deterministic_units`, and its row had no
        note, while the same flag on a column unit got both. Both
        disclosures are built from the flag now.
* M13 — an unset `outage_rate_basis` in a mixed column reloads from netCDF
        as `""`; GET returned `""` where it had returned `null`, and echoing
        the row into PUT was a 422. `""` is `null` on the wire and accepted
        as unset on the way in.
* M14 — MultiIndex peak and net-window hours serialised as tuple reprs
        (`"(2030, Timestamp('…'))"`) straight into the panel. One label
        rule (`window.snapshot_label`), the timestep's ISO string.
* M7  — the margin loop reported the SCHEMA cap (5.0) as `margin_ceiling`
        for an unbounded fleet; the TS doc says `null = unbounded`. `null`
        now; the cap stays internal.
* M6  — the reserve margin derated storage by its outage rate while the MC
        and the portfolio ELCC dispatched it with none. ONE rule: the MC
        reads the same resolver and applies `(1 − q)` as an expected-value
        derate of power and energy (no new random draws — the CRN stream
        is untouched); an unusable storage rate is refused like a
        generator's.

Bites (verified red against the named removal): the fold's `max(cf, 0)`
(M2); the all-NaN early return (M3); the `np.maximum` clamp (N1);
`deterministic_row_note` → the flag note unconditionally (M4);
`is_flag_deterministic` → `split.deterministic` (M5); the serialiser's
`""`→`null` and the schema's before-validator (M13); `snapshot_label` →
`str` (M14); `m_max` → `m_ceiling` in the payload (M7); `_avail` → 1.0 in
`block_store_arrays` (M6).
"""
from __future__ import annotations

import dataclasses
import math

import numpy as np
import pandas as pd
import pypsa
import pytest

from services.adequacy import copt as C
from services.adequacy import mc as MC
from services.adequacy.occurrence import OutageRateError
from services.adequacy.window import snapshot_label
from services.solver_service import reserve_margin_facts


def _base(periods=8) -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=periods, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Carrier", "battery")
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=300.0,
          marginal_cost=10.0, outage_rate_value=0.05, outage_rate_basis="EFORd",
          mttr_hours=24.0)
    n.add("Load", "l", bus="b", p_set=100.0)
    return n


# ── M2 ────────────────────────────────────────────────────────────────────

def test_a_negative_static_availability_folds_to_zero_everywhere():
    """★ M2. COPT capacity 0, the MC's expectation 0, the margin's derate 0.

    Bite (verified): `return max(cf, 0.0)` back to `return None` for cf < 0
    — the engines credit nameplate × (1 − q) = 95 MW."""
    n = _base()
    n.add("Generator", "neg", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=12.0, p_max_pu=-0.2, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    units, _, _ = C.fleet_and_residual(n)
    u = next(x for x in units if x.name == "neg")
    assert u.capacity_mw == 0.0
    assert u.folded_constant == 0.0
    assert C.static_fold_factor(n.generators, None, "neg") == 0.0
    # …and the fold still declines a factor of 1 or more (the 12h gate).
    n.generators.at["neg", "p_max_pu"] = 1.5
    assert C.static_fold_factor(n.generators, None, "neg") is None


# ── M3 ────────────────────────────────────────────────────────────────────

def test_an_all_nan_availability_column_is_zero_availability():
    """★ M3. A column with no finite hour: profile of zeros (rule 1), so the
    unit contributes nothing — the margin's window nets it the same way.

    Bite (verified): drop the `not finite.any()` early return — profile None
    and the unit credited at nameplate × (1 − q)."""
    n = _base()
    n.add("Generator", "blank", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=12.0, outage_rate_value=0.05, outage_rate_basis="EFORd",
          mttr_hours=24.0)
    n.generators_t.p_max_pu["blank"] = np.nan
    units, _, _ = C.fleet_and_residual(n)
    u = next(x for x in units if x.name == "blank")
    assert u.profile is not None
    assert np.all(u.profile == 0.0)
    # Direct: the profile helper itself.
    prof = C._occurrence_profile(n.generators_t.p_max_pu, "blank", n.snapshots)
    assert prof is not None and float(np.abs(prof).sum()) == 0.0


# ── N1 ────────────────────────────────────────────────────────────────────

def test_lole_is_never_negative_when_capacity_exactly_covers_load(monkeypatch):
    """★ N1. The review measured −1.7763568394002505e-15 on its ten-unit
    fixture (`agentA/s1`): the survival table's rounding, summed. The
    rounding is not reproducible on demand from a hand fixture (six fleets
    tried all read ≥ 1e-9), so the clamp is pinned by INJECTING the rounding
    the review saw: `mixture_hourly` returns a −1.8e-15 hour and
    `hourly_adequacy` must sum to a non-negative LOLE and EUE.

    Bite (verified): drop the `np.maximum` clamp — the metric reads
    −1.8e-15 × weight."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, outage_rate_value=0.1, outage_rate_basis="EFORd",
          mttr_hours=24.0)
    n.add("Load", "l", bus="b", p_set=100.0)
    units, residual, w = C.fleet_and_residual(n)
    dist = C.build_copt(units)
    real = C.mixture_hourly

    def rounding(d, r, mixed, **kw):
        lolp, eue = real(d, r, mixed, **kw)
        # Every hour, as on the review's fixture, where capacity exactly
        # covered load in every hour and every hour rounded below zero.
        lolp = np.full_like(lolp, -1.7763568394002505e-15)
        eue = np.full_like(eue, -1.7763568394002505e-13)
        return lolp, eue

    monkeypatch.setattr(C, "mixture_hourly", rounding)
    out = C.hourly_adequacy(dist, residual, weights=w)
    assert out["lole_hours"] >= 0.0 and out["eue_mwh"] >= 0.0
    assert all(v["lole_hours"] >= 0.0 and v["eue_mwh"] >= 0.0
               for v in out["by_period"].values())


# ── M4 / M5 ───────────────────────────────────────────────────────────────

def _flag_fixture() -> pypsa.Network:
    n = _base()
    # A typed-zero profiled unit (NOT flagged) …
    n.add("Generator", "typed0", bus="b", carrier="wind", p_nom=100.0,
          marginal_cost=0.0, outage_rate_value=0.0, outage_rate_basis="EFORd",
          mttr_hours=24.0)
    n.generators_t.p_max_pu["typed0"] = [0.2, 0.5, 0.0, 0.0, 0.5, 0.5, 0.9, 0.9]
    # … a flagged unit with a folded STATIC (no column) …
    n.add("Generator", "flag_static", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=12.0, p_max_pu=0.8, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=24.0,
          p_max_pu_includes_outages=True)
    # … and a flagged unit with a column.
    n.add("Generator", "flag_col", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=12.0, outage_rate_value=0.05, outage_rate_basis="EFORd",
          mttr_hours=24.0, p_max_pu_includes_outages=True)
    n.generators_t.p_max_pu["flag_col"] = [0.5] * 4 + [0.9] * 4
    n.generators["p_max_pu_includes_outages"] = n.generators[
        "p_max_pu_includes_outages"].fillna(False).astype(bool)
    return n


def test_the_deterministic_disclosure_claims_the_flag_only_when_set():
    """★ M4 + M5. The row note names the reason; `deterministic_units` is
    every flag-zeroed unit — folded or profiled — and nothing else.

    Bites (verified): `deterministic_row_note` → the flag note always (the
    typed-zero row claims the flag); `is_flag_deterministic` → profiled-only
    (the folded flagged unit drops out of the list)."""
    n = _flag_fixture()
    units, residual, w = C.fleet_and_residual(n)
    by = {u.name: u for u in units}
    assert by["typed0"].outages_in_availability is False
    assert by["flag_static"].outages_in_availability is True
    assert by["flag_col"].outages_in_availability is True
    assert C.rate_is_zero(by["typed0"]) and C.rate_is_zero(by["flag_static"])
    assert C.is_flag_deterministic(by["flag_static"])
    assert C.is_flag_deterministic(by["flag_col"])
    assert not C.is_flag_deterministic(by["typed0"])

    out = C.screening_analysis(units, residual, weights=w, voll=1000.0)
    notes = {r["name"]: (r.get("note") or r.get("failure_mode", {}).get("note") or "")
             for r in out["rows"]}
    assert "p_max_pu_includes_outages" in notes["flag_col"]
    assert "p_max_pu_includes_outages" in notes["flag_static"]
    assert "p_max_pu_includes_outages" not in notes["typed0"]
    assert "0 as entered" in notes["typed0"]
    assert notes["firm"] == ""


def test_copt_and_mc_payloads_list_every_flag_zeroed_unit(client, install_network,
                                                          session_state):
    """The two routes agree: `deterministic_units` is the flagged pair and
    not the typed-zero unit; `folded_units` still names the fold."""
    install_network(_flag_fixture())
    r = client.get("/api/results/copt")
    assert r.status_code == 200, r.text
    fleet = r.json()["fleet"]
    assert sorted(fleet["deterministic_units"]) == ["flag_col", "flag_static"]
    assert [f["name"] for f in fleet["folded_units"]] == ["flag_static"]
    st = session_state(client)
    st["solver_config"] = dataclasses.replace(st["solver_config"], voll=1000.0)
    r = client.post("/api/results/mc", json={"draws": 20, "seed": 1})
    assert r.status_code == 200, r.text
    import time
    for _ in range(200):
        body = client.get("/api/results/mc").json()
        if body.get("status") in ("done", "failed", "aborted"):
            break
        time.sleep(0.05)
    assert body["status"] == "done", body
    res = body["result"]
    assert sorted(res["deterministic_units"]) == ["flag_col", "flag_static"]
    # The typed-zero unit is named by its OWN list (F8) and by neither the
    # flag's nor the profile one. Written as an `or` first, which no fleet
    # could fail (IEEE 39-bus review, N-a).
    assert "typed0" not in res["deterministic_units"]
    assert "typed0" not in res["profile_units"]
    assert res["rate_zero_units"] == ["typed0"]


# ── M13 ───────────────────────────────────────────────────────────────────

def test_an_unset_basis_is_null_on_the_wire_and_accepted_back(client, install_network):
    """★ M13. A mixed column after the netCDF round trip spells the unset
    basis as ""; GET says null and PUT of that row is 200.

    Bites (verified): drop the serialiser's mapping → GET returns "";
    drop the before-validator → PUT of "" is 422."""
    from services.pypsa_service import PyPSAService
    import pathlib, tempfile

    n = _base()
    n.add("Generator", "unset", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=12.0, outage_rate_value=0.1, mttr_hours=24.0)
    # Round-trip through the helpers: "EFORd" beside NaN → object column with "".
    d = pathlib.Path(tempfile.mkdtemp())
    PyPSAService.export_network_to_netcdf(n, d / "n.nc")
    m = pypsa.Network()
    PyPSAService.import_network_from_netcdf(m, d / "n.nc")
    assert m.generators.at["unset", "outage_rate_basis"] == ""
    install_network(m)
    rows = {r["name"]: r for r in client.get("/api/network/generators").json()}
    assert rows["unset"]["outage_rate_basis"] is None
    assert rows["firm"]["outage_rate_basis"] == "EFORd"
    r = client.put("/api/network/generators/unset", json=rows["unset"])
    assert r.status_code == 200, r.text
    # And the schema accepts the blank spellings directly.
    from models.schemas import GeneratorCreate
    for blank in ("", " ", "nan", "None"):
        assert GeneratorCreate(name="x", bus="b", outage_rate_basis=blank).outage_rate_basis is None


# ── M14 ───────────────────────────────────────────────────────────────────

def test_snapshot_labels_are_timesteps_not_tuple_reprs():
    """★ M14. Bite (verified): `snapshot_label` → `str` — the label reads
    "(2030, Timestamp('2030-01-01 21:00:00'))"."""
    ts = pd.Timestamp("2030-01-01 21:00:00")
    assert snapshot_label((2030, ts)) == "2030-01-01 21:00:00"
    assert snapshot_label(ts) == "2030-01-01 21:00:00"
    assert "Timestamp(" not in snapshot_label((2030, ts))
    assert snapshot_label("x") == "x"


def test_multi_period_margin_payload_carries_iso_labels(client, install_network,
                                                        session_state):
    """The two label sites — the margin stash and the net-window block —
    through the surface a user reads."""
    n = pypsa.Network()
    idx = pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=6, freq="h")],
        names=["period", "timestep"])
    n.set_snapshots(idx)
    n.investment_periods = [2030, 2040]
    n.add("Bus", "b", carrier="AC")
    n.add("Carrier", "gas")
    n.add("Carrier", "wind")
    n.add("Generator", "firm", bus="b", carrier="gas", p_nom=300.0,
          marginal_cost=10.0, outage_rate_value=0.05, outage_rate_basis="EFORd",
          mttr_hours=24.0)
    n.add("Generator", "w", bus="b", carrier="wind", p_nom=100.0, marginal_cost=0.0)
    n.generators_t.p_max_pu["w"] = [0.1, 0.9, 0.2, 0.8, 0.3, 0.7] * 2
    n.add("Load", "l", bus="b", p_set=100.0)
    n.loads_t.p_set = pd.DataFrame({"l": [100, 120, 90, 130, 95, 125] * 2}, index=idx)
    install_network(n)
    st = session_state(client)
    from services.solver_service import SolverConfig
    st["solver_config"] = SolverConfig(multi_investment_periods=True,
                                       investment_periods=[2030, 2040],
                                       reserve_margin=0.2, voll=1000.0)
    r = client.post("/api/simulation/run")
    assert r.status_code == 200, r.text
    import time
    for _ in range(600):
        s = client.get("/api/simulation/status").json()
        if s.get("status") in ("completed", "failed", "aborted", "idle"):
            break
        time.sleep(0.1)
    assert s.get("status") == "completed", s
    rm = client.get("/api/results/reserve_margin")
    assert rm.status_code == 200, rm.text
    for per in rm.json()["by_period"]:
        for lab in per["peak_snapshots"]:
            assert "Timestamp(" not in lab and not lab.startswith("("), lab
        nw = per.get("net_window") or {}
        for lab in nw.get("snapshots") or []:
            assert "Timestamp(" not in lab and not lab.startswith("("), lab


# ── M7 ────────────────────────────────────────────────────────────────────

def test_margin_ceiling_is_null_for_an_unbounded_fleet(client, install_network,
                                                        session_state, monkeypatch):
    """★ M7. Bite (verified): `m_max` → `m_ceiling` in the payload — 5.0."""
    from tests.test_adequacy_margin_loop import (
        _Stubs, _install_stubs, _poll, _setup, _start, _unbounded_network,
    )
    stubs = _Stubs(firm_base=130.0, lole_fn=lambda m: 9.0)
    _install_stubs(monkeypatch, stubs)
    _setup(client, install_network, network=_unbounded_network(),
           reserve_margin=0.10)
    _start(client, max_solves=3)
    body = _poll(client)
    assert body["margin_ceiling"] is None, body.get("margin_ceiling")


# ── M6 ────────────────────────────────────────────────────────────────────

def test_storage_outage_rate_is_one_rule_across_surfaces():
    """★ M6. A battery's carrier-default rate (0.02) derates the MC's power
    and energy arrays and the margin's credit by the same (1 − q); a typed
    storage rate does the same; an unusable one is refused.

    Bite (verified): `_avail` → 1.0 in `block_store_arrays` — the MC
    dispatches nameplate while the margin credits 0.98 × nameplate."""
    n = _base()
    n.add("StorageUnit", "bat", bus="b", carrier="battery", p_nom=50.0,
          max_hours=4.0, marginal_cost=1.0)
    n.add("StorageUnit", "typed", bus="b", carrier="battery", p_nom=20.0,
          max_hours=2.0, marginal_cost=1.0, outage_rate_value=0.1,
          outage_rate_basis="FOR", mttr_hours=12.0)
    inputs = MC.snapshot_inputs(n)
    by = {s.name: s for s in inputs.storage}
    assert by["bat"].q == pytest.approx(0.02) and by["bat"].source == "carrier_default"
    assert by["typed"].q == pytest.approx(0.1) and by["typed"].source == "asset"
    assert by["bat"].p_nom_mw == pytest.approx(50.0)          # nameplate kept
    k, p, e, es, ed = MC.block_store_arrays(inputs.storage, 0, len(n.snapshots),
                                           any_series=False)
    assert k == 2
    assert p[0] == pytest.approx(50.0 * 0.98) and e[0] == pytest.approx(200.0 * 0.98)
    assert p[1] == pytest.approx(20.0 * 0.9) and e[1] == pytest.approx(40.0 * 0.9)
    # The margin credits the same (1 − q).
    from services.solver_service import SolverConfig
    facts = reserve_margin_facts(n, SolverConfig(reserve_margin=0.2))
    rows = {r["name"]: r for r in facts["stash"]["assets"]}
    assert rows["bat"]["q"] == pytest.approx(0.02)
    assert rows["bat"]["derate"] == pytest.approx(0.98)      # (1 − q) × haircut 1.0
    assert rows["typed"]["q"] == pytest.approx(0.1)
    # The sampler's per-unit availability factor is the margin's (1 − q).
    assert p[0] / by["bat"].p_nom_mw == pytest.approx(1.0 - rows["bat"]["q"])
    assert p[1] / by["typed"].p_nom_mw == pytest.approx(1.0 - rows["typed"]["q"])
    # Refused like a generator's.
    n.storage_units.at["typed", "outage_rate_value"] = 1.5
    with pytest.raises(OutageRateError):
        MC.snapshot_inputs(n)
