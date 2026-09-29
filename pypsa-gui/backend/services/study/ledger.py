"""
The assumptions ledger: user edits, re-seed, maturity, CSV (MVP-1 S2).

Plan: docs/superpowers/plans/2026-09-28-guided-investment-study-mvp1-v2.md (S2)
Spec: docs/superpowers/specs/2026-09-28-guided-investment-study-design.md §4.3

Every function here is pure: it takes a ledger and returns a new one. The
routes (``routers/studies.py``) store the result on the study record.

* :func:`apply_user_row` sets one row's value; the row becomes
  ``provenance=user`` (or ``measured`` / ``imported``) and
  ``status=customised``. The unit is required and must equal the row's: a
  value in the wrong unit is refused, never converted.
* :func:`reseed_ledger` is "re-seed": seed afresh from the library and the
  current intake (library rows take the library's values, tariff rows follow
  the chosen tariff), then put every row the user set back over its seed.
* :func:`maturity_from_ledger` is the maturity badge (spec decision 12).
* :func:`ledger_to_csv` is the export (CSV import is MVP-2);
  :func:`ledger_rows_from_csv` is its internal reader, which the MVP-2 import
  will call.
"""
from __future__ import annotations

import csv
import io
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, UTC
from typing import Any, Literal

from pydantic import ValidationError

from models.study import (
    AccuracyBand,
    AssumptionsLedger,
    DecisionQuestion,
    LedgerDomain,
    LedgerRange,
    LedgerRow,
    StudyMaturity,
)
from services.study.library import DERIVED, Library, key_drivers_of, seed_ledger

__all__ = [
    "CSV_COLUMNS", "LedgerEditError", "LedgerImportError", "LoadProvenance",
    "apply_user_row", "diff_against_defaults", "key_driver_rows",
    "ledger_rows_from_csv", "ledger_to_csv", "load_provenance",
    "maturity_from_ledger", "refresh_derived", "reseed_ledger", "reset_rows",
    "with_study_currency_year",
]

LoadProvenance = Literal["uploaded", "synthetic", "missing"]
_EDIT_PROVENANCES = frozenset({"user", "measured", "imported"})


class LedgerEditError(ValueError):
    """A user edit the ledger refuses; the message names the row."""


class LedgerImportError(ValueError):
    """A CSV row the reader refuses; the message names the line and row."""


# Why a row with no value cannot be typed over (gate S2 BC-S2-2). The code is
# the row's `unavailable["value"]` flag.
_NOT_EDITABLE = {
    "not_used_in_mvp1": "MVP-1 does not model this row (not_used_in_mvp1)",
    "not_applicable": "not applicable under the chosen tariff (not_applicable)",
    "tariff_descriptor": ("the tariff is chosen or supplied at the tariff step, "
                          "not typed into the ledger"),
}
_NOT_APPLICABLE_WHY = {"demand_charge_price": "the chosen tariff has no demand charge"}

# A user row a re-seed could not keep as it was is flagged in the ledger's
# honesty notes as `needs_attention:<key>:<reason>`; it must be reset before
# it can be edited again, so it is never silently re-customised.
_ATTENTION = "needs_attention:"


def _attention_notes(ledger: AssumptionsLedger, key: str) -> list[str]:
    return [n for n in ledger.honesty_notes if n.startswith(f"{_ATTENTION}{key}:")]


def _money(unit: str) -> str:
    return unit.split("/", 1)[0] if "/" in unit else unit


# ── edits ─────────────────────────────────────────────────────────────────

def _row_index(ledger: AssumptionsLedger, key: str) -> int:
    for i, row in enumerate(ledger.rows):
        if row.key == key:
            return i
    raise LedgerEditError(f"{key}: no such ledger row")


def refresh_derived(ledger: AssumptionsLedger) -> AssumptionsLedger:
    """
    Recompute every derived row that is still a library default from its
    inputs (round-trip efficiency follows an edited inverter efficiency). A
    derived row the user set is left alone.
    """
    values = {r.key: r.value for r in ledger.rows}
    rows = list(ledger.rows)
    for i, row in enumerate(rows):
        spec = DERIVED.get(row.key)
        if spec is None or row.provenance != "library" or row.status != "default":
            continue
        inputs, fn = spec
        args = [values.get(k) for k in inputs]
        if any(a is None for a in args):
            continue
        rows[i] = row.model_copy(update={"value": round(fn(*args), 6)})
    return ledger.model_copy(update={"rows": rows})


def apply_user_row(ledger: AssumptionsLedger, key: str, value: float, *,
                   unit: str, changed_by: str | None,
                   changed_at: datetime | None = None,
                   provenance: str = "user",
                   source: str | None = None,
                   source_year: int | None = None,
                   source_url: str | None = None,
                   currency_year: int | None = None) -> AssumptionsLedger:
    """
    Set row ``key`` to ``value`` in ``unit``.

    The row keeps its key, label, range and sensitivity flag; it takes
    ``provenance`` (``user`` unless the caller states ``measured`` or
    ``imported``), ``status=customised``, the editor and the time. ``source`` defaults to
    "User entry" and ``source_year`` to the year of the edit.

    Refused, with the row named (gate S2): a row with no value that MVP-1
    does not use, that the tariff makes inapplicable, or that describes the
    tariff (BC-S2-2); a row a re-seed flagged, until it is reset (BC-S2-2);
    a value outside the row's physical ``domain`` (BC-S2-3); a
    ``currency_year`` other than the row's, since nothing is converted
    (BC-S2-4). An omitted ``currency_year`` means the row's.
    """
    i = _row_index(ledger, key)
    row = ledger.rows[i]
    if row.value is None and row.unavailable.get("value") in _NOT_EDITABLE:
        code = row.unavailable["value"]
        why = _NOT_EDITABLE[code]
        if code == "not_applicable" and key in _NOT_APPLICABLE_WHY:
            why = f"{_NOT_APPLICABLE_WHY[key]} ({code})"
        raise LedgerEditError(f"{key}: cannot be edited: {why}")
    flagged = _attention_notes(ledger, key)
    if flagged:
        reason = flagged[0][len(f"{_ATTENTION}{key}:"):]
        raise LedgerEditError(
            f"{key}: needs attention after a re-seed ({reason}); reset the row, "
            "then enter a value")
    if not unit or not str(unit).strip():
        raise LedgerEditError(f"{key}: a unit is required (the row is in {row.unit!r})")
    if unit.strip() != row.unit:
        raise LedgerEditError(
            f"{key}: unit {unit!r} does not match the row's {row.unit!r}; "
            "enter the value in the row's unit")
    if provenance not in _EDIT_PROVENANCES:
        raise LedgerEditError(f"{key}: provenance {provenance!r} is not a user edit")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise LedgerEditError(f"{key}: {value!r} is not a number") from None
    if not math.isfinite(value):
        raise LedgerEditError(f"{key}: the value must be finite, got {value!r}")
    if row.domain is not None and not row.domain.contains(value):
        raise LedgerEditError(
            f"{key}: {value!r} is outside the row's domain {row.domain} "
            f"(in {row.unit})")
    if (currency_year is not None and row.currency_year is not None
            and currency_year != row.currency_year):
        raise LedgerEditError(
            f"{key}: the value is given in {currency_year} money but the row is in "
            f"{row.currency_year} {_money(row.unit)}; enter the value in "
            f"{row.currency_year} {_money(row.unit)} (nothing is converted)")
    changed_at = changed_at or datetime.now(tz=UTC)
    data = row.model_dump()
    data.update(
        value=value, unavailable={}, provenance=provenance, status="customised",
        changed_by=changed_by, changed_at=changed_at,
        # The library's citation no longer describes the number; the user's
        # (or "User entry" in the year of the edit) does.
        source=source or "User entry",
        source_year=source_year if source_year is not None else changed_at.year,
        source_url=source_url,
    )
    if currency_year is not None:
        data["currency_year"] = currency_year
    try:
        new_row = LedgerRow.model_validate(data)
    except ValidationError as exc:
        raise LedgerEditError(f"{key}: {exc}") from None
    rows = list(ledger.rows)
    rows[i] = new_row
    return refresh_derived(ledger.model_copy(update={"rows": rows}))


def reseed_ledger(existing: AssumptionsLedger,
                  question: DecisionQuestion | Iterable[str],
                  intake: Mapping[str, Any] | None,
                  library: Library) -> AssumptionsLedger:
    """
    Re-seed: library rows refreshed, user rows kept.

    A fresh seed (library values, the intake's tariff, the question's key
    drivers), then every row the user set in ``existing`` replaces its seed
    row, with the sensitivity flag re-read from the question. A user row
    whose key the library no longer carries is kept at the end and named in
    an honesty note, so a user's number is never dropped silently.

    Gate S2 BC-S2-2: a kept user row that no longer applies (its fresh seed
    is ``not_applicable`` or ``not_used_in_mvp1``) or whose fresh seed is in
    another unit is marked ``needs_attention`` and named in a
    ``needs_attention:<key>:<reason>`` note; it is never silently kept as
    ``customised``. :func:`reset_rows` clears it. Rows that came with a
    supplied tariff are re-derived from the intake, not kept.
    """
    fresh = seed_ledger(question, intake, library)
    drivers = set(key_drivers_of(question))
    kept = {r.key: r for r in existing.rows
            if r.provenance != "library" and r.changed_at is not None}
    rows = []
    notes = list(fresh.honesty_notes)
    for row in fresh.rows:
        user = kept.pop(row.key, None)
        if user is None:
            rows.append(row)
            continue
        reason = None
        if row.value is None and row.unavailable.get("value") in _NOT_EDITABLE:
            reason = row.unavailable["value"]
        elif row.unit != user.unit:
            reason = f"unit_changed_{user.unit}_to_{row.unit}"
        status = "needs_attention" if reason else "customised"
        if reason:
            notes.append(f"{_ATTENTION}{row.key}:{reason}")
        rows.append(user.model_copy(
            update={"sensitivity_flag": row.key in drivers, "status": status}))
    for key, user in kept.items():
        rows.append(user.model_copy(update={"sensitivity_flag": key in drivers}))
        notes.append(f"user_row_not_in_library:{key}")
    return refresh_derived(fresh.model_copy(
        update={"rows": rows, "honesty_notes": tuple(notes)}))


def reset_rows(ledger: AssumptionsLedger, keys: Iterable[str],
               question: DecisionQuestion | Iterable[str],
               intake: Mapping[str, Any] | None,
               library: Library) -> AssumptionsLedger:
    """
    Put rows back to their fresh seed (library and intake), dropping the
    user's value and any ``needs_attention`` note. A user row the seed no
    longer carries is removed. An unknown key is refused.
    """
    fresh = {r.key: r for r in seed_ledger(question, intake, library).rows}
    keys = list(keys)
    present = {r.key for r in ledger.rows}
    for key in keys:
        if key not in present:
            raise LedgerEditError(f"{key}: no such ledger row")
    drivers = set(key_drivers_of(question))
    rows = []
    for row in ledger.rows:
        if row.key not in keys:
            rows.append(row)
        elif row.key in fresh:
            rows.append(fresh[row.key].model_copy(
                update={"sensitivity_flag": row.key in drivers}))
    notes = tuple(n for n in ledger.honesty_notes
                  if not any(n.startswith(f"{_ATTENTION}{k}:") for k in keys))
    return refresh_derived(ledger.model_copy(
        update={"rows": rows, "honesty_notes": notes}))


_STUDY_YEAR_NOTE = "study_currency_year_"


def with_study_currency_year(ledger: AssumptionsLedger,
                             study_year: int | None) -> AssumptionsLedger:
    """
    Gate S2 BC-S2-4: name a study currency year that differs from the year
    of the ledger's money rows (recognised by a currency unit, ``EUR/...``).
    Nothing is converted. Idempotent; a matching or unset year leaves no note.
    """
    notes = [n for n in ledger.honesty_notes if not n.startswith(_STUDY_YEAR_NOTE)]
    years = sorted({r.currency_year for r in ledger.rows
                    if r.currency_year is not None and r.unit.startswith("EUR")})
    if study_year is not None and years and years != [study_year]:
        notes.append(f"{_STUDY_YEAR_NOTE}{study_year}_differs_from_ledger_"
                     f"{'_'.join(map(str, years))}_nothing_converted")
    return ledger.model_copy(update={"honesty_notes": tuple(notes)})


def diff_against_defaults(ledger: AssumptionsLedger,
                          defaults: AssumptionsLedger) -> list[dict[str, Any]]:
    """
    The rows that differ from the defaults, in ledger order: a changed value
    or a non-library provenance. What the review screen shows as "edited".
    """
    base = {r.key: r for r in defaults.rows}
    out = []
    for row in ledger.rows:
        d = base.get(row.key)
        if d is not None and row.provenance == "library" and row.value == d.value:
            continue
        out.append({
            "key": row.key, "label": row.label, "unit": row.unit,
            "default_value": None if d is None else d.value,
            "value": row.value, "provenance": row.provenance,
            "status": row.status, "changed_by": row.changed_by,
            "changed_at": row.changed_at,
        })
    return out


def key_driver_rows(ledger: AssumptionsLedger) -> list[LedgerRow]:
    return [r for r in ledger.rows if r.sensitivity_flag]


# ── maturity ──────────────────────────────────────────────────────────────

def load_provenance(intake: Mapping[str, Any] | None) -> LoadProvenance:
    """
    ``uploaded`` when the intake's load step is an upload (a meter file);
    ``synthetic`` for a sector profile or a sketch; ``missing`` when the
    load step has not been answered.
    """
    load = (intake or {}).get("load") if isinstance(intake, Mapping) else None
    if not isinstance(load, Mapping) or not load.get("source"):
        return "missing"
    return "uploaded" if load.get("source") in ("upload", "uploaded", "measured") \
        else "synthetic"


# Indicative accuracy by analogy with AACE International RP 18R-97 (cost
# estimate classes): screening ~ Class 5, feasibility ~ Class 4. The badge is
# indicative; no estimate class is claimed.
_AACE = "AACE International RP 18R-97 (indicative analogy, Class {cls})"
_BANDS = {
    "screening": AccuracyBand(low_pct=-50.0, high_pct=100.0, low_pct_narrow=-20.0,
                              high_pct_narrow=30.0, reference=_AACE.format(cls=5)),
    "feasibility": AccuracyBand(low_pct=-30.0, high_pct=50.0, low_pct_narrow=-15.0,
                                high_pct_narrow=20.0, reference=_AACE.format(cls=4)),
}


def _established(row: LedgerRow) -> bool:
    """A key-driver row the user has stood behind: customised or measured."""
    if row.status == "needs_attention":
        return False
    return row.status == "customised" or row.provenance == "measured"


def maturity_from_ledger(ledger: AssumptionsLedger,
                         load: LoadProvenance) -> StudyMaturity:
    """
    The maturity badge (spec decision 12).

    ``screening`` while any key-driver row is still a default (or needs
    attention), or while the load is synthetic or missing; ``feasibility``
    when every key-driver row is customised or measured AND the load is an
    upload. ``design`` is reserved (it needs a detailed design basis MVP-1
    does not collect). ``reasons`` names every row, and the load, that keeps
    the study at screening, as ``"<key>: <status> (<provenance>)"`` and
    ``"load: <provenance>"``.

    A key driver the tariff makes inapplicable (value ``None`` flagged
    ``not_applicable``, e.g. the demand charge of a tariff without one) is
    not a default the user could customise, so it does not hold the badge.

    Gate S2 BC-S2-1: the tariff row holds the badge at screening unless the
    tariff is one the user supplied (``provenance`` ``user``, ``imported`` or
    ``measured``, never ``library``) and is not marked ``illustrative``;
    the reason reads ``"tariff: <tariff_id> (<source>, <provenance>)"``.
    Re-entering a seed tariff's prices does not make it the user's.
    """
    drivers = key_driver_rows(ledger)
    reasons: list[str] = []
    if not drivers:
        reasons.append("no key driver rows: the question names none, so "
                       "nothing establishes the study beyond screening")
    for row in drivers:
        if row.value is None and row.unavailable.get("value") == "not_applicable":
            continue
        if not _established(row):
            reasons.append(f"{row.key}: {row.status} ({row.provenance})")
    tariffs = [r for r in ledger.rows if r.key == "tariff"]
    if not tariffs:
        reasons.append("tariff: the ledger names no tariff (re-seed it)")
    for t in tariffs:
        if t.provenance == "library" or t.source.strip().lower() == "illustrative":
            reasons.append(f"tariff: {t.technical_name} ({t.source}, {t.provenance})")
    if load != "uploaded":
        reasons.append(f"load: {load} (upload metered load to raise maturity)")
    cls = "screening" if reasons else "feasibility"
    return StudyMaturity(status="ok", class_=cls, accuracy_band=_BANDS[cls],
                         reasons=reasons)


# ── CSV ───────────────────────────────────────────────────────────────────

CSV_COLUMNS = (
    "key", "label", "technical_name", "value", "unit", "basis",
    "currency_year", "source", "source_year", "source_url", "range_low",
    "range_high", "range_source", "domain", "provenance", "status", "sensitivity_flag",
    "changed_by", "changed_at", "unavailable",
)
_TEXT_COLUMNS = frozenset({
    "key", "label", "technical_name", "unit", "basis", "source", "source_url",
    "range_source", "domain", "provenance", "status", "changed_by", "unavailable",
})
# A spreadsheet evaluates a cell that starts with one of these as a formula
# (OWASP "CSV injection"). The backend has no shared CSV writer that guards
# this, so the ledger export does it here: a text cell that starts with one is
# prefixed with a single quote, which the reader strips again.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")


def _neutralise(text: str) -> str:
    return "'" + text if text.startswith(_FORMULA_LEADS) else text


def _restore(text: str) -> str:
    if text.startswith("'") and text[1:].startswith(_FORMULA_LEADS):
        return text[1:]
    return text


def _num(value: float | int | None) -> str:
    return "" if value is None else repr(value)


def ledger_to_csv(ledger: AssumptionsLedger) -> str:
    """The ledger as CSV (UTF-8 text, CRLF rows, one header row)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLUMNS)
    for r in ledger.rows:
        cells = {
            "key": r.key, "label": r.label, "technical_name": r.technical_name or "",
            "value": _num(r.value), "unit": r.unit, "basis": r.basis.value,
            "currency_year": _num(r.currency_year), "source": r.source,
            "source_year": _num(r.source_year), "source_url": r.source_url or "",
            "range_low": _num(r.range.low if r.range else None),
            "range_high": _num(r.range.high if r.range else None),
            "range_source": (r.range.source or "") if r.range else "",
            "domain": str(r.domain) if r.domain else "",
            "provenance": r.provenance, "status": r.status,
            "sensitivity_flag": "true" if r.sensitivity_flag else "false",
            "changed_by": r.changed_by or "",
            "changed_at": r.changed_at.isoformat() if r.changed_at else "",
            "unavailable": ";".join(f"{k}={v}" for k, v in r.unavailable.items()),
        }
        w.writerow([_neutralise(cells[c]) if c in _TEXT_COLUMNS else cells[c]
                    for c in CSV_COLUMNS])
    return buf.getvalue()


def _csv_num(text: str, cast, where: str):
    if not text:
        return None
    try:
        return cast(text)
    except ValueError:
        raise LedgerImportError(f"{where}: {text!r} is not a number") from None


def ledger_rows_from_csv(text: str) -> list[LedgerRow]:
    """
    Parse a ledger CSV written by :func:`ledger_to_csv`. Internal: the import
    route is MVP-2. A row without a unit, or one that does not validate, is
    refused with its line and key named.
    """
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != CSV_COLUMNS:
        raise LedgerImportError(
            f"header {reader.fieldnames} != {list(CSV_COLUMNS)}")
    rows: list[LedgerRow] = []
    for line, raw in enumerate(reader, start=2):
        cell = {c: (_restore(raw[c] or "") if c in _TEXT_COLUMNS else (raw[c] or ""))
                for c in CSV_COLUMNS}
        key = cell["key"] or "?"
        where = f"line {line} ({key})"
        if not cell["unit"].strip():
            raise LedgerImportError(f"{where}: unit is empty; every row needs a unit")
        low = _csv_num(cell["range_low"], float, where)
        high = _csv_num(cell["range_high"], float, where)
        unavailable = dict(p.split("=", 1) for p in cell["unavailable"].split(";") if p)
        try:
            rows.append(LedgerRow(
                key=cell["key"], label=cell["label"],
                technical_name=cell["technical_name"] or None,
                value=_csv_num(cell["value"], float, where), unit=cell["unit"],
                basis=cell["basis"],
                currency_year=_csv_num(cell["currency_year"], int, where),
                source=cell["source"],
                source_year=_csv_num(cell["source_year"], int, where),
                source_url=cell["source_url"] or None,
                range=None if low is None and high is None else LedgerRange(
                    low=low, high=high, source=cell["range_source"] or None),
                domain=LedgerDomain.parse(cell["domain"]) if cell["domain"] else None,
                provenance=cell["provenance"], status=cell["status"],
                sensitivity_flag=cell["sensitivity_flag"].lower() == "true",
                changed_by=cell["changed_by"] or None,
                changed_at=(datetime.fromisoformat(cell["changed_at"])
                            if cell["changed_at"] else None),
                unavailable=unavailable,
            ))
        except (ValidationError, ValueError) as exc:
            raise LedgerImportError(f"{where}: {exc}") from None
    return rows
