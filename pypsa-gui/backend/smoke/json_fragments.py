"""Merge a shared, hand-written JSON file with its fragment directory.

``<dir>/<name>.json`` may have a sibling ``<dir>/<name>.d/``. A pull request adds
its new entries as one new fragment file there (name it after the branch, as in
``doc/release-notes.d``), so two pull requests never edit the same lines and
merge in either order. Edits to existing entries stay in the base file.

The merged view is the base followed by the fragments in file-name order. It
fails closed, naming the offending file: a non-object top level, a key the base
does not define, a list item or dict key already present, or an unreadable file.
Standard library only, so the stdlib-only CI checks can read it.

A registry that is a hand-written Python table (a route's lock decision, a
chat disposition, a not-via-chat entry, a family-manifest variant) takes its
fragments through ``extend`` / ``extend_items``: the Python rows first, then the
JSON base, then the fragments, and a key defined twice fails (RETRO-FRAG-1).
"""
from __future__ import annotations

import json
from pathlib import Path

COMMENT = '$comment'


def fragment_dir(base: Path) -> Path:
    return base.with_suffix('.d')


def fragment_files(base: Path) -> list[Path]:
    folder = fragment_dir(base)
    if not folder.is_dir():
        return []
    stray = sorted(p.name for p in folder.iterdir() if p.suffix != '.json' and p.name != 'README.md')
    if stray:
        raise ValueError(f'{folder}: only *.json fragments and README.md belong here, not {stray}')
    return sorted(folder.glob('*.json'))


def _read_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'{path}: unreadable JSON ({exc})') from exc
    if not isinstance(data, dict):
        raise ValueError(f'{path}: top level must be a JSON object')
    return data


def _shared_keys(items: list) -> set:
    """The keys every object item in the base list has; empty when the items are not objects."""
    if not items or not all(isinstance(item, dict) for item in items):
        return set()
    return set.intersection(*(set(item) for item in items))


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
            required = _shared_keys(merged[key])
            for item in value:
                if required and not (isinstance(item, dict) and required <= item.keys()):
                    raise ValueError(f'{path}: each {key} item needs the keys {sorted(required)}; got {item!r}')
                text = json.dumps(item, sort_keys=True)
                if text in seen[key]:
                    raise ValueError(f'{path}: duplicate {key} item {text}')
                seen[key].add(text)
            merged[key] = merged[key] + value
    return merged


def route_key(text: str) -> tuple[str, str]:
    """``"POST /api/x"`` -> ``("POST", "/api/x")``: how a fragment spells a route key."""
    method, _, path = text.partition(' ')
    if not (method.isalpha() and method.isupper() and path.startswith('/') and ' ' not in path):
        raise ValueError(f'a route key is "METHOD /path", not {text!r}')  # repr-ok: names a JSON key in a CI/import error
    return method, path


def _section(base: Path, section: str, kind: type) -> None:
    load_merged(base)  # every load_merged rule: unknown key, bad file, duplicate across fragments
    if not isinstance(_read_object(base).get(section), kind):
        raise ValueError(f'{base}: needs a {kind.__name__} {section!r}')  # repr-ok: names a JSON key in a CI/import error


def extend(table: dict, base: Path, section: str, convert=None) -> dict:
    """A hand-written Python registry ``table`` plus the rows ``section`` holds in
    ``base`` and then its fragments in file-name order.

    ``convert(raw_key, raw_value) -> (key, value)`` turns a JSON row into the
    table's own form; whatever it raises is reported against the file. A key the
    table or an earlier file already defines fails, naming the file: a fragment
    adds rows and never changes one, so a reviewed row cannot be overridden.
    """
    _section(base, section, dict)
    out = dict(table)
    for path in [base, *fragment_files(base)]:
        for raw, row in _read_object(path).get(section, {}).items():
            try:
                key, value = convert(raw, row) if convert else (raw, row)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'{path}: {section} {raw!r}: {exc}') from exc  # repr-ok: names a JSON key in a CI/import error
            if key in out:
                raise ValueError(f'{path}: {section} {raw!r} is already defined; a fragment adds '  # repr-ok: names a JSON key in a CI/import error
                                 'rows, so change that row where it is')
            out[key] = value
    return out


def extend_items(items, base: Path, section: str, key: str = 'id', convert=None) -> list:
    """``extend`` for a list of objects told apart by ``key``: ``items`` first, then
    ``section`` of ``base``, then of each fragment in file-name order."""
    _section(base, section, list)
    out, seen = list(items), {item[key] for item in items}
    for path in [base, *fragment_files(base)]:
        for raw in _read_object(path).get(section, []):
            try:
                item = convert(raw) if convert else raw
                ident = item[key]
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f'{path}: {section} item {raw!r}: {exc}') from exc  # repr-ok: names a JSON key in a CI/import error
            if ident in seen:
                raise ValueError(f'{path}: {section} {ident!r} is already defined; a fragment adds '  # repr-ok: names a JSON key in a CI/import error
                                 'rows, so change that row where it is')
            seen.add(ident)
            out.append(item)
    return out


def origin(base: Path, section: str, raw_key: str) -> Path | None:
    """The file (``base`` or a fragment) whose ``section`` defines ``raw_key``."""
    for path in [base, *fragment_files(base)]:
        if raw_key in _read_object(path).get(section, {}):
            return path
    return None



def named_with_file(base: Path, sections: tuple[str, ...], keys) -> list[str]:
    """Each key as a failure message shows it, with the JSON file that adds it, if one does.

    A key is a name or a ``(METHOD, path)`` route; rows from the Python table show bare.
    """
    out = []
    for key in keys:
        raw = f'{key[0]} {key[1]}' if isinstance(key, tuple) else str(key)
        path = next((o for o in (origin(base, s, raw) for s in sections) if o), None)
        out.append(f'{raw} ({path.name})' if path else raw)
    return out

TOOL_ERROR_KINDS = Path(__file__).resolve().parents[2] / 'tool-error-kinds.json'


def tool_error_kinds() -> dict:
    """The `tool_error` kind manifest: `tool-error-kinds.json` plus `tool-error-kinds.d/`."""
    return load_merged(TOOL_ERROR_KINDS)['kinds']
