"""Generator categories at the classifier, profile API and readiness seams (task F)."""
import pandas as pd
import pypsa
import pytest

from services.profile_shapes import _gen_category


@pytest.mark.parametrize('carrier', ['hydro', 'Hydro', 'hydro-reservoir', 'hydropower', 'hydroelectric', 'HydroPlant'])
def test_hydro_files_under_renewable(carrier):
    assert _gen_category(carrier) == 'renewable'


@pytest.mark.parametrize('carrier,category', [
    ('hydrogen', 'other'), ('hydrogen supply', 'other'), ('H2 (hydrogen)', 'other'),
    ('hydrogen CHP', 'conventional'), ('hydrogen_gas_turbine', 'conventional'),
    ('gas_hydrogen', 'conventional'), ('solar_hydrogen', 'renewable'),
    ('hydrogenated', 'other'), ('dr_hydrogen', 'dr'),
    ('gasHydrogen', 'conventional'), ('hydrogenSolar', 'renewable'), ('GeoHydrogenThermal', 'conventional'),
])
def test_hydrogen_is_not_demand_response(carrier, category):
    assert _gen_category(carrier) == category


@pytest.mark.parametrize('carrier', [
    'dr', 'DR-1', 'dr_industrial', 'DR load', 'DR2', 'dsm', 'industrial DSM', 'dsmLoad',
    'dsr', 'flex', 'Flex Load', 'flexible_load', 'flexibility', 'interruptible',
    'curtail', 'curtailment', 'demand_response', 'demand response', 'demand-response',
    'DemandResponse', 'dr\tload', 'load.dr', 'load/dr', '(dr)', 'demand2response',
])
def test_demand_response_keywords_match_as_words(carrier):
    assert _gen_category(carrier) == 'dr'


@pytest.mark.parametrize('carrier,category', [
    ('drain', 'other'), ('dryer', 'other'), ('hydraulic', 'other'), ('flexload', 'other'),
    ('drload', 'other'), ('dsmload', 'other'), ('curtailed_wind', 'renewable'),
    ('drill_diesel', 'conventional'), ('noninterruptible', 'other'), ('curtailing', 'other'),
])
def test_dr_inside_a_word_is_not_demand_response(carrier, category):
    assert _gen_category(carrier) == category


@pytest.mark.parametrize('category,carriers', [
    ('renewable', ['onwind', 'offwind-ac', 'offwind-dc', 'offwind-float', 'solar-hsat', 'ror',
                   'biomass', 'geothermal', 'wind', 'solar', 'solar-rooftop', 'wave', 'hydro',
                   'GeoThermal', 'run-of-river', 'solarpv']),
    ('conventional', ['gas', 'coal', 'oil', 'diesel', 'thermal', 'CCGT', 'OCGT', 'nuclear',
                     'lignite', 'gas_unrated', 'diesel_unrated', 'load_sink', 'spillage',
                     'heat-dump', 'hardcoal', 'biodiesel', 'natgas']),
    ('other', ['grid', 'import', 'NH3 import', 'NH3 shortfall', 'load', 'PHS', 'load_shedding', 'hydrogen',
               'AC', 'DC', 'H2', 'H2 electrolysis', 'H2 fuel cell', 'heat', 'NH3', 'N2',
               'ore', 'concentrate', 'battery', 'air', 'flywheel', 'heat-pump-air']),
    ('dr', ['curtailment', 'dr', 'dsm', 'dsr', 'flex', 'interruptible', 'curtail', 'demand_response']),
])
def test_shipped_carriers_keep_their_category(category, carriers):
    assert {c: _gen_category(c) for c in carriers} == dict.fromkeys(carriers, category)


def test_generator_profiles_report_the_word_category(client, install_network):
    n = pypsa.Network()
    n.set_snapshots(pd.date_range('2030-01-01', periods=2, freq='h'))
    n.add('Bus', 'b')
    for carrier in ('hydro', 'hydrogen', 'dsm', 'gas'):
        n.add('Generator', carrier, bus='b', carrier=carrier, p_nom=1.0)
    install_network(n)
    r = client.get('/api/network/generators/profiles')
    assert r.status_code == 200, r.text
    assert {name: g['category'] for name, g in r.json().items()} == {
        'hydro': 'renewable', 'hydrogen': 'other', 'dsm': 'dr', 'gas': 'conventional',
    }


def test_readiness_thermal_scan_follows_the_word_category():
    from models.energy_hub import default_strong_grid_pack
    from services.adequacy.eh_readiness import eh_readiness
    from tests.test_energy_hub_frontier_fmea import _feeder_hub

    n = _feeder_hub()
    for name, carrier in [('hydrogen CHP', 'hydrogen CHP'), ('hydro_unit', 'hydrogen')]:
        n.add('Generator', name, bus=n.buses.index[0], carrier=carrier, p_nom=1.0)
        n.generators.at[name, 'outage_rate_value'] = float('nan')
        n.generators.at[name, 'mttr_hours'] = float('nan')
    missing = {x['name'] for x in eh_readiness(n, default_strong_grid_pack(), budget_solves=10)['outage_units']['missing']
               if x['class'] == 'Generator'}
    assert 'hydrogen CHP' in missing
    assert 'hydro_unit' not in missing
