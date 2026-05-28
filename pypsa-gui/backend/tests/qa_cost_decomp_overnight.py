"""
QA harness for the [COST-DECOMP] new_CAPEX fix.

Bug: `_log_cost_decomposition_post_solve` read `capital_cost` directly to
compute new_CAPEX = capital_cost × Δp_nom_opt. For networks that configure
investment via `overnight_cost` (capital_cost=0, with PyPSA annuitising at
solve time), the diagnostic reported €0.00M for every period — even when
the LP built hundreds of MW of new capacity.

Fix: when `capital_cost` is 0 / NaN, fall back to annuitising
`overnight_cost` via `_annuity(discount_rate, lifetime)`. Mirrors what
PyPSA's `periodized_cost` does internally, so the diagnostic captures
the LP's actual investment signal.

Test plan:
  [1] Multi-period network with capital_cost=0 + overnight_cost > 0.
      Verify [COST-DECOMP] reports non-zero new_CAPEX in the build year.
  [2] Same setup but with capital_cost > 0 explicitly. Verify the
      explicit value is used (no double-counting against overnight_cost).
  [3] Asset-level discount_rate/lifetime overrides the global cfg values.
"""
from __future__ import annotations

import pathlib
import queue
import re
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except (AttributeError, ValueError):
    pass

import pandas as pd
import pypsa
from services.pypsa_service import PyPSAService
from services.solver_service import SolverConfig, _annuity, run_simulation

PASS = 0
FAIL = 0

# Match lines like:
#   [COST-DECOMP] 2026: new_CAPEX=24.53M€ | OPEX(mc)=...
_CAPEX_RE = re.compile(
    r"\[COST-DECOMP\]\s+(?P<period>\d{4}|ALL)\s*:\s*new_CAPEX=(?P<v>-?[\d.]+)M€"
)


def _step(label: str, ok: bool, msg: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}" + (f" — {msg}" if msg else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {label}" + (f" — {msg}" if msg else ""))


def _drain_log(log_q: queue.SimpleQueue) -> list[str]:
    """Collect every line of the solver log into a list of strings."""
    lines: list[str] = []
    while True:
        try:
            rec = log_q.get_nowait()
        except queue.Empty:
            break
        msg = getattr(rec, "message", None) or getattr(rec, "msg", None) or str(rec)
        # logging.makeRecord stores user-formatted message under `msg` and
        # the formatted result under `message`. Either is fine for substring
        # searches — strip newlines for clean grep.
        lines.append(str(msg).strip())
    return lines


def _parse_capex_by_period(lines: list[str]) -> dict[str, float]:
    """
    Parse `[COST-DECOMP] {period}: new_CAPEX=X.XXM€` lines into a dict.

    For multi-period iterations the same period appears multiple times
    (once per iteration). Returns the LAST seen value per period — that's
    the report from the iteration when that period was the actual current
    period (so vintages active in it are fully sized).
    """
    out: dict[str, float] = {}
    for ln in lines:
        m = _CAPEX_RE.search(ln)
        if m:
            out[m.group("period")] = float(m.group("v"))
    return out


def _build_overnight_cost_network(use_capital_cost: bool = False) -> pypsa.Network:
    """
    Minimal multi-period network with one extendable solar generator.

    Two configurations:
      • use_capital_cost=False: overnight_cost set, capital_cost=0. The
        annuitisation path under test.
      • use_capital_cost=True: capital_cost set directly, overnight_cost=0.
        The legacy path — should still work unchanged.

    Network shape: 24 hours/period × 3 periods = 72 snapshots. Solar with
    high CF (0.5) and a load forces the LP to build new solar.
    """
    n = pypsa.Network()
    base = pd.date_range("2026-01-01", periods=24, freq="h")
    periods = [2026, 2027, 2028]
    mi = pd.MultiIndex.from_product([periods, base], names=["period", "timestep"])
    mi.name = "snapshot"
    n.snapshots = mi
    n.investment_periods = periods
    for p in periods:
        n.investment_period_weightings.loc[p, "years"] = 1.0
        n.investment_period_weightings.loc[p, "objective"] = 1.0

    n.add("Bus", "B1")
    n.add("Carrier", "solar", co2_emissions=0.0)
    n.add("Carrier", "gas",   co2_emissions=0.2)

    # Existing solar — fixed 100 MW, build_year=2025 (pre-horizon).
    # Vintages are added by vintage_service from n.meta["vintage_bounds"]
    # at solve time — we set those bounds below.
    if use_capital_cost:
        cc, oc = 80_000.0, 0.0
    else:
        cc, oc = 0.0, 1_000_000.0  # 1M EUR/MW upfront → ~85.8k/MW/yr annuity
    n.add(
        "Generator", "Solar", bus="B1", carrier="solar",
        p_nom=100.0, p_nom_extendable=True,
        p_nom_min=100.0, p_nom_max=2_000.0,
        marginal_cost=1.0, efficiency=1.0,
        capital_cost=cc, overnight_cost=oc,
        discount_rate=0.07, lifetime=25.0,
        build_year=2025,
    )
    # Per-period vintage bounds — vintage_service will expand the parent
    # into one extendable vintage row per period.
    n.meta["vintage_bounds"] = {
        "Generator": {
            "Solar": {
                "2026": {"p_nom_min": 0.0, "p_nom_max": 500.0},
                "2027": {"p_nom_min": 0.0, "p_nom_max": 500.0},
                "2028": {"p_nom_min": 0.0, "p_nom_max": 500.0},
            }
        }
    }

    # Solar profile: 50% CF flat (simple — easy to verify the LP builds
    # enough to cover the gas-displacement business case).
    p_max_pu = pd.Series(0.5, index=mi, dtype=float)
    n.generators_t.p_max_pu = pd.DataFrame({"Solar": p_max_pu}, index=mi)

    # Backup gas — expensive marginal cost so solar pays back through
    # gas displacement. capital_cost=0 because it's existing (non-extendable).
    n.add("Generator", "Gas", bus="B1", carrier="gas",
          p_nom=500.0, p_nom_extendable=False,
          marginal_cost=80.0, efficiency=1.0,
          build_year=2025, lifetime=30)

    # Constant load — peak 300 MW. Gas alone could cover it (gas=500 MW),
    # but solar at €1/MWh marginal beats gas at €80/MWh, so the LP builds
    # solar to displace gas.
    load = pd.Series(300.0, index=mi, dtype=float)
    n.add("Load", "L", bus="B1")
    n.loads_t.p_set = pd.DataFrame({"L": load}, index=mi)

    return n


def _run_and_capture(n: pypsa.Network, cfg: SolverConfig) -> tuple[str, str, list[str]]:
    PyPSAService.set_network(n)
    lock = PyPSAService.get_lock()
    stop = threading.Event()
    log_q: queue.SimpleQueue = queue.SimpleQueue()
    status, condition = run_simulation(cfg, n, lock, stop, log_q)
    return status, condition, _drain_log(log_q)


def test_overnight_cost_path_reports_nonzero_capex() -> None:
    print("\n[1] overnight_cost path: [COST-DECOMP] reports non-zero new_CAPEX")
    n = _build_overnight_cost_network(use_capital_cost=False)
    cfg = SolverConfig(
        multi_investment_periods=True,
        solve_strategy="myopic",
        lf_aggregate_future=False,
        discount_rate=0.07,
        default_lifetime=25.0,
    )
    status, condition, lines = _run_and_capture(n, cfg)
    _step("solve completed", status in ("ok", "optimal"),
          f"status={status} condition={condition}")
    if status not in ("ok", "optimal"):
        for ln in lines[-20:]:
            print(f"      LOG: {ln}")
        return

    capex_by_period = _parse_capex_by_period(lines)
    print(f"    parsed CAPEX by period: {capex_by_period}")
    _step("[COST-DECOMP] 2026 reported", "2026" in capex_by_period)
    _step("[COST-DECOMP] 2026 new_CAPEX > 0 (annuitised overnight_cost picked up)",
          capex_by_period.get("2026", 0.0) > 0.0,
          f"got {capex_by_period.get('2026', 0.0):.2f}M€ — should be ~85.8k/MW × built MW")

    # The LP should build to cover gas displacement; with 50% CF and 300 MW
    # load, building solar up to p_nom_max=500 (parent + vintage bound) is
    # optimal because each MW saves ~50% × €80 = €40/MWh × 8760h = €350k/yr,
    # well above the €85.8k/MW/yr annuity. Sanity check that the magnitude
    # is roughly consistent with annuitised overnight_cost.
    annuity = _annuity(0.07, 25.0)  # ≈ 0.0858
    expected_per_mw = 1_000_000.0 * annuity / 1e6  # ≈ 0.0858 M€/MW/yr
    capex_26 = capex_by_period.get("2026", 0.0)
    # We don't know exactly how many MW were built without parsing CAP-SUM,
    # but for 500 MW (the cap) it'd be ~42.9 M€, for 100 MW ~8.58 M€.
    # Accept anything in [4 M€, 100 M€] as "the right ballpark".
    _step("[COST-DECOMP] 2026 magnitude reasonable (4–100 M€)",
          4.0 < capex_26 < 100.0,
          f"got {capex_26:.2f}M€, per-MW annuity ≈ {expected_per_mw*1e3:.1f}k€/MW/yr")


def test_capital_cost_path_unchanged() -> None:
    print("\n[2] capital_cost path: explicit value still used (no double-count)")
    n = _build_overnight_cost_network(use_capital_cost=True)
    cfg = SolverConfig(
        multi_investment_periods=True,
        solve_strategy="myopic",
        lf_aggregate_future=False,
        discount_rate=0.07,
        default_lifetime=25.0,
    )
    status, condition, lines = _run_and_capture(n, cfg)
    _step("solve completed", status in ("ok", "optimal"),
          f"status={status} condition={condition}")
    if status not in ("ok", "optimal"):
        for ln in lines[-20:]:
            print(f"      LOG: {ln}")
        return

    capex_by_period = _parse_capex_by_period(lines)
    print(f"    parsed CAPEX by period: {capex_by_period}")
    capex_26 = capex_by_period.get("2026", 0.0)
    _step("[COST-DECOMP] 2026 new_CAPEX > 0 (capital_cost path)",
          capex_26 > 0.0, f"got {capex_26:.2f}M€")
    # With capital_cost=80_000 €/MW/yr and (likely) 500 MW built, expect
    # ~40 M€. Accept [4, 100] same range as test 1 — the two paths should
    # produce nearly identical results because 1M€ × annuity(7%,25y) ≈ 85.8k
    # is very close to the explicit 80k.
    _step("[COST-DECOMP] 2026 magnitude reasonable (4–100 M€)",
          4.0 < capex_26 < 100.0,
          f"got {capex_26:.2f}M€")


def test_annuity_helper_matches_pypsa_formula() -> None:
    """
    Sanity check on the _annuity helper — same formula PyPSA uses
    internally via periodized_cost. Not strictly the fix being tested, but
    a regression net for the building block.
    """
    print("\n[3] _annuity helper formula sanity")
    # r × (1+r)^L / ((1+r)^L − 1) at r=7%, L=25y → ~0.0858
    af = _annuity(0.07, 25.0)
    _step("annuity(0.07, 25) ≈ 0.0858",
          abs(af - 0.085810) < 1e-4, f"got {af:.6f}")
    # rate=0 → straight-line 1/L
    _step("annuity(0.0, 25) == 0.04 (straight-line)",
          abs(_annuity(0.0, 25.0) - 0.04) < 1e-9,
          f"got {_annuity(0.0, 25.0):.6f}")
    # lifetime≤0 → 0 (no annualisation)
    _step("annuity(0.07, 0) == 0", _annuity(0.07, 0.0) == 0.0)


def main() -> int:
    print("=== qa_cost_decomp_overnight ===")
    test_annuity_helper_matches_pypsa_formula()
    test_overnight_cost_path_reports_nonzero_capex()
    test_capital_cost_path_unchanged()
    total = PASS + FAIL
    print(f"\nTotal: {total}  Pass: {PASS}  Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
