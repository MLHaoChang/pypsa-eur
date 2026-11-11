"""Inspect a few cached workbook metadata cells, not a historical data import.

Usage: python inspect_workbooks.py /path/to/private/research-cache
Requires openpyxl in the research environment; no application dependency is added.
Source files are hash-checked. Only cached values are read, read-only, no external
links retained. No formulas or macros execute and no third-party price rows emit.
"""

import hashlib
import json
import sys
from pathlib import Path

import openpyxl


def inspect(cache):
    root = Path(__file__).parent
    observations = {x['id']: x for x in json.loads((root / 'fetch-observations.json').read_text())}
    result = {}
    for key in ('lbl_solar_xlsx', 'lbl_wind_xlsx'):
        path = cache / (key + '.raw')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == observations[key]['sha256']
        with path.open('rb') as handle:
            book = openpyxl.load_workbook(handle, read_only=True, data_only=True, keep_links=False)
            try:
                if key == 'lbl_solar_xlsx':
                    note = str(book['PPA Price by Project (PV only)']['A23'].value)
                    assert '2023' in note and '4%' in note and 'leveliz' in note.lower()
                    sheet = book['PPA Price by Project (PV only)']
                    for cell, expected in [('A25', 'Execution'), ('B25', 'Capacity'), ('C25', 'Term')]:
                        assert expected in str(sheet[cell].value)
                    assert 'first-year' in str(book['LevelTen Solar PPA Prices']['B23'].value).lower()
                    assert 'first-year' in str(book['Trio Solar PPA Prices']['B23'].value).lower()
                    for name in ('LevelTen Solar PPA Prices', 'Trio Solar PPA Prices'):
                        assert 'nominal' in str(book[name]['B24'].value).lower()
                    assert 'Excluded' in str(book['O&M Cost Time Trend']['A23'].value)
                    assert 'fixed effects' in str(book['Degradation']['A1'].value).lower()
                    result[key] = {
                        'sha256': observations[key]['sha256'],
                        'metadata_cells_checked': {
                            'PPA Price by Project (PV only)': ['A23', 'A25', 'B25', 'C25'],
                            'LevelTen Solar PPA Prices': ['B23', 'B24'],
                            'Trio Solar PPA Prices': ['B23', 'B24'],
                            'O&M Cost Time Trend': ['A23'], 'Degradation': ['A1']},
                        'executed_price_basis': 'full-term levelized, real2023 dollars, 4% real discount',
                        'offered_indices_basis': 'first-year nominal offers; separate third-party sources',
                        'om_scope': 'includes and excludes named cost categories; not all-in site O&M',
                        'age_performance_model': 'fixed-effects performance with project age'}
                else:
                    sheet = book['Wind PPAs by Plant']
                    for cell, expected in [('A25', 'Execution'), ('B25', 'Capacity'), ('C25', 'Term'),
                                           ('D25', 'Region'), ('E25', 'Region')]:
                        assert expected in str(sheet[cell].value)
                    header = str(book['Wind PPAs by Plant']['F25'].value)
                    assert '2025' in header and 'Real' in header
                    assert 'Marex' in str(book['REC Prices']['A21'].value)
                    assert '#N/A' in str(book['REC Prices']['A23'].value)
                    assert '2025' in str(book['REC Prices']['A24'].value)
                    result[key] = {
                        'sha256': observations[key]['sha256'],
                        'metadata_cells_checked': {'Wind PPAs by Plant': ['A25', 'B25', 'C25', 'D25', 'E25', 'F25'],
                                                   'REC Prices': ['A21', 'A23', 'A24']},
                        'executed_price_header': header,
                        'rec_sheet_source': 'Marex Spectron; third-party rights assessment required'}
            finally:
                book.close()
    return {'scope': 'selected workbook metadata, not formula recalculation or price-history validation',
            'raw_price_records_published': False, 'results': result}


if __name__ == '__main__':
    print(json.dumps(inspect(Path(sys.argv[1])), indent=2))
