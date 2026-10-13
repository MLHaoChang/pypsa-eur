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
"""
from __future__ import annotations

from typing import Any

from services.library.defaults_pack.loader import load_defaults_pack

BATTERY = "battery"


def _value(pack, key: str) -> float:
    for row in pack.cost_values:
        if row.key == key:
            return float("nan") if row.value is None else float(row.value)
    raise KeyError(f"defaults pack {pack.version} has no row {key!r}")


def price_from_pack(n, component: str, name: str, technology: str, *,
                    version: str) -> dict[str, Any]:
    """Write `technology`'s pack costs onto `n.<component>.loc[name]`; return what was written with
    the pack's id, version and hash. Raises `KeyError` for a technology the pack does not price."""
    pack = load_defaults_pack(version)
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
        return {**stamp, **parts, "capital_cost": float(df.at[name, "capital_cost"]),
                "fom_cost": float(df.at[name, "fom_cost"])}
    overnight = _value(pack, f"{technology}.investment.overnight")
    if component == "links":
        overnight *= float(df.at[name, "efficiency"])
    written = {"overnight_cost": overnight,
               "lifetime": _value(pack, f"{technology}.investment.lifetime"),
               "fom_cost": overnight * _value(pack, f"{technology}.investment.fom_share"),
               "discount_rate": rate, "capital_cost": 0.0}
    for col, v in written.items():
        df.at[name, col] = v
    return {**stamp, **written}
