"""
Business-case presets: pre-filled lines per template (BC-ALL slice 07).

Spec: .scratch/business-case/spec.md §4 ("A preset is just pre-filled lines"), §6.1, §6.2;
GK B plan GO for slice 07 (P1–P12, 2026-11-12).

A preset is a versioned data file `project_templates/business_case_presets/<id>.json`:
rows with every column the user would type, revenue prices empty (a `hint` beside them,
never a value), cost columns named in `default_keys` with their pack unit in
`default_units`, and the revenue rows' `preset_rule`. `preset()` copies the rows and fills
the cost defaults from the pack the file pins, marked `assumed`; nothing about a preset is
special at run time except its `preset_rule`. `offered_rows()` lists the "add line"
picker's one-click starts (§6.1).

Files are read on call, never at import, so a broken file cannot stop the app (P7).
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from models.business_case import BusinessCaseInputs, LineItem, Unit

if TYPE_CHECKING:
    from services.library.defaults_pack.loader import DefaultsPack

PRESETS_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "project_templates"
    / "business_case_presets"
)
#: The version each preset file must carry; a bump is a new file version (G-C6 hashes
#: `preset_id@version`).
VERSIONS: dict[str, str] = {"eh_datacenter": "1", "eh_site": "1"}

_FORBID = ConfigDict(extra="forbid", allow_inf_nan=False)
#: `<pack id>@<version>:sha256:<hex>`, the defaults pack's stamp.
_STAMP = re.compile(r"^[a-z_]+@(?P<version>[0-9A-Za-z_.+\-]+):sha256:[0-9a-f]{64}$")


class PresetInvalid(ValueError):
    """A preset file that does not match its schema, name, version or pack pin."""


class Variant(BaseModel):
    model_config = _FORBID

    label: str = Field(min_length=1)
    lines: list[LineItem] = Field(min_length=1)


class OfferedRow(BaseModel):
    """One "add line" start (§6.1): the line to copy, the units and quantity sources the
    picker may switch it to."""

    model_config = _FORBID

    label: str = Field(min_length=1)
    units: list[Unit] = Field(min_length=1)
    sources: list[
        Literal["typed", "linked:capacity", "share_of:capex", "share_of:revenue"]
    ]
    line: LineItem


class PresetFile(BaseModel):
    model_config = _FORBID

    id: str
    version: str
    pack: str = Field(min_length=1)  # the pinned pack's stamp: id, version and hash
    templates: list[str] = Field(min_length=1)
    variants: dict[str, Variant]
    offered: list[OfferedRow]


def _files(directory: pathlib.Path) -> list[pathlib.Path]:
    return sorted(directory.glob("*.json"))


def load(path: pathlib.Path) -> PresetFile:
    """One preset file, checked: schema, its name, its version and its pack pin (P7)."""
    try:
        f = PresetFile.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise PresetInvalid(f"{path.name}: {exc}") from exc
    if f.id != path.stem:
        raise PresetInvalid(f"{path.name}: id '{f.id}' is not the file name")
    if VERSIONS.get(f.id) != f.version:
        raise PresetInvalid(
            f"{path.name}: version '{f.version}' is not '{VERSIONS.get(f.id)}'"
        )
    for off in f.offered:
        if off.line.unit not in off.units:
            raise PresetInvalid(
                f"{path.name}: '{off.label}' line unit not in its units"
            )
        if off.line.quantity_source not in off.sources:
            raise PresetInvalid(
                f"{path.name}: '{off.label}' line source not in its sources"
            )
    return f


def _for_template(template_id: str, directory: pathlib.Path) -> PresetFile:
    found = [f for f in map(load, _files(directory)) if template_id in f.templates]
    if len(found) != 1:
        raise LookupError(
            f"business-case preset for '{template_id}': {len(found)} files"
        )
    return found[0]


def _pack(preset_file: PresetFile) -> DefaultsPack:
    """The pack the file pins, byte-equal by its stamp (P8)."""
    from services.library.defaults_pack.loader import load_defaults_pack

    f = preset_file
    m = _STAMP.match(f.pack)
    if m is None:
        raise PresetInvalid(f"{f.id}: pack '{f.pack}' is not a pack stamp")
    pack = load_defaults_pack(m["version"])
    if pack.stamp != f.pack:
        raise PresetInvalid(f"{f.id}: pack '{f.pack}' is now '{pack.stamp}'")
    return pack


def variants(
    template_id: str, *, directory: pathlib.Path = PRESETS_DIR
) -> dict[str, str]:
    """{variant: label} the template offers; empty for a blank table (`eh_site`)."""
    f = _for_template(template_id, directory)
    return {k: v.label for k, v in f.variants.items()}


def preset(
    template_id: str,
    variant: str | None = None,
    *,
    currency: str,
    directory: pathlib.Path = PRESETS_DIR,
) -> BusinessCaseInputs:
    """The template's pre-filled lines, cost defaults from the pinned pack as `assumed`.

    A template without variants (`eh_site`) is the blank table: no preset, no lines. A
    default in another currency is never filled (the run then refuses
    `cost_not_established`). A pack row in another unit than the row names raises
    `DefaultUnitMismatch` here; the pinned stamp check makes that a changed pack file."""
    f = _for_template(template_id, directory)
    if variant is None and not f.variants:
        return BusinessCaseInputs()
    if variant not in f.variants:
        raise LookupError(f"'{template_id}' has no business-case preset '{variant}'")
    case = BusinessCaseInputs(
        preset=f"{f.id}.{variant}@{f.version}",
        lines=[ln.model_copy(deep=True) for ln in f.variants[variant].lines],
    )
    return case.with_cost_defaults(_pack(f), currency=currency)


def offered_rows(
    template_id: str, *, directory: pathlib.Path = PRESETS_DIR
) -> list[OfferedRow]:
    """The "add line" picker's one-click starts (§6.1); copies, so an edit stays local."""
    return [
        o.model_copy(deep=True) for o in _for_template(template_id, directory).offered
    ]
