"""One name, unit and basis per result figure (guided-results 04).

`figure_words.json` beside this module is the single source: the reports and the
`/results/headline_kpis` cards read it here, and `tests/test_figure_words.py` pins
every backend surface to it (the checked metrics' contract labels as recorded
divergences until their v2 profiles land). The frontend reads the same file in
guided-results 04 slice 2.

The file is validated at import against a closed schema and a bad file stops
the app loudly: a figure never falls back to a default label.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

SOURCE = Path(__file__).with_name("figure_words.json")
FIELDS = {"name", "unit", "tag", "basis"}
REQUIRED = {"name", "unit"}
PLACEHOLDERS = {"currency", "money"}


def _validate(doc) -> dict[str, dict[str, str]]:
    if not isinstance(doc, dict) or set(doc) - {"version", "_comment", "figures"}:
        raise ValueError("figure_words.json: top level must hold only version, _comment, figures")
    if doc.get("version") != 1:
        raise ValueError("figure_words.json: version must be 1")
    figures = doc.get("figures")
    if not isinstance(figures, dict) or not figures:
        raise ValueError("figure_words.json: figures must be a non-empty object")
    for fid, entry in figures.items():
        if not isinstance(entry, dict):
            raise ValueError(f"figure_words.json: {fid} must be an object")
        unknown, missing = set(entry) - FIELDS, REQUIRED - set(entry)
        if unknown:
            raise ValueError(f"figure_words.json: {fid} has unknown keys {sorted(unknown)}")
        if missing:
            raise ValueError(f"figure_words.json: {fid} lacks {sorted(missing)}")
        for key, value in entry.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"figure_words.json: {fid}.{key} must be a non-empty string")
            fields = {f for _t, f, _s, _c in string.Formatter().parse(value) if f is not None}
            if fields - PLACEHOLDERS:
                raise ValueError(f"figure_words.json: {fid}.{key} has unknown placeholders {sorted(fields - PLACEHOLDERS)}")
    return figures


def load(path: Path = SOURCE) -> dict[str, dict[str, str]]:
    return _validate(json.loads(Path(path).read_text(encoding="utf-8")))


FIGURES = load()


def _fill(text: str, currency: str | None, money: str | None) -> str:
    return text.format(currency=currency or "report currency", money=money or "")


def name(fid: str) -> str:
    return FIGURES[fid]["name"]


def tag(fid: str) -> str | None:
    return FIGURES[fid].get("tag")


def unit(fid: str, currency: str | None = None) -> str:
    """The unit; a missing currency reads "report currency", never a silent €."""
    return _fill(FIGURES[fid]["unit"], currency, None)


def basis(fid: str, currency: str | None = None, money: str | None = None) -> str | None:
    text = FIGURES[fid].get("basis")
    return None if text is None else _fill(text, currency, money)


def label(fid: str) -> str:
    """"Expected shortfall (h/yr)": the name with a fixed unit, for table rows."""
    return f"{name(fid)} ({unit(fid)})"
