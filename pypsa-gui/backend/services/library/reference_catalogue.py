"""The shipped (`common`) cost references as Library entries: list them, and show one
beside the pack value an asset would otherwise be priced from.

Read-only. The references are `europe_reference`'s pinned, attributed benchmarks; the
pack is chosen per asset and named in every answer. Nothing here writes a project,
a pack or a Library item; applying a reference to an asset is a separate, previewed
edit.
"""
from __future__ import annotations

import inspect

from typing import Literal

from pydantic import BaseModel, ConfigDict

from services.library import europe_reference as R
from services.library.defaults_pack import loader
from services.library.defaults_pack.loader import (
    DefaultsPack,
    DefaultsPackError,
    DefaultsPackLookupError,
    available_versions,
    load_defaults_pack,
)

SCOPE = "common"

_HEX = frozenset("0123456789abcdef")


def _parse_pack_source(source: str) -> tuple[str, str, str] | None:
    """`(pack id, version, hash16)` from the `<pack id>@<version>#<hash16>` stamp
    `price_from_pack` writes, else None. Plain string splitting on a capped length:
    the source can arrive from a request (`pack_source`)."""
    if not isinstance(source, str) or len(source) > 200:
        return None
    head, sep, digest = source.rpartition("#")
    pack_id, at, version = head.partition("@")
    if not sep or not at or len(digest) != 16 or not set(digest) <= _HEX:
        return None
    if not pack_id or not version or any(c.isspace() or c in "@#" for c in pack_id + version):
        return None
    return pack_id, version, digest


class ReferenceSummary(BaseModel):
    """One list row: enough to pick a reference without loading its cells."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["common"]
    reference_id: str
    technology: str
    projection_year: int
    currency: str
    currency_year: int
    capacity_basis: str
    capacity_basis_code: str
    includes_grid_connection: bool | Literal["unknown"]
    applicability: str
    publisher: str
    licence: str
    attribution: str
    source_url: str


class PackChoice(BaseModel):
    """Which pack a comparison stands beside, and why that one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stamp: str
    version: str
    chosen_by: Literal["asset_provenance", "latest_generic"]
    note: str | None = None


class ReferenceDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: Literal["common"]
    reference: R.EuropeanTechnologyReference
    pack: PackChoice
    comparison: list[R.ReferenceComparison]


def list_references() -> list[ReferenceSummary]:
    """Every shipped reference, in the reader's selector order."""
    rows = []
    for technology, year in R.available_europe_technology_references():
        ref = R.load_europe_technology_reference(technology, year)
        rows.append(ReferenceSummary(
            scope=SCOPE, reference_id=ref.reference_id, technology=ref.technology,
            projection_year=ref.projection_year, currency=ref.currency,
            currency_year=ref.currency_year, capacity_basis=ref.capacity_basis,
            capacity_basis_code=ref.capacity_basis_code,
            includes_grid_connection=ref.includes_grid_connection,
            applicability=ref.applicability, publisher="Danish Energy Agency and Energinet",
            licence=ref.licence, attribution=ref.attribution, source_url=ref.source_url))
    return rows


def _load_named_pack(version: str) -> DefaultsPack | None:
    """The pack a provenance stamp names, or None when this build cannot load it.

    No caller value reaches the loader (it may come from a request): a plain version is
    the matching entry of `available_versions()`; a regional `<date>+<region>` version
    (GS-2, #499) needs the loader's `region=`, takes the region from the loader's own
    list and the date rebuilt from integers. A build without regional packs cannot load
    one and says so (None)."""
    date, plus, region = version.partition("+")
    try:
        if not plus:
            shipped = next((v for v in available_versions() if v == version), None)
            return None if shipped is None else load_defaults_pack(shipped)
        listed = getattr(loader, "available_regions", None)
        if listed is None or "region" not in inspect.signature(load_defaults_pack).parameters:
            return None
        names = [getattr(r, "region", r) for r in listed()]
        shipped_region = next((r for r in names if isinstance(r, str) and r == region), None)
        parts = date.split("-")
        if shipped_region is None or len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None
        year, month, day = (int(p) for p in parts)
        return load_defaults_pack(f"{year:04d}-{month:02d}-{day:02d}", region=shipped_region)
    except (DefaultsPackError, DefaultsPackLookupError):
        return None


def pack_for_asset(provenance: dict[str, dict] | None) -> tuple[DefaultsPack, PackChoice]:
    """The pack an asset would be priced from: the one its `overnight_cost` provenance
    names, else the latest generic version. A named pack this build cannot load falls
    back to the latest generic, and the note says so."""
    # After a reference was applied, the pack it replaced is what the asset "would
    # otherwise get" (`replaces`), not the reference itself.
    entry = (provenance or {}).get("overnight_cost") or {}
    source = entry.get("replaces") or entry.get("source") or ""
    m = _parse_pack_source(source)
    if m is not None:
        pack_id, version, digest = m
        pack = _load_named_pack(version)
        if pack is not None and pack.pack_id == pack_id and pack.hash.startswith(digest):
            return pack, PackChoice(stamp=pack.stamp, version=pack.version,
                                    chosen_by="asset_provenance")
        note = f"the asset's pack {source!r} is not available here; compared with the latest generic pack"
    else:
        note = None
    pack = load_defaults_pack()
    return pack, PackChoice(stamp=pack.stamp, version=pack.version,
                            chosen_by="latest_generic", note=note)


def reference_detail(technology: str, projection_year: int,
                     provenance: dict[str, dict] | None = None) -> ReferenceDetail:
    """One reference beside the pack value it would otherwise be; raises `DefaultsPackError`
    for a selector the reader does not hold."""
    ref = R.load_europe_technology_reference(technology, projection_year)
    pack, choice = pack_for_asset(provenance)
    return ReferenceDetail(scope=SCOPE, reference=ref, pack=choice,
                           comparison=R.compare_europe_reference_with_pack(ref, pack))
