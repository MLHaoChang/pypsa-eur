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


@pytest.mark.xfail(
    strict=True,
    reason="requires the _merge_partial_update custom-column fix from PR #4 "
    "(claude/fix-lost-load-cost-and-custom-attr-drop); this XPASSes the "
    "moment that merge lands — then DELETE this marker",
)
def test_partial_put_preserves_outage_attributes():
    """Task 0 gate. `_update_component` goes through remove+add; without the
    PR #4 fix, a partial PUT omitting the custom columns silently resets
    them."""
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
