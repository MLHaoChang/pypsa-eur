"""Coordination CLI contracts: comment markers and honest context display (task M)."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
LIVE = ROOT / 'scripts/coord'
TEMPLATE = ROOT / 'templates/agent-coordination/files/scripts/coord'
SHA = 'a' * 40


def _load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verdicts(comments, logins=None, raw=None, directory=LIVE):
    env = os.environ.copy()
    env.pop('PREMERGE_REVIEWER_LOGINS', None)
    if logins is not None:
        env['PREMERGE_REVIEWER_LOGINS'] = logins
    return subprocess.run([sys.executable, str(directory / 'verdicts.py')],
                          input=raw if raw is not None else '\n'.join(json.dumps(c) for c in comments),
                          text=True, capture_output=True, env=env, check=True)


@pytest.mark.parametrize('directory', [LIVE, TEMPLATE], ids=['live', 'template'])
def test_first_nonempty_line_is_the_only_verdict(directory):
    comments = [
        {'login': 'gk', 'body': f'\n\nGK OK @{SHA}: checked\nDetails'},
        {'login': 'ux', 'body': f'UX NOT OK @{SHA}: fix it'},
    ]
    got = _verdicts(comments, directory=directory)
    assert got.stdout == f'GK\tOK\t{SHA}\nUX\tNOT OK\t{SHA}\n'
    assert got.stderr == ''


@pytest.mark.parametrize('body', [
    f'Report\nGK OK @{SHA}: copied',
    f'> GK OK @{SHA}: quote',
    f'Report\nNotes\nUX NOT OK @{SHA}: copied\nEnd',
])
def test_reported_or_quoted_verdict_is_diagnosed_not_counted(body):
    got = _verdicts([{'login': 'reporter', 'body': body}])
    assert got.stdout == ''
    assert 'reporter' in got.stderr
    line = next(line for line in body.splitlines() if 'OK @' in line)
    assert line[:60] in got.stderr


def test_optional_login_filter_is_off_by_default():
    comment = {'login': 'shared-user', 'body': f'GK OK @{SHA}: checked'}
    assert _verdicts([comment]).stdout == f'GK\tOK\t{SHA}\n'
    got = _verdicts([comment], logins='gk-bot ux-bot')
    assert got.stdout == ''
    assert 'shared-user' in got.stderr
    comment['login'] = 'gk-bot'
    assert _verdicts([comment], logins='gk-bot ux-bot').stdout == f'GK\tOK\t{SHA}\n'


def test_bad_json_does_not_discard_later_comments():
    raw = 'not json\n' + json.dumps({'login': 'gk', 'body': f'GK OK @{SHA}: checked'})
    got = _verdicts([], raw=raw)
    assert got.stdout == f'GK\tOK\t{SHA}\n'
    assert got.stderr


@pytest.mark.parametrize('directory', [LIVE, TEMPLATE], ids=['live', 'template'])
def test_sessions_distinguishes_unknown_from_reported_context(directory):
    sessions = _load(directory / 'sessions.py')
    assert sessions.fmt_context(None) == '?'
    for session in [{}, {'id': 'some-session'}, {'context_usage': {'used_tokens': 0}}]:
        assert sessions.context_tokens(session) is None
    assert sessions.context_tokens({'context_usage': {'used_tokens': 120000}}) == 120000
    assert sessions.fmt_context(120000) == '120k'
    assert sessions.fmt_context(250000) == '250k ROTATE'
    row = sessions.row({'id': 'abc', 'context_usage': {'used_tokens': 0}})
    assert '?' in row and 'ROTATE' not in row
    assert 'get_session' in sessions.__doc__


@pytest.mark.parametrize('name', ['verdicts.py', 'sessions.py'])
def test_template_has_the_same_top_level_functions(name):
    def names(path):
        return {n.name for n in ast.parse(path.read_text()).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert names(LIVE / name) == names(TEMPLATE / name)


@pytest.mark.parametrize('directory', [LIVE, TEMPLATE], ids=['live', 'template'])
def test_premerge_still_fails_closed_when_gh_fails(directory, tmp_path):
    # Run the REAL reviewer block and pipeline with a failing gh, without a
    # network request, git mutation or substitution of its error handling.
    text = (directory / 'premerge.sh').read_text()
    start = text.index('if verdicts=$(')
    end = text.index('echo "== checks"', start)
    gh = tmp_path / 'gh'
    gh.write_text('#!/bin/sh\nexit 1\n')
    gh.chmod(0o755)
    script = tmp_path / 'premerge.sh'
    script.write_text('set -euo pipefail\nfail=0\nreasons=()\npr=1\ngh_repo=owner/repo\n'
                      + text[start:end] + '\necho "failure=$fail"\n')
    if (directory / 'verdicts.py').exists():
        (tmp_path / 'verdicts.py').write_text((directory / 'verdicts.py').read_text())
    result = subprocess.run(['bash', str(script)], text=True, capture_output=True,
                            env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH']}, check=True)
    assert 'verdicts: not read' in result.stdout
    assert 'failure=1' in result.stdout
