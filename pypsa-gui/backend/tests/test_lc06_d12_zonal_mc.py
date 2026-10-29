# ruff: noqa: F811 -- imported pytest fixtures are used as arguments.
"""LC-06 LC-D12: the zonal Monte Carlo import and the common-mode outage, through the chat tools.

The zonal MC runs inside `run_eh_study` (weak_flexible pack, stages apply_pack,
ens_solve, mc_certify) whenever the grid side of the import Link carries outage
data, and is read back through `get_adequacy_results('eh_reference_design')`.
The certification section names `engine mc_zonal`, `import_model zonal`.

Zonal, deterministic. Every outage rate is 0, so each draw is the same and the
EUE must equal the hand value exactly (CI width 0). Per hour, the hub imports

    min(import_cap x p_max_pu x efficiency,  max(grid_p_nom - grid_load, 0) x efficiency)

(GK A's min(available Link, max(surplus, 0) x delivery ratio), with the
delivery ratio being the Link efficiency). The weak_flexible pack sets the
import cap itself (`import_overlay.import_p_nom_mw`, 50 MW by default) and
overrides the Link's own p_nom, so the test passes the cap through
`pack_overrides.import_p_nom_mw` and the oracle reads that value. The fixture has one pair of hours
where the Link binds, one where the surplus binds, one with zero surplus and
one with no hub deficit. The oracle is computed from the network, and each
mutation (cap halved, grid load raised) moves only the hours its bound
governs. The engine accumulates in float32, so EUE is compared at rel 1e-6
(float32 carries ~6e-8 relative per add; the hand value 256,230 MWh is exactly
representable).

Common-mode, stochastic. With the same seed, a Link `common_mode_rate` takes
the Link and its grid area down together on its own RNG stream, so the hub's
unit draws do not move. Where the Link cannot help (zero grid surplus every
hour), EUE and LOLE with and without the common mode are bitwise equal. Where
it can help, the common mode strictly raises EUE.
"""
import time

import pandas as pd
import pypsa
import pytest

from services import chat_service, chat_tools
from tests.eh_stage_fixtures import certifiable_weak_network
from tests.test_lc06_d05_foresight import seam  # noqa: F401
from tests.test_workflow_tools import helpers_state  # noqa: F401

VOLL = 10000.0
SEED, DRAWS = 3, 200
HUB_LOAD = [120.0, 120.0, 120.0, 120.0, 120.0, 120.0, 50.0, 50.0]
GRID_LOAD = [50.0, 50.0, 180.0, 180.0, 200.0, 200.0, 50.0, 50.0]
LINK_P_NOM, LINK_EFF = 50.0, 0.9
IMPORT_CAP = LINK_P_NOM     # the pack's import_p_nom_mw, passed explicitly
EUE_REL = 1e-6


def zonal_network(*, hub_load=HUB_LOAD, grid_load=GRID_LOAD, base_q=0.0, common_mode=None) -> pypsa.Network:
    n = certifiable_weak_network()
    n.remove('Generator', 'peaker')
    for c, name in (('Generator', 'base'), ('Generator', 'grid_supply'), ('Link', 'import_poc')):
        n.df(c).at[name, 'outage_rate_value'] = 0.0
    n.generators.at['base', 'outage_rate_value'] = base_q
    n.links.loc['import_poc', ['p_nom', 'p_nom_max', 'efficiency']] = [LINK_P_NOM, LINK_P_NOM, LINK_EFF]
    n.loads_t.p_set['hub_load'] = hub_load
    n.add('Load', 'grid_load', bus='grid', p_set=pd.Series(grid_load, index=n.snapshots, dtype=float))
    if common_mode is not None:
        n.links['common_mode_rate'] = 0.0
        n.links['common_mode_mttr_hours'] = 0.0
        n.links.loc['import_poc', ['common_mode_rate', 'common_mode_mttr_hours']] = common_mode
    return n


def zonal_oracle(n: pypsa.Network, import_cap: float = IMPORT_CAP) -> dict:
    """Exact EUE / LOLE and the binding bound of each hour, from the network."""
    link = n.links.loc['import_poc']
    offered = import_cap * link.p_max_pu * link.efficiency
    own = n.generators.loc[n.generators.bus == 'hub', 'p_nom'].sum()
    grid_cap = n.generators.loc[n.generators.bus == 'grid', 'p_nom'].sum()
    w = n.snapshot_weightings.generators
    eue = lole = 0.0
    binds = []
    for h in n.snapshots:
        via_grid = max(grid_cap - n.loads_t.p_set.at[h, 'grid_load'], 0.0) * link.efficiency
        short = max(n.loads_t.p_set.at[h, 'hub_load'] - own - min(offered, via_grid), 0.0)
        binds.append('none' if short == 0 else 'link' if offered < via_grid else 'zero_surplus' if via_grid == 0 else 'surplus')
        eue += w[h] * short
        lole += w[h] * (short > 0)
    return {'eue': eue, 'lole': lole, 'binds': binds}


def certify(seam, n, import_cap: float = IMPORT_CAP) -> dict:
    seam(n, voll=VOLL)
    frames = list(chat_service._dispatch_real_tool_call(
        chat_tools._CHAT_SESSION.get(),
        {'id': f'lc06-d12z-{time.monotonic_ns()}', 'name': 'run_eh_study',
         'input': {'archetype': 'weak_flexible', 'stages': ['apply_pack', 'ens_solve', 'mc_certify'],
                   'pack_overrides': {'ens_cap_permyriad': 10000, 'import_p_nom_mw': import_cap}, 'mc': {'seed': SEED, 'draws': DRAWS}}}, []))
    assert not [d for e, d in frames if e == 'tool_error'], frames
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = chat_tools.get_adequacy_results('eh_study')
        if status.get('status') != 'running':
            assert status.get('status') == 'done', status
            break
        time.sleep(0.2)
    else:
        raise AssertionError('EH study did not finish')
    report = chat_tools.get_adequacy_results('eh_reference_design')
    section = (report.get('result') or report)['sections']['certification']
    assert section['status'] == 'ok', section
    cert = section['payload']
    assert cert['engine'] == 'mc_zonal' and cert['fleet_scope']['import_model'] == 'zonal'
    assert cert['seed'] == SEED and cert['n_samples'] == DRAWS
    return cert


def test_the_fixture_has_every_binding_case():
    assert zonal_oracle(zonal_network())['binds'] == ['link'] * 2 + ['surplus'] * 2 + ['zero_surplus'] * 2 + ['none'] * 2


def test_the_zonal_import_is_the_lesser_bound_every_hour(seam):
    n = zonal_network()
    oracle = zonal_oracle(n)
    # 1095 h per snapshot x 2 x (15 + 42 + 60) MW; 6 short hours
    assert (oracle['eue'], oracle['lole']) == pytest.approx((1095 * 2 * (15 + 42 + 60), 6 * 1095), abs=1e-6)
    cert = certify(seam, n)
    assert cert['eue_mwh'] == pytest.approx(oracle['eue'], rel=EUE_REL)
    assert cert['eue_ci'][0] == cert['eue_ci'][1] == cert['eue_mwh']
    assert cert['lole_h_per_horizon'] == pytest.approx(oracle['lole'], abs=0.0)
    assert cert['fleet_scope']['import_cap_mw_max'] == pytest.approx(IMPORT_CAP * LINK_EFF)
    assert cert['fleet_scope']['import_common_mode_sampled'] is False


def _common_mode_pair(seam, grid_load):
    base = certify(seam, zonal_network(grid_load=grid_load, base_q=0.1))
    cm = certify(seam, zonal_network(grid_load=grid_load, base_q=0.1, common_mode=(0.05, 30.0)))
    assert base['fleet_scope']['import_common_mode_sampled'] is False
    assert cm['fleet_scope']['import_common_mode_sampled'] is True
    return base, cm


def test_a_common_mode_outage_draws_on_its_own_stream(seam):
    """Zero grid surplus every hour: the Link never helps, so a common mode that
    left the hub's unit draws alone changes nothing, bit for bit."""
    base, cm = _common_mode_pair(seam, [200.0] * 8)
    assert base['eue_mwh'] > 0
    assert (cm['eue_mwh'], cm['lole_h_per_horizon']) == (base['eue_mwh'], base['lole_h_per_horizon'])


def test_a_common_mode_outage_raises_eue_where_the_link_matters(seam):
    base, cm = _common_mode_pair(seam, [50.0] * 8)
    assert cm['eue_mwh'] > base['eue_mwh']
    assert cm['lole_h_per_horizon'] >= base['lole_h_per_horizon']


@pytest.mark.parametrize('mutation, kw, delta', [
    # Z1: the cap halved moves only the two Link-bound hours, by 45 - 22.5 MW each.
    ('Z1', {'import_cap': IMPORT_CAP / 2}, 2 * 1095 * 22.5),
    # Z2: grid load 190 on the surplus-bound hours cuts the import by 10 x 0.9 MW.
    ('Z2', {'grid_load': GRID_LOAD[:2] + [190.0, 190.0] + GRID_LOAD[4:]}, 2 * 1095 * 9.0),
])
def test_each_mutation_moves_eue_by_the_hand_delta(seam, mutation, kw, delta):
    cap = kw.pop('import_cap', IMPORT_CAP)
    n = zonal_network(**kw)
    assert zonal_oracle(n, cap)['eue'] == pytest.approx(256230.0 + delta, abs=1e-6), mutation
    cert = certify(seam, n, cap)
    assert cert['eue_mwh'] == pytest.approx(256230.0 + delta, rel=EUE_REL), mutation
    assert cert['eue_ci'][0] == cert['eue_ci'][1] == cert['eue_mwh']


# The engine layer: GK A's per-draw and per-hour checks, which the chat
# surface cannot show (it reports totals only). `simulate_zonal_blocks` runs on
# the inputs `freeze_fixed_plan` builds from the same networks, with the
# weak_flexible overlay, and the same seed and draw count as the seam.

def _frozen(n):
    import threading

    from models.energy_hub import default_weak_flexible_pack
    from services.adequacy import eh_stages as ST
    from services.solver_service import SolverConfig

    frozen = ST.freeze_fixed_plan(n, SolverConfig(voll=VOLL), threading.Lock(),
                                  overlay=default_weak_flexible_pack().import_overlay)
    assert frozen.zonal_inputs is not None
    return frozen.zonal_inputs


def _engine_run(monkeypatch, z, *, hook: bool):
    """One run of the engine; with ``hook`` the area's ``offer`` is wrapped
    (call through, values unchanged) to record each hour's offer, import and
    grid surplus. The first ``sample_capacity`` call is the hub's units."""
    import numpy as np

    from services.adequacy import mc_zonal as Z

    seen = {'hub': [], 'hours': []}
    sample = Z.sample_capacity

    def sample_spy(*a, **k):
        out = sample(*a, **k)
        seen['hub'].append(np.array(out, copy=True))
        return out

    monkeypatch.setattr(Z, 'sample_capacity', sample_spy)
    if hook:
        offer = Z._AreaState.offer

        def offer_hook(self, h):
            offered, imp = offer(self, h)
            surplus = None if self.surplus_g is None else self.surplus_g.copy()
            seen['hours'].append((h, offered.copy(), imp.copy(), surplus, float(self.ratio[h])))
            return offered, imp

        monkeypatch.setattr(Z._AreaState, 'offer', offer_hook)
    blocks = Z.simulate_zonal_blocks(z, draws=DRAWS, seed=SEED)
    monkeypatch.undo()
    eue = sum(v[1] for v in blocks.values())
    return blocks, eue, seen['hub'][0], seen['hours']


def _same_blocks(a, b):
    import numpy as np

    assert a.keys() == b.keys()
    for k in a:
        np.testing.assert_array_equal(a[k][0], b[k][0])
        np.testing.assert_array_equal(a[k][1], b[k][1])


def test_the_engine_imports_the_lesser_bound_per_hour_and_the_hook_changes_nothing(monkeypatch):
    import numpy as np

    n = zonal_network()
    z = _frozen(n)
    oracle = zonal_oracle(n)
    plain, _, _, _ = _engine_run(monkeypatch, z, hook=False)
    hooked, eue, _, hours = _engine_run(monkeypatch, z, hook=True)
    _same_blocks(plain, hooked)
    assert np.all(eue == eue[0]) and eue[0] == pytest.approx(oracle['eue'], rel=EUE_REL)
    link = n.links.loc['import_poc']
    grid_cap = n.generators.loc[n.generators.bus == 'grid', 'p_nom'].sum()
    assert [h for h, *_ in hours] == list(range(len(n.snapshots)))
    for (h, offered, imp, surplus, ratio), bind in zip(hours, oracle['binds']):
        # Hand value: min(cap x p_max_pu x eff, max(grid surplus, 0) x eff).
        hand = min(IMPORT_CAP * link.p_max_pu * LINK_EFF,
                   max(grid_cap - GRID_LOAD[h], 0.0) * LINK_EFF)
        np.testing.assert_allclose(imp, hand, rtol=0, atol=1e-9, err_msg=f'hour {h} ({bind})')
        np.testing.assert_array_equal(imp, np.minimum(offered, np.maximum(surplus, 0.0) * ratio))


def test_the_engine_common_mode_raises_eue_draw_by_draw_and_leaves_the_hub_draws(monkeypatch):
    import numpy as np

    z_base = _frozen(zonal_network(grid_load=[50.0] * 8, base_q=0.1))
    z_cm = _frozen(zonal_network(grid_load=[50.0] * 8, base_q=0.1, common_mode=(0.05, 30.0)))
    plain_cm, _, _, _ = _engine_run(monkeypatch, z_cm, hook=False)
    _, eue_base, hub_base, _ = _engine_run(monkeypatch, z_base, hook=True)
    blocks_cm, eue_cm, hub_cm, hours = _engine_run(monkeypatch, z_cm, hook=True)
    _same_blocks(plain_cm, blocks_cm)
    np.testing.assert_array_equal(hub_cm, hub_base)
    assert np.all(eue_cm >= eue_base)
    assert eue_cm.sum() > eue_base.sum()
    for h, offered, imp, surplus, ratio in hours:
        np.testing.assert_array_equal(imp, np.minimum(offered, np.maximum(surplus, 0.0) * ratio))
