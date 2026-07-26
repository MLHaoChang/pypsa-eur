from sqlalchemy import inspect

from db.models import Organization, Project, User


def test_project_has_parent_fk() -> None:
    assert User.__table__.name == "users"
    assert Organization.__table__.name == "organizations"
    assert "parent_project_id" in Project.__table__.columns
    assert "org_id" in Project.__table__.columns


def test_models_create_tables(db_engine) -> None:
    table_names = set(inspect(db_engine).get_table_names())
    assert {"organizations", "projects", "users"}.issubset(table_names)
