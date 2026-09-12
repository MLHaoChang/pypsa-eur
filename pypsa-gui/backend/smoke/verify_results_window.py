"""
Verify the Results-tabs windowing feature (2026-08-05 plan) at REAL scale.

The plan's own final verification was typechecks + unit tests only. Its goal
statement is a claim about a 26,280-snapshot network — "~470 MB and an
unreadable 26,280-point chart" — and nothing had ever exercised that size.
This closes that gap.

Not under tests/ on purpose: it builds 26,280-snapshot networks and
serialises multi-million-value payloads, which would dominate the unit
suite's runtime for no benefit. Run it directly:

    .pixi/envs/test/bin/python -u pypsa-gui/backend/smoke/verify_results_window.py

`-u` is load-bearing — without unbuffered stdout the prints never flush to a
background task's output file and a working run looks like a silent hang.

DISPATCH IS SYNTHESISED, NOT SOLVED. Windowing is a pure serialisation
concern: `_serve_ts` -> `slice_ts` -> `_ts_payload` never consults the
optimiser, only the shape of `generators_t.p`. Running a real 2-million-
variable LP would take minutes and verify nothing extra about the slice
path. The networks below therefore carry dispatch tables that are
internally consistent with their topology (which is exactly what
`_dispatch_ready` gates on) rather than LP output.

The window bounds under test are not hardcoded here: they are read out of
the FRONTEND source (`filterContext.tsx`), so if `WINDOW_THRESHOLD` or
`DEFAULT_FLAT_WINDOW` drift, this verification fails instead of silently
checking the wrong window.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

REPO = BACKEND.parent.parent
FILTER_CONTEXT = REPO / "pypsa-gui/frontend/src/pages/results/filterContext.tsx"

from routers.results import (  # noqa: E402
    _dispatch_ready,
    get_generator_results,
)
from services.pypsa_service import PyPSAService  # noqa: E402
from services.serialization import MAX_RESPONSE_VALUES  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def record(label: str, ok: bool, detail: str) -> None:
    RESULTS.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {detail}", flush=True)


def frontend_constant(name: str) -> int:
    """
    Read a window constant out of the frontend source.

    Coupling the two halves on purpose: the backend serves whatever bounds it
    is given, so a verification that hardcoded 720 would keep passing after
    someone changed DEFAULT_FLAT_WINDOW and left this check measuring a
    window no tab ever requests.
    """
    src = FILTER_CONTEXT.read_text()
    m = re.search(rf"export const {name} = (\d+)", src)
    if not m:
        raise AssertionError(f"{name} not found in {FILTER_CONTEXT}")
    return int(m.group(1))


def build(n_snapshots: int, n_gens: int, periods: list[int] | None = None):
    """A network whose dispatch is consistent with its topology."""
    n = pypsa.Network()
    if periods:
        per_period = n_snapshots // len(periods)
        idx = pd.MultiIndex.from_product(
            [periods, pd.date_range("2030-01-01", periods=per_period, freq="h")],
            names=["period", "timestep"],
        )
        idx.name = "snapshot"
        n.set_snapshots(idx)
        n.investment_periods = periods
    else:
        idx = pd.date_range("2030-01-01", periods=n_snapshots, freq="h")
        idx.name = "snapshot"
        n.set_snapshots(idx)

    n.add("Bus", "B")
    n.add("Carrier", "gas")
    names = [f"G{i}" for i in range(n_gens)]
    for g in names:
        n.add("Generator", g, bus="B", carrier="gas", p_nom=100.0)
    n.add("Load", "L", bus="B", p_set=50.0)

    # Deterministic, distinguishable per (row, col) so a mis-sliced window
    # cannot coincidentally equal the right one.
    rows = len(n.snapshots)
    data = (np.arange(rows).reshape(-1, 1) * 1000.0
            + np.arange(n_gens).reshape(1, -1))
    n.generators_t.p = pd.DataFrame(data, index=n.snapshots, columns=names)
    # `is_solved` is a read-only property deriving from the objective, so mark
    # it the way PyPSA does (matching tests/test_asset_economics_capital_costs).
    n._objective = 0.0
    return n


def install(n) -> None:
    PyPSAService.set_network(n)
    with PyPSAService._registry_lock:
        for k in [k for k in PyPSAService._contexts if k.startswith("scratch:")]:
            PyPSAService._contexts.pop(k, None)


def payload_bytes(p) -> int:
    return len(json.dumps(p, default=str).encode())


def case_a(threshold: int, flat_window: int) -> None:
    """The plan's motivating case: 3 hourly years, flat."""
    total = 26_280
    n = build(total, 12)
    install(n)
    record("A0 dispatch gate open", _dispatch_ready(n), f"snapshots={total}")

    whole = get_generator_results()
    win = get_generator_results(from_=0, to_=flat_window - 1)

    # The AggregatedOverview path: no bounds -> byte-identical to pre-window.
    record("A1 unranged payload carries NO range key",
           "range" not in whole,
           f"keys={sorted(whole.keys())}")
    record("A2 unranged serves the whole horizon",
           len(whole["index"]) == total,
           f"rows={len(whole['index'])}")

    # The windowing-tab path.
    rng = win.get("range")
    record("A3 windowed payload serves exactly the window",
           len(win["index"]) == flat_window,
           f"rows={len(win['index'])} expected={flat_window}")
    record("A4 range metadata is honest",
           rng == {"from": 0, "to": flat_window - 1, "total": total,
                   "complete": False, "capped": False},
           f"range={rng}")

    # The identity property, live: server slice == client slice.
    record("A5 server slice == client-side slice of the whole payload",
           win["data"] == whole["data"][:flat_window]
           and win["index"] == whole["index"][:flat_window],
           f"compared {flat_window} rows x {len(win['columns'])} cols")

    # The claim in the plan's goal statement.
    b_whole, b_win = payload_bytes(whole), payload_bytes(win)
    record("A6 window is a large payload reduction",
           b_win < b_whole / 10,
           f"{b_whole/1e6:.1f} MB -> {b_win/1e6:.3f} MB "
           f"({b_whole/max(b_win,1):.0f}x smaller)")

    # isPartialPayload's contract, from the backend side.
    record("A7 complete flag would trip AggregatedOverview's guard",
           rng["complete"] is False and "range" not in whole,
           "windowed complete=False; unranged emits no range at all")


def case_b() -> None:
    """
    `fetchRange: undefined` is load-bearing, not a simplification.

    useResultsWindow returns `undefined` rather than `{0, count-1}` for a
    whole-horizon window. The stated reason is that only the RANGED path
    applies MAX_RESPONSE_VALUES, so passing explicit full bounds would newly
    expose every default view to silent truncation. Verify both halves.
    """
    rows, gens = 12_000, 170
    values = rows * gens
    n = build(rows, gens)
    install(n)
    record("B0 fixture exceeds the response cap",
           values > MAX_RESPONSE_VALUES,
           f"{values:,} values > cap {MAX_RESPONSE_VALUES:,}")

    whole = get_generator_results()
    record("B1 unranged is NOT capped (no range path, no cap)",
           "range" not in whole and len(whole["index"]) == rows,
           f"rows={len(whole['index'])} of {rows}, range key absent")

    full_bounds = get_generator_results(from_=0, to_=rows - 1)
    rng = full_bounds.get("range")
    record("B2 same window as EXPLICIT bounds IS capped",
           rng is not None and rng["capped"] is True
           and len(full_bounds["index"]) < rows,
           f"rows={len(full_bounds['index'])} of {rows}, capped={rng and rng['capped']}")
    record("B3 => fetchRange:undefined avoids a real truncation",
           len(whole["index"]) > len(full_bounds["index"]),
           f"undefined={len(whole['index'])} rows vs "
           f"explicit={len(full_bounds['index'])} rows")


def case_c(threshold: int) -> None:
    """Long multi-period opens on its FIRST period, not an ISO window."""
    periods = [2030, 2035, 2040]
    per_period = 8_760
    total = per_period * len(periods)
    n = build(total, 8, periods=periods)
    install(n)
    record("C0 multi-period fixture is over threshold",
           total > threshold,
           f"{len(periods)} periods x {per_period} = {total} > {threshold}")

    # defaultWindow returns {kind:'period', period:2030}; resolveRange maps
    # that to the positional bounds of period 2030 — the first block.
    win = get_generator_results(from_=0, to_=per_period - 1)
    whole = get_generator_results()
    record("C1 period window serves exactly one period",
           len(win["index"]) == per_period,
           f"rows={len(win['index'])} expected={per_period}")
    record("C2 period window equals the first period's rows",
           win["data"] == whole["data"][:per_period],
           f"compared {per_period} rows")
    record("C3 range reports the multi-period total",
           win["range"]["total"] == total and win["range"]["complete"] is False,
           f"range={win['range']}")


def main() -> int:
    threshold = frontend_constant("WINDOW_THRESHOLD")
    flat_window = frontend_constant("DEFAULT_FLAT_WINDOW")
    print(f"frontend constants: WINDOW_THRESHOLD={threshold} "
          f"DEFAULT_FLAT_WINDOW={flat_window}\n", flush=True)

    case_a(threshold, flat_window)
    print(flush=True)
    case_b()
    print(flush=True)
    case_c(threshold)

    print("\n" + "=" * 64, flush=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed", flush=True)
    for label, _, detail in failed:
        print(f"  FAILED {label}: {detail}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
