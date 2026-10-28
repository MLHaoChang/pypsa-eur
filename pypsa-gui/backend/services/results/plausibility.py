"""Pure native screening rules; no authority, solver, HTTP or publication policy.

The caller supplies this result's own loss capture and admitted ENS targets.
Study-harness gate adapters consume this same service, never another rule set.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numbers import Real

from services.period_utils import snapshot_weights

RULE_IDS = ('lost_load', 'branch_overload', 'curtailment_share', 'storage_simultaneous', 'build_at_bound')
# Numerical LP screens, not physical/compliance limits.
REL_TOL = 1e-6
ABS_POWER_TOL_MW = 1e-6
# Deliberately a heuristic: a least-cost design may curtail more than this.
CURTAILMENT_SHARE = 0.30
# Positive loss is a physical observation, with no numerical-zero substitution.
LOST_LOAD_MWH = 0.0


def _groups(n):
    if isinstance(n.snapshots, pd.MultiIndex):
        return [(str(p), n.snapshots[n.snapshots.get_level_values(0) == p])
                for p in n.snapshots.get_level_values(0).unique()]
    return [(None, n.snapshots)]


def _valid(frame):
    try:
        return frame is not None and not frame.empty and np.isfinite(frame.to_numpy(dtype=float)).all()
    except (TypeError, ValueError):
        return False


def _weights(n, column):
    # The generic results helper has legacy fallbacks. A screening rule must
    # not silently label objective or unit weights as generator energy weights.
    frame = n.snapshot_weightings
    if column not in frame or not frame.index.equals(n.snapshots) or not _valid(frame[column]):
        return pd.Series(dtype=float)
    if isinstance(n.snapshots, pd.MultiIndex):
        periods = n.snapshots.get_level_values(0).unique()
        ipw = n.investment_period_weightings
        if 'years' not in ipw or not set(periods) <= set(ipw.index):
            return pd.Series(dtype=float)
        years = ipw.loc[periods, 'years']
        if not _valid(years) or (years <= 0).any():
            return pd.Series(dtype=float)
    return snapshot_weights(n, column)


def _fact(source, value, unit, **where):
    return {'source': source, 'value': float(value), 'unit': unit, **where}


def _finding(rule, severity, facts, threshold, basis, explanation):
    return dict(rule_id=rule, severity=severity, verdict='flagged', facts=facts,
                threshold=threshold, basis=basis, explanation='Native screening: ' + explanation)


def _lost_load(n, capture, ens_targets, raw_prices, period_objective_weights):
    findings, missing = [], []
    frame = (capture or {}).get('lost_load_t')
    if (not _valid(frame) or not frame.index.equals(n.snapshots) or not frame.columns.is_unique
            or not set(frame.columns) <= set(n.buses.index) | set(n.loads.index) or (frame < 0).any().any()):
        return [], ['No complete finite lost-load capture for this result.']
    buses = {col: str(n.loads.at[col, 'bus']) if col in n.loads.index else str(col) for col in frame.columns}
    if not set(buses.values()) <= set(n.buses.index):
        return [], ['Lost-load locations cannot be resolved to this result network.']
    captured_energy = (capture or {}).get('lost_load_load_period_mwh')
    if captured_energy is not None and (not _valid(captured_energy) or set(captured_energy.columns) != set(frame.columns)):
        return [], ['Solve-time loss energy capture is incomplete or non-finite.']
    w = _weights(n, 'generators').reindex(frame.index) if captured_energy is None else None
    if w is not None and (not _valid(w) or (w < 0).any()):
        return [], ['Generator energy weightings are unavailable or invalid.']
    from services.adequacy.metrics import electrical_columns
    electrical = electrical_columns(n, list(frame.columns))
    for period, sns in _groups(n):
        if captured_energy is None:
            if w.loc[sns].sum() <= 0:
                missing.append(f'Period {period}: no positive energy weighting.'); continue
            energy = frame.loc[sns].mul(w.loc[sns], axis=0).sum()
            energy_source = 'capture.lost_load_t; generator weights'
        else:
            labels = [str(p) for p in captured_energy.index]
            label = 'ALL' if period is None else period
            if labels.count(label) != 1:
                missing.append(f'Period {period}: solve-time loss energy is unavailable.'); continue
            energy = captured_energy.iloc[labels.index(label)].reindex(frame.columns)
            energy_source = 'capture.lost_load_load_period_mwh'
        if not _valid(energy) or (energy < 0).any():
            missing.append(f'Period {period}: loss energy is non-finite or negative.'); continue
        total = float(energy.sum())
        if total > LOST_LOAD_MWH:
            target = (ens_targets or {}).get(period)
            cap = target.get('cap_mwh') if isinstance(target, dict) else None
            if not isinstance(cap, Real) or isinstance(cap, bool) or not np.isfinite(cap) or cap < 0:
                cap = None
            admitted = (cap is not None
                        and target.get('source') and energy.drop(electrical, errors='ignore').sum() == 0)
            severity = 'warning' if admitted and total <= cap else 'error'
            facts = [_fact(energy_source, total, 'MWh', period=period)]
            for col, value in energy.items():
                if value > 0:
                    at = frame.loc[sns, col].idxmax()
                    facts.append(_fact(energy_source, value, 'MWh', bus=buses[col], period=period,
                        **({'load': str(col)} if col in n.loads.index else {}), peak_snapshot=str(at[-1] if isinstance(at, tuple) else at)))
            if admitted:
                facts.append(_fact(target['source'], cap, 'MWh', period=period))
            findings.append(_finding('lost_load', severity, facts, {'positive_loss_mwh': LOST_LOAD_MWH, 'cap_mwh': cap if admitted else None},
                target['source'] if admitted else 'No admitted ENS cap; observed unserved energy.',
                f'{total:g} MWh of lost load' + (f' against the admitted {cap:g} MWh ENS cap.' if admitted else ' without an admitted ENS cap.')))
    if findings:
        return findings, missing + ['Price trigger not evaluated separately: loss already reported.']
    # Native nodal duals, NEVER an already normalised marginal-price table.
    if not _valid(raw_prices) or not raw_prices.index.equals(n.snapshots):
        missing.append('Price trigger not evaluated: no admitted raw nodal duals and objective-weight basis.')
    else:
        weights = n.snapshot_weightings.objective.copy()
        if isinstance(n.snapshots, pd.MultiIndex):
            if period_objective_weights is None:
                missing.append('Price trigger not evaluated: solve-time investment objective weightings are unavailable.')
                return findings, missing
            weights *= pd.Series([period_objective_weights.get(p, np.nan)
                                  for p in n.snapshots.get_level_values(0)], index=n.snapshots)
        voll = (capture or {}).get('voll_eur_per_mwh')
        if not _valid(weights) or (weights <= 0).any() or voll is None or not np.isfinite(voll) or voll <= 0:
            missing.append('Price trigger not evaluated: positive objective weights and explicit VoLL are required.')
        elif not findings:
            prices = raw_prices.div(weights, axis=0)
            peak = float(prices.max().max())
            if peak >= voll * (1 - REL_TOL):
                findings.append(_finding('lost_load', 'warning', [_fact('raw nodal dual / snapshot objective / investment objective', peak, 'EUR/MWh'),
                    _fact('capture.voll_eur_per_mwh', voll, 'EUR/MWh')], {'voll_relative_tolerance': REL_TOL},
                    'Raw nodal dual normalised by both objective weightings.',
                    'price at the value of lost load without shedding: an ENS cap or another constraint is binding.'))
    return findings, missing


def _branches(n, source):
    findings, missing = [], []
    if source != 'lopf':
        return [], ['No labelled LOPF results; AC PF apparent power is not covered.']
    for table, dynamic in ((n.lines, n.lines_t), (n.transformers, n.transformers_t)):
        for name, row in table.iterrows():
            rating = row.get('s_nom_opt') if row.get('s_nom_extendable') else row.get('s_nom')
            for period, sns in _groups(n):
                if period is not None and not row.get('build_year', 0) <= int(period) < row.get('build_year', 0) + row.get('lifetime', np.inf):
                    missing.append(f'{name}, period {period}: outside build year/lifetime.'); continue
                if period is not None and row.get('s_nom_extendable'):
                    missing.append(f'{name}, period {period}: effective branch capacity is not covered.'); continue
                p0, p1 = dynamic.p0.reindex(index=sns).get(name), dynamic.p1.reindex(index=sns).get(name)
                limit_pu = dynamic.s_max_pu.reindex(index=sns).get(name) if 's_max_pu' in dynamic else None
                if limit_pu is None:
                    limit_pu = pd.Series(row.get('s_max_pu', 1.), index=sns)
                if (rating is None or not np.isfinite(rating) or rating <= 0 or not _valid(p0) or not _valid(p1)
                        or not _valid(limit_pu) or (limit_pu <= 0).any()):
                    missing.append(f'{name}, period {period}: missing finite endpoint flows or positive planning rating.'); continue
                limit = limit_pu * rating
                util = pd.concat([p0.abs(), p1.abs()], axis=1).max(axis=1) / limit
                if util.max() > 1 + REL_TOL:
                    at = util.idxmax()
                    facts = [_fact('LOPF endpoint flows / planning limit', util.at[at], 'p.u.', component=str(name), snapshot=str(at[-1] if isinstance(at, tuple) else at), period=period),
                             _fact('s_nom_opt' if row.get('s_nom_extendable') else 's_nom', rating, 'MVA'),
                             _fact('s_max_pu * rating', limit.at[at], 'MW')]
                    findings.append(_finding('branch_overload', 'error', facts, {'utilisation': 1 + REL_TOL},
                        'LOPF active power / (s_max_pu * rating).',
                        f'{name} exceeds its LOPF active-power planning limit; a numerical or solver sanity check, not grid-code compliance.'))
    return findings, missing or ([] if len(n.lines) + len(n.transformers) else ['No passive branches.'])


def _curtailment(n):
    from services.results.curtailment import curtailment_frame
    if not _valid(n.generators_t.p) or not n.generators_t.p.index.equals(n.snapshots):
        return [], ['No complete finite generator dispatch.']
    availability = n.get_switchable_as_dense('Generator', 'p_max_pu')
    if not _valid(availability) or (availability < 0).any().any():
        return [], ['Generator availability is missing, negative or non-finite.']
    curtailed = curtailment_frame(n)
    if curtailed is None or curtailed.empty:
        return [], ['No native curtailable generators.']
    w = _weights(n, 'generators')
    if not _valid(w) or (w < 0).any():
        return [], ['Invalid generator energy weightings.']
    findings, missing = [], []
    carriers = n.generators.reindex(curtailed.columns).carrier
    for period, sns in _groups(n):
        for carrier in carriers.unique():
            cols = list(carriers[carriers == carrier].index)
            dispatch = n.generators_t.p.loc[sns, cols]
            energy = float(curtailed.loc[sns, cols].mul(w.loc[sns], axis=0).sum().sum())
            available = energy + float(dispatch.mul(w.loc[sns], axis=0).sum().sum())
            if not np.isfinite(available) or available <= 0 or (dispatch < 0).any().any():
                missing.append(f'{carrier}, period {period}: no positive admitted available energy.'); continue
            if energy / available > CURTAILMENT_SHARE:
                findings.append(_finding('curtailment_share', 'warning', [
                    _fact('curtailment_frame; generator weights', energy / available, 'fraction', carrier=str(carrier), period=period),
                    _fact('curtailment_frame; generator weights', energy, 'MWh'),
                    _fact('curtailment_frame + dispatch; generator weights', available, 'MWh')],
                    {'share': CURTAILMENT_SHARE}, 'Per-investment-period screening heuristic.',
                    f'{carrier} curtailment exceeds {CURTAILMENT_SHARE:.0%}: screening heuristic; high curtailment can be the least-cost design.'))
    return findings, missing


def _storage(n):
    findings, missing = [], []
    for name, row in n.storage_units.iterrows():
        capacity = row.get('p_nom_opt') if row.get('p_nom_extendable') else row.get('p_nom')
        charge, discharge = n.storage_units_t.p_store.get(name), n.storage_units_t.p_dispatch.get(name)
        if (capacity is None or not np.isfinite(capacity) or capacity < 0 or not _valid(charge) or not _valid(discharge)
                or not charge.index.equals(n.snapshots) or not discharge.index.equals(n.snapshots)):
            missing.append(f'{name}: separate finite StorageUnit charge/discharge and capacity are required.'); continue
        tolerance = max(ABS_POWER_TOL_MW, REL_TOL * capacity)
        simultaneous = pd.concat([charge, discharge], axis=1).min(axis=1)
        if simultaneous.max() > tolerance:
            at = simultaneous.idxmax()
            findings.append(_finding('storage_simultaneous', 'warning', [
                _fact('storage_units_t.p_store', charge.at[at], 'MW', component=str(name), snapshot=str(at[-1] if isinstance(at, tuple) else at), period=str(at[0]) if isinstance(at, tuple) else None),
                _fact('storage_units_t.p_dispatch', discharge.at[at], 'MW'),
                _fact('max(absolute tolerance, relative tolerance * capacity)', tolerance, 'MW')],
                {'absolute_mw': ABS_POWER_TOL_MW, 'relative': REL_TOL}, 'Separate StorageUnit flows, not signed net dispatch.',
                f'{name} is charging and discharging: with efficiency below one a StorageUnit can dissipate surplus; Store/Link cycling is not covered.'))
    return findings, missing or ([] if len(n.storage_units) else ['No StorageUnits; Store/Link cycling is not covered.'])


def _bounds(n):
    findings, missing, count = [], [], 0
    for kind, table, nominal in (('Generator', n.generators, 'p_nom'), ('StorageUnit', n.storage_units, 'p_nom'),
                                  ('Line', n.lines, 's_nom'), ('Transformer', n.transformers, 's_nom'),
                                  ('Link', n.links, 'p_nom'), ('Store', n.stores, 'e_nom')):
        for name, row in table.iterrows():
            if not row.get(nominal + '_extendable', False):
                continue
            count += 1
            if isinstance(n.snapshots, pd.MultiIndex):
                missing.append(f'{kind} {name}: multi-period effective-capacity facts are not covered.'); continue
            opt = row.get(nominal + '_opt')
            if opt is None or not np.isfinite(opt) or opt < 0:
                missing.append(f'{kind} {name}: no finite nonnegative optimised capacity.'); continue
            for suffix in ('max', 'min'):
                bound = row.get(f'{nominal}_{suffix}')
                if bound is None or not np.isfinite(bound) or bound <= 0:
                    continue
                if abs(opt - bound) <= max(ABS_POWER_TOL_MW, REL_TOL * abs(bound)):
                    unit = 'MWh' if nominal == 'e_nom' else 'MVA' if nominal == 's_nom' else 'MW'
                    findings.append(_finding('build_at_bound', 'warning', [
                        _fact(nominal + '_opt', opt, unit, component=str(name), component_class=kind),
                        _fact(f'{nominal}_{suffix}', bound, unit)], {'absolute': ABS_POWER_TOL_MW, 'relative': REL_TOL},
                        'A capacity boundary is a design signal, not an invalid solution.',
                        f'{kind} {name} reaches its upper capacity bound.' if suffix == 'max' else f'{kind} {name} is a forced build at its positive lower capacity bound.'))
                    break
    return findings, missing or ([] if count else ['No extendable capacity bounds.'])


def evaluate(n, *, capture, ens_targets=None, raw_prices=None, period_objective_weights=None, source='lopf') -> dict:
    """Return flagged native facts plus explicit per-rule/partial coverage.

    ens_targets maps a period (None for one period) to cap_mwh and its native
    source. It must come from this solve's adequacy report, never a draft goal.
    Raw prices are UNNORMALISED nodal duals; multi-period objective weights
    must be the solve-time values (restored input weights are insufficient).
    """
    findings, rules = [], {}
    readers = (lambda: _lost_load(n, capture, ens_targets, raw_prices, period_objective_weights),
               lambda: _branches(n, source), lambda: _curtailment(n), lambda: _storage(n), lambda: _bounds(n))
    for rule, reader in zip(RULE_IDS, readers, strict=True):
        try:
            found, missing = reader() if source == 'lopf' else ([], ['LOPF result provenance is unavailable.'])
        except (KeyError, ValueError, TypeError, AttributeError):
            found, missing = [], ['Native facts are missing or malformed for this screening rule.']
        findings.extend(found)
        rules[rule] = {'verdict': 'flagged' if found else 'not_evaluated' if missing else 'passed', 'not_evaluated': missing}
    findings.sort(key=lambda f: (f['severity'] != 'error', RULE_IDS.index(f['rule_id'])))
    return {'findings': findings, 'coverage': {'rules': rules, 'not_covered': [
        'AC PF apparent power and grid-code compliance.',
        'Store/Link cycling; StorageUnits only.',
        'Strict publication (unsupported).',
        'Zone ENS limits and LOLE-to-energy conversion.',
    ]}}
