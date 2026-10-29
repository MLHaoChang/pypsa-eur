"""
Which result views a workbook includes, and the sheet builder for each.

RW-TABS-1: by default the views of the cards the user switched on for the
project (`flow_scope.selected` over `result_views.RESULT_VIEWS`' card and
sub-option), plus any `extra_views` the user ticked, or every view with
`all_`. About and Overview are always written and are not views.

`SHEET_BUILDERS` maps a `result_views` view id to the function that writes
its sheet(s). A builder takes `(wb, ctx)` and returns True when it wrote a
sheet, False when the run has no results for the view (About then lists the
view as "no results" and no empty sheet is left). A view with no builder is
listed in About as not yet exported. Package 02 adds its builders with
`@register(view_id)` in its own modules, imported by `BUILDER_MODULES`.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from typing import Any

from services import flow_scope
from services.result_views import RESULT_VIEWS, ResultView

__all__ = ["BUILDER_MODULES", "NO_RESULTS", "SHEET_BUILDERS", "SheetBuilder", "UnknownView",
           "load_builders", "register", "resolve_views", "workbook_views"]

SheetBuilder = Callable[[Any, Any], bool]

SHEET_BUILDERS: dict[str, SheetBuilder] = {}
#: What About says for a view whose builder found no results, when not the default.
NO_RESULTS: dict[str, str] = {}

#: Modules whose import registers builders (`load_builders`); 02 appends its own.
BUILDER_MODULES: tuple[str, ...] = ("services.results_workbook.finance",
                                    "services.results_workbook.views_network",
                                    "services.results_workbook.views_grid",
                                    "services.results_workbook.views_adequacy")


class UnknownView(ValueError):
    """A requested view id that is not a result view a workbook can hold."""

    def __init__(self, unknown: list[str]):
        self.unknown = unknown
        super().__init__(f"Unknown result view(s): {', '.join(unknown)}. "
                         f"Choose from: {', '.join(v.view_id for v in workbook_views())}.")


def workbook_views() -> tuple[ResultView, ...]:
    """The Results panel's views that belong to a card, in the panel's order."""
    return tuple(v for v in RESULT_VIEWS if v.panel == "results" and v.card is not None)


def register(view_id: str, *, no_results: str | None = None
             ) -> Callable[[SheetBuilder], SheetBuilder]:
    if view_id not in {v.view_id for v in workbook_views()}:
        raise ValueError(f"{view_id!r} is not a workbook result view")

    def deco(fn: SheetBuilder) -> SheetBuilder:
        if view_id in SHEET_BUILDERS and SHEET_BUILDERS[view_id] is not fn:
            raise ValueError(f"a sheet builder for {view_id!r} is already registered")
        SHEET_BUILDERS[view_id] = fn
        if no_results:
            NO_RESULTS[view_id] = no_results
        return fn
    return deco


def load_builders() -> dict[str, SheetBuilder]:
    for name in BUILDER_MODULES:
        importlib.import_module(name)
    return SHEET_BUILDERS


def _on(scope: dict, v: ResultView) -> bool:
    return flow_scope.selected(scope, v.card, v.sub_option)


def resolve_views(scope: dict | None, extra_views: Iterable[str] = (),
                  all_: bool = False) -> list[str]:
    """The included view ids, in the panel's order. Raises `UnknownView`."""
    views = workbook_views()
    known = {v.view_id for v in views}
    extra = [e for e in dict.fromkeys(extra_views) if e]
    unknown = [e for e in extra if e not in known]
    if unknown:
        raise UnknownView(unknown)
    if all_:
        return [v.view_id for v in views]
    wanted = set(extra)
    return [v.view_id for v in views if v.view_id in wanted or (scope and _on(scope, v))]
