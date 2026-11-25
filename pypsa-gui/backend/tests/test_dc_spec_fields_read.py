"""DC 05: no spec field is accepted and then ignored (issue 05 AC3, GK A W8).

Every field of the data-centre load spec and its sub-specs must be read by code
outside the model definitions (an attribute read in a non-test backend module), or sit in ``READ_ELSEWHERE`` with the reason it
is fine. A new field nothing reads fails here."""

from __future__ import annotations

import pathlib
import re


from models import dc_compute_spec as C
from models import flex_archetypes as F

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SPECS = [
    F.DataCentreLoadSpec,
    F.DcPhase,
    F.GensetSpec,
    F.AiWorkloadSpec,
    F.BackupSpec,
    C.WorkloadMixSpec,
    C.ComputeSpec,
]
#: Files that define or only validate the specs do not count as readers.
DEFINITIONS = {"models/flex_archetypes.py", "models/dc_compute_spec.py"}
#: field -> why it is accepted without a backend reader.
READ_ELSEWHERE: dict[str, str] = {
    # Found by this guard on its first run; accepted-but-unread today, outside DC 05's
    # scope, so listed here and reported in the PR as a follow-up issue .scratch/dc-template-review-2026-11-24/issues/05a-unread-dc-spec-fields.md (remove or model each).
    "DataCentreLoadSpec.workload_shift_window_h": "KNOWN UNREAD: documented in dc_it_energy as moving no load (DC 06); follow-up: issue 05a",
    "GensetSpec.power_mw": "KNOWN UNREAD on GensetSpec: the fleet size comes from the template, the spec value is only a constructor placeholder; follow-up: issue 05a",
    "GensetSpec.fuel_price_ref": "KNOWN UNREAD on GensetSpec: the fuel price is the backup block's fuel_price_per_mwh_fuel; follow-up: issue 05a",
}


def _sources() -> dict[str, str]:
    out = {}
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", ".")) or "/tests/" in rel or rel in DEFINITIONS:
            continue
        if any(part in {"node_modules", ".pixi", "__pycache__"} for part in path.parts):
            continue
        out[rel] = path.read_text(encoding="utf-8", errors="ignore")
    return out


def _fields():
    for model in SPECS:
        for name in model.model_fields:
            yield model.__name__, name


def _read(name: str, sources: dict[str, str]) -> bool:
    # An attribute read or a getattr on the spec only: a bare quoted name (a ledger
    # label, a SOURCES key) is not a read.
    pat = re.compile(rf"(\.{name}\b|getattr\([^)]*[\"']{name}[\"'])")
    return any(pat.search(text) for text in sources.values())


def test_every_spec_field_is_read_by_some_code():
    sources = _sources()
    unread = [
        f"{m}.{n}"
        for m, n in _fields()
        if f"{m}.{n}" not in READ_ELSEWHERE and not _read(n, sources)
    ]
    assert not unread, f"accepted but read by no code: {unread}"


def test_a_field_nothing_reads_is_caught():
    sources = _sources()
    assert not _read("zz_no_such_field_zz", sources)


def test_waste_heat_export_is_not_a_field():
    assert "waste_heat_export" not in F.DataCentreLoadSpec.model_fields


def test_allowlist_entries_name_real_fields_and_carry_a_reason():
    names = {f"{m}.{n}" for m, n in _fields()}
    for name, why in READ_ELSEWHERE.items():
        assert name in names and len(why) > 20
