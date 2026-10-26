"""Hydrogen headroom is not renewable spill on either results surface (task H)."""
import pandas as pd
import pypsa
import pytest

from services.results import carrier_kpis


@pytest.mark.parametrize('carrier', [
    'hydrogen', 'Hydrogen', 'Hydrogen Storage', 'HydrogenFuelCell',
    'hydrogen supply', 'H2 (hydrogen)', 'HydrogenImport', 'hydrogen CHP',
])
def test_hydrogen_carriers_are_not_renewable(carrier):
    assert carrier_kpis._is_renewable_carrier(carrier) is False


def test_carrier_table_keeps_its_renewable_answer():
    # Literal expectations include shipped ids, nice names and user-coined labels.
    renewable = [
        'onwind', 'offwind-ac', 'offwind-dc', 'offwind-float', 'solar',
        'solar-hsat', 'solar-rooftop', 'ror', 'hydro', 'geothermal', 'biomass',
        'wave', 'tidal', 'pv', 'biogas', 'wind', 'Onshore Wind',
        'Offshore Wind (AC)', 'Offshore Wind (DC)', 'Offshore Wind (Floating)', 'Solar PV (utility)',
        'Solar PV (rooftop)', 'Run-of-River', 'Hydro Reservoir', 'Geothermal',
        'Biomass', 'Wave', 'Pumped Hydro', 'Pumped Hydro Storage',
        'Hydro', 'hydropower', 'hydroelectric', 'HydroPlant', 'hydro-reservoir',
        'solar_hydrogen', 'HydrogenSolar', 'flex solar', 'solar_dr', 'Solar DR',
        'dsm wind', 'wind_curtail', 'solar thermal', 'solar PV', 'Solar', 'Wind',
    ]
    other = [
        'AC', 'DC', 'CCGT', 'OCGT', 'coal', 'lignite', 'oil', 'nuclear',
        'gas', 'diesel', 'grid', 'NH3 import', 'NH3 shortfall', 'load',
        'curtailment', 'PHS', 'H2', 'H2 electrolysis', 'H2 fuel cell',
        'H2 Electrolysis', 'H2 Fuel Cell', 'Hydrogen', 'hydrogen',
        'Hydrogen Storage', 'HydrogenFuelCell', 'hydrogen supply',
        'H2 (hydrogen)', 'HydrogenImport', 'hydrogen CHP', 'heat', 'Heat',
        'NH3', 'N2', 'ore', 'concentrate', 'Ammonia', 'Nitrogen', 'Ore',
        'Concentrate', 'battery', 'Battery', 'CCGT (Gas)', 'OCGT (Gas)',
        'Hard Coal', 'Lignite', 'Oil', 'Nuclear', 'Natural Gas', 'Diesel',
        'Open-Cycle Gas', 'Combined-Cycle Gas',
        'Run of River', 'Reservoir & Dam', 'Load Shedding', 'load_shedding',
        'product_shortfall', 'GAS',
    ]
    for carrier, expected in [(c, True) for c in renewable] + [(c, False) for c in other]:
        assert carrier_kpis._is_renewable_carrier(carrier) is expected, carrier


def test_renewable_answer_matches_time_series_category():
    from services.profile_shapes import _gen_category

    for carrier in [
        'H2', 'hydrogen', 'Hydrogen', 'Hydrogen Storage', 'HydrogenFuelCell',
        'H2 fuel cell', 'hydro', 'Hydro', 'ror', 'Run of River', 'onwind',
        'solar', 'biomass', 'gas',
    ]:
        category = _gen_category(carrier)
        if category != 'dr':
            assert carrier_kpis._is_renewable_carrier(carrier) == (category == 'renewable'), carrier
    for carrier in ['flex solar', 'solar_dr', 'dsm wind', 'wind_curtail']:
        assert carrier_kpis._is_renewable_carrier(carrier) is True


def _network(generators):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2025-01-01', periods=4, freq='h'))
    n.add('Bus', 'B')
    n.add('Carrier', 'H2', nice_name='Hydrogen')
    for name, carrier in generators.items():
        n.add('Generator', name, bus='B', carrier=carrier, p_nom=10.0)
    n.generators['p_nom_opt'] = 10.0
    n.generators_t.p = pd.DataFrame(4.0, index=n.snapshots, columns=n.generators.index)
    n.add('Load', 'demand', bus='B', p_set=20.0)
    return n


def _rows(n):
    def plain(net, accessor, attr, source='lopf'):
        return getattr(getattr(net, accessor), attr, None)

    return {r['carrier']: r for r in carrier_kpis.compute_carrier_kpis(n, result_df=plain)['rows']}


def test_hydrogen_generator_row_has_no_curtailment():
    n = _network({'h2': 'hydrogen', 'catalogue_h2': 'H2', 'pv': 'solar', 'water': 'hydro', 'thermal': 'gas'})
    rows = _rows(n)
    for carrier in ['hydrogen', 'Hydrogen']:
        assert rows[carrier]['curtailment_mwh'] == 0.0
        assert rows[carrier]['curtailment_pct'] == 0.0
        assert rows[carrier]['energy_mwh'] == pytest.approx(16.0)
    for carrier in ['solar', 'hydro']:
        assert rows[carrier]['curtailment_mwh'] == pytest.approx(24.0)
        assert rows[carrier]['curtailment_pct'] == pytest.approx(60.0)
    assert rows['gas']['curtailment_mwh'] == 0.0


def test_curtailment_tab_agrees_with_carrier_kpis_on_hydrogen():
    from services.results.curtailment import compute_curtailment, curtailment_frame

    n = _network({'h2_unit': 'hydrogen', 'hydro_unit': 'hydro', 'gas_unit': 'gas'})
    frame = curtailment_frame(n)
    assert frame['hydro_unit'].sum() == pytest.approx(24.0)
    assert 'h2_unit' not in frame.columns
    assert 'gas_unit' not in frame.columns
    columns = compute_curtailment(n, None, None)['columns']
    assert 'hydro_unit' in columns
    assert 'h2_unit' not in columns
    rows = _rows(n)
    assert rows['hydrogen']['curtailment_mwh'] == 0.0
    assert rows['hydro']['curtailment_mwh'] == pytest.approx(24.0)
    # Subsidy is an independent opt-in and remains effective for hydrogen.
    n.generators['curtailment_cost'] = 0.0
    n.generators.loc['h2_unit', 'curtailment_cost'] = 5.0
    assert curtailment_frame(n)['h2_unit'].sum() == pytest.approx(24.0)
    # Camel-case must be split before lowercasing, on the raw-carrier surface too.
    n.generators.loc['h2_unit', 'carrier'] = 'HydrogenSolar'
    n.generators.loc['h2_unit', 'curtailment_cost'] = 0.0
    assert curtailment_frame(n)['h2_unit'].sum() == pytest.approx(24.0)
