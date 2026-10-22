"""
Price one asset from the generic defaults pack (IC S3 seeds; owner 2026-10-13).

The one way a template or a redundancy scenario turns pack rows into an asset's costs, through the
asset schema (plan §10): investment is overnight parts, annuitised at the pack's discount rate, never
an annualised literal.

  * a battery (StorageUnit) gets the pack's power and energy parts through `derive.apply_parts`, which
    writes the derived two-annuity `capital_cost` and leaves `overnight_cost` empty;
  * any other asset gets one `investment` part on PyPSA's own columns: `overnight_cost`, `lifetime`,
    `fom_cost` (fom_share × overnight) and `discount_rate`, with `capital_cost` 0 (PyPSA annuitises
    `overnight_cost` and would ignore it anyway);
  * a Link's `p_nom` is its input, so a pack price per kW of output (the fuel cell) is scaled by the
    Link's efficiency (PyPSA-Eur's convention).

Each TYPED field it writes is stamped `default` through `asset_schema.provenance.record` (source
`<pack id>@<version>#<hash16>`, the row's source year, money year, range and illustrative flag); a
field the asset schema marks `derived` (`fields.fields_for`) is never recorded. A class the schema does
not describe yet (Link) records everything it writes.
"""
from __future__ import annotations

from typing import Any

from services.library.defaults_pack.loader import load_defaults_pack

BATTERY = "battery"
_CLASS = {"generators": "Generator", "storage_units": "StorageUnit", "links": "Link",
          "stores": "Store"}


def _refuse_unpriced(pack, key: str) -> None:
    """A regional pack's missing money row: `MissingRegionalRow`, never the EU row."""
    info = pack.region_info
    if info is not None and key in info.unpriced:
        pack.value(key)               # raises MissingRegionalRow


def _row(pack, key: str):
    for row in pack.cost_values:
        if row.key == key:
            return row
    _refuse_unpriced(pack, key)
    raise KeyError(f"defaults pack {pack.version} has no row {key!r}")


def _derived(component_class: str) -> set[str]:
    from services.asset_schema import fields

    try:
        return {f.key for f in fields.fields_for(component_class) if f.derived}
    except KeyError:              # not described yet: record what is written
        return set()


def _stamp(n, component_class: str, name: str, pack, entries: dict, *, money=()) -> None:
    """`entries`: {field: (row or None, range scale)}; a row None is the finance discount rate.
    A regional pack's finance defaults are the EU base's, so that source names the base pack;
    `money` are the fields in the region's currency; a row taken from the EU pack is `eu_fallback`."""
    from services.asset_schema import provenance

    info = pack.region_info
    source = f"{pack.pack_id}@{pack.version}#{pack.hash[:16]}"
    base_source = source if info is None else \
        f"{pack.pack_id}@{info.base_version}#{info.base_hash[:16]}"
    skip = _derived(component_class)
    for field, (row, scale) in entries.items():
        if field in skip:
            continue
        src = pack.finance.discount_rate if row is None else row
        rng = None if src.range is None else (src.range.low * scale, src.range.high * scale)
        fell_back = info is not None and row is not None and \
            info.row_source.get(row.key) == "eu_fallback"
        provenance.record(n, component_class, name, field, status="default",
                          source=base_source if row is None else source,
                          source_year=src.source_year,
                          currency_year=getattr(src, "currency_year", None),
                          range=rng, illustrative=bool(src.illustrative),
                          currency=info.currency if info is not None and field in money else None,
                          fallback="eu_fallback" if fell_back else None)


def _value(pack, key: str) -> float:
    for row in pack.cost_values:
        if row.key == key:
            return float("nan") if row.value is None else float(row.value)
    _refuse_unpriced(pack, key)
    raise KeyError(f"defaults pack {pack.version} has no row {key!r}")


def price_from_pack(n, component: str, name: str, technology: str, *,
                    version: str, region: str | None = None) -> dict[str, Any]:
    """Write `technology`'s pack costs onto `n.<component>.loc[name]`; return what was written with
    the pack's id, version and hash, and the annual charge: `capital_cost` (the annuity alone),
    `fom_cost` and `annualised_cost_eur_per_mw_yr` (their sum, what the LP charges per MW-year). Raises `KeyError` for a technology the pack does not price.
    `region` (explicit; None is the EU pack) prices from that region's pack, `version` being its
    date; a money row the region lacks raises `MissingRegionalRow` before anything is written, the
    EU row is never substituted."""
    pack = load_defaults_pack(version, region=region)
    rate = float(pack.finance.discount_rate.value)
    df = getattr(n, component)
    stamp = {"technology": technology, "pack_id": pack.pack_id, "pack_version": pack.version,
             "pack_hash": pack.hash, "discount_rate": rate}
    if technology == BATTERY:
        from services.asset_schema.derive import apply_parts

        parts = {f"inv_{part}_{field}": _value(pack, f"battery.{part}.{field}")
                 for part in ("power", "energy") for field in ("overnight", "lifetime", "fom_share")}
        df.at[name, "discount_rate"] = rate
        apply_parts(n, "StorageUnit", name, parts, discount_rate=rate)
        _stamp(n, "StorageUnit", name, pack,
               {**{f"inv_{part}_{field}": (_row(pack, f"battery.{part}.{field}"), 1.0)
                   for part in ("power", "energy") for field in ("overnight", "lifetime", "fom_share")},
                "discount_rate": (None, 1.0)})
        capital, fom = float(df.at[name, "capital_cost"]), float(df.at[name, "fom_cost"])
        return {**stamp, **parts, "capital_cost": capital, "fom_cost": fom,
                "annualised_cost_eur_per_mw_yr": capital + fom}
    overnight = _value(pack, f"{technology}.investment.overnight")
    scale = float(df.at[name, "efficiency"]) if component == "links" else 1.0
    overnight *= scale
    written = {"overnight_cost": overnight,
               "lifetime": _value(pack, f"{technology}.investment.lifetime"),
               "fom_cost": overnight * _value(pack, f"{technology}.investment.fom_share"),
               "discount_rate": rate, "capital_cost": 0.0}
    for col, v in written.items():
        df.at[name, col] = v
    over_row = _row(pack, f"{technology}.investment.overnight")
    _stamp(n, _CLASS.get(component, component), name, pack,
           {"overnight_cost": (over_row, scale),
            "lifetime": (_row(pack, f"{technology}.investment.lifetime"), 1.0),
            # FOM per MW: the overnight cost × the share's range
            "fom_cost": (_row(pack, f"{technology}.investment.fom_share"), overnight),
            "discount_rate": (None, 1.0)},   # capital_cost: derived on every class
           money=("overnight_cost", "fom_cost"))
    from pypsa.costs import annuity as _annuity   # PyPSA's own; an IC package never imports the solver

    # The disclosure states what the LP charges per MW-year: `capital_cost` the annuity alone
    # (its meaning everywhere in the GUI; the column itself stays 0, PyPSA annuitises
    # `overnight_cost`), `fom_cost` beside it, and their sum.
    annuity = overnight * _annuity(rate, written["lifetime"])
    if pack.region_info is not None:           # a regional sum is in the region's money, not EUR
        return {**stamp, "currency": pack.region_info.currency, **written, "capital_cost": annuity,
                "annualised_cost_per_mw_yr": annuity + written["fom_cost"]}
    return {**stamp, **written, "capital_cost": annuity,
            "annualised_cost_eur_per_mw_yr": annuity + written["fom_cost"]}
