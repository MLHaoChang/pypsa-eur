"""The coordinator sweep's one command: delete declarations a tool now serves.

A PR that serves a declared route leaves the declaration in place, so two such
PRs never edit the same lines (RETRO-COST-1). The checks list those as stale and
pass. This command removes them in one small sweep PR:

* ``needs_tool`` / ``limitations`` rows in the route-parity declarations (the
  base file and its ``.d/`` fragments; a fragment left empty is deleted);
* ``KNOWN_GAPS`` rows in ``tests/test_route_chat_disposition.py`` that a tool
  serves, after which ``_KNOWN_GAPS_CEILING`` is lowered to the rows left.

Run from ``pypsa-gui/backend`` with the backend environment:
``python -m smoke.prune_stale_declarations`` (dry run) or ``... --apply``.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from smoke import assistant_route_parity as parity
from smoke.json_fragments import fragment_files

DISPOSITION = Path(__file__).resolve().parents[1] / 'tests/test_route_chat_disposition.py'
_PRUNED_KEYS = ('needs_tool', 'limitations')


def _dump(data: dict) -> str:
    # The base file's own format, so a prune touches only the removed rows.
    return json.dumps(data, indent=1, ensure_ascii=False) + '\n'


def prune_declarations(stale: set[tuple[str, str]], apply: bool) -> list[str]:
    done = []
    for path in [parity.DECLARATIONS, *fragment_files(parity.DECLARATIONS)]:
        data = json.loads(path.read_text(encoding='utf-8'))
        removed = []
        for key in _PRUNED_KEYS:
            keep = [d for d in data.get(key, []) if (d['method'], d['path']) not in stale]
            removed += [f'{key} {d["method"]} {d["path"]}' for d in data.get(key, []) if d not in keep]
            if key in data:
                data[key] = keep
        if not removed:
            continue
        done += [f'{path.name}: {r}' for r in removed]
        if not apply:
            continue
        empty = path != parity.DECLARATIONS and not any(v for k, v in data.items() if k != '$comment')
        if empty:
            path.unlink()
        else:
            path.write_text(_dump(data), encoding='utf-8')
    return done


def prune_known_gaps(apply: bool) -> list[str]:
    from tests import test_route_chat_disposition as disposition  # noqa: PLC0415

    stale = disposition.stale_known_gaps()
    left = len(disposition.KNOWN_GAPS) - len(stale)
    source = DISPOSITION.read_text(encoding='utf-8')
    for method, path in stale:
        row = re.compile(rf"^    \({re.escape(repr(method))}, {re.escape(repr(path))}\): .*\n", re.M)
        source, n = row.subn('', source, count=1)
        if n != 1:
            raise ValueError(f'KNOWN_GAPS row {method} {path} is not one line; delete it by hand')
    ceiling = re.compile(r'^_KNOWN_GAPS_CEILING = (\d+)$', re.M)
    match = ceiling.search(source)
    if match is None:
        raise ValueError('_KNOWN_GAPS_CEILING not found')
    done = [f'KNOWN_GAPS {m} {p}' for m, p in stale]
    if int(match.group(1)) != left:
        done.append(f'_KNOWN_GAPS_CEILING {match.group(1)} -> {left}')
        source = ceiling.sub(f'_KNOWN_GAPS_CEILING = {left}', source, count=1)
    if apply and done:
        DISPOSITION.write_text(source, encoding='utf-8')
    return done


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--apply', action='store_true', help='Write the changes (default: dry run).')
    args = parser.parse_args(argv)
    matrix, problems = parity.build()
    if problems:
        print('route parity fails; fix it before pruning:', *problems, sep='\n  ')
        return 1
    stale = {(s['method'], s['path']) for s in matrix['stale_declarations']}
    done = prune_declarations(stale, args.apply) + prune_known_gaps(args.apply)
    for line in done:
        print(('pruned ' if args.apply else 'would prune ') + line)
    if not done:
        print('nothing stale')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
