"""Apply one shipped (`common`) cost reference to one asset: the plan, and its provenance.

`plan` reads the asset and the reference and says exactly what an apply would write,
or refuses. It writes nothing. The chat tool, the edit card and the HTTP route all
take the write from it, through the native component update, then `stamp` records
each written field's provenance. Nothing here touches a pack or a Library item.

What the plan writes, per the class's own pack convention (`price_from_pack`):
`overnight_cost`, `lifetime`, `fom_cost` = overnight × the reference's FOM share, and,
on a Link, `efficiency` when the reference gives one. `capital_cost` and
`discount_rate` are left as they are (PyPSA annuitises `overnight_cost`). An
`input_electric` price is per MW of electrical input and is written as it is: it is
never scaled by the Link's efficiency, before or after.

Refusals (`error_kind`): a target class the references do not price
(`reference_target_unsupported`), an EUR reference beside a pack in another currency
(`currency_mismatch`, nothing is converted), and a confirmation the plan needs and
was not given (`reference_confirmation_needed`, naming each one):

- `dc_ac`: an AC-output PV price on a generator whose capacity basis is DC or not
  declared;
- `grid_connection`: a reference that already includes grid connection, on a
  project that prices its connection (a PoC Link cost or a connection fee);
- `money_year`: the reference's money year is not the money the project reads it in:
  the investment case's base year when the project has a case (the engine reads typed
  costs as real base-year money), else the money year of the pack value it replaces.
  The value is copied in its own money year and not adjusted (gatekeeper, 2026-10-21:
  no price index is declared; an index would apply to packs and references alike).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from services.library import europe_reference as R
from services.library import reference_catalogue as C

CONFIRMATIONS = ("dc_ac", "grid_connection", "money_year")
# One closing line for every confirmation, in the card, the drawer and the assistant's refusal.
LEFT_UNCONFIRMED = "Left unticked, nothing is applied."

# Which class a capacity basis prices: output on a Generator, electrical input on a Link.
_TARGET_CLASS = {"output_electric": "Generator", "output_electric_ac": "Generator",
                 "output_electric_dc": "Generator", "input_electric": "Link"}
_ATTR = {"Generator": "generators", "Link": "links"}


def _refuse(kind: str, message: str, status: int = 422, **extra):
    raise HTTPException(status, {"error_kind": kind, "message": message, **extra})


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _connection_priced(n, commercial: Mapping[str, Any] | None) -> str | None:
    """Why the project already pays for its grid connection, or None."""
    commercial = commercial if isinstance(commercial, Mapping) else {}
    for key in ("poc_link", "export_link"):
        link = commercial.get(key)
        if link and str(link) in n.links.index:
            for column in ("overnight_cost", "capital_cost"):
                if column in n.links.columns and (_num(n.links.at[str(link), column]) or 0.0) > 0:
                    return f"the connection Link {link!r} carries a {column}"
    connection = commercial.get("connection")
    if isinstance(connection, Mapping) and connection.get("capacity_fee"):
        return "the connection agreement charges a capacity fee"
    return None


def _asset_period(n, frame, name: str) -> int | None:
    build = _num(frame.at[name, "build_year"]) if "build_year" in frame.columns else None
    if build:
        return int(build)
    periods = list(getattr(n, "investment_periods", None) if len(getattr(n, "investment_periods", ())) else ())
    if len(periods) == 1:
        return int(periods[0])
    years = {int(t.year) for t in getattr(n, "snapshots", []) if hasattr(t, "year")}
    return years.pop() if len(years) == 1 else None


def money_disclosure(price_year: int, currency: str, read_as: int) -> str:
    """The one sentence for a value copied in another money year (the pack's wording)."""
    return f"{price_year} {currency} price data, read as {read_as} money: not adjusted for inflation."


# The investment-cost fields whose provenance carries a money year.
COST_FIELDS = ("overnight_cost", "fom_cost", "inv_power_overnight", "inv_energy_overnight")


def money_years_in_use(n) -> dict[int, list[tuple[str, str, str]]]:
    """`{money year: [(class, asset, field), ...]}` over every recorded cost field of the
    network. More than one key means the asset costs mix money years. Read-only; a field
    without a recorded money year is left out (unknown is not a year)."""
    meta = getattr(n, "meta", None)
    root = meta.get("asset_params", {}) if isinstance(meta, dict) else {}
    years: dict[int, list[tuple[str, str, str]]] = {}
    for klass, assets in root.items():
        for name, fields in assets.items():
            for field in COST_FIELDS:
                year = (fields.get(field) or {}).get("currency_year")
                if isinstance(year, int):
                    years.setdefault(year, []).append((klass, name, field))
    return {year: sorted(years[year]) for year in sorted(years)}


def case_base_year(n, config) -> int | None:
    """The investment case's base year, derived as the edit card's money basis is (the first
    investment period, else the first snapshot year); None when the project has no case."""
    if not getattr(config, "finance", None):
        return None
    periods = list(n.investment_periods) if len(getattr(n, "investment_periods", ())) else []
    if periods:
        return int(periods[0])
    return int(n.snapshots[0].year) if len(n.snapshots) and hasattr(n.snapshots[0], "year") else None


def plan(n, commercial: Mapping[str, Any] | None, component_class: str, name: str,
         technology: str, projection_year: int, confirm: Iterable[str] = (),
         base_year: int | None = None) -> dict:
    """What applying `technology`/`projection_year` to `component_class` `name` writes,
    with the reference and the pack value beside each field. Writes nothing."""
    confirm = set(confirm or ())
    unknown = sorted(confirm - set(CONFIRMATIONS))
    if unknown:
        _refuse("invalid_edit_value", f"Unknown confirmation(s) {unknown}; known: {list(CONFIRMATIONS)}.")
    try:
        ref = R.load_europe_technology_reference(technology, projection_year)
    except R.DefaultsPackError as exc:
        _refuse("library_reference_unknown", str(exc), 404)
    target = _TARGET_CLASS[ref.capacity_basis_code]
    if component_class != target:
        _refuse("reference_target_unsupported",
                f"{ref.technology} is priced per {ref.capacity_basis} and applies to a {target}, "
                f"not a {component_class}.")
    frame = getattr(n, _ATTR[target])
    if name not in frame.index:
        _refuse("component_not_found", f"{component_class} {name!r} not found.", 404)

    from services.asset_schema import provenance

    stamps = provenance.get(n, component_class, name)
    # The currency the asset is priced in, as its provenance records it (a regional pack
    # stamps it). Checked before any pack is loaded: a pack this build cannot load falls
    # back to the generic EUR pack, and that fallback must never turn a mismatch into a match.
    recorded = (stamps.get("overnight_cost") or {}).get("currency")
    if recorded and recorded != ref.currency:
        _refuse("currency_mismatch", f"This asset is priced in {recorded}; the reference is in "
                f"{ref.currency}. Nothing is converted.")
    detail = C.reference_detail(technology, projection_year, stamps)
    by_parameter = {row.parameter: row for row in detail.comparison}
    pack_overnight = by_parameter["overnight"].pack
    pack_currency = pack_overnight.currency if pack_overnight is not None else None
    if pack_currency and pack_currency != ref.currency:
        _refuse("currency_mismatch", f"This asset is priced from a {pack_currency} pack ({detail.pack.stamp}); "
                f"the reference is in {ref.currency}. Nothing is converted.")

    part = ref.cost_part
    overnight = part.overnight.value  # per MW of the reference's own capacity basis, never scaled
    attrs: dict[str, float] = {"overnight_cost": overnight, "lifetime": part.lifetime.value,
                               "fom_cost": overnight * part.fom_share.value}
    if target == "Link" and part.efficiency is not None:
        attrs["efficiency"] = part.efficiency.value

    needs: list[dict] = []
    if ref.capacity_basis_code == "output_electric_ac":
        needs.append({"confirmation": "dc_ac", "text":
                      "This PV cost is per MW of AC output. This generator's capacity isn't marked as AC or DC; "
                      "if it is the DC module rating, the cost per MW is overstated by the DC/AC ratio. "
                      f"{LEFT_UNCONFIRMED}"})
    why = _connection_priced(n, commercial) if ref.includes_grid_connection is True else None
    if why:
        needs.append({"confirmation": "grid_connection", "text":
                      f"This reference's investment already includes grid connection, and {why}: the connection "
                      f"would be counted twice. {LEFT_UNCONFIRMED}"})
    pack_year = pack_overnight.currency_year if pack_overnight is not None else None
    read_as = base_year if base_year is not None else pack_year
    no_money_year = read_as is None
    if read_as is not None and read_as != ref.currency_year:
        against = (f"the investment case's base year ({read_as})" if base_year is not None
                   else f"the default it replaces ({read_as} {pack_currency or ref.currency})")
        needs.append({"confirmation": "money_year", "reference_year": ref.currency_year,
                      "read_as_year": read_as, "read_as_source": against,
                      "disclosure": money_disclosure(ref.currency_year, ref.currency, read_as),
                      "text": f"{money_disclosure(ref.currency_year, ref.currency, read_as)} "
                              f"Compared with {against}. {LEFT_UNCONFIRMED}"})

    period = _asset_period(n, frame, name)
    notes = []
    if period is not None and period != ref.projection_year:
        notes.append(f"The reference is the {ref.projection_year} projection; this asset's period is {period}.")
    if no_money_year:
        notes.append(f"No investment case or default value to compare money years with: the value is "
                     f"{ref.currency_year} {ref.currency} price data, as the source states.")

    fields = {"overnight_cost": "overnight", "lifetime": "lifetime", "fom_cost": "fom_share",
              "efficiency": "efficiency"}
    rows = []
    for field, value in attrs.items():
        cmp = by_parameter[fields[field]]
        cell = ref.source_cells[fields[field]]
        rows.append({"field": field, "new": value, "reference": {
            "parameter": cmp.parameter, "value": cmp.reference.value, "unit": cmp.reference.unit,
            "currency_year": ref.currency_year if field in ("overnight_cost", "fom_cost") else None,
            "projection_year": ref.projection_year,
            "source": cmp.reference.source, "cell": cell["cell"], "label": cell["label"]},
            "pack": None if cmp.pack is None else {
                "key": cmp.pack.key, "value": cmp.pack.value, "unit": cmp.pack.unit,
                "currency_year": cmp.pack.currency_year, "projection_year": cmp.pack.projection_year,
                "source": cmp.pack.source},
            "differs": cmp.differs})
    return {"component_class": component_class, "name": name, "technology": technology,
            "projection_year": projection_year, "attrs": attrs, "rows": rows, "base_year": base_year,
            "needs": needs, "missing": [x["confirmation"] for x in needs if x["confirmation"] not in confirm],
            "notes": notes, "pack": detail.pack.model_dump(), "source": source_id(ref),
            "source_year": part.overnight.source_year,
            # What a re-apply compares against: the pack source this asset had before any reference.
            "replaces": (stamps.get("overnight_cost") or {}).get("replaces")
            or (stamps.get("overnight_cost") or {}).get("source"),
            "currency": ref.currency, "currency_year": ref.currency_year,
            "licence": ref.licence, "attribution": ref.attribution}


def plan_active(args: Mapping[str, Any]) -> dict:
    """`plan` on the active network, with its commercial config and investment-case base year."""
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.get_active_context()
    config = ctx.solver_state.get("solver_config")
    return plan(ctx.network, getattr(config, "commercial", None), args.get("component_class"),
                args.get("name"), args.get("technology"), args.get("projection_year"),
                args.get("confirm") or (), base_year=case_base_year(ctx.network, config))


def require_confirmed(p: dict) -> None:
    if p["missing"]:
        _refuse("reference_confirmation_needed",
                "Confirm before applying: " + " ".join(x["text"] for x in p["needs"]
                                                       if x["confirmation"] in p["missing"]),
                missing=p["missing"])


def source_id(ref: R.EuropeanTechnologyReference) -> str:
    """The provenance source of an applied reference: `<reference id>#<hash16>/<technology>@<year>`."""
    return f"{ref.reference_id}#{ref.reference_hash[:16]}/{ref.technology}@{ref.projection_year}"


def stamp(n, p: dict, *, approved_by: str | None, approved_at: str | None = None) -> None:
    """Record each written field as the user's explicit choice of this reference."""
    from services.asset_schema import provenance

    when = approved_at or datetime.now(UTC).isoformat(timespec="seconds")
    for field in p["attrs"]:
        provenance.record(n, p["component_class"], p["name"], field, status="customised",
                          source=p["source"], source_year=p["source_year"], replaces=p["replaces"],
                          currency_year=p["currency_year"] if field in ("overnight_cost", "fom_cost") else None,
                          currency=p["currency"] if field in ("overnight_cost", "fom_cost") else None,
                          approved_by=approved_by, approved_at=when)
