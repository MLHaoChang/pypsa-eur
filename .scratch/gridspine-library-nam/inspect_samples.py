"""Offline research inspection; never imports data into the application.

Usage: python inspect_samples.py /path/to/private/research-cache > sample-inspection.json
The cache holds <observation_id>.raw files whose expected hashes are in
fetch-observations.json. No network, macros, formulas or external workbook links run.
Only schema, counts and metadata are emitted, not third-party price records.
"""

import csv
import hashlib
import io
import json
import sys
import zipfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET


def inspect(cache):
    root = Path(__file__).parent
    evidence = {x['id']: x for x in json.loads((root / 'fetch-observations.json').read_text())}
    results = {}

    def read(key):
        data = (cache / (key + '.raw')).read_bytes()
        assert hashlib.sha256(data).hexdigest() == evidence[key]['sha256'], key
        return data

    def rows(data):
        return list(csv.reader(io.StringIO(data.decode('utf-8-sig'))))

    def finite(value):
        assert Decimal(value).is_finite()

    r = rows(read('nyiso_sample'))
    assert r[0] == ['Time Stamp', 'Name', 'PTID', 'LBMP ($/MWHr)',
                    'Marginal Cost Losses ($/MWHr)', 'Marginal Cost Congestion ($/MWHr)']
    keys = [(a[0], a[2]) for a in r[1:]]
    assert len(keys) == len(set(keys))
    for a in r[1:]:
        datetime.strptime(a[0], '%m/%d/%Y %H:%M')
        for value in a[3:]:
            finite(value)
    results['nyiso_sample'] = {'data_rows': len(r)-1, 'locations': len({a[2] for a in r[1:]}),
                              'hours': len({a[0] for a in r[1:]}), 'offset_in_timestamp': False}

    r = rows(read('miso_sample'))
    assert 'Eastern Standard Time (EST)' in ','.join(r[3])
    assert r[4] == ['Node', 'Type', 'Value'] + [f'HE {i}' for i in range(1, 25)]
    counts = Counter(a[2] for a in r[5:])
    assert set(counts) == {'LMP', 'MCC', 'MLC'}
    assert len({tuple(a[:3]) for a in r[5:]}) == len(r)-5
    for a in r[5:]:
        assert len(a) == 27
        for value in a[3:]:
            finite(value)
    results['miso_sample'] = {'data_rows': len(r)-5, 'component_rows': dict(counts),
                             'hours_per_row': 24, 'time_standard': 'fixed EST (source preamble)'}

    z = zipfile.ZipFile(io.BytesIO(read('caiso_oasis_download')))
    assert len(z.namelist()) == 1 and z.namelist()[0].endswith('.csv')
    r = list(csv.DictReader(io.StringIO(z.read(z.namelist()[0]).decode('utf-8-sig'))))
    for a in r:
        assert datetime.fromisoformat(a['INTERVALSTARTTIME_GMT']).tzinfo is not None
        assert a['MARKET_RUN_ID'] == 'DAM'
        finite(a['MW'])
    assert len({(a['INTERVALSTARTTIME_GMT'], a['NODE'], a['LMP_TYPE']) for a in r}) == len(r)
    results['caiso_oasis_download'] = {'data_rows': len(r),
        'hours': len({a['INTERVALSTARTTIME_GMT'] for a in r}),
        'component_rows': dict(Counter(a['LMP_TYPE'] for a in r)),
        'value_column': 'MW in PRC_LMP CSV; price semantics require query/schema context'}

    r = rows(read('ieso_da_csv'))
    assert r[0][0].startswith('CREATED AT ') and ' FOR ' in r[0][0]
    assert r[1] == ['Delivery Hour', 'Pricing Location', 'LMP',
                    'Energy Loss Price', 'Energy Congestion Price']
    assert len({tuple(a[:2]) for a in r[2:]}) == len(r)-2
    for a in r[2:]:
        assert 1 <= int(a[0]) <= 24
        for value in a[2:]:
            finite(value)
    results['ieso_da_csv'] = {'data_rows': len(r)-2,
        'locations': len({a[1] for a in r[2:]}), 'delivery_date': r[0][0].split(' FOR ')[1],
        'timestamp_rule': 'delivery date and delivery hour, not creation date'}

    doc = ET.fromstring(read('ieso_ozp_xml'))
    def values(tag):
        return [e.text for e in doc.iter() if e.tag.split('}')[-1] == tag]
    assert values('DeliveryDate') == ['2026-10-20']
    assert len(values('PricingHour')) == 24
    for v in values('ZonalPrice'):
        finite(v)
    results['ieso_ozp_xml'] = {'delivery_date': values('DeliveryDate')[0],
        'hours': len(values('PricingHour')), 'flags': sorted(set(values('Flag'))),
        'schema_namespace': doc.tag.split('}')[0].removeprefix('{')}

    z = zipfile.ZipFile(io.BytesIO(read('cenace_original_sample')))
    names = [n for n in z.namelist() if n.startswith('PreciosMargLocales SIN MDA')]
    assert len(names) == 2
    versions = []
    for n in names:
        r = rows(z.read(n))
        h = next(i for i, a in enumerate(r) if a and a[0].strip() == 'Hora')
        assert 'Precio marginal local ($/MWh)' in [a.strip() for a in r[h]]
        records = [a for a in r[h+1:] if a and a[0].strip()]
        assert len({(a[0], a[1]) for a in records}) == len(records)
        for a in records:
            assert 1 <= int(a[0]) <= 24
            for value in a[2:]:
                finite(value)
        versions.append({'entry': n, 'data_rows': len(records),
                         'nodes': len({a[1] for a in records}), 'header_row_1based': h+1})
    results['cenace_original_sample'] = {'operating_date': '2024-03-23',
        'status': 'original historical publications, not final corrected values', 'versions': versions}

    fx = json.loads(read('boc_fx'))
    assert 'FXUSDCAD' in fx['seriesDetail']
    for a in fx['observations']:
        finite(a['FXUSDCAD']['v'])
    results['boc_fx'] = {'observations': len(fx['observations']),
        'series': 'FXUSDCAD', 'unit': 'CAD per USD',
        'description': fx['seriesDetail']['FXUSDCAD']['description']}
    return {'scope': 'bounded offline source-schema inspection, not application validation',
            'raw_records_published': False, 'results': results}


if __name__ == '__main__':
    print(json.dumps(inspect(Path(sys.argv[1])), indent=2))
