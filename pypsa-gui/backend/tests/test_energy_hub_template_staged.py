"""The staged-growth data centre template (E2E demo plan 2026-10-14, slice 8).

The single-year campus planned from a projected load: three investment
periods (2027, 2030, 2035), the IT load ramping 12 → 24 → 36 MW, the utility
energizing the 40 MW connection in steps (20 MW until 2030), and one genset
and one battery candidate per period with that period as its build year. The
single-year template is untouched (a golden digest of its build).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TPL_DIR = BACKEND / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

TID = "eh_datacenter_staged"
YEARS = [2027, 2030, 2035]


def _digest(n) -> str:
    """A build's fingerprint that does not depend on the pandas or PyPSA
    version: only the attributes that differ from PyPSA's defaults, numbers
    rounded to 1e-6 and hashed as bytes (a JSON or CSV dump of the frames
    differed between this venv and CI's)."""
    import numpy as np
    import pandas as pd

    h = hashlib.sha256()

    def feed(label, df, defaults):
        h.update(label.encode())
        h.update("|".join(map(str, df.index)).encode())
        for c in sorted(df.columns):
            col = df[c]
            d = defaults.get(c)
            if d is not None and len(col) and (col.astype(object) == d).all():
                continue
            h.update(str(c).encode())
            if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
                h.update(np.round(col.to_numpy(dtype=float), 6).tobytes())
            else:
                h.update("|".join(map(str, col.tolist())).encode())

    for c in sorted(n.components.keys()):
        comp = n.components[c]
        if not len(comp.static):
            continue
        defaults = {a: comp.attrs.at[a, "default"] for a in comp.attrs.index
                    if a in comp.static.columns}
        feed(c, comp.static, defaults)
        for attr, ts in sorted(comp.dynamic.items()):
            if len(ts.columns):
                feed(f"{c}.{attr}", ts, {})
    feed("weights", n.snapshot_weightings, {})
    return h.hexdigest()[:16]


@pytest.fixture(scope="module")
def staged():
    return T.BUILDERS[TID]()


@pytest.fixture(scope="module")
def solved(staged):
    probe = staged.copy()
    probe.snapshots.name = "snapshot"      # PyPSA's copy() drops the MultiIndex's name
    from services.adequacy.energization import apply_energization

    apply_energization(probe)                # as run_simulation does
    status, cond = probe.optimize(solver_name="highs", multi_investment_periods=True)
    assert (status, cond) == ("ok", "optimal")
    return probe


def test_the_single_year_template_is_unchanged():
    # the digest of master's build_eh_datacenter() before staging was added
    assert _digest(T.build_eh_datacenter()) == "84c80723f0e72176"


def test_three_periods_each_a_year_of_hours_with_their_own_length(staged):
    assert list(staged.investment_periods) == YEARS
    w = staged.snapshot_weightings
    for col in ("objective", "generators"):
        assert list(w[col].groupby(level=0).sum()) == pytest.approx([8760.0] * 3)
    assert (w["stores"] == 1.0).all()
    assert list(staged.investment_period_weightings["years"]) == [3.0, 5.0, 10.0]


def test_the_it_load_and_cooling_ramp_with_the_projected_load(staged):
    flat = T.build_eh_datacenter()
    p = staged.loads_t.p_set
    for y, it_mw, _pue in T.DC_PHASES:
        scale = it_mw / 36.0
        for load in ("it_load", "cooling"):
            assert p.loc[y, load].to_numpy() == pytest.approx(
                flat.loads_t.p_set[load].to_numpy() * scale), (y, load)
    assert p.loc[2035, "it_load"].max() == pytest.approx(flat.loads_t.p_set["it_load"].max())


def test_the_import_follows_the_energization_schedule(staged):
    """In MW (`energization`), never as a p_nom fraction a lever would rescale."""
    from services.adequacy.energization import energization_mw

    assert "grid_import" not in staged.links_t.p_max_pu.columns
    e = energization_mw(staged, "grid_import")
    period = staged.snapshots.get_level_values(0)
    for y in YEARS:
        assert e[period == y] == pytest.approx(T.DC_ENERGIZATION_MW[y])


def test_one_candidate_per_period_that_outlives_the_horizon(staged):
    gens = staged.generators[staged.generators.p_nom_extendable]
    bess = staged.storage_units[staged.storage_units.p_nom_extendable]
    assert sorted(gens.build_year) == YEARS and sorted(bess.build_year) == YEARS
    assert "genset_new" not in staged.generators.index and "bess_new" not in staged.storage_units.index
    horizon_end = YEARS[-1] + T.DC_PERIOD_YEARS[YEARS[-1]]          # 2045
    for df in (gens, bess):
        assert ((df.build_year + df.lifetime) >= horizon_end).all()
    # A part that ends inside the horizon (the battery inverter, 10 y) is
    # stated in the ledger, never hidden behind the row's 25-year lifetime.
    parts = [c for c in bess.columns if c.startswith("inv_") and c.endswith("_lifetime")]
    short = sorted({(name, c) for name, row in bess.iterrows() for c in parts
                    if row[c] == row[c] and row.build_year + row[c] < horizon_end})
    assert short, "the inverter really does end inside the horizon"
    ledger = " ".join(f"{r['item']} {r['note']}" for r in T.TEMPLATE_META[TID]["ledger"])
    for name, _c in short:
        if name.endswith(("2027", "2030")):
            assert name in ledger and "part_not_replaced" in ledger, name


def test_the_plan_solves_and_respects_the_20_mw_cap_in_2027(solved):
    flow = solved.links_t.p0["grid_import"]
    assert flow.loc[2027].max() <= 20.0 + 1e-6
    assert flow.loc[2035].max() <= 40.0 + 1e-6
    # a candidate built in a later period produces nothing before it
    for g in ("genset_new_2030", "genset_new_2035"):
        y = int(g.rsplit("_", 1)[1])
        early = solved.generators_t.p[g].loc[[p for p in YEARS if p < y]]
        assert float(early.abs().max()) == pytest.approx(0.0, abs=1e-6)


def test_the_meta_and_solver_config_say_staged_and_multi_period():
    meta = T.TEMPLATE_META[TID]
    assert [r["year"] for r in meta["projected_load"]] == YEARS
    assert [r["available_import_mw"] for r in meta["energization"]] == [20.0, 40.0, 40.0]
    assert all(r["source"] == "assumed" for r in meta["energization"])
    assert meta["ledger"] and meta["ledger"][0]["source"] == "assumed"
    cfg = T.solver_config(TID)
    assert cfg["multi_investment_periods"] is True and cfg["auto_discount_periods"] is True
    assert cfg["voll"] == T.SOLVER_CONFIG["voll"] and cfg["discount_rate"] == 0.07
    assert any(r["item"] == "discount rate" for r in meta["ledger"])


def test_the_island_sidecar_names_every_per_period_candidate(staged):
    cfg = T.ISLAND_CONFIG[TID]
    for y in YEARS:
        assert f"genset_new_{y}" in cfg["units"] and f"bess_new_{y}" in cfg["units"]
    assert "genset_new" not in cfg["units"] and "bess_new" not in cfg["units"]
    assert set(cfg["requirements"]["ride_through_storage"]) == {f"bess_new_{y}" for y in YEARS}
    named = (set(staged.generators.index) | set(staged.storage_units.index)
             | set(staged.links.index) | set(staged.loads.index))
    assert set(cfg["units"]) <= named
    # the single-year sidecar is not mutated by the copy
    assert "genset_new" in T.ISLAND_CONFIG["eh_datacenter"]["units"]


def test_the_committed_sidecars_are_written(staged):
    d = TPL_DIR / TID
    meta = json.loads((d / T.META_FILE).read_text())
    assert meta["id"] == TID


@pytest.mark.parametrize("tid", sorted(T.BUILDERS))
def test_the_build_scripts_verify_step_solves_every_template(tid):
    """`_build.py` verifies each template before exporting it; a crash there
    stops the loop and leaves later templates without a network.nc."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_tpl_build", TPL_DIR / "_build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    build._verify_feasible(T.BUILDERS[tid](), tid, T.solver_config(tid))


def test_with_the_island_rows_the_plan_stages():
    """The island rows (on in the template's solver settings) drive the build:
    a genset in more than one period, and the 2027 import at its 20 MW cap."""
    import json
    import tempfile

    from services.adequacy.island_config import SIDECAR_NAME, load_island_config
    from services.solver import island as I

    d = pathlib.Path(tempfile.mkdtemp())
    (d / SIDECAR_NAME).write_text(json.dumps(T.ISLAND_CONFIG[TID]))
    isl = load_island_config(d / SIDECAR_NAME)
    isl["cuts"] = []
    from services.adequacy.energization import apply_energization

    n = T.BUILDERS[TID]()
    n.snapshots.name = "snapshot"
    apply_energization(n)                    # as run_simulation does
    status, cond = n.optimize(solver_name="highs", multi_investment_periods=True,
                              extra_functionality=I.island_extra_functionality(isl))
    assert (status, cond) == ("ok", "optimal")
    built = {y: float(n.generators.at[f"genset_new_{y}", "p_nom_opt"])
             + float(n.storage_units.at[f"bess_new_{y}", "p_nom_opt"]) for y in YEARS}
    assert sum(1 for v in built.values() if v > 0.1) >= 2, built
    assert built[2035] > built[2027], built
    assert n.links_t.p0["grid_import"].loc[2027].max() == pytest.approx(20.0, abs=1e-4)
