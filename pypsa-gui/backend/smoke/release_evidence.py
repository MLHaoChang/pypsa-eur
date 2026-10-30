"""Check/write the LC-08 release evidence per capability-matrix family.

Reads the LC-06 family manifest (`smoke/family_manifest.py`) at run time: the
families, their LC-D suites, variants and tool denominators all come from
``build_manifest()``, so a manifest change shows here on the next run. Per
family it reports:

- ``fixture_testing``, the one status this package computes: ``evidenced``
  when no variant is pending and at least one is covered, ``partial`` when any
  is pending (listed with the owner from ``OWNERS``), ``not_evidenced`` when
  nothing is covered. One journey never closes a family;
- the covered variants' oracles and test node ids, and the pending ones with
  their reason and owner;
- the owner journeys and cross-family tests mapped to it, as CI job, node ids
  and command. The JSON never carries a run result; the runs made for a
  release are recorded in `.scratch/live-copilot/issues/08-release-evidence.md`;
- the five other statuses (implementation, vendor readiness, domain review,
  calibration, production enablement), fixed at ``not_established`` with why.

``source`` pins the evidence: the git HEAD, whether the tree was dirty, and the
sha256 of every file the evidence maps. ``full_coverage`` stays
``not_established``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import family_manifest as fm  # noqa: E402

ROOT = fm.ROOT
DEFAULT_OUTPUT = ROOT / '.scratch/live-copilot/release-evidence.json'
B = 'pypsa-gui/backend/tests/'
FRONTEND = 'pypsa-gui/frontend/scripts/'
BROWSER_JOB = 'GUI owner journeys (browser) in .github/workflows/test.yaml'
BROWSER_COMMAND = ('cd pypsa-gui/frontend && npm run build && PYPSA_COMPOSER_PYTHON=<python> '
                   'CHROMIUM_BIN=/opt/pw-browsers/chromium node scripts/{script}')
BACKEND_COMMAND = 'cd pypsa-gui/backend && python -m pytest {nodes}'

LC32 = 'LC-32 (owner decision LC32-OPEN-1)'
UNASSIGNED = 'unassigned: no open issue builds it; the coordinator ranks it'
LC33 = 'LC-33 (.scratch/live-copilot/issues/33-unassigned-variants-d06-d13.md)'
# Owner of each pending variant, by variant id or, failing that, by suite.
OWNERS: dict[str, str] = {
    **{f'LC-D{i:02d}': LC32 for i in (1, 2, 3, 20, 21, 22, 23, 24)},
    'LC-D06.sclopf': LC33,
    'LC-D06.ac_stage': LC33,
    'LC-D11.fmea_criticality': 'LC-30 (.scratch/live-copilot/issues/30-fmea-criticality-per-horizon.md)',
    **{f'LC-D13.{v}': LC33 for v in ('margin_loop', 'reserve_margin_coupling', 'elcc_frontier_campaign',
                                     'eh_redundancy', 'eh_levers', 'eh_dtc_planning', 'multi_energy')},
}


def _browser(name: str, script: str, what: str) -> dict:
    return {'kind': 'browser', 'name': name, 'what': what, 'script': FRONTEND + script, 'ci': BROWSER_JOB,
            'command': BROWSER_COMMAND.format(script=script),
            'wire': 'loopback (scripted model turns); real native backend and Chromium'}


def _backend(name: str, what: str, *nodes: str) -> dict:
    return {'kind': 'backend', 'name': name, 'what': what, 'nodes': [B + n for n in nodes],
            'ci': 'GUI backend in .github/workflows/test.yaml',
            'command': BACKEND_COMMAND.format(nodes=' '.join('tests/' + n for n in nodes))}


# Owner journeys (LC-13 Tier A) by the family whose native seam they drive.
JOURNEYS: dict[str, list[dict]] = {
    'projects': [_backend('OJ-09', 'named project resolved, activated on approval, then read',
                          'test_oj09_named_project_journey.py::test_named_project_is_resolved_activated_on_approval_then_read',
                          'test_oj09_named_project_loadflow.py::test_the_named_projects_load_flow_is_read_and_shown')],
    'network-topology': [
        _backend('OJ-05', 'selected line edit: card, then read-back',
                 'test_oj05_selected_line_edit_journey.py::test_selected_line_edit_shows_the_card_then_reads_back'),
        _browser('OJ-05', 'smoke-copilot-oj05.mjs', 'selected line s_nom edit in the browser'),
        _browser('OJ-21', 'smoke-copilot-oj21.mjs', 'one card, one approval for a multi-asset edit'),
    ],
    'economics-investment': [_backend('OJ-06', 'ambiguous storage cost asks one choice',
                                      'test_oj06_ambiguous_storage_cost_journey.py::test_ambiguous_storage_cost_asks_one_choice')],
    'solver-simulation': [_backend('OJ-07', 'edit, edit, run, then walk the new run',
                                   'test_oj07_edit_relaunch_walk_journey.py::test_edit_edit_run_then_the_conversation_walks_the_new_run')],
    'all-results': [
        _backend('OJ-01', 'latest load flow read, then shown',
                 'test_oj01_latest_loadflow_journey.py::test_latest_load_flow_is_read_then_shown'),
        _browser('OJ-01', 'smoke-copilot-oj01.mjs', 'two chat solves, the latest load flow shown'),
        _browser('LC-03', 'smoke-copilot-lc03-historical.mjs', 'past runs re-priced and compared'),
    ],
    'harness-workflows': [_backend('OJ-46', 'a feature chat must refuse names the reason and the page',
                                   'test_oj46_feature_chat_must_refuse_journey.py::'
                                   'test_a_feature_chat_must_refuse_names_the_reason_and_the_page')],
}

# Prolonged use, restart and provider cases: shared by every family.
CROSS_FAMILY: list[dict] = [
    _backend('prolonged', 'long conversation keeps refs and budget',
             'test_durable_conversation_prolonged_b_c1.py::test_prolonged_journey_refs_and_budget_settle_survive'),
    _backend('restart', 'conversation recovery after restart',
             'test_conversation_recovery.py::test_parent_cannot_control_foreign_episode_without_checked_state'),
    _backend('shutdown', 'quit keeps the queue and names the project',
             'test_shutdown.py::test_no_solve_running_means_nothing_is_in_flight',
             'test_shutdown_keeps_the_queue.py::test_the_quit_confirmation_names_the_project_not_the_job_id'),
    _backend('provider', 'same native effect on both wires',
             'test_assistant_provider_parity.py::test_task_preview_solve_chart_export_delivery_native_streams',
             'test_harness_provider_parity.py::test_openai_wire_usage_has_anthropic_semantics',
             'test_workflow_provider_parity.py::test_shared_tools_execute_and_continue_on_each_transport',
             'test_assistant_navigation_provider_parity.py::'
             'test_real_adapters_navigate_all_application_targets_and_continue'),
]

ISSUE = '.scratch/live-copilot/issues/08-release-evidence.md'
LIMITATIONS: list[dict] = [
    {'what': 'Tier B live owner journeys (a real model plays each journey) not run',
     'source': f'{ISSUE}: paid calls stay outside this package; .github/workflows/owner-journeys-live.yaml '
               'is manual, never a merge gate'},
    {'what': 'Real speech and device probes not run (LC-T18)',
     'source': 'owner decision VOICE-ORDER-2: LC-07 continuous speech and live-voice 09 are held; '
               '.scratch/live-copilot/issues/07-continuous-speech.md'},
    {'what': 'Browser journeys play scripted model turns over the loopback wire',
     'source': f'{FRONTEND}smoke-copilot-oj01.mjs: the backend and Chromium are real, the model is not'},
    {'what': 'Families without an owner journey rest on their covered variants\' seam tests only',
     'source': 'JOURNEYS in pypsa-gui/backend/smoke/release_evidence.py'},
]

FIXED_STATUSES: dict[str, str] = {
    'implementation': 'LC-08 enables no capability; implementation is each package\'s own status',
    'vendor_readiness': 'needs separately authorized capped vendor probes (LC-T18), outside this package',
    'domain_review': 'independent domain approval is a separate status (domain-coverage.md)',
    'calibration': 'real-use calibration stays pending (domain-coverage.md)',
    'production_enablement': 'deployment stays outside this package (issue 08)',
}


def _owner(variant_id: str, owners: dict) -> str | None:
    return owners.get(variant_id) or owners.get(variant_id.split('.')[0])


def _fixture_testing(counts: dict) -> str:
    if counts['pending']:
        return 'partial'
    return 'evidenced' if counts['covered'] else 'not_evidenced'


def build_evidence(built: dict, *, owners: dict = OWNERS, journeys: dict = JOURNEYS,
                   source: dict | None = None) -> dict:
    families = {}
    covered_tools = set(built['covered_tools'])
    for name, title in built['families'].items():
        suites = set(built['suites_by_family'][name])
        variants = [v for v in built['variants'] if v['suite'] in suites]
        counts = {s: sum(v['status'] == s for v in variants) for s in ('covered', 'pending', 'unavailable')}
        tools = built['tools_by_family'][name]
        families[name] = {
            'title': title,
            'suites': list(built['suites_by_family'][name]),
            'variants': {'total': len(variants), **counts},
            'tools': {'total': len(tools), 'covered': len(covered_tools & set(tools))},
            'statuses': {'fixture_testing': _fixture_testing(counts), **{k: 'not_established' for k in FIXED_STATUSES}},
            'status_reasons': {'fixture_testing': f'named LC-D variants only; {len(covered_tools & set(tools))} '
                                                  f'of {len(tools)} family tools are listed by a covered variant',
                               **FIXED_STATUSES},
            'covered': [{'id': v['id'], 'oracle': v['oracle'], 'tests': v['tests']}
                        for v in variants if v['status'] == 'covered'],
            'pending': [{'id': v['id'], 'reason': v['reason'], 'owner': _owner(v['id'], owners)}
                        for v in variants if v['status'] == 'pending'],
            'unavailable': [{'id': v['id'], 'reason': v['reason'], 'pointer': v.get('pointer')}
                            for v in variants if v['status'] == 'unavailable'],
            'journeys': journeys.get(name, []),
        }
    return {'format_version': 1, 'source': source if source is not None else pin(built),
            'manifest': 'pypsa-gui/backend/smoke/family_manifest.py', 'families': families,
            'cross_family': CROSS_FAMILY, 'limitations': LIMITATIONS, 'full_coverage': 'not_established'}


def _mapped_files(built: dict, journeys: dict = JOURNEYS) -> list[str]:
    files = {'pypsa-gui/backend/smoke/family_manifest.py', 'pypsa-gui/backend/smoke/release_evidence.py',
             '.github/workflows/test.yaml'}
    files |= {t.split('::')[0] for v in built['variants'] for t in v.get('tests', [])}
    for entry in [j for js in journeys.values() for j in js] + CROSS_FAMILY:
        files |= {n.split('::')[0] for n in entry.get('nodes', [])}
        files |= {entry['script']} if entry.get('script') else set()
    return sorted(files)


def pin(built: dict) -> dict:
    git = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()  # noqa: E731
    return {'git_head': git('rev-parse', 'HEAD'), 'dirty': bool(git('status', '--porcelain')),
            'files': {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest()
                      for f in _mapped_files(built) if (ROOT / f).is_file()}}


def evidence_problems(built: dict, *, owners: dict = OWNERS, journeys: dict = JOURNEYS,
                      collect: bool = True) -> list[str]:
    problems = [f'{v["id"]}: pending variant has no owner in OWNERS'
                for v in built['variants'] if v['status'] == 'pending' and not _owner(v['id'], owners)]
    problems += [f'JOURNEYS: {f} is not a manifest family' for f in journeys if f not in built['families']]
    entries = [j for js in journeys.values() for j in js] + (CROSS_FAMILY if journeys is JOURNEYS else [])
    nodes: dict[str, str] = {}
    for entry in entries:
        if entry.get('script') and not (ROOT / entry['script']).is_file():
            problems.append(f'{entry["name"]}: {entry["script"]}: no such file')
        for node in entry.get('nodes', []):
            if not (ROOT / node.split('::')[0]).is_file():
                problems.append(f'{entry["name"]}: {node.split("::")[0]}: no such file')
            else:
                nodes[node] = entry['name']
    if collect and nodes:
        problems += [f'{nodes[n]}: {n} does not collect' for n in fm.uncollected_nodes(sorted(nodes))]
    return problems


def report(evidence: dict) -> str:
    lines = [f'ok source={evidence["source"].get("git_head", "?")[:12]}'
             f'{" dirty" if evidence["source"].get("dirty") else ""} families={len(evidence["families"])} '
             f'full_coverage={evidence["full_coverage"]}']
    for name, f in evidence['families'].items():
        v = f['variants']
        lines.append(f'{name}: {f["statuses"]["fixture_testing"]} variants {v["covered"]}/{v["total"]} covered '
                     f'{v["pending"]} pending {v["unavailable"]} unavailable; tools {f["tools"]["covered"]}/'
                     f'{f["tools"]["total"]}; journeys {len(f["journeys"])}')
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Build in memory; fail on any guard.')
    mode.add_argument('--write', action='store_true', help='Also write the evidence (not committed) for reading.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    built = fm.build_manifest()
    problems = fm.manifest_problems() + evidence_problems(built)
    if problems:
        print('\n'.join(problems))
        return 1
    evidence = build_evidence(built)
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print(report(evidence))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
