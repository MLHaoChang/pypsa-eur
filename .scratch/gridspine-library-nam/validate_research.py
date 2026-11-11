"""Portable offline structural checks; not an application or domain test suite."""

import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path


def validate():
    here = Path(__file__).parent
    repo = here.parents[1]
    load = lambda name: json.loads((here / name).read_text())
    requirements = load('data-requirements.json')['requirements']
    snapshot = load('native-model-snapshot.json')
    sources = load('source-register.json')['sources']
    evidence = load('fetch-observations.json')
    mappings = load('source-map.json')['mapping']
    req_ids = {r['requirement_id'] for r in requirements}
    source_ids = {s['source_id'] for s in sources}
    ev_ids = {e['id'] for e in evidence}
    assert len(requirements) == len(req_ids) == 48
    assert len(sources) == len(source_ids) == 49
    assert len(evidence) == len(ev_ids) == 140
    assert len(mappings) == len(req_ids) and {m['requirement_id'] for m in mappings} == req_ids
    assert all(m['source_ids'] and set(m['source_ids']) <= source_ids and m['remaining_gap'] for m in mappings)
    assert all(s['evidence_ids'] and set(s['evidence_ids']) <= ev_ids for s in sources)
    assert all(s['admitted_by_this_research'] is False for s in sources)
    assert all(e.get('error') or re.fullmatch('[a-f0-9]{64}', e['sha256']) for e in evidence)
    old = load('previous-research-map.json')['mapping']
    assert len(old) == len({x['previous_source_id'] for x in old}) == 19
    for x in old:
        assert any(s['source_id'] == x['current_source_id'] and
                   s['existing_source_id'] == x['previous_source_id'] for s in sources)
    # Read pinned git objects, so the checks also work after master evolves.
    for path, expected in snapshot['files'].items():
        data = subprocess.check_output(['git', 'show', snapshot['baseline'] + ':' + path], cwd=repo)
        assert hashlib.sha256(data).hexdigest() == expected, path
        classes = {n.name: n for n in ast.parse(data).body if isinstance(n, ast.ClassDef)}
        for name, recorded in snapshot['classes'].items():
            if recorded['path'] != path:
                continue
            actual = {n.target.id: ast.unparse(n.annotation) for n in classes[name].body
                      if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
            assert actual == {x['name']: x['annotation'] for x in recorded['fields']}, name
    for r in requirements:
        for binding in r['native_bindings']:
            name, _, field = binding.partition('.')
            assert name in snapshot['classes'], name
            assert not field or field in {f['name'] for f in snapshot['classes'][name]['fields']}, binding
    comparison = load('master-reconciliation.json')
    assert all(x['valid'] for x in comparison['requirement_bindings'])
    assert len(load('sample-inspection.json')['results']) == 7
    assert len(load('workbook-inspection.json')['results']) == 2
    plan = load('library-additions-plan.json')
    collections = plan['collections']
    collection_ids = {c['id'] for c in collections}
    assert len(collections) == len(collection_ids) == 6
    test_ids = set(re.findall(r'^\| ([A-Z]\d{2}) \|',
                             (here / '05-INTEGRATION-VALIDATION.md').read_text(), re.MULTILINE))
    assert set(plan['first_release'] + plan['follow_up']) == collection_ids
    for collection in collections:
        assert collection['source_ids'] and set(collection['source_ids']) <= source_ids
        assert collection['requirement_ids'] and set(collection['requirement_ids']) <= req_ids
        assert collection['acceptance_test_ids'] and set(collection['acceptance_test_ids']) <= test_ids
        assert collection['depends_on'] == ['NAM-0']
        assert collection['status'] == 'planned_not_implemented'
        assert collection['runtime_admission'] is False
    for file in list(here.glob('*.md')) + [repo / 'docs/research/gridspine-nam-commercial-overview.md']:
        for target in re.findall(r'\]\(([^)]+)\)', file.read_text()):
            if '://' in target or target.startswith('#'):
                continue
            assert (file.parent / target.split('#')[0]).exists(), (file, target)
    print(json.dumps({'status': 'pass', 'scope': 'offline authored research consistency only',
                      'requirements': len(req_ids), 'source_families': len(source_ids),
                      'retrieval_observations': len(ev_ids), 'prior_ids_reconciled': len(old),
                      'library_collections_planned': len(collections),
                      'native_files_hash_checked': len(snapshot['files']),
                      'runtime_admission': False, 'domain_approval': False}, indent=2))


if __name__ == '__main__':
    validate()
