"""Market packs (SCU06 K8a): a bundle per bidding zone, applied as an ordinary edit.

A pack (``models.commercial.MarketPack``) carries a zone's day-ahead price ref
and the reserve products it offers (DC-06's one model). Applying it writes the
products into ``CommercialConfig.reserve_products`` and the day-ahead ref into
the export-price pick, through the same ``PUT /solver_config`` path as hand
entry (its validators, write gate and lock), so the stored config, the run
fingerprint and the IC digest are what hand entry of the same values gives.

What the last apply wrote is pinned per cell in the project sidecar
``market_pack.json`` (outside the config and every hash), with the pack's
``pack_hash``. A later apply ("update available" when the hash moved) changes a
cell only where the config still holds what the last apply wrote; a cell the
user edited is a conflict and is kept. Empty refs fail closed: an empty
day-ahead ref never clears or overwrites the export pick, an empty capacity
price never overwrites a product's price (a product with no price is refused at
solve, ``reserve_price_missing``, and settles to None).

Shipped templates: NO-1 to NO-5 with empty refs, so no prices ship (reserve
prices come from user uploads only, SCU06-K8-3). Pure service: no routers.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from models.commercial import MarketPack, ReserveProduct

PIN_FILE = "market_pack.json"
PIN_VERSION = 1
EXPORT_CELL = "export_price_ref"

# Refusal codes (also `error_kind`s).
UNKNOWN = "market_pack_unknown"
STALE = "market_pack_stale"
ZONE_UNKNOWN = "market_pack_zone_unknown"
ZONE_MISMATCH = "market_pack_zone_mismatch"
CURRENCY_MISMATCH = "market_pack_currency_mismatch"
CURRENCY_UNKNOWN = "market_pack_currency_unknown"
INVALID = "market_pack_invalid"
NO_COMMERCIAL = "market_pack_needs_connection"
UNSAVED = "market_pack_needs_saved_project"
PIN_UNREADABLE = "market_pack_pin_unreadable"

_MISSING = object()


class PackRefusal(ValueError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


# ── the shipped templates ────────────────────────────────────────────────────

# Generic shapes only (no market names, no prices; SAGC-1). The sustain hours are
# the template's starting values; the user confirms them against the TSO's
# prequalification, like any hand-entered product.
_NORDIC_PRODUCTS: tuple[dict[str, Any], ...] = (
    {
        "id": "fast_symmetric",
        "kind": "fast_symmetric",
        "direction": "symmetric",
        "sustain_h": 1.0,
    },
    {"id": "secondary_up", "kind": "secondary", "direction": "up", "sustain_h": 1.0},
    {
        "id": "secondary_down",
        "kind": "secondary",
        "direction": "down",
        "sustain_h": 1.0,
    },
    {"id": "tertiary_up", "kind": "tertiary", "direction": "up", "sustain_h": 1.0},
)
_SHIPPED_ZONES = ("NO-1", "NO-2", "NO-3", "NO-4", "NO-5")
_VALID_YEAR = 2026


def _content(pack: MarketPack | dict) -> dict:
    """The hashed content: everything but ``pack_hash`` and the load ``flags``."""
    raw = pack.model_dump(mode="json") if isinstance(pack, MarketPack) else dict(pack)
    return {k: v for k, v in raw.items() if k not in ("pack_hash", "flags")}


def pack_hash(pack: MarketPack | dict) -> str:
    """sha256 of the pack's canonical content (sorted keys, no whitespace)."""
    text = json.dumps(_content(pack), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def load_pack(raw: Any) -> MarketPack:
    """A pack from stored JSON: an unknown region or any other invalid field is a
    typed refusal (``market_pack_invalid``), never a validation error passing
    through; a pre-K8a pack loads stripped with a named flag."""
    from pydantic import ValidationError

    try:
        return MarketPack.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ())) or "pack"
        raise PackRefusal(
            INVALID,
            (f"The market pack cannot be read: {where}: {first.get('msg', 'invalid')}"),
            422,
        ) from exc


def _template(zone: str) -> MarketPack:
    body = {
        "id": f"{zone.lower()}-{_VALID_YEAR}",
        "label": f"{zone} market bundle ({_VALID_YEAR} template)",
        "region": zone,
        "currency": "EUR",
        "valid_year": _VALID_YEAR,
        "day_ahead": None,
        "products": [
            {
                **p,
                "capacity_price": None,
                "max_commit_mw": None,
                "storages": [],
                "counterparty": "tso",
            }
            for p in _NORDIC_PRODUCTS
        ],
    }
    return MarketPack.model_validate(
        {
            **body,
            "pack_hash": pack_hash(
                MarketPack.model_validate({**body, "pack_hash": "0" * 8})
            ),
        }
    )


def shipped() -> dict[str, MarketPack]:
    """The shipped templates, by id."""
    return {p.id: p for p in (_template(z) for z in _SHIPPED_ZONES)}


def get(pack_id: str) -> MarketPack:
    pack = shipped().get(pack_id)
    if pack is None:
        raise PackRefusal(UNKNOWN, f"No market pack “{pack_id}”.", 404)
    return pack


# ── fit: zone and currency (each refused on its own) ────────────────────────


def project_zone(project_dir: Path) -> str | None:
    """The project's bidding zone as S1 reads it: the zone the user picked,
    else the zone of the site pin; None when neither is known."""
    from services import market_prices

    chosen = market_prices.chosen_zone(project_dir)
    if chosen:
        return chosen
    try:
        from services.site_scene import store as scene_store

        pin = market_prices.pin_of(scene_store.project_sites(project_dir))
    except (OSError, ValueError):
        return None
    return market_prices.zone_for_pin(*pin) if pin else None


def check_fit(pack: MarketPack, zone: str | None, currency: str | None) -> None:
    """Refuses a pack for another zone, a project with no known zone, and a
    pack whose currency is not the project's stated one."""
    if zone is None:
        raise PackRefusal(
            ZONE_UNKNOWN,
            (
                f"The project has no bidding zone, so the {pack.region} pack cannot be checked "
                "against it: place the site pin or pick the zone in the market-price card first."
            ),
        )
    if pack.region != zone:
        raise PackRefusal(
            ZONE_MISMATCH,
            (
                f"The pack is for {pack.region}; the project is in {zone}. Use the {zone} pack, "
                "or change the project's bidding zone."
            ),
        )
    if not currency:
        raise PackRefusal(
            CURRENCY_UNKNOWN,
            (
                f"The project's finance inputs state no currency, so the pack's {pack.currency} "
                "prices cannot be checked against it: set the project's currency first."
            ),
        )
    if pack.currency != currency:
        raise PackRefusal(
            CURRENCY_MISMATCH,
            (
                f"The pack's prices are in {pack.currency}; the project's finance inputs are in "
                f"{currency}. Nothing is converted: change the project's currency, or enter the "
                "products by hand."
            ),
        )


# ── the pin ──────────────────────────────────────────────────────────────────


def read_pin(project_dir: Path | None) -> dict | None:
    """The last apply ``{pack_id, pack_hash, applied: {cell: value}}``, None when
    none was made. An unreadable pin raises (it is never read as none: that would
    treat every cell as unpinned and could not tell a user edit from the pack's)."""
    if project_dir is None:
        return None
    path = Path(project_dir) / PIN_FILE
    if not path.exists():
        return None
    try:
        pin = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackRefusal(
            PIN_UNREADABLE,
            (
                f"{PIN_FILE} cannot be read, so what the last pack apply wrote is unknown; "
                "restore or delete it"
            ),
        ) from exc
    if not (
        isinstance(pin, dict)
        and isinstance(pin.get("applied"), dict)
        and isinstance(pin.get("pack_id"), str)
        and isinstance(pin.get("pack_hash"), str)
    ):
        raise PackRefusal(
            PIN_UNREADABLE, f"{PIN_FILE} does not hold a pack pin; restore or delete it"
        )
    return pin


def write_pin(project_dir: Path, pack: MarketPack, applied: dict) -> None:
    from services.atomic_io import atomic_write_text

    atomic_write_text(
        Path(project_dir) / PIN_FILE,
        json.dumps(
            {
                "version": PIN_VERSION,
                "pack_id": pack.id,
                "pack_hash": pack.pack_hash,
                "applied": applied,
            },
            sort_keys=True,
            indent=1,
        ),
    )


def update_available(pack: MarketPack, pin: dict | None) -> bool:
    return (
        bool(pin)
        and pin.get("pack_id") == pack.id
        and pin.get("pack_hash") != pack.pack_hash
    )


# ── the per-cell plan ────────────────────────────────────────────────────────

_PRODUCT_FIELDS = tuple(ReserveProduct.model_fields)


def _cell(pid: str, field: str) -> str:
    return f"reserve_products/{pid}/{field}"


def plan_apply(pack: MarketPack, commercial: dict | None, pin: dict | None) -> dict:
    """What applying ``pack`` onto the stored ``commercial`` would do, per cell:
    ``changes`` [{cell, from, to}], ``conflicts`` [{cell, yours, pack, last_applied}]
    (kept: the user edited it), ``kept`` [{cell, reason}] (empty refs, no export
    Link), the new ``commercial`` dict and the ``applied`` pin to store."""
    if not isinstance(commercial, dict) or not commercial.get("poc_link"):
        raise PackRefusal(
            NO_COMMERCIAL,
            (
                "Set the site's grid connection (Commercial & parties) first: a market pack "
                "adds to the commercial settings, which start from the connection."
            ),
        )
    pinned = (
        (pin or {}).get("applied", {}) if (pin or {}).get("pack_id") == pack.id else {}
    )
    out = json.loads(json.dumps(commercial))
    changes: list[dict] = []
    conflicts: list[dict] = []
    kept: list[dict] = []
    applied: dict[str, Any] = {}

    def decide(cell: str, cur: Any, new: Any) -> bool:
        """True when ``cell`` takes ``new``; records the outcome."""
        last = pinned.get(cell, _MISSING)
        if cur == new:
            applied[cell] = new
            return False
        if last is not _MISSING and cur == last:
            changes.append({"cell": cell, "from": cur, "to": new})
            applied[cell] = new
            return True
        conflicts.append(
            {
                "cell": cell,
                "yours": cur,
                "pack": new,
                "last_applied": None if last is _MISSING else last,
            }
        )
        if last is not _MISSING:
            applied[cell] = last
        return False

    # The day-ahead ref onto the export pick: never cleared, never over a user's pick.
    new_da = pack.day_ahead.model_dump(mode="json") if pack.day_ahead else None
    cur_da = commercial.get(EXPORT_CELL)
    if new_da is None:
        kept.append({"cell": EXPORT_CELL, "reason": "empty_ref"})
        if EXPORT_CELL in pinned:
            applied[EXPORT_CELL] = pinned[EXPORT_CELL]
    elif cur_da is None and not commercial.get("export_link"):
        kept.append({"cell": EXPORT_CELL, "reason": "no_export_link"})
        if EXPORT_CELL in pinned:
            applied[EXPORT_CELL] = pinned[EXPORT_CELL]
    elif cur_da is None and EXPORT_CELL not in pinned:
        changes.append({"cell": EXPORT_CELL, "from": None, "to": new_da})
        applied[EXPORT_CELL] = new_da
        out[EXPORT_CELL] = new_da
    elif decide(EXPORT_CELL, cur_da, new_da):
        out[EXPORT_CELL] = new_da

    products = [dict(p) for p in (commercial.get("reserve_products") or [])]
    by_id = {p.get("id"): p for p in products}
    for prod in pack.products:
        new = prod.model_dump(mode="json")
        have = by_id.get(prod.id)
        cells = {f: _cell(prod.id, f) for f in _PRODUCT_FIELDS}
        if have is None:
            if any(c in pinned for c in cells.values()):
                # The last apply added it and the user removed it: not re-added.
                conflicts.append(
                    {
                        "cell": _cell(prod.id, "id"),
                        "yours": None,
                        "pack": prod.id,
                        "last_applied": prod.id,
                    }
                )
                applied.update({c: pinned[c] for c in cells.values() if c in pinned})
                continue
            for f, c in cells.items():
                changes.append({"cell": c, "from": None, "to": new[f]})
                applied[c] = new[f]
            products.append(new)
            continue
        for f, c in cells.items():
            if f == "id":
                applied[c] = prod.id
                continue
            cur = have.get(f, _MISSING)
            cur = None if cur is _MISSING else cur
            if f == "capacity_price" and new[f] is None and cur is not None:
                kept.append({"cell": c, "reason": "empty_ref"})
                if c in pinned:
                    applied[c] = pinned[c]
                continue
            if decide(c, cur, new[f]):
                have[f] = new[f]
    if products or "reserve_products" in commercial:
        out["reserve_products"] = products
    return {
        "pack_id": pack.id,
        "pack_hash": pack.pack_hash,
        "changes": changes,
        "conflicts": conflicts,
        "kept": kept,
        "commercial": out,
        "applied": applied,
    }
