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
  * the overnight cost is scaled by the ratio from the component's nominal to what the row's
    `capacity_basis` measures (`SCALING_RULES`, one table: component x basis -> scale or refusal).
    A Link's `p_nom` is its input (`bus0`), so a price per kW of electrical OUTPUT (the fuel cell) is
    scaled by the efficiency of the bus that carries the output, a price per kW of electrical INPUT
    (electrolysis) is not, and a price per kW of PRODUCT (Haber-Bosch NH3) is scaled by the product
    bus's efficiency. A pair that cannot be converted unambiguously raises `CapacityBasisMismatch`
    before anything is written, never a guess;
  * a version without typed cost units (`LEGACY_VERSIONS`) cannot price: `LegacyPackVersion`.

Each TYPED field it writes is stamped `default` through `asset_schema.provenance.record` (source
`<pack id>@<version>#<hash16>`, the row's source year, money year, range and illustrative flag); a
field the asset schema marks `derived` (`fields.fields_for`) is never recorded. A class the schema does
not describe yet (Link) records everything it writes.
"""
from __future__ import annotations

from typing import Any

from services.library.defaults_pack.loader import (
    LegacyPackVersion, load_defaults_pack, typed_successor)

BATTERY = "battery"
_CLASS = {"generators": "Generator", "storage_units": "StorageUnit", "links": "Link",
          "stores": "Store"}


class CapacityBasisMismatch(ValueError):
    """The component cannot be priced from this row: what its nominal measures does not convert
    unambiguously to what the row's capacity measures."""


#: Bus carriers that are electricity, and the carrier of a product-priced technology's output.
ELECTRIC_CARRIERS = frozenset({"AC", "DC", "electricity"})
PRODUCT_CARRIER = {"haber-bosch": "NH3"}

#: Component x row capacity basis -> how the overnight cost is scaled (or "refuse"). `_ac` / `_dc`
#: bases follow `output_electric`. A battery is priced through its parts, not through this table.
SCALING_RULES: dict[tuple[str, str], str] = {
    ("generators", "output_electric"): "unit",
    ("generators", "input_electric"): "refuse",
    ("generators", "output_product"): "unit_on_product_bus",
    ("generators", "storage_energy"): "refuse",
    ("generators", "not_applicable"): "refuse",
    ("storage_units", "output_electric"): "unit",
    ("storage_units", "input_electric"): "refuse",
    ("storage_units", "output_product"): "refuse",
    ("storage_units", "storage_energy"): "refuse",
    ("storage_units", "not_applicable"): "refuse",
    ("stores", "output_electric"): "refuse",
    ("stores", "input_electric"): "refuse",
    ("stores", "output_product"): "refuse",
    ("stores", "storage_energy"): "unit",
    ("stores", "not_applicable"): "refuse",
    ("links", "output_electric"): "link_output_electric",
    ("links", "input_electric"): "link_input_electric",
    ("links", "output_product"): "link_output_product",
    ("links", "storage_energy"): "refuse",
    ("links", "not_applicable"): "refuse",
}


def _rule_basis(capacity_basis: str) -> str:
    return "output_electric" if capacity_basis.startswith("output_electric") else capacity_basis


def _carrier(n, bus) -> str:
    return str(n.buses.at[bus, "carrier"]) if bus in n.buses.index else ""


def _link_outputs(n, df, name) -> list[tuple[str, float]]:
    """(carrier, efficiency) of each bus a Link delivers to, bus1 upwards."""
    out = []
    for i in range(1, 10):
        col = f"bus{i}"
        if col not in df.columns or not str(df.at[name, col]).strip():
            continue
        eff = df.at[name, "efficiency" if i == 1 else f"efficiency{i}"]
        out.append((_carrier(n, df.at[name, col]), float(eff)))
    return out


def _scale_for(n, component: str, name: str, technology: str, capacity_basis: str) -> float:
    df, basis = getattr(n, component), _rule_basis(capacity_basis)
    rule = SCALING_RULES.get((component, basis), "refuse")

    def refuse(why: str):
        raise CapacityBasisMismatch(
            f"{component[:-1]} {name!r} cannot be priced from {technology!r} "
            f"(capacity_basis {capacity_basis}): {why}")

    if rule == "unit":
        return 1.0
    if rule == "refuse":
        refuse("the component's nominal does not measure what the row's capacity measures")
    product = PRODUCT_CARRIER.get(technology)
    if rule == "unit_on_product_bus":
        if product is None or _carrier(n, df.at[name, "bus"]) != product:
            refuse(f"its bus does not carry the product {product!r}")
        return 1.0
    if rule == "link_input_electric":
        if _carrier(n, df.at[name, "bus0"]) not in ELECTRIC_CARRIERS:
            refuse("bus0 is not an electricity bus, so p_nom is not the electrical input")
        return 1.0
    wanted = ELECTRIC_CARRIERS if rule == "link_output_electric" else {product}
    if rule == "link_output_product" and product is None:
        refuse("the pack names no product carrier for this technology")
    hits = [eff for carrier, eff in _link_outputs(n, df, name) if carrier in wanted]
    if len(hits) != 1:
        refuse(f"{len(hits)} of its output buses carry {sorted(wanted)}; exactly one must")
    return hits[0]


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


def _review(flag) -> bool:
    """`connection_fee_review`: the row includes the connection or does not say."""
    return flag is not False


def _annotate(n, component_class: str, name: str, field: str, row) -> None:
    """The row's typed cost units on the field's provenance: a row that includes the grid
    connection (or does not say) is flagged, so a separately priced connection can be checked
    against a double count (`connection_fee_review`)."""
    from services.asset_schema import provenance

    provenance.annotate(n, component_class, name, field, capacity_basis=row.capacity_basis,
                        includes_grid_connection=row.includes_grid_connection,
                        connection_fee_review=_review(row.includes_grid_connection))


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
    if not pack.typed:
        raise LegacyPackVersion(pack.version, typed_successor(pack.version, region))
    rate = float(pack.finance.discount_rate.value)
    df = getattr(n, component)
    currency = pack.region_info.currency if pack.region_info is not None else pack.finance.currency
    stamp = {"technology": technology, "pack_id": pack.pack_id, "pack_version": pack.version,
             "pack_hash": pack.hash, "discount_rate": rate, "currency": currency}
    annual_key = ("annualised_cost_per_mw_yr" if pack.region_info is not None
                  else "annualised_cost_eur_per_mw_yr")
    if technology == BATTERY:
        from services.asset_schema.derive import apply_parts

        parts = {f"inv_{part}_{field}": _value(pack, f"battery.{part}.{field}")
                 for part in ("power", "energy") for field in ("overnight", "lifetime", "fom_share")}
        rows = {part: _row(pack, f"battery.{part}.overnight") for part in ("power", "energy")}
        df.at[name, "discount_rate"] = rate
        apply_parts(n, "StorageUnit", name, parts, discount_rate=rate)
        _stamp(n, "StorageUnit", name, pack,
               {**{f"inv_{part}_{field}": (_row(pack, f"battery.{part}.{field}"), 1.0)
                   for part in ("power", "energy") for field in ("overnight", "lifetime", "fom_share")},
                "discount_rate": (None, 1.0)},
               money=tuple(f"inv_{part}_overnight" for part in rows))
        for part, row in rows.items():
            _annotate(n, "StorageUnit", name, f"inv_{part}_overnight", row)
        capital, fom = float(df.at[name, "capital_cost"]), float(df.at[name, "fom_cost"])
        flags = [r.includes_grid_connection for r in rows.values()]
        return {**stamp, **parts, "capital_cost": capital, "fom_cost": fom,
                "money_year": rows["power"].money_year,
                "capacity_basis": {part: r.capacity_basis for part, r in rows.items()},
                "includes_grid_connection": flags[0] if len(set(map(str, flags))) == 1 else "unknown",
                "connection_fee_review": any(_review(f) for f in flags),
                annual_key: capital + fom}
    over_row = _row(pack, f"{technology}.investment.overnight")
    scale = _scale_for(n, component, name, technology, over_row.capacity_basis)
    overnight = _value(pack, f"{technology}.investment.overnight") * scale
    written = {"overnight_cost": overnight,
               "lifetime": _value(pack, f"{technology}.investment.lifetime"),
               "fom_cost": overnight * _value(pack, f"{technology}.investment.fom_share"),
               "discount_rate": rate, "capital_cost": 0.0}
    for col, v in written.items():
        df.at[name, col] = v
    _stamp(n, _CLASS.get(component, component), name, pack,
           {"overnight_cost": (over_row, scale),
            "lifetime": (_row(pack, f"{technology}.investment.lifetime"), 1.0),
            # FOM per MW: the overnight cost × the share's range
            "fom_cost": (_row(pack, f"{technology}.investment.fom_share"), overnight),
            "discount_rate": (None, 1.0)},   # capital_cost: derived on every class
           money=("overnight_cost", "fom_cost"))
    _annotate(n, _CLASS.get(component, component), name, "overnight_cost", over_row)
    from pypsa.costs import annuity as _annuity   # PyPSA's own; an IC package never imports the solver

    # The disclosure states what the LP charges per MW-year: `capital_cost` the annuity alone
    # (its meaning everywhere in the GUI; the column itself stays 0, PyPSA annuitises
    # `overnight_cost`), `fom_cost` beside it, and their sum. A regional sum is in the region's
    # money, not EUR.
    annuity = overnight * _annuity(rate, written["lifetime"])
    return {**stamp, **written, "capital_cost": annuity, "scale": scale,
            "money_year": over_row.money_year, "capacity_basis": over_row.capacity_basis,
            "includes_grid_connection": over_row.includes_grid_connection,
            "connection_fee_review": _review(over_row.includes_grid_connection),
            annual_key: annuity + written["fom_cost"]}
