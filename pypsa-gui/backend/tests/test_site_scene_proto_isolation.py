"""
SC-00 N "disabled from production" (execution-guide.md, Contract freeze) and
the D02 route table against the current app.

No router, service, harness, catalogue or app module imports the prototype, and
none of the frozen SC-01 scene routes is mounted yet or collides with a
mounted one.
"""
from __future__ import annotations

import pathlib
import re

from tests.site_scene_proto import contract

BACKEND = pathlib.Path(__file__).resolve().parents[1]
PRODUCTION = ("routers", "services", "harness", "smoke", "tools", "db", "models")


def test_no_production_module_imports_the_prototype():
    offenders = []
    files = [p for d in PRODUCTION for p in (BACKEND / d).rglob("*.py")]
    files += [BACKEND / "main.py", BACKEND / "deps.py", BACKEND / "mcp_stdio.py"]
    for path in files:
        if "site_scene_proto" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(path.relative_to(BACKEND).as_posix())
    assert len(files) > 100 and offenders == []


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_frozen_routes_are_not_mounted_and_do_not_collide():
    from main import app

    paths = app.openapi()["paths"]
    mounted = {(m.upper(), _shape(path)) for path, ops in paths.items() for m in ops}
    for route in contract.ROUTES:
        assert (route["method"], _shape(route["path"])) not in mounted, route
        assert route["chat"] == "none"
    # The legacy sites routes the guard wraps are the ones that exist today.
    assert ("GET", "/api/projects/{}/sites") in mounted and ("PUT", "/api/projects/{}/sites") in mounted


def test_every_error_kind_has_one_status_and_unclassified_kinds_are_refused():
    import pytest

    assert set(contract.ERROR_STATUS) >= {"invalid_geometry", "revision_conflict", "scene_managed_site",
                                          "source_unavailable", "worker_unavailable", "resource_limit",
                                          "proposal_expired", "proposal_cancelled", "integrity_error",
                                          "not_found", "forbidden"}
    e = contract.SceneError("revision_conflict", "reload", {"current_revision": 3})
    assert e.status == 409
    assert e.to_body() == {"detail": {"error_kind": "revision_conflict", "message": "reload", "current_revision": 3}}
    with pytest.raises(ValueError):
        contract.SceneError("scene_oops", "x")


def test_the_numeric_null_contract():
    # V57: unavailable is null with a reason; a resolved zero stays zero.
    import pytest

    assert contract.quantity(None, "t/h", "conversion_factor_missing") == {
        "value": None, "available": False, "reason": "conversion_factor_missing", "unit": "t/h"}
    assert contract.quantity(0, "MW") == {"value": 0, "available": True, "reason": None, "unit": "MW"}
    with pytest.raises(ValueError):
        contract.quantity(None, "MW")
    with pytest.raises(ValueError):
        contract.quantity(0, "MW", "conversion_factor_missing")
