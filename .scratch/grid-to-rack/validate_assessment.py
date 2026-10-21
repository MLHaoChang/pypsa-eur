"""Offline research consistency checks, not an engine or domain validation."""

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path


def validate():
    here = Path(__file__).parent
    repo = here.parents[1]

    def load(name):
        return json.loads((here / name).read_text())

    native = load('native-evidence.json')
    for entry in native['files']:
        data = subprocess.check_output(
            ['git', 'show', native['baseline'] + ':' + entry['path']], cwd=repo)
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['path']
    sources = load('source-observations.json')
    for item in sources['repositories']:
        if 'error' in item:
            continue
        assert re.fullmatch('[a-f0-9]{40}', item['commit'])
        assert re.fullmatch('[a-f0-9]{64}', item['metadata_sha256'])
        for key in ('readme', 'license_file_evidence'):
            if item.get(key):
                assert re.fullmatch('[a-f0-9]{64}', item[key]['sha256'])
    for item in sources['selected_files'] + sources['pages']:
        assert item.get('error') or re.fullmatch('[a-f0-9]{64}', item['sha256'])
    plan = load('implementation-plan.json')
    assert plan['baseline'] == native['baseline'] and plan['runtime_implementation'] is False
    packages = plan['packages']
    ids = {x['id'] for x in packages}
    assert ids == {f'GTR-{i:02d}' for i in range(7)} and len(packages) == 7
    tests = set(re.findall(r'^\| ([A-Z]\d{2}) \|',
                          (here / '04-IMPLEMENTATION-TESTS.md').read_text(), re.MULTILINE))
    gaps = set(re.findall(r'^\| (G\d{2}) \|',
                         (here / '01-CURRENT-GAPS.md').read_text(), re.MULTILINE))
    covered = set()
    for item in packages:
        assert item['status'] == 'proposed_not_implemented'
        assert set(item['depends_on']) <= ids and item['id'] not in item['depends_on']
        assert item['acceptance_test_ids'] and set(item['acceptance_test_ids']) <= tests
        assert item['gap_ids'] and set(item['gap_ids']) <= gaps
        covered.update(item['gap_ids'])
    assert covered == gaps and len(gaps) == 14
    example = load('illustrative-balance.json')
    assert math.isclose(example['energy_mwh'], example['pulse_mw'] * example['duration_s'] / 3600)
    assert math.isclose(example['energy_kwh'], example['energy_mwh'] * 1000)
    files = list(here.glob('*.md')) + [repo / 'docs/research/gridspine-grid-to-rack-overview.md']
    for file in files:
        for link in re.findall(r'\]\(([^)]+)\)', file.read_text()):
            if '://' in link or link.startswith('#'):
                continue
            assert (file.parent / link.split('#')[0]).exists(), (file, link)
    print(json.dumps({'status': 'pass', 'scope': 'research consistency only',
                      'native_files_hash_checked': len(native['files']), 'gap_ids': len(gaps),
                      'proposed_packages': len(packages), 'validation_scenarios_documented': len(tests),
                      'external_engine_executed': False, 'domain_approval': False}, indent=2))


if __name__ == '__main__':
    validate()
