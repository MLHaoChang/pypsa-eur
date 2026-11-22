"""
`SolverConfig` (the persisted dataclass) and `SolverConfigSchema` (the API
boundary) name the same fields (Edge Investment Case P1 WP1.3).

A field on the dataclass the schema lacks cannot be set over HTTP; a field on
the schema the dataclass lacks is accepted by the route and then dropped by
`SolverConfig(**merged)` with a TypeError — a 500 on a valid-looking request.
"""

from __future__ import annotations

from dataclasses import fields

from models.schemas import SolverConfigSchema
from services.solver_service import SolverConfig


# Dataclass fields that are DELIBERATELY absent from the API boundary: the EH
# energy import cap is set only by a weak_flexible pack's overlay, never a user
# global (EH P17; `SolverConfig` comment and projects._solver_config_from_dict,
# which strips them on load). A new entry needs the same kind of reason.
PACK_ONLY = {"import_energy_cap_mwh_per_year", "import_energy_links"}
# Written only by a template at creation (the record of its shipped finance
# inputs, WALK-IMPROVE-1 b), never a user setting: a settings PUT merges over
# the stored config, so the record survives it, and a client cannot forge it
# (test_template_finance_defaults: a settings PUT keeps the record).
TEMPLATE_ONLY = {"finance_assumed"}


def test_dataclass_and_schema_name_the_same_fields():
    dc = {f.name for f in fields(SolverConfig)} - PACK_ONLY - TEMPLATE_ONLY
    schema = set(SolverConfigSchema.model_fields)
    assert dc - schema == set(), f"dataclass-only: {sorted(dc - schema)}"
    assert schema - dc == set(), f"schema-only: {sorted(schema - dc)}"


def test_the_pack_only_fields_are_on_the_dataclass_and_off_the_schema():
    dc = {f.name for f in fields(SolverConfig)}
    assert PACK_ONLY | TEMPLATE_ONLY <= dc
    assert not (PACK_ONLY | TEMPLATE_ONLY) & set(SolverConfigSchema.model_fields)
