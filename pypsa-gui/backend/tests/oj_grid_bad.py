"""Synthetic screening defects; these frames are NOT claimed to be an optimum."""
import pandas as pd
from tests.oj_grid import build_oj_grid


def build_oj_grid_bad():
    n = build_oj_grid()
    n._objective = 1.0
    n.generators_t.p = pd.DataFrame({'Gas': 50., 'Solar': n.generators_t.p_max_pu.Solar * 32.}, index=n.snapshots)
    n.generators['p_nom_opt'] = n.generators.p_nom
    n.generators.loc['Gas', ['p_nom_extendable', 'p_nom_max']] = [True, 100.]
    n.lines_t.p0 = pd.DataFrame(0., index=n.snapshots, columns=n.lines.index)
    n.lines_t.p1 = n.lines_t.p0.copy()
    n.lines_t.p0.loc[n.snapshots[19], 'L2'] = 480.
    n.lines_t.p1.loc[n.snapshots[19], 'L2'] = -480.
    n.transformers_t.p0 = pd.DataFrame(0., index=n.snapshots, columns=n.transformers.index)
    n.transformers_t.p1 = n.transformers_t.p0.copy()
    n.storage_units_t.p_store = pd.DataFrame(0., index=n.snapshots, columns=n.storage_units.index)
    n.storage_units_t.p_dispatch = n.storage_units_t.p_store.copy()
    n.storage_units_t.p_store.iloc[19, 0] = 2.
    n.storage_units_t.p_dispatch.iloc[19, 0] = 3.
    return n


def loss_capture(n):
    loss = pd.DataFrame(0., index=n.snapshots, columns=n.buses.index)
    loss.loc[n.snapshots[19], 'B3'] = 12.
    return {'lost_load_t': loss, 'voll_eur_per_mwh': 8000.}
