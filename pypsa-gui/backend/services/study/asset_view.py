"""
The guided ledger as a view of the asset parameters (U3 S2 and rule 5; parent
plan §3 rule 5 and §10 S2; owner decision on provenance, 2026-10-13).

S1a owns the per-field provenance store, `n.meta["asset_params"]`, through
its single writer `services/asset_schema/provenance`. This module never
touches that key. It does three things:

* **Stamping.** `record_pack_defaults` is called by `packs.build_site_network`.
  Every asset field the pack writes from a ledger row is stamped through
  `provenance.record`. A row the guided user has not changed is `default`,
  with the defaults pack's id and hash (the ledger version) as its source. A
  changed row keeps its own status (`customised`, or `measured` for a
  measured value) and the source the user gave. So the Expert card (S1b)
  shows which values are the pack's.
* **Detecting Expert edits.** `expert_edits` finds them on a solved option
  fork. A field counts when the store calls it `customised` (S1a stamps a
  field changed through the Expert card or the chat tool) and its value no
  longer matches what the ledger compiles to. A stamp without a change is not
  an edit, and neither is a change without a stamp: the study does not guess
  who moved a value.
* **Marking (rule 5).** `mark_expert_edits` shows each such row `customised`,
  `changed_by: expert`, with the note `changed_in_expert:<key>:<option>`. It is
  a view for the study routes' reads only. The stored ledger is not
  rewritten: its value stays the study's, and the fork's edit already makes
  the findings stale through `run_hashes`.

`LEDGER_FIELDS` is the one table that maps a ledger row to the asset field
the pack writes from it. Its scales are the pack's own (`packs.battery_parts`
and the PV block of `build_site_network`), and a test pins each field to
S1a's typed, non-derived fields.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from models.study import AssumptionsLedger, LedgerRow
import services.asset_schema.provenance as provenance

__all__ = ["AssetSnapshot", "ExpertEdit", "LEDGER_FIELDS", "LedgerField", "asset_snapshot",
           "compiled_value", "expert_edits", "mark_expert_edits", "record_pack_defaults"]

BATTERY = ("StorageUnit", "battery")
PV = ("Generator", "pv")
EXPERT = "expert"
NOTE_PREFIX = "changed_in_expert"
# Relative tolerance for "the value moved": a netCDF round trip and the
# per-kW to per-MW scale must not read as an edit.
_RTOL = 1e-6


def _scale(k: float) -> Callable[[float], float]:
    return lambda v: v * k


@dataclass(frozen=True)
class LedgerField:
    """One asset field the pack writes from one ledger row, and how."""

    key: str
    component: str
    name: str
    field: str
    to_store: Callable[[float], float]


def _battery(key: str, field: str, to_store=_scale(1.0)) -> LedgerField:
    return LedgerField(key, *BATTERY, field, to_store)


def _pv(kind: str, suffix: str, field: str, to_store=_scale(1.0)) -> LedgerField:
    return LedgerField(f"pv_{kind}_{suffix}", *PV, field, to_store)


LEDGER_FIELDS: tuple[LedgerField, ...] = (
    _battery("battery_inverter_eur_per_kw", "inv_power_overnight", _scale(1000.0)),
    _battery("battery_inverter_lifetime_years", "inv_power_lifetime"),
    _battery("battery_inverter_fom_pct_per_year", "inv_power_fom_share", _scale(0.01)),
    _battery("battery_storage_eur_per_kwh", "inv_energy_overnight", _scale(1000.0)),
    _battery("battery_storage_lifetime_years", "inv_energy_lifetime"),
    # The pack splits the one round-trip row sqrt/sqrt (`packs.round_trip_efficiency`).
    _battery("battery_round_trip_efficiency", "efficiency_store", math.sqrt),
    _battery("battery_round_trip_efficiency", "efficiency_dispatch", math.sqrt),
    _battery("discount_rate", "discount_rate"),
    *(f for kind in ("rooftop", "utility") for f in (
        _pv(kind, "eur_per_kw", "overnight_cost", _scale(1000.0)),
        _pv(kind, "lifetime_years", "lifetime"),
    )),
    LedgerField("discount_rate", *PV, "discount_rate", _scale(1.0)),
)


@dataclass(frozen=True)
class ExpertEdit:
    """A field an Expert changed on a fork, against the ledger row it came from."""

    key: str
    component: str
    name: str
    field: str
    fork_value: float
    ledger_value: float


def _rows(ledger: AssumptionsLedger) -> dict[str, LedgerRow]:
    return {r.key: r for r in ledger.rows}


def _table(n, component: str):
    return n.storage_units if component == "StorageUnit" else n.generators


def compiled_value(m: LedgerField, ledger: AssumptionsLedger) -> float:
    """The value the pack writes on the asset from the ledger row, in storage units."""
    row = _rows(ledger)[m.key]
    if row.value is None:
        raise ValueError(f"ledger row {m.key!r} has no value")
    return float(m.to_store(float(row.value)))


def record_pack_defaults(n, ledger: AssumptionsLedger, *, pv_kind: str | None = None) -> None:
    """Stamp every asset field the pack wrote from a ledger row (see the module docstring)."""
    rows = _rows(ledger)
    for m in LEDGER_FIELDS:
        if m.name not in _table(n, m.component).index or m.key not in rows:
            continue
        if m.component == PV[0] and m.key.startswith("pv_") and pv_kind is not None \
                and not m.key.startswith(f"pv_{pv_kind}_"):
            continue
        row = rows[m.key]
        if row.value is None:
            continue
        changed = row.status != "default" or row.provenance != "library"
        status = ("measured" if row.provenance == "measured" else "customised") if changed else "default"
        rng = None
        if row.range is not None:
            lo, hi = sorted((m.to_store(row.range.low), m.to_store(row.range.high)))
            rng = (lo, hi)
        provenance.record(
            n, m.component, m.name, m.field, status=status,
            source=(row.source if changed else ledger.ledger_version),
            source_year=row.source_year, currency_year=row.currency_year,
            range=rng, illustrative=bool(row.illustrative) and not changed)


@dataclass(frozen=True)
class AssetSnapshot:
    """
    What the view needs from one network: its option, the PV kind its pack
    read, and each mapped field's value and provenance entry. Small enough to
    cache per fork file, so a ledger read does not re-import every fork.
    """

    option_id: str | None
    pv_kind: str | None
    values: Mapping[tuple[str, str, str], float]
    stamps: Mapping[tuple[str, str, str], Mapping]


def asset_snapshot(n) -> AssetSnapshot:
    meta = (getattr(n, "meta", None) or {}).get("decision_study_pack") or {}
    values: dict[tuple[str, str, str], float] = {}
    stamps: dict[tuple[str, str, str], Mapping] = {}
    for m in LEDGER_FIELDS:
        table = _table(n, m.component)
        if m.name not in table.index or m.field not in table.columns:
            continue
        k = (m.component, m.name, m.field)
        try:
            values[k] = float(table.at[m.name, m.field])
        except (TypeError, ValueError):
            continue
        entry = provenance.get(n, m.component, m.name).get(m.field)
        if entry:
            stamps[k] = entry
    return AssetSnapshot(meta.get("option_id"), meta.get("pv_kind"), values, stamps)


def expert_edits(n_or_snapshot, ledger: AssumptionsLedger) -> list[ExpertEdit]:
    """The fields a person changed on this network since the pack wrote them."""
    snap = (n_or_snapshot if isinstance(n_or_snapshot, AssetSnapshot)
            else asset_snapshot(n_or_snapshot))
    rows = _rows(ledger)
    out: list[ExpertEdit] = []
    seen: set[str] = set()
    for m in LEDGER_FIELDS:
        k = (m.component, m.name, m.field)
        if k not in snap.values or m.key not in rows or rows[m.key].value is None:
            continue
        if m.key.startswith("pv_") and snap.pv_kind is not None \
                and not m.key.startswith(f"pv_{snap.pv_kind}_"):
            continue
        if (snap.stamps.get(k) or {}).get("status") != "customised":
            continue
        got, want = snap.values[k], compiled_value(m, ledger)
        if math.isfinite(got) and math.isclose(got, want, rel_tol=_RTOL, abs_tol=1e-12):
            continue
        if m.key in seen:
            continue
        seen.add(m.key)
        out.append(ExpertEdit(m.key, m.component, m.name, m.field, got, want))
    return out


def mark_expert_edits(ledger: AssumptionsLedger,
                      edits_by_option: Mapping[str, Sequence[ExpertEdit]]) -> AssumptionsLedger:
    """The ledger as the study routes show it: each Expert-edited row `customised` by `expert`."""
    hits = {(e.key, opt) for opt, edits in edits_by_option.items() for e in edits}
    if not hits:
        return ledger
    keys = {k for k, _ in hits}
    rows = [r.model_copy(update={"status": "customised", "changed_by": EXPERT})
            if r.key in keys else r for r in ledger.rows]
    notes = tuple(ledger.honesty_notes) + tuple(
        f"{NOTE_PREFIX}:{k}:{opt}" for k, opt in sorted(hits)
        if f"{NOTE_PREFIX}:{k}:{opt}" not in ledger.honesty_notes)
    return ledger.model_copy(update={"rows": rows, "honesty_notes": notes})
