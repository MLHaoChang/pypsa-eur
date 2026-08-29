"""
The published-benchmark gate for the adequacy substrate (plan Task 7, spec §7).

WHAT THIS FILE IS FOR
---------------------
Every unit test in ``test_adequacy_copt.py`` checks the convolution against
hand arithmetic on two-unit toy systems. That proves the code does what the
code intends. It does not prove the *substrate* — the FOR interpretation, the
capacity distribution, the hourly LOLP/EUE accounting — agrees with what the
reliability literature means by LOLE. Only a published test system does that.

Per the plan's ordering: **COPT vs published FIRST**. The analytic engine and
the sequential MC share `fleet_and_residual`, the CoptUnit semantics and the
residual-load convention; if the analytic engine misses the published number,
the MC inherits the same defect, and this is the cheapest place to find out.
The MC half of this gate (published value inside the MC 95% CI, half-width
<= 5% of published) lands with `services/adequacy/mc.py`; only the analytic
benchmark is present today.

Fixtures and their provenance: ``tests/benchmark_data/``. Read those headers
before touching a number in this file — the sources, the retrieval caveats,
the cross-check record and the sensitivity notes all live there.

TIME BASIS (stated once, applies to every benchmark here)
---------------------------------------------------------
The 1979 load model is 52 x 7 x 24 = 8736 hours, which is 0.99726 of a
8760-hour year. Snapshot weights are ones, so the sums below are sums over
those 8736 modelled hours. The reliability literature reports exactly that sum
AS the annual index — no 8760/8736 rescaling is applied to the published
figures and none is applied here. Treating 8736 h as "the year" is the
convention the published numbers are stated in; deviating from it would move
LOLE by 0.27%, comparable to the tolerance bands below.
"""
from __future__ import annotations

import pathlib

import pandas as pd
import pytest

from services.adequacy import copt as C

BENCH = pathlib.Path(__file__).resolve().parent / "benchmark_data"

# ── published figures, adopted from the fixture headers ──────────────────────
# RTS-79: LOLE ~= 9.39 h/yr, LOEE ~= 1176 MWh/yr on the 8736-hour hourly load
# model at a 2850 MW annual peak (IEEE Trans. PAS-98, 1979 system; the figure
# the phase plan and the literature cite).
RTS79_PUBLISHED_LOLE_H = 9.39
RTS79_PUBLISHED_EUE_MWH = 1176.0
# RBTS: LOLE = 1.0919 h/yr (1.0917 in some reproductions), LOEE = 9.8613 MWh/yr
# at a 185 MW annual peak (Billinton et al., IEEE Trans. PWRS 5(1), 1990).
RBTS_PUBLISHED_LOLE_H = 1.0919
RBTS_PUBLISHED_EUE_MWH = 9.8613

# ── regression anchors: THIS reconstruction's own values, not published claims.
# They pin the fixtures + Delta = 1 MW convolution so a later edit to either
# cannot drift silently past the (deliberately looser) published bands.
RTS79_ANCHOR_LOLE_H = 9.3941755
RTS79_ANCHOR_EUE_MWH = 1176.29846
RBTS_ANCHOR_LOLE_H = 1.09156047
RBTS_ANCHOR_EUE_MWH = 9.86135070


def _units_from_csv(path: pathlib.Path) -> list[C.CoptUnit]:
    """CoptUnit list from a benchmark CSV. ``basis="FOR"`` is pinned (spec §7):
    the ``forced_outage_rate`` column is the two-state unavailability q, not an
    EFORd-style derating."""
    df = pd.read_csv(path, comment="#")
    return [
        C.CoptUnit(
            name=str(row["name"]),
            capacity_mw=float(row["capacity_mw"]),
            q=float(row["forced_outage_rate"]),
            basis="FOR",
            mttr_hours=float(row["mttr_hours"]),
            source=path.name,
        )
        for _, row in df.iterrows()
    ]


def _run_copt(units: list[C.CoptUnit], load) -> dict:
    """COPT + hourly screening on an 8736-hour residual with unit weights.

    Delta = 1 MW is EXACT for both benchmark fleets, not an approximation: every
    RTS-79 capacity (12, 20, 50, 76, 100, 155, 197, 350, 400) and every RBTS
    capacity (5, 10, 20, 40) is an integer number of MW, so ``_unit_states``
    apportions with frac == 0 and no rounding mass is created. A finer Delta
    would change nothing and cost linearly more table.
    """
    residual = pd.Series(load)
    weights = pd.Series(1.0, index=residual.index)
    dist = C.build_copt(units, delta_mw=1.0)
    assert dist.total_probability == pytest.approx(1.0, abs=1e-9)
    return C.hourly_adequacy(dist, residual, weights=weights)


@pytest.mark.slow
def test_copt_reproduces_rts79_published_lole():
    """
    IEEE RTS-79 generation system (32 units, 3405 MW) at a 2850 MW annual peak
    on the 1979 hourly load model: the analytic COPT must land on the published
    LOLE ~= 9.39 h/yr.

    TOLERANCE, chosen from the sources' spread rather than from the result:
    +/- 1.0% on LOLE. Independent reproductions of this benchmark disagree at
    roughly that level because the number is sensitive to conventions the
    published text does not fully pin — the weekday phase of the week-alignment,
    8736 vs 8760 h, and whether the FOR column or MTTR/(MTTF+MTTR) is used. A
    band tighter than the convention spread would fail for reasons that say
    nothing about this engine; a band looser than it would stop discriminating
    (a 5% band, for instance, survives dropping a 12 MW unit). EUE is held to
    +/- 2%, looser because the companion energy index is quoted to fewer digits
    in the literature (~1176 MWh/yr).

    See ``tests/benchmark_data/rts79_load.py`` for the retrieval caveat behind
    the published figure and the numeric validation that compensates for it.
    """
    from tests.benchmark_data import rts79_load

    units = _units_from_csv(BENCH / "rts79_units.csv")
    assert len(units) == 32
    assert sum(u.capacity_mw for u in units) == pytest.approx(3405.0)

    load = rts79_load.build_hourly_load(2850.0)
    assert len(load) == 8736

    res = _run_copt(units, load)
    print(f"\n[RTS-79] LOLE = {res['lole_hours']:.6f} h/yr "
          f"(published ~{RTS79_PUBLISHED_LOLE_H}); "
          f"EUE = {res['eue_mwh']:.4f} MWh/yr "
          f"(published ~{RTS79_PUBLISHED_EUE_MWH}); "
          f"LOLP_max = {res['lolp_max']:.6f}; hours = {len(load)}")

    assert res["lole_hours"] == pytest.approx(RTS79_PUBLISHED_LOLE_H, rel=0.01)
    assert res["eue_mwh"] == pytest.approx(RTS79_PUBLISHED_EUE_MWH, rel=0.02)

    # outputs are finite and positive
    assert 0.0 < res["lole_hours"] < 8736.0
    assert 0.0 < res["eue_mwh"] < float("inf")
    assert 0.0 < res["lolp_max"] < 1.0
    assert res["by_period"]["ALL"]["lole_hours"] == pytest.approx(res["lole_hours"])
    assert res["by_period"]["ALL"]["eue_mwh"] == pytest.approx(res["eue_mwh"])

    # regression anchor (this reconstruction, not a published claim)
    assert res["lole_hours"] == pytest.approx(RTS79_ANCHOR_LOLE_H, rel=1e-6)
    assert res["eue_mwh"] == pytest.approx(RTS79_ANCHOR_EUE_MWH, rel=1e-6)


@pytest.mark.slow
def test_copt_reproduces_rbts_published_lole():
    """
    Roy Billinton Test System (11 units, 240 MW) at a 185 MW annual peak on the
    same 1979 hourly load model: published LOLE = 1.0919 h/yr, LOEE = 9.8613
    MWh/yr (Billinton et al., IEEE Trans. PWRS 5(1), 1990).

    TOLERANCE: +/- 1.0% on LOLE and +/- 2% on EUE, for the same reasons as the
    RTS-79 test. The RBTS's own citation spread (1.0917 vs 1.0919) is only
    0.02%, but the FOR-rounding sensitivity documented in ``rbts_units.csv``
    moves the answer by 0.3%, so the band is set by that, not by the printed
    digits. A tighter band would be asserting a convention, not a benchmark.
    """
    from tests.benchmark_data import rbts_load

    units = _units_from_csv(BENCH / "rbts_units.csv")
    assert len(units) == 11
    assert sum(u.capacity_mw for u in units) == pytest.approx(240.0)

    load = rbts_load.build_hourly_load(185.0)
    assert len(load) == 8736

    res = _run_copt(units, load)
    print(f"\n[RBTS]   LOLE = {res['lole_hours']:.6f} h/yr "
          f"(published {RBTS_PUBLISHED_LOLE_H}); "
          f"EUE = {res['eue_mwh']:.4f} MWh/yr "
          f"(published {RBTS_PUBLISHED_EUE_MWH}); "
          f"LOLP_max = {res['lolp_max']:.6f}; hours = {len(load)}")

    assert res["lole_hours"] == pytest.approx(RBTS_PUBLISHED_LOLE_H, rel=0.01)
    assert res["eue_mwh"] == pytest.approx(RBTS_PUBLISHED_EUE_MWH, rel=0.02)

    assert 0.0 < res["lole_hours"] < 8736.0
    assert 0.0 < res["eue_mwh"] < float("inf")
    assert 0.0 < res["lolp_max"] < 1.0

    assert res["lole_hours"] == pytest.approx(RBTS_ANCHOR_LOLE_H, rel=1e-6)
    assert res["eue_mwh"] == pytest.approx(RBTS_ANCHOR_EUE_MWH, rel=1e-6)
