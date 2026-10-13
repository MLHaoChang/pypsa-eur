"""
The section-list helpers a report assembler shares (plan S7, review v1 N7).

Lifted out of ``services/adequacy/study_report.py`` so the decision report
(``services/study/report.py``) disclosing what it did not establish uses the
same rules as the adequacy write-up, over a generic section list:

* a section is a mapping (the adequacy write-up's rows) or an object (a
  ``models.study.ReportSection``) with an id and a ``status``;
* the STATUS MAPPING: ``ok`` is established; ``no_data`` (the adequacy
  surfaces' word) and ``not_established`` (the decision study's) are both
  missing, and the omission is stated; anything else (``skipped``) is a
  section that does not apply, and says nothing;
* a disclosure RULE is ``(section_id, sentence)``: the sentence is required
  when that section is established. Each report keeps its own rule set (the
  adequacy sentences are not reused by the decision report).

``build_study_report``'s output is pinned byte-identical across the lift
(``tests/test_adequacy_study_report_pinned.py``).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

__all__ = ["ESTABLISHED", "MISSING_STATUSES", "disclosures", "is_missing",
           "not_established", "present_ids", "status_of"]

ESTABLISHED = "ok"
MISSING_STATUSES = frozenset({"no_data", "not_established"})


def _field(section: Any, key: str) -> Any:
    if isinstance(section, Mapping):
        return section.get(key)
    return getattr(section, key, None)


def status_of(section: Any) -> str | None:
    return _field(section, "status")


def is_missing(section: Any) -> bool:
    return status_of(section) in MISSING_STATUSES


def present_ids(sections: Iterable[Any], *, id_key: str = "id") -> set[str]:
    """The ids of the established sections."""
    return {_field(s, id_key) for s in sections if status_of(s) == ESTABLISHED}


def not_established(sections: Iterable[Any], line: Callable[[Any], str]) -> list[str]:
    """One line per missing section (``no_data`` or ``not_established``)."""
    return [line(s) for s in sections if is_missing(s)]


def disclosures(present: set[str], rules: Sequence[tuple[str, str]], *,
                always: Sequence[str] = ()) -> list[str]:
    """``always``, then each rule's sentence whose section is established."""
    return [*always, *(text for section_id, text in rules if section_id in present)]
