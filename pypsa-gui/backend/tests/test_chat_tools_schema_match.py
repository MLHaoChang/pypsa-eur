"""
Phase 1 — v6-F3 schema-match audit.

For every chat tool whose underlying handler returns a Pydantic model, this
test verifies the tool's `description` lists the actual field names from
``models/schemas.py`` — so the LLM is never told a returned key exists when
the live API never emits it (v5-MINOR-F3 root cause).

Produces ``backend/tests/fixtures/tool_schema_audit_phase1.csv`` with one
row per (tool_name, claimed_field, actual_field, match). The test FAILS if
any row has `match=False`.

Audit table:

    Tool name              → Pydantic model (file:line)
    -------------------------------------------------------
    load_project           → ImportSummary (schemas.py:455-464)
    import_network_nc      → ImportSummary
    import_csv_bundle      → ImportSummary
    import_excel           → ImportSummary
    import_matpower        → ImportSummary
    list_projects          → ProjectInfo (schemas.py:467-491)
    list_scenarios         → ProjectInfo

Not audited (no Pydantic return — frozen JSON dict, or out of scope for
Phase 1's contract):
  * create_scenario / rename_project / delete_project / save_project —
    these return ad-hoc dicts assembled in the route handler.
  * Project snapshots — SnapshotInfo not present in schemas.py (Phase 1+ add).
"""
from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass

from pydantic import BaseModel

from models import schemas as model_schemas
from services.chat_tools_schema import TOOLS


# (tool_name, Pydantic model class) — auditable returns only.
AUDIT_TABLE: list[tuple[str, type[BaseModel]]] = [
    ("load_project",        model_schemas.ImportSummary),
    ("import_network_nc",   model_schemas.ImportSummary),
    ("import_csv_bundle",   model_schemas.ImportSummary),
    ("import_excel",        model_schemas.ImportSummary),
    ("import_matpower",     model_schemas.ImportSummary),
    ("list_projects",       model_schemas.ProjectInfo),
    ("list_scenarios",      model_schemas.ProjectInfo),
]


CSV_OUT = (
    pathlib.Path(__file__).resolve().parent
    / "fixtures" / "tool_schema_audit_phase1.csv"
)


@dataclass
class AuditRow:
    tool_name: str
    pydantic_model: str
    claimed_field: str
    actual_field: str
    match: bool


def _tool_description(name: str) -> str:
    for t in TOOLS:
        if t["name"] == name:
            return t["description"]
    raise AssertionError(f"tool {name!r} not in TOOLS")


def _audit_rows() -> list[AuditRow]:
    """For each (tool, model), one row per field expected/found."""
    rows: list[AuditRow] = []
    for tool_name, model_cls in AUDIT_TABLE:
        desc = _tool_description(tool_name)
        actual_fields = list(model_cls.model_fields.keys())
        for field_name in actual_fields:
            # v6-F3: the tool description MUST mention each actual field name
            # verbatim. Allow surrounding punctuation (e.g. "buses,",
            # "{buses}").
            match = field_name in desc
            rows.append(AuditRow(
                tool_name=tool_name,
                pydantic_model=model_cls.__name__,
                claimed_field=field_name,
                actual_field=field_name,
                match=match,
            ))
    return rows


def _write_csv(rows: list[AuditRow]) -> None:
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    with CSV_OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "tool_name", "pydantic_model", "claimed_field",
            "actual_field", "match",
        ])
        for r in rows:
            w.writerow([
                r.tool_name, r.pydantic_model, r.claimed_field,
                r.actual_field, "True" if r.match else "False",
            ])


def test_schema_match_audit_produces_csv():
    rows = _audit_rows()
    _write_csv(rows)
    assert CSV_OUT.exists()


def test_zero_false_rows_in_schema_audit():
    """
    v6-F3 hard constraint: every Pydantic-return tool's description must
    list every actual field name. Drift here means the LLM will be told a
    key exists that the live API never emits.
    """
    rows = _audit_rows()
    failures = [r for r in rows if not r.match]
    if failures:
        _write_csv(rows)
        msg_lines = ["v6-F3 schema drift detected:"]
        for r in failures:
            msg_lines.append(
                f"  {r.tool_name} → {r.pydantic_model}: missing field "
                f"{r.claimed_field!r} in description"
            )
        msg_lines.append(f"\nFull audit CSV: {CSV_OUT}")
        raise AssertionError("\n".join(msg_lines))


# ── v6-F3 known-claim sanity checks ────────────────────────────────────────


def test_load_project_description_matches_import_summary_literally():
    """
    Explicit claim — load_project description MUST literally enumerate the
    9 ImportSummary fields per schemas.py:455-464. Drift = LLM is told a
    wrong shape.
    """
    desc = _tool_description("load_project")
    for f in ("buses", "generators", "lines", "links", "storage_units",
              "stores", "loads", "transformers", "snapshots"):
        assert f in desc, (
            f"load_project description missing ImportSummary field {f!r}"
        )
    # ImportSummary model name itself should appear so the LLM has a stable handle.
    assert "ImportSummary" in desc


def test_list_projects_description_matches_project_info_literally():
    """
    list_projects description must enumerate ProjectInfo fields per
    schemas.py:467-491.
    """
    desc = _tool_description("list_projects")
    required_fields = ("name", "created_at", "has_solver_config",
                       "bus_count", "snapshot_count", "objective",
                       "has_orphan_tmp", "parent_project")
    for f in required_fields:
        assert f in desc, (
            f"list_projects description missing ProjectInfo field {f!r}"
        )
    assert "ProjectInfo" in desc


def test_import_tools_describe_import_summary():
    """All 4 import_* tools return ImportSummary — explicit string check."""
    for tool_name in ("import_network_nc", "import_csv_bundle",
                       "import_excel", "import_matpower"):
        desc = _tool_description(tool_name)
        assert "ImportSummary" in desc, (
            f"{tool_name} description missing ImportSummary handle"
        )


# ── Cross-check: AUDIT_TABLE is in sync with TOOLS ─────────────────────────


def test_audit_table_tools_all_exist():
    """Every audited tool name appears in TOOLS (no stale audit entries)."""
    tool_names = {t["name"] for t in TOOLS}
    for tool_name, _ in AUDIT_TABLE:
        assert tool_name in tool_names, (
            f"AUDIT_TABLE references non-existent tool {tool_name!r}"
        )
