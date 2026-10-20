"""Bounded class metadata, never model values or permission to change them."""
from __future__ import annotations

import hashlib
import json
import math

from fastapi import HTTPException

from services.serialization import clean_scalar


MAX_PAGE_CHARS = 3800
MAX_OFFSET = 1_000_000


def _default(value):
    item = getattr(value, 'item', None)
    value = item() if callable(item) else value
    non_finite = isinstance(value, float) and not math.isfinite(value)
    return {'value': clean_scalar(value), 'text': str(value) if value is not None else None,
            'non_finite': non_finite}


def _text(value):
    value = clean_scalar(value)
    return str(value) if value is not None else None


def schema_page(component_class, model, native_defaults, asset_fields, *, query='', offset=0, limit=8):
    """
    Project three existing schemas without reading component rows.

    JSON Schema describes declared field constraints, not every native Python
    validator. The digest identifies this metadata only, never an admission.
    """
    if (not isinstance(query, str) or len(query) > 80
            or type(offset) is not int or not 0 <= offset <= MAX_OFFSET
            or type(limit) is not int or not 1 <= limit <= 50):
        raise HTTPException(400, 'Use query <=80 characters, offset 0–1000000 and limit 1–50.')
    rows = field_rows(model, native_defaults, asset_fields)
    return _page(component_class, rows, query, offset, limit)


def field_rows(model, native_defaults, asset_fields):
    """Every field of one class, unpaged: what schema_page pages and the edit card checks."""
    request = model.model_json_schema()
    # Current component models have no referenced definitions. Refuse a future
    # unsupported shape instead of returning an unresolved validation contract.
    if request.get('$defs'):
        raise HTTPException(409, 'Component schema uses unsupported referenced definitions.')
    properties = request['properties']
    assets = {field.key: field for field in asset_fields}
    rows = []
    for name in sorted(set(properties) | set(native_defaults.index) | set(assets)):
        native = native_defaults.loc[name] if name in native_defaults.index else None
        prop = properties.get(name)
        asset = assets.get(name)
        status = _text(native['status']) if native is not None else None
        derived = bool(asset and asset.derived)
        output = bool(status and status.startswith('Output'))
        native_input = bool(status and status.startswith('Input') and native['static'])
        editable = not derived and not output and (
            prop is not None or (native_input and request.get('additionalProperties') is True))
        default = prop.get('default') if prop is not None and 'default' in prop else (
            native['default'] if native is not None else None)
        rows.append({
            'name': name,
            'label': asset.label if asset else (prop or {}).get('title', name),
            'description': asset.help if asset else (_text(native['description']) if native is not None else None),
            'unit_storage': asset.unit_storage if asset else (_text(native['unit']) if native is not None else None),
            'native_unit': _text(native['unit']) if native is not None else None,
            'unit_display': asset.unit_display if asset else None,
            'display_scale': asset.scale if asset else None,
            'native_type': str(native['type']) if native is not None else None,
            'native_status': status,
            'varying': bool(native['varying']) if native is not None else None,
            'validation_schema': {k: v for k, v in prop.items() if k not in {'default', 'title'}} if prop is not None else None,
            'options': list(asset.options) if asset else [],
            'required_on_create': name in request.get('required', []),
            'default': _default(default),
            'derived': derived, 'editable': editable,
            'editability_reason': ('derived' if derived else 'native_output' if output else
                                   'declared_input' if editable and prop is not None else
                                   'native_input' if editable and native_input else 'read_only'),
            'sources': [source for source, present in [('request', prop is not None),
                        ('pypsa', native is not None), ('asset', asset is not None)] if present],
        })
    return rows


def _page(component_class, rows, query, offset, limit):
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, allow_nan=False).encode()).hexdigest()
    term = query.strip().casefold()
    filtered = [row for row in rows if term in row['name'].casefold() or term in row['label'].casefold()]
    out = {'version': 1, 'component_class': component_class, 'schema_sha256': digest,
           'items': [], 'total_count': len(filtered), 'offset': offset, 'returned': 0,
           'has_more': offset < len(filtered),
           'note': 'Class metadata only. Native handlers validate writes; editability is not permission. '
                   'Read actual values on demand with get_component/list_components. Page with offset += returned.'}
    for row in filtered[offset:offset + limit]:
        out['items'].append(row)
        out['returned'] = len(out['items'])
        out['has_more'] = offset + out['returned'] < len(filtered)
        if len(json.dumps(out, allow_nan=False)) > MAX_PAGE_CHARS:
            out['items'].pop()
            if not out['items']:
                raise HTTPException(413, {'error_kind': 'schema_field_too_large', 'field': row['name']})
            out['returned'] = len(out['items'])
            out['has_more'] = True
            break
    return out
