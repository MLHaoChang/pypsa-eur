"""Merge a shared, hand-written JSON file with its fragment directory.

``<dir>/<name>.json`` may have a sibling ``<dir>/<name>.d/``. A pull request adds
its new entries as one new fragment file there (name it after the branch, as in
``doc/release-notes.d``), so two pull requests never edit the same lines and
merge in either order. Edits to existing entries stay in the base file.

The merged view is the base followed by the fragments in file-name order. It
fails closed, naming the offending file: a non-object top level, a key the base
does not define, a list item or dict key already present, or an unreadable file.
Standard library only, so the stdlib-only CI checks can read it.
"""
from __future__ import annotations

import json
from pathlib import Path

COMMENT = '$comment'


def fragment_dir(base: Path) -> Path:
    return base.with_suffix('.d')


def fragment_files(base: Path) -> list[Path]:
    folder = fragment_dir(base)
    return sorted(folder.glob('*.json')) if folder.is_dir() else []


def _read_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'{path}: unreadable JSON ({exc})') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{path}: top level must be a JSON object')
    return data


def load_merged(base: Path) -> dict:
    """Return the base merged with every fragment; the base's value types define the keys."""
    merged = _read_object(base)
    seen = {k: {json.dumps(item, sort_keys=True) for item in v}
            for k, v in merged.items() if isinstance(v, list) and k != COMMENT}
    for path in fragment_files(base):
        for key, value in _read_object(path).items():
            if key == COMMENT:
                continue
            if not isinstance(merged.get(key), (dict, list)):
                raise ValueError(f'{path}: unknown key {key!r}; allowed: '
                                 f'{sorted(k for k in merged if k != COMMENT)}')
            if isinstance(merged[key], dict):
                if not isinstance(value, dict):
                    raise ValueError(f'{path}: {key!r} must be an object')
                clash = sorted(value.keys() & merged[key].keys())
                if clash:
                    raise ValueError(f'{path}: {key} already defines {clash}')
                merged[key] = {**merged[key], **value}
                continue
            if not isinstance(value, list):
                raise ValueError(f'{path}: {key!r} must be a list')
            for item in value:
                text = json.dumps(item, sort_keys=True)
                if text in seen[key]:
                    raise ValueError(f'{path}: duplicate {key} item {text}')
                seen[key].add(text)
            merged[key] = merged[key] + value
    return merged


TOOL_ERROR_KINDS = Path(__file__).resolve().parents[2] / 'tool-error-kinds.json'


def tool_error_kinds() -> dict:
    """The `tool_error` kind manifest: `tool-error-kinds.json` plus `tool-error-kinds.d/`."""
    return load_merged(TOOL_ERROR_KINDS)['kinds']
