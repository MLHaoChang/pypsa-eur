"""
Plain names in write-tool results (expert-catalogue issue 05, spec §3.6 / §7.1).

The generic write tools keep taking model names (`attrs`, `updates`: the
model's words), but what they answer names each column the way the user reads
it: a result gains `plain_names` {model_name: plain_name} for the columns the
call named, and a refusal about one of them gains `plain_name` beside its
`field`; the model reads it as "Capacity (p_nom): <the shared message>", so the
assistant relays the user's word (RAW-NAMES-1). The message itself is never
changed: every write path refuses with one text (01a-3a R7).

Columns the catalogue does not hold are left out; a refusal that names none of
the call's columns, or whose detail is plain text, is passed on unchanged.
"""

from __future__ import annotations

import functools
import inspect
import re
from typing import Any
from collections.abc import Callable

from fastapi import HTTPException
from pydantic import ValidationError

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
    # The column the text names first, not the first of the call's columns: in
    # "p_nom_min must not exceed p_nom" the refusal is about p_nom_min.
    hits = []
    for k in names:
        m = re.search(rf"(?<![\w]){re.escape(k)}(?![\w])", text)
        if m:
            hits.append((m.start(), k))
    return min(hits)[1] if hits else None


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
            d = exc.detail
            if key is not None and isinstance(d, dict) and "plain_name" not in d:
                # A key beside the shared message, never a change to it: REST, preview
                # and chat keep one text (01a-3a R7); the model-facing copy opens with
                # it (`harness.results._error_result_content`).
                exc.detail = {
                    **d,
                    "field": d.get("field") or key,
                    "plain_name": names[key],
                }
            raise
        except ValidationError as exc:
            # A schema refusal (a bad value for one column) before the route runs.
            # Same kind and text as before, with the column's plain name beside it.
            errs = exc.errors()
            loc = errs[0].get("loc") if errs else None
            key = loc[0] if loc and loc[0] in names else None
            if key is None:
                raise
            raise HTTPException(
                422,
                detail={
                    "error_kind": "validation_error",
                    "message": str(exc),
                    "field": key,
                    "plain_name": names[key],
                },
            ) from exc
        if names and isinstance(result, dict) and "plain_names" not in result:
            result = {**result, "plain_names": names}
        return result

    _wrapped.plain_names = True  # what tests walk the gate chain for
    return _wrapped


def _keys_of(arg: str) -> Callable[[dict[str, Any]], Any]:
    """The columns of a call's dict argument `arg` (`attrs`, `updates`)."""

    def keys(bound: dict[str, Any]):
        v = bound.get(arg)
        return list(v) if isinstance(v, dict) else ()

    return keys


def _batch_keys(bound: dict[str, Any]):
    rows = bound.get("components")
    if not isinstance(rows, list):
        return ()
    return list(dict.fromkeys(k for r in rows if isinstance(r, dict) for k in r))


#: The write tools that take model names, and where each call names them.
COLUMNS = {
    "create_component": _keys_of("attrs"),
    "update_component": _keys_of("attrs"),
    "bulk_update_components": _keys_of("updates"),
    "batch_create_components": _batch_keys,
}
