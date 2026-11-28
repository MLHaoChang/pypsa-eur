"""
Plain names in write-tool results (expert-catalogue issue 05, spec §3.6 / §7.1).

The generic write tools keep taking model names (`attrs`, `updates`: the
model's words), but what they answer names each column the way the user reads
it: a result gains `plain_names` {model_name: plain_name} for the columns the
call named, and a refusal about one of them opens with its plain name
("Capacity (p_nom): …"), so the assistant relays the user's word (RAW-NAMES-1).

Columns the catalogue does not hold are left out; a refusal that names none of
the call's columns is passed on unchanged.
"""

from __future__ import annotations

import functools
import inspect
import re
from typing import Any, Callable

from fastapi import HTTPException

from services.asset_schema import lookup


def plain_names_for(component_class: str, keys) -> dict[str, str]:
    """{model_name: plain_name} for the given columns of one class (the catalogue's only)."""
    out: dict[str, str] = {}
    for k in keys or ():
        if not isinstance(k, str):
            continue
        p = lookup.plain_name(component_class, k)
        if p:
            out[k] = p
    return out


def _named_column(detail: Any, names: dict[str, str]) -> str | None:
    """The first of the call's columns the refusal names: its `field`, else a whole word of its text."""
    if isinstance(detail, dict):
        field = detail.get("field")
        if isinstance(field, str) and field in names:
            return field
        text = detail.get("message")
    else:
        text = detail
    if not isinstance(text, str):
        return None
    for k in names:
        if re.search(rf"(?<![\w]){re.escape(k)}(?![\w])", text):
            return k
    return None


def _opened(text: str, plain: str, key: str) -> str:
    head = f"{plain} ({key})"
    return text if text.startswith(head) else f"{head}: {text}"


def with_plain_names(
    fn: Callable[..., Any], *, columns: Callable[[dict[str, Any]], Any]
) -> Callable[..., Any]:
    """Wrap a write dispatcher; `columns(bound_args)` gives the model names the call writes."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def _wrapped(*args, **kwargs):
        try:
            bound = sig.bind_partial(*args, **kwargs).arguments
        except TypeError:
            return fn(*args, **kwargs)
        cls = bound.get("component_class")
        names = plain_names_for(cls, columns(bound)) if isinstance(cls, str) else {}
        try:
            result = fn(*args, **kwargs)
        except HTTPException as exc:
            key = _named_column(exc.detail, names) if names else None
            if key is not None:
                d = exc.detail
                if isinstance(d, dict) and isinstance(d.get("message"), str):
                    exc.detail = {**d, "message": _opened(d["message"], names[key], key)}
                elif isinstance(d, str):
                    exc.detail = _opened(d, names[key], key)
            raise
        if names and isinstance(result, dict) and "plain_names" not in result:
            result = {**result, "plain_names": names}
        return result

    _wrapped.plain_names = True  # what tests walk the gate chain for
    return _wrapped


def _attr_keys(bound: dict[str, Any]):
    attrs = bound.get("attrs")
    return list(attrs) if isinstance(attrs, dict) else ()


def _update_keys(bound: dict[str, Any]):
    updates = bound.get("updates")
    return list(updates) if isinstance(updates, dict) else ()


def _batch_keys(bound: dict[str, Any]):
    rows = bound.get("components")
    if not isinstance(rows, list):
        return ()
    return list(dict.fromkeys(k for r in rows if isinstance(r, dict) for k in r))


#: The write tools that take model names, and where each call names them.
COLUMNS = {
    "create_component": _attr_keys,
    "update_component": _attr_keys,
    "bulk_update_components": _update_keys,
    "batch_create_components": _batch_keys,
}
