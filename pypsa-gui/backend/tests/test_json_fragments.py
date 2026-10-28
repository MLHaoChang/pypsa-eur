"""Shared JSON files merge with their `.d/` fragments and fail closed (RETRO-COST-1)."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from smoke import json_fragments
from smoke.json_fragments import load_merged


@pytest.fixture
def base(tmp_path):
    path = tmp_path / 'kinds.json'
    path.write_text(json.dumps({'$comment': ['base'], 'kinds': {'a': {'surface': 'inline'}},
                                'rows': [{'path': '/a'}]}))
    (tmp_path / 'kinds.d').mkdir()
    return path


def _fragment(base, name, data):
    path = base.with_suffix('.d') / name
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    return path


def test_without_fragments_the_base_is_returned_unchanged(base):
    assert load_merged(base) == json.loads(base.read_text())


def test_fragments_merge_in_file_name_order(base):
    _fragment(base, 'claude-b.json', {'rows': [{'path': '/b'}], 'kinds': {'b': {}}})
    _fragment(base, 'claude-a.json', {'$comment': 'ignored', 'rows': [{'path': '/z'}], 'kinds': {'z': {}}})
    _fragment(base, 'README.md', 'not json and not read')
    merged = load_merged(base)
    assert [r['path'] for r in merged['rows']] == ['/a', '/z', '/b']
    assert list(merged['kinds']) == ['a', 'z', 'b']
    assert merged['$comment'] == ['base']


@pytest.mark.parametrize('name, data, expected', [
    ('x.json', '[1]', 'top level must be a JSON object'),
    ('x.json', '{', 'unreadable JSON'),
    ('x.json', {'nope': []}, "unknown key 'nope'"),
    ('x.json', {'kinds': []}, "'kinds' must be an object"),
    ('x.json', {'rows': {}}, "'rows' must be a list"),
    ('x.json', {'kinds': {'a': {'surface': 'banner'}}}, "kinds already defines ['a']"),
    ('x.json', {'rows': [{'path': '/a'}]}, 'duplicate rows item'),
    ('x.json', {'rows': [{'method': 'GET'}]}, "each rows item needs the keys ['path']"),
    ('x.json', {'rows': ['/b']}, "each rows item needs the keys ['path']"),
])
def test_a_bad_fragment_fails_and_names_its_file(base, name, data, expected):
    path = _fragment(base, name, data)
    with pytest.raises(ValueError) as err:
        load_merged(base)
    assert str(path) in str(err.value) and expected in str(err.value)


@pytest.mark.parametrize('name', ['typo.JSON', 'x.json5', 'notes.txt'])
def test_a_file_that_is_not_a_fragment_fails_instead_of_being_skipped(base, name):
    _fragment(base, name, '{}')
    with pytest.raises(ValueError, match=name.replace('.', r'\.')):
        load_merged(base)


def test_two_fragments_may_not_define_the_same_kind(base):
    _fragment(base, 'one.json', {'kinds': {'b': {}}})
    later = _fragment(base, 'two.json', {'kinds': {'b': {}}})
    with pytest.raises(ValueError, match=str(later)):
        load_merged(base)


def test_the_real_shared_files_merge():
    assert json_fragments.tool_error_kinds()
    from smoke.assistant_route_parity import load_declarations
    assert load_declarations()['needs_tool']


def test_the_loader_needs_no_site_packages():
    code = ('import sys; sys.path.insert(0, sys.argv[1]); import json_fragments; '
            'assert json_fragments.tool_error_kinds()')
    proc = subprocess.run([sys.executable, '-S', '-c', code, str(json_fragments.Path(json_fragments.__file__).parent)],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
