"""Portable offline consistency check for the published PM specifications.

Validates document/plan references and recorded baseline pins. Does not run the
application, test the proposed implementation or turn failed restore probes green.
"""
from pathlib import Path
import ast
import hashlib
import json
import re
import subprocess


def main():
    here = Path(__file__).resolve().parent
    repo = here.parents[1]
    read = lambda name: json.loads((here / name).read_text())
    plan = read('implementation-plan.json')
    validation = read('VALIDATION.json')
    chatbot = read('chatbot-integration.json')
    audit = read('master-feature-audit.json')
    findings = read('findings.json')
    for path in here.glob('*.json'):
        json.loads(path.read_text())
    assert plan['baseline'] == validation['baseline'] == chatbot['baseline'] == audit['baseline']
    assert plan['runtime_implementation'] is False and not plan['new_test_cases_executed']
    assert not chatbot['runtime_implementation'] and not validation['fixes_implemented']
    cases = {t['id']: t for t in plan['test_cases']}
    assert len(cases) == len(plan['test_cases']) == 98
    packages = {p['id']: p for p in plan['packages']}
    assert set(packages) == {f'PM{i:02}' for i in range(15)}
    visited, visiting = set(), set()
    def visit(key):
        assert key not in visiting, 'dependency cycle: ' + key
        if key in visited:
            return
        visiting.add(key)
        for dependency in packages[key]['depends_on']:
            assert dependency in packages
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
    for key in packages:
        visit(key)
    covered = set()
    for key, package in packages.items():
        assert package['status'] == 'planned_not_implemented'
        assert set(package['acceptance_ids']) <= cases.keys()
        if key != 'PM14':
            covered.update(package['acceptance_ids'])
        for path in package['existing_seams']:
            subprocess.check_output(['git', 'cat-file', '-e', plan['baseline'] + ':' + path], cwd=repo)
        for path in package['proposed_files'] + package['new_test_targets']:
            assert path.startswith('pypsa-gui/') and '..' not in Path(path).parts
    assert covered == cases.keys(), 'acceptance case has no implementation owner'
    assert set(plan['audit_feature_delivery_map']) == {f'PM{i:02}' for i in range(1,25)}
    for owners in plan['audit_feature_delivery_map'].values():
        assert owners and set(owners) <= packages.keys()
    docs = ['02-SHARED-ASSUMPTIONS-SPEC.md', '03-CHATBOT-INTEGRATION-SPEC.md', '04-IMPLEMENTATION-PLAN.md']
    documented = set()
    for name in docs:
        documented.update(re.findall(r'^\| ((?:SR|PD|UI|VM|LF|BV|AI)\d{2}) \|', (here / name).read_text(), re.M))
    assert documented == cases.keys()
    assert set(chatbot['acceptance_ids']) == {f'AI{i:02}' for i in range(1,27)}
    assert len(chatbot['capabilities']) == 12 and len(plan['domain_journeys']) == 9
    catalogue = subprocess.check_output(['git', 'show', plan['baseline'] + ':pypsa-gui/backend/harness/catalogue.py'], cwd=repo, text=True)
    tools = {n.args[0].value for n in ast.walk(ast.parse(catalogue))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in {'_t','_empty'} and n.args
             and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}
    for capability in chatbot['capabilities']:
        assert set(capability['source_confirmed_existing_tool_refs']) <= tools
        assert capability['full_new_journey_implemented'] is False
    assert [x['id'] for x in findings['findings'][:3]] == ['F01', 'F02', 'F03']
    assert validation['new_safe_restore_expectations']['failures'] == 3
    for entry in validation['offline_source_pins']:
        data = subprocess.check_output(['git', 'show', plan['baseline'] + ':' + entry['path']], cwd=repo)
        assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['path']
    for path in list(here.glob('*.md')) + [repo / 'docs/research/gridspine-project-management-overview.md']:
        for link in re.findall(r'\]\(([^)]+)\)', path.read_text()):
            if '://' not in link and not link.startswith('#'):
                assert (path.parent / link.split('#')[0]).exists(), (path, link)
    for path in here.rglob('*.py'):
        ast.parse(path.read_text())
    for name, digest in validation.get('authored_artifacts', {}).items():
        assert hashlib.sha256((here / name).read_bytes()).hexdigest() == digest, name
    print(json.dumps(dict(status='pass', scope='specification consistency only',
                          delivery_packages=15, acceptance_cases_documented=98,
                          audited_features=24, domain_contexts=9,
                          chatbot_capabilities=12, restore_findings_still_open=3,
                          new_runtime_tests_executed=False), indent=2))


if __name__ == '__main__':
    main()
