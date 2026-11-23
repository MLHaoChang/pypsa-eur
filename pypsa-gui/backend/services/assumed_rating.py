"""A rating an importer assumed is not a rating the file stated (psse-import 05).

The PSS/E and DGS readers give an unrated line, or an unbounded external grid, a
finite rating that never binds, and mark the element ``s_nom_source`` /
``p_nom_source`` = "assumed". Results read it as having no stated rating: the
loading % stays, the row is labelled, and it is left out of the overload counts.
Only the exact string "assumed" means assumed; "file", "", NaN and a missing
column all read as stated.
"""

from __future__ import annotations

from typing import Any

ASSUMED = "assumed"
LABEL = "rating assumed"
# the nominal column a person types, and the marker column that qualifies it
SOURCE_COLUMN = {"s_nom": "s_nom_source", "p_nom": "p_nom_source"}


def is_assumed(row: Any, nom: str = "s_nom") -> bool:
    """True when this row's ``nom`` rating carries the importer's "assumed" marker.

    An extendable element's rating is the solver's ``*_opt``, which no one assumed.
    """
    try:
        if bool(row.get(f"{nom}_extendable", False)):
            return False
        return row.get(SOURCE_COLUMN[nom]) == ASSUMED
    except (AttributeError, KeyError, TypeError):
        return False


def mark_entered(n: Any, component_class: str, name: str, fields: Any) -> None:
    """A person set ``s_nom`` / ``p_nom``: the rating now reads as stated ("file")."""
    try:
        frame = n.components[component_class].static
    except (AttributeError, KeyError):
        return
    if name not in frame.index:
        return
    for nom, column in SOURCE_COLUMN.items():
        if (
            nom in fields
            and column in frame.columns
            and frame.at[name, column] == ASSUMED
        ):
            frame.at[name, column] = "file"
