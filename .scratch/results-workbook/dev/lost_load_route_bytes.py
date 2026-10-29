"""
Dev helper (RW-02 R1, GK B plan condition 1): dump the exact /results/lost_load
response bytes for the route's cases, so the lifted body can be shown
byte-equal to master's. Run from pypsa-gui/backend on each tree and diff:

    python3 ../../.scratch/results-workbook/dev/lost_load_route_bytes.py \
        | grep -E "^(nothing_solved|voll_|captured)" > out.txt
"""
import logging
import sys
import warnings

sys.path.insert(0, ".")
sys.path.insert(0, "tests")
logging.disable(logging.WARNING)
warnings.simplefilter("ignore")

import pandas as pd  # noqa: E402
import pypsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from routers import results as R  # noqa: E402
from services.pypsa_service import PyPSAService  # noqa: E402


def network():
    n = pypsa.Network()
    n.set_snapshots(pd.MultiIndex.from_product(
        [[2030, 2040], pd.date_range("2030-01-01", periods=3, freq="h")]))
    n.investment_periods = [2030, 2040]
    n.snapshot_weightings.loc[:, :] = 3.0
    n.add("Bus", "A", carrier="AC")
    n.add("Bus", "H", carrier="H2")
    n.add("Load", "L", bus="A", p_set=[5.0] * 6)
    n.add("Generator", "g", bus="A", p_nom=10.0, marginal_cost=1.0)
    n.optimize(solver_name="highs", multi_investment_periods=True)
    return n


def main():
    from main import app
    from routers.results import results_router  # noqa: F401
    n = network()
    ll = pd.DataFrame({"A": [0, 1.5, 0, 0, 2, 0], "H": [0, 0, 1, 0, 0, 0]}, index=n.snapshots, dtype=float)
    cases = {
        "nothing_solved": (None, None, None),
        "voll_off": (None, {"voll_eur_per_mwh": 0.0}, None),
        "voll_on_none_captured": (None, {"voll_eur_per_mwh": 5000.0}, None),
        "captured": ({"lost_load_t": ll, "lost_load_total_mwh": 13.5, "lost_load_cost_eur": 67500.0,
                      "voll_eur_per_mwh": 5000.0}, None, None),
        "captured_old_no_voll": ({"lost_load_t": ll, "lost_load_total_mwh": 13.5,
                                  "lost_load_cost_eur": 67500.0}, None, None),
        "captured_window": ({"lost_load_t": ll, "lost_load_total_mwh": 13.5,
                             "lost_load_cost_eur": 67500.0, "voll_eur_per_mwh": 5000.0}, None, (1, 3)),
    }
    for name, (capture, settings, window) in cases.items():
        PyPSAService.set_network(n)
        R._state["last_lost_load"] = capture
        R._state["last_solve_settings"] = settings
        orig = R._dispatch_ready
        if name == "nothing_solved":
            R._dispatch_ready = lambda n: False
        try:
            resp = R.get_lost_load(*(window or (None, None)))
        finally:
            R._dispatch_ready = orig
        if hasattr(resp, "status_code"):
            print(name, "status", resp.status_code, resp.body)
        else:
            import json
            print(name, json.dumps(resp, sort_keys=False, default=str))


if __name__ == "__main__":
    main()
