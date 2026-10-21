"""Explicit European technology benchmarks in the existing Library lineage.

Offline, pinned reference data only. No default selection, project writes,
database, network access, finance calculations or automatic cost application.
Source scope travels with the native cost part. See the adjacent data README.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool

from services.library.defaults_pack.loader import CostPart, DefaultsPackError, PackValue

_DATA_FILE = Path(__file__).with_name("reference_data") / "eu-dea-2026-08.json"
_EXPECTED_SHA256 = "902258be6632a18bc050b604eae1275b3c181124e88af6a6e9bcd3f67a18a11c"

# Typed cost units, the same vocabulary as defaults-pack values. A price per
# `input_electric` MW is never scaled by efficiency; `output_electric_ac` PV is
# not interchangeable with a DC-rated asset.
CapacityBasisCode = Literal["output_electric", "output_electric_ac", "output_electric_dc", "input_electric"]
GridConnection = StrictBool | Literal["unknown"]


class EuropeanTechnologyReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference_id: str
    reference_hash: str
    technology: str
    projection_year: int
    currency: str
    currency_year: int
    price_basis: str
    capacity_basis: str
    capacity_basis_code: CapacityBasisCode
    includes_grid_connection: GridConnection
    applicability: str
    included_costs: str
    unmapped_terms: list[str]
    source_url: str
    source_hash: str
    source_sheet: str
    source_cells: dict[str, dict[str, Any]]
    licence: str
    attribution: str
    cost_part: CostPart


def _catalogue() -> dict:
    raw = _DATA_FILE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != _EXPECTED_SHA256:
        raise DefaultsPackError("European technology reference bytes differ from the pinned release")
    return json.loads(raw)


def available_europe_technology_references() -> tuple[tuple[str, int], ...]:
    """Explicitly available selectors; no invented/interpolated years."""
    return tuple(sorted((r["technology"], r["projection_year"]) for r in _catalogue()["entries"]))


def load_europe_technology_reference(technology: str, projection_year: int) -> EuropeanTechnologyReference:
    """Return a fresh source-backed native cost part for one explicit selection.

    This is a Danish representative benchmark, not a local EPC/OEM quote or a
    complete project O&M model. Consumers must review the accompanying scope.
    Existing project approvals govern application; this function never applies.
    """
    if not isinstance(technology, str) or type(projection_year) is not int:
        raise DefaultsPackError("Select an explicit technology string and integer projection year")
    data = _catalogue()
    row = next((r for r in data["entries"] if (r["technology"], r["projection_year"]) ==
                (technology, projection_year)), None)
    if row is None:
        raise DefaultsPackError(f"No European technology reference for {technology!r}, {projection_year}")
    cells, source = row["cells"], data["sources"][row["source"]]
    capex_factor = 1000.0 if row["source"] == "fuels" else 1_000_000.0
    capex = cells["overnight"]["value"] * capex_factor
    if capex <= 0 or not math.isfinite(capex):
        raise DefaultsPackError("Invalid benchmark investment denominator")
    scope = f"{row['capacity_basis']}; {row['applicability']}; {row['included_costs']}"

    def value(parameter: str, unit: str, original_unit: str, factor: float, domain: str) -> PackValue:
        cell = cells[parameter]
        original = float(cell["value"])
        normalized = original * factor
        if not math.isfinite(normalized) or normalized < 0:
            raise DefaultsPackError(f"Invalid European reference {parameter}")
        if parameter in ("efficiency", "fom_share") and normalized > 1:
            raise DefaultsPackError(f"European reference {parameter} exceeds one")
        if parameter == "lifetime" and not 1 <= normalized <= 200:
            raise DefaultsPackError("European reference plant lifetime outside admitted range")
        note = scope
        if parameter == "fom_share" and row["source"] == "power":
            note += "; derived as source absolute annual FOM / same-year total investment in EUR/MW"
        return PackValue(
            key=f"{technology}.investment.{parameter}", label=cell["label"],
            technology=technology, part="investment", basis="per_MW",
            source_technology=row["sheet"], parameter=parameter,
            value=normalized, unit=unit, original_value=original,
            original_unit=original_unit, conversion_factor=factor,
            currency="EUR" if parameter == "overnight" else None,
            currency_year=row["currency_year"] if parameter == "overnight" else None,
            price_basis="real", source=f"Danish Energy Agency / Energinet; {row['sheet']}!{cell['cell']}",
            source_year=2026, source_url=source["url"], projection_year=projection_year,
            domain=domain, illustrative=False, note=note,
        )

    native = {
        "overnight": value("overnight", "EUR/MW", "EUR/kW_e" if row["source"] == "fuels"
                           else "MEUR/MW_e", capex_factor, "[0, inf)"),
        "fom_share": value("fom_share", "share/year", "%/year" if row["source"] == "fuels"
                           else "EUR/MW_e/year", 0.01 if row["source"] == "fuels"
                           else 1.0 / capex, "[0, 1]"),
        "lifetime": value("lifetime", "years", "years", 1.0, "[1, inf)"),
    }
    if "efficiency" in cells:
        native["efficiency"] = value("efficiency", "per unit", "% LHV per electrical input", 0.01, "(0, 1]")
    return EuropeanTechnologyReference(
        reference_id=data["reference_id"], reference_hash=_EXPECTED_SHA256,
        **{key: row[key] for key in ("technology", "projection_year", "currency", "currency_year",
                                   "price_basis", "capacity_basis", "capacity_basis_code",
                                   "includes_grid_connection", "applicability", "included_costs",
                                   "unmapped_terms")},
        source_url=source["url"], source_hash=source["sha256"],
        source_sheet=row["sheet"], source_cells=cells,
        licence=data["licence"], attribution=data["attribution"],
        cost_part=CostPart(technology=technology, part="investment", basis="per_MW",
                           source_technology=row["sheet"], **native),
    )


# Pack technology a reference stands beside when it is not the same key. The pack's
# `electrolysis` row is technology-data's AEC; PEM, commercial rooftop PV and the
# rest have no pack counterpart and are compared against nothing.
_PACK_COUNTERPART = {"electrolysis-aec-100mw": "electrolysis"}


class ReferenceComparison(BaseModel):
    """One parameter of a reference beside the value a pack would otherwise give.

    Both sides keep their own source and money year. Nothing is converted, chosen
    or written; `differs` only tells a preview to show both."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter: str
    reference: PackValue
    pack_stamp: str
    pack: PackValue | None
    differs: bool


def compare_europe_reference_with_pack(reference: EuropeanTechnologyReference,
                                       pack) -> list[ReferenceComparison]:
    """Show `reference` beside `pack` (a `DefaultsPack`, e.g. `load_defaults_pack()`).

    Matching is by the pack's own key for the reference technology (or its listed
    counterpart). The pack is only read. A pack value differs when its value, unit,
    currency or money year is not the reference's."""
    technology = _PACK_COUNTERPART.get(reference.technology, reference.technology)
    by_key = {v.key: v for v in pack.cost_values}
    rows = []
    for parameter in ("overnight", "fom_share", "lifetime", "efficiency"):
        ref_value = getattr(reference.cost_part, parameter)
        if ref_value is None:
            continue
        pack_value = by_key.get(f"{technology}.investment.{parameter}")
        differs = pack_value is not None and (
            not math.isclose(pack_value.value, ref_value.value, rel_tol=1e-9)
            or (pack_value.unit, pack_value.currency, pack_value.currency_year)
            != (ref_value.unit, ref_value.currency, ref_value.currency_year))
        rows.append(ReferenceComparison(parameter=parameter, reference=ref_value,
                                        pack_stamp=pack.stamp, pack=pack_value, differs=differs))
    return rows
