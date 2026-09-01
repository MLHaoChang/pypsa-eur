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


def test_preflight_warns_when_outage_data_SHADOWS_an_availability_profile():
    """★ A1/A3: entering outage data silently discards the asset's profile.

    Measured on this exact fixture before the warning existed. Two identical
    100 MW farms on one 25 %-capacity-factor profile:

      * `wind_no_for`   -> must-take, netted at its profile   ->  25 MW mean
      * `wind_with_for` -> sampled fleet, profile DISCARDED,
                           flat two-state at capacity_mw       ->  90 MW

    and the reserve margin credits that SAME asset `(1-q)*profile̅` = 22.5 MW.
    So the constraint says 22.5 MW and the sampler that certifies it says 90 —
    4x — and entering MORE data credits the asset ~3.6x more.

    `copt.py` documents the split deliberately ("a generator with resolvable
    occurrence params is a two-state COPT unit at its firm capacity"), on the
    assumption that VRE carries no FOR — `occurrence.CARRIER_DEFAULTS` says
    "Deliberately ABSENT: wind / solar". Nothing stops a user entering one by
    hand, and then the profile is dropped in silence.

    The warning must name the DIRECTION, not merely the conflict: a warning
    that does not say which way it errs cannot be acted on.

    Bite (verified): drop the `_profile_is_informative` test and warn on every
    unit with outage data — `gas1` then appears and the warning is noise.
    """
    from services import validation_service as VS
    issues = VS._check_outage_params(_two_farm_network())
    shadow = [i for i in issues if i.code == "outage_shadows_profile"]
    assert shadow, "the shadowed profile produced no preflight warning"
    msg = " ".join(i.message for i in shadow)
    assert "wind_with_for" in msg, msg
    # …and NOT the farm that has no outage data — its profile is honoured.
    assert "wind_no_for" not in msg, msg
    # …nor the thermal unit, whose p_max_pu is a flat 1.0.
    assert "gas1" not in msg, msg
    # The direction is the actionable part.
    assert "OVERSTAT" in msg.upper(), msg
    assert all(i.severity == "warning" for i in shadow), shadow


def test_a_flat_profile_is_not_a_shadowed_profile():
    """★ A2, the false-positive guard — the single way this ships as noise.

    Every conventional generator in a real project carries outage data, and
    many carry a `p_max_pu` column that is identically 1.0. If the warning
    fires there it is noise on every unit of every project and will be
    ignored, taking the real signal with it.

    Bite (verified): test only for the COLUMN's presence rather than for a
    profile that actually varies.
    """
    from services import validation_service as VS
    n = _two_farm_network()
    # A thermal unit with outage data AND an explicit all-ones profile column.
    n.add("Generator", "gas2", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=12.0, outage_rate_value=0.06,
          outage_rate_basis="EFORd", mttr_hours=30.0)
    n.generators_t.p_max_pu["gas2"] = np.ones(len(n.snapshots))
    issues = VS._check_outage_params(n)
    msg = " ".join(i.message for i in issues
                   if i.code == "outage_shadows_profile")
    assert "gas2" not in msg, (
        "a flat all-ones profile is not a resource profile, and warning on it "
        "would fire on every thermal unit in every real project: " + msg)


def test_a_CARRIER_DEFAULT_outage_rate_shadows_a_profile_too(client=None):
    """★ SERIOUS 6a: the check saw only HAND-ENTERED outage data.

    `_check_outage_params` narrows to `params["source"] == "asset"` before
    the shadow check runs, but `copt.fleet_and_residual` admits a unit on
    `source != "missing"` — which includes `carrier_default`. And
    `CARRIER_DEFAULTS` covers hydro and battery among others, so a HYDRO unit
    with an inflow `p_max_pu` needs ZERO user input to hit the defect: the
    engines drop its profile and model it as firm capacity at the library's
    outage rate.

    That destroys the "rare by construction, so it cannot become wallpaper"
    argument the original commit rested on — the commonest instance needs no
    user input at all.

    Bite (verified): pass the `source == "asset"` filtered frame to the check.
    """
    from services import validation_service as VS

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=8, freq="h"))
    n.snapshot_weightings.loc[:, :] = 1.0
    n.add("Carrier", "hydro"); n.add("Carrier", "gas")
    n.add("Bus", "b", carrier="AC", country="AA")
    n.add("Load", "l", bus="b", p_set=100.0)
    # Hand-entered data on ONE unit, so the enclosing validator runs at all.
    n.add("Generator", "gas_asset", bus="b", carrier="gas", p_nom=50.0,
          marginal_cost=10.0, outage_rate_value=0.05,
          outage_rate_basis="EFORd", mttr_hours=24.0)
    # …and a hydro unit whose outage rate comes from the LIBRARY, with an
    # inflow profile the engines will discard.
    n.add("Generator", "hydro_inflow", bus="b", carrier="hydro", p_nom=100.0,
          marginal_cost=0.0)
    n.generators_t.p_max_pu["hydro_inflow"] = np.tile(
        [0.05, 0.15, 0.35, 0.45], 2)

    msg = " ".join(i.message for i in VS._check_outage_params(n)
                   if i.code == "outage_shadows_profile")
    assert "hydro_inflow" in msg, (
        "a carrier-default outage rate shadows the profile just as a "
        "hand-entered one does, and needs no user input at all: " + msg)


def test_a_STATIC_derate_below_one_is_shadowed_too():
    """★ SERIOUS 6b: the check looked only at the time-series frame.

    `solved_capacity` uses `p_nom_opt`/`p_nom` only, so a static
    `p_max_pu = 0.5` is discarded exactly like an hourly profile — the unit
    enters the fleet at full nameplate. This was open question 3 in the
    plan and shipped unanswered.

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

    msg = " ".join(i.message for i in VS._check_outage_params(n)
                   if i.code == "outage_shadows_profile")
    assert "gas_static" in msg, (
        "a static p_max_pu below 1 is discarded exactly like a profile: "
        + msg)


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
    msg = " ".join(i.message for i in VS._check_outage_params(n)
                   if i.code == "outage_shadows_profile")
    assert "plain" not in msg, msg
