"""The sweep's prune command deletes only stale rows and tightens the ceiling (RETRO-COST-1)."""
from __future__ import annotations

import json
import shutil

from smoke import assistant_route_parity as parity
from smoke import prune_stale_declarations as prune
from tests import test_route_chat_disposition as disposition


def test_prune_removes_stale_rows_from_the_base_and_fragments(tmp_path, monkeypatch):
    base = tmp_path / 'decl.json'
    base.write_text(prune._dump({
        'needs_tool': [{'method': 'GET', 'path': '/served', 'family': 'projects'},
                       {'method': 'GET', 'path': '/kept', 'family': 'projects'}],
        'limitations': [],
    }))
    (tmp_path / 'decl.d').mkdir()
    emptied = tmp_path / 'decl.d/claude-a.json'
    emptied.write_text(json.dumps({'limitations': [{'method': 'POST', 'path': '/served2', 'reason': 'admin'}]}))
    kept = tmp_path / 'decl.d/claude-b.json'
    kept.write_text(json.dumps({'needs_tool': [{'method': 'GET', 'path': '/other', 'family': 'projects'}]}))
    monkeypatch.setattr(parity, 'DECLARATIONS', base)

    stale = {('GET', '/served'), ('POST', '/served2')}
    assert len(prune.prune_declarations(stale, apply=False)) == 2
    assert '/served' in base.read_text()  # a dry run writes nothing

    assert len(prune.prune_declarations(stale, apply=True)) == 2
    assert [d['path'] for d in json.loads(base.read_text())['needs_tool']] == ['/kept']
    assert not emptied.exists() and kept.exists()


def test_prune_removes_served_known_gaps_and_lowers_the_ceiling(tmp_path, monkeypatch):
    copy = tmp_path / 'disposition.py'
    shutil.copyfile(prune.DISPOSITION, copy)
    route = next(iter(disposition.KNOWN_GAPS))
    monkeypatch.setattr(prune, 'DISPOSITION', copy)
    monkeypatch.setattr(disposition, 'stale_known_gaps', lambda: [route])

    done = prune.prune_known_gaps(apply=True)
    source = copy.read_text()
    left = len(disposition.KNOWN_GAPS) - 1
    assert f'KNOWN_GAPS {route[0]} {route[1]}' in done
    assert f"    ({route[0]!r}, {route[1]!r}):" not in source
    assert f'_KNOWN_GAPS_CEILING = {left}\n' in source
