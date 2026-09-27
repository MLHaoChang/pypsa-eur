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


def test_dataclass_and_schema_name_the_same_fields():
    dc = {f.name for f in fields(SolverConfig)}
    schema = set(SolverConfigSchema.model_fields)
    assert dc - schema == set(), f"dataclass-only: {sorted(dc - schema)}"
    assert schema - dc == set(), f"schema-only: {sorted(schema - dc)}"
