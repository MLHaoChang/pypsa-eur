"""
Outage-rate attributes + the per-carrier occurrence library (Phase 0 Task 2).

Design: docs/superpowers/specs/2026-08-27-solution-fmea-adequacy-design.md §5.4.
Three custom columns on five components (`outage_rate_value`,
`outage_rate_basis`, `mttr_hours`), following the `curtailment_cost` pattern:
stored on the component DataFrame, no PyPSA meaning, netCDF round-trip for
free. NaN/None means "unset → fall back to the carrier default library".
The basis (FOR vs EFORd) is a label and is NEVER silently converted.
"""
from __future__ import annotations

import math
import pathlib
import tempfile

import numpy as np
import pandas as pd
import pypsa
import pytest

from models import schemas
from services.adequacy import occurrence


# ── schema declarations ───────────────────────────────────────────────────

@pytest.mark.parametrize("schema_cls", [
    schemas.GeneratorCreate, schemas.StorageUnitCreate, schemas.StoreCreate,
    schemas.LinkCreate, schemas.LineCreate,
])
def test_all_five_schemas_declare_the_outage_fields(schema_cls):
    """Pydantic's extra="ignore" silently drops undeclared fields on POST/PUT,
    so a missing declaration means the attribute can never be written."""
    fields = schema_cls.model_fields
    for f in ("outage_rate_value", "outage_rate_basis", "mttr_hours"):
        assert f in fields, f"{schema_cls.__name__} is missing {f}"


def test_basis_rejects_unknown_values():
    with pytest.raises(Exception):
        schemas.GeneratorCreate(name="g", bus="b", outage_rate_basis="EFOR")


# ── persistence ───────────────────────────────────────────────────────────

def _net_with_outage_gen() -> pypsa.Network:
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "g_set", bus="b", carrier="gas", p_nom=100.0,
          outage_rate_value=0.05, outage_rate_basis="EFORd", mttr_hours=50.0)
    n.add("Generator", "g_unset", bus="b", carrier="gas", p_nom=50.0)
    return n


def test_netcdf_round_trip_preserves_values_and_nan():
    n = _net_with_outage_gen()
    with tempfile.TemporaryDirectory() as td:
        path = str(pathlib.Path(td) / "n.nc")
        n.export_to_netcdf(path)
        m = pypsa.Network(path)
    assert float(m.generators.at["g_set", "outage_rate_value"]) == pytest.approx(0.05)
    assert str(m.generators.at["g_set", "outage_rate_basis"]) == "EFORd"
    assert float(m.generators.at["g_set", "mttr_hours"]) == pytest.approx(50.0)
    # Unset stays unset — custom columns get no fillna(default) on import,
    # so NaN must survive rather than becoming a fake 0.0.
    unset = m.generators.at["g_unset", "outage_rate_value"]
    assert unset is None or (isinstance(unset, float) and math.isnan(unset)) or unset == "" or str(unset) == "nan"


def test_partial_put_preserves_outage_attributes():
    """Task 0 gate. `_update_component` goes through remove+add, so without
    the `_merge_partial_update` custom-column widening a partial PUT that
    omits the outage columns silently resets them — which would make every
    occurrence-driven number downstream quietly wrong.

    This carried a `strict=True` xfail until the fix landed on master
    (07b32c2, PR #4). Strict was the point: an unexpected PASS is a failure,
    so the dependency could not be forgotten once the merge made it moot."""
    import routers.network as NET
    n = _net_with_outage_gen()
    merged = NET._merge_partial_update(n, "generators", "g_set", {"p_nom": 7.0})
    assert merged["p_nom"] == 7.0
    assert merged.get("outage_rate_value") == pytest.approx(0.05)
    assert merged.get("outage_rate_basis") == "EFORd"
    assert merged.get("mttr_hours") == pytest.approx(50.0)


# ── resolution: asset → carrier default → missing ─────────────────────────

def test_resolve_prefers_asset_values():
    n = _net_with_outage_gen()
    res = occurrence.resolve_outage_params(n, "generators")
    row = res.loc["g_set"]
    assert row["rate"] == pytest.approx(0.05)
    assert row["basis"] == "EFORd"
    assert row["mttr_hours"] == pytest.approx(50.0)
    assert row["source"] == "asset"


def test_resolve_falls_back_to_carrier_default():
    n = _net_with_outage_gen()
    res = occurrence.resolve_outage_params(n, "generators")
    row = res.loc["g_unset"]
    d = occurrence.CARRIER_DEFAULTS["gas"]
    assert row["rate"] == pytest.approx(d.rate)
    assert row["basis"] == d.basis
    assert row["mttr_hours"] == pytest.approx(d.mttr_hours)
    assert row["source"] == "carrier_default"


def test_resolve_marks_unknown_carriers_missing_never_guesses():
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "exotic", bus="b", carrier="unobtainium", p_nom=1.0)
    res = occurrence.resolve_outage_params(n, "generators")
    row = res.loc["exotic"]
    assert row["source"] == "missing"
    assert math.isnan(float(row["rate"]))


def test_vre_carriers_have_no_default_on_purpose():
    """VRE availability is profile-borne (p_max_pu); a mechanical FOR default
    would double-count. The library must not carry wind/solar entries."""
    for c in ("wind", "solar", "onwind", "offwind", "solar rooftop"):
        assert c not in occurrence.CARRIER_DEFAULTS


def test_every_default_names_its_source():
    for carrier, d in occurrence.CARRIER_DEFAULTS.items():
        assert d.source and len(d.source) > 10, f"{carrier} default has no source"
        assert 0.0 < d.rate < 1.0
        assert d.mttr_hours > 0
        assert d.basis in ("FOR", "EFORd")


# ── the consistency validator ─────────────────────────────────────────────

def _params_df(**row) -> pd.DataFrame:
    base = {"rate": 0.05, "basis": "EFORd", "mttr_hours": 50.0, "source": "asset"}
    base.update(row)
    return pd.DataFrame([base], index=["u1"])


def test_validator_passes_a_plausible_pair():
    assert occurrence.validate_outage_params(_params_df(rate=0.05, mttr_hours=72.0)) == []


def test_validator_flags_the_specs_canonical_bad_pair():
    """FOR 0.10 with MTTR 24 h ⇒ 8760·0.10/24 = 36.5 outages/yr — the
    over-determined pair the spec calls out. Must warn, naming the unit."""
    warnings = occurrence.validate_outage_params(_params_df(rate=0.10, mttr_hours=24.0))
    assert any("u1" in w and "36.5" in w for w in warnings), warnings


def test_validator_flags_out_of_range_rate_and_nonpositive_mttr():
    assert occurrence.validate_outage_params(_params_df(rate=1.2))
    assert occurrence.validate_outage_params(_params_df(rate=-0.1))
    assert occurrence.validate_outage_params(_params_df(mttr_hours=0.0))


def test_validator_ignores_missing_rows():
    df = _params_df(rate=float("nan"), source="missing")
    assert occurrence.validate_outage_params(df) == []


# ── preflight wiring ──────────────────────────────────────────────────────

def test_preflight_warns_on_implausible_asset_pair():
    from services import validation_service as VS
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Bus", "b", carrier="AC")
    n.add("Load", "l", bus="b", p_set=1.0)
    n.add("Generator", "bad_pair", bus="b", carrier="gas", p_nom=10.0,
          marginal_cost=1.0,
          outage_rate_value=0.10, outage_rate_basis="EFORd", mttr_hours=24.0)
    issues = VS._check_outage_params(n)
    assert issues, "implausible pair produced no preflight warning"
    assert all(i.severity == "warning" for i in issues)
    assert any("bad_pair" in i.message and i.code == "outage_params_implausible"
               for i in issues)


def test_preflight_is_silent_without_outage_data():
    from services import validation_service as VS
    n = pypsa.Network()
    n.add("Bus", "b", carrier="AC")
    n.add("Generator", "g", bus="b", carrier="gas", p_nom=10.0)
    assert VS._check_outage_params(n) == []


# ── ★ a profile SHADOWED by outage data ───────────────────────────────────

def _profiled(n, name, *, outage: bool, profile=None):
    """A 100 MW generator carrying an hourly p_max_pu profile, optionally with
    an outage rate entered as well."""
    kw = dict(bus="b", carrier="wind", p_nom=100.0, marginal_cost=0.0)
    if outage:
        kw.update(outage_rate_value=0.10, outage_rate_basis="EFORd",
                  mttr_hours=24.0)
    n.add("Generator", name, **kw)
    n.generators_t.p_max_pu[name] = (
        profile if profile is not None
        else np.tile([0.05, 0.15, 0.35, 0.45], len(n.snapshots) // 4))


def _two_farm_network():
    """Two IDENTICAL 100 MW wind farms sharing one 25 %-capacity-factor
    profile. The ONLY difference is whether an outage rate was entered."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "wind"); n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "gas1", bus="b", carrier="gas", p_nom=80.0,
          marginal_cost=10.0, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    _profiled(n, "wind_no_for", outage=False)
    _profiled(n, "wind_with_for", outage=True)
    return n


def _codes(issues, code):
    return " ".join(i.message for i in issues if i.code == code)


def test_preflight_DISCLOSES_how_a_profile_plus_outage_unit_is_modelled():
    """★ Phase 12c-pre A10 (supersedes 12a's A1/A3): the engines now MODEL
    the series — outages sampled on it, the COPT mixed exactly per hour —
    so the preflight issue is a disclosure of how, not a warning that the
    profile is discarded. It fires for the farm whose outage data the user
    typed (`source == "asset"`), names it, says how it is modelled, and is a
    `warning` (the only non-error severity on the wire).

    Silent on the farm with NO outage data (must-take, netted as before) and
    on the thermal unit whose column is a flat 1.0. The old
    `outage_shadows_profile` code is gone: with the profile modelled it
    would be a false statement.

    Bite (verified): leave the old series branch in — `outage_shadows_profile`
    reappears on `wind_with_for`.
    """
    from services import validation_service as VS
    issues = VS._check_profiled_occurrence_units(_two_farm_network())
    assert not [i for i in issues if i.code == "outage_shadows_profile"], issues
    msg = _codes(issues, "profile_and_outage_modelled")
    assert "wind_with_for" in msg, msg
    assert "wind_no_for" not in msg, msg
    assert "gas1" not in msg, msg
    assert "mixes" in msg and "sampled on the availability series" in msg, msg
    assert all(i.severity == "warning" for i in issues), issues
    # …and the engines really do carry the series in (the disclosure is true).
    from services.adequacy.copt import fleet_and_residual
    units, _res, _w = fleet_and_residual(_two_farm_network())
    by = {u.name: u for u in units}
    assert by["wind_with_for"].profile is not None
    assert by["gas1"].profile is None


def test_a_flat_profile_is_not_disclosed():
    """★ A2, the false-positive guard, kept from 12a — the single way this
    ships as noise: a thermal unit with outage data AND an all-ones column
    must stay silent, and must NOT carry a profile into the engines.

    Bite (verified): test only for the COLUMN's presence.
    """
    from services import validation_service as VS
    from services.adequacy.copt import fleet_and_residual
    n = _two_farm_network()
    n.add("Generator", "gas2", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=12.0, outage_rate_value=0.06,
          outage_rate_basis="EFORd", mttr_hours=30.0)
    n.generators_t.p_max_pu["gas2"] = np.ones(len(n.snapshots))
    msg = _codes(VS._check_profiled_occurrence_units(n),
                 "profile_and_outage_modelled")
    assert "gas2" not in msg, msg
    units, _r, _w = fleet_and_residual(n)
    assert {u.name: u.profile is None for u in units}["gas2"]


def _hydro_library_network():
    """A hydro unit whose outage rate comes from the LIBRARY with an inflow
    series, and NO hand-entered outage data anywhere (no outage columns at
    all — the PyPSA-Eur import shape)."""
    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "hydro"); n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "gas_lib", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=10.0)
    n.add("Generator", "hydro_inflow", bus="b", carrier="hydro", p_nom=100.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu["hydro_inflow"] = np.tile(
        [0.05, 0.15, 0.35, 0.45], 2)
    return n


def test_a_CARRIER_DEFAULT_profiled_unit_is_modelled_but_not_warned_about():
    """★ A10, Q4 decided (plan 12c-pre v2.1): a hydro unit with an inflow
    series and a LIBRARY outage rate is modelled on its series like any other
    profiled unit — the engines carry the profile — but preflight is SILENT:
    the user typed nothing, and a warning on every hydro project is one
    nobody reads. The `/copt` and `/mc` payloads carry the disclosure.

    Bite (verified): emit `profile_and_outage_modelled` for carrier-default
    sources too.
    """
    from services import validation_service as VS
    from services.adequacy.copt import fleet_and_residual
    n = _hydro_library_network()
    units, _r, _w = fleet_and_residual(n)
    by = {u.name: u for u in units}
    assert by["hydro_inflow"].source == "carrier_default"
    assert by["hydro_inflow"].profile is not None
    issues = VS._check_profiled_occurrence_units(n)
    assert not [i for i in issues if i.code == "profile_and_outage_modelled"], issues


def test_a_STATIC_derate_below_one_is_NOT_applied_and_says_so():
    """★ A10 (12a's SERIOUS 6b re-scoped): a static `p_max_pu < 1` on a unit
    with outage data is not applied by either engine (plan §1.3 — it is
    ambiguous in the wild and folding it in double-counts PyPSA-Eur's
    nuclear CF, which already contains outages). The issue names the unit
    and the disagreement, offers "enter it as a time series", and does NOT
    offer "set q = 0" — that remedy models a perfectly firm unit (plan v2
    review, finding 3).

    Bite (verified): inspect only `generators_t.p_max_pu`.
    """
    from services import validation_service as VS

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "gas_static", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, p_max_pu=0.5, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=24.0)

    msg = _codes(VS._check_profiled_occurrence_units(n),
                 "static_p_max_pu_not_applied")
    assert "gas_static" in msg, msg
    assert "time series" in msg, msg
    assert "q = 0" not in msg and "q=0" not in msg, msg


def test_the_static_warning_reaches_an_import_with_NO_outage_columns():
    """★ A10 (plan v2 review, finding 3): the PyPSA-Eur nuclear import — a
    static CF below 1, a LIBRARY outage rate, and no outage column anywhere
    — is the case the static warning is written for, and the column-gated
    `_check_outage_params` could never reach it. The disclosure walks the
    membership instead, and `validate_for_run`-style callers get it without
    any outage column.

    Bite (verified): gate the check on `outage_rate_value` being present.
    """
    from services import validation_service as VS

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "nuclear")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "nuc", bus="b", carrier="nuclear", p_nom=1000.0,
          marginal_cost=5.0, p_max_pu=0.8)
    assert "outage_rate_value" not in n.generators.columns
    msg = _codes(VS._check_profiled_occurrence_units(n),
                 "static_p_max_pu_not_applied")
    assert "nuc" in msg, msg


def test_a_static_p_max_pu_of_ONE_is_still_silent():
    """★ The noise guard, restated for the static arm: p_max_pu = 1.0 is the
    default on every generator and must never warn."""
    from services import validation_service as VS

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=4, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    n.add("Generator", "plain", bus="b", carrier="gas", p_nom=100.0,
          marginal_cost=10.0, outage_rate_value=0.10,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    issues = VS._check_profiled_occurrence_units(n)
    assert not [i for i in issues if i.code == "static_p_max_pu_not_applied"], issues
