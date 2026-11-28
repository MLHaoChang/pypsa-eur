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

from services.asset_schema import catalogue, lookup


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
        if isinstance(field, str):
            # The refusal names its column: that one or none, never a second guess from the text.
            return field if field in names else None
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
                    "field": key,
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


# ── display replacement (spec §7.3) ─────────────────────────────────────────
# The server's copy of `frontend/src/catalogue/displayPlainNames.ts`, for the
# live-voice TTS path (`services/live_speech.py`): a backticked model name in
# the assistant's text is spoken as its plain name. Same rule as the raw-name
# guard (`catalogue/modelNameRule.ts`), same case table
# (`tests/data/display_plain_names_cases.json`).

# The Properties panel still shows `FieldSpec.label` for these, which is not the
# catalogue's word (Line s_nom: "Capacity" there, "Rating" here), so chat keeps
# them raw until expert-catalogue slice 03 moves the panel to the catalogue.
# `tests/test_display_plain_names.py` fails once a name here no longer differs.
# Mirrored in `frontend/src/catalogue/displayPlainNames.ts`.
PANEL_NAMES_DIFFER = frozenset({"marginal_cost", "capital_cost", "p_nom_max", "s_nom"})

_EDGE = r"(?<![A-Za-z0-9_])"
_END = r"(?![A-Za-z0-9_])"
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_QUOTE = re.compile(r"^\s*>")
_RUN = re.compile(r"`+")


@functools.lru_cache(maxsize=1)
def _display_index() -> tuple[re.Pattern[str], dict[str, str | None]]:
    """The snake_case rule over the catalogue's model names, and each name's one
    plain name (None when classes name it differently)."""
    words: dict[str, str | None] = {}
    for scope, by_name in catalogue.model_names().items():
        for key in by_name:
            word = lookup.plain_name(scope, key)
            words[key] = None if key in words and words[key] != word else word
    for key in PANEL_NAMES_DIFFER:
        words[key] = None
    snake = sorted((k for k in words if "_" in k), key=len, reverse=True)
    rule = re.compile(_EDGE + "(?:" + "|".join(map(re.escape, snake)) + ")" + _END)
    return rule, words


def _replace_line(line: str, swap: Callable[[str], str | None]) -> str:
    """One line outside fenced code and quotes: its single-backtick spans,
    paired by run length (CommonMark)."""
    runs = list(_RUN.finditer(line))
    out, at, i = [], 0, 0
    while i < len(runs):
        o = runs[i]
        close = next(
            (
                j
                for j in range(i + 1, len(runs))
                if len(runs[j].group()) == len(o.group())
            ),
            None,
        )
        if close is None:
            i += 1
            continue
        word = (
            swap(line[o.end() : runs[close].start()]) if len(o.group()) == 1 else None
        )
        if word is not None:
            out.append(line[at : o.start()] + word)
            at = runs[close].end()
        i = close + 1
    return "".join(out) + line[at:]


def display_plain_names(text: str, user_text: str = "") -> str:
    """``text`` (assistant markdown) with each backticked catalogue model name as
    its plain name. Never bare prose, fenced code or quoted (``>``) text; an
    ambiguous or unknown name, or one ``user_text`` names, stays raw."""
    if not text or "`" not in text:
        return text
    rule, words = _display_index()
    asked = {m.group() for m in rule.finditer(user_text or "")}

    def swap(token: str) -> str | None:
        if token in asked or not rule.fullmatch(token):
            return None
        return words.get(token)

    fence: str | None = None
    quoted = False
    lines = []
    for line in text.split("\n"):
        if fence is not None:
            closing = (
                r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$"
            )
            if re.match(closing, line):
                fence = None
            lines.append(line)
            continue
        f = _FENCE.match(line)
        if f:
            fence, quoted = f.group(1), False
            lines.append(line)
            continue
        if not line.strip():
            quoted = False
        elif _QUOTE.match(line):
            quoted = True
        # A quote runs on (lazy continuation) until a blank line.
        lines.append(line if quoted else _replace_line(line, swap))
    return "\n".join(lines)
