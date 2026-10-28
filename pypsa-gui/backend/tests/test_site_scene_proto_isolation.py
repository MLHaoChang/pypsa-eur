"""
SC-00 N isolation, as SC-01a left it, and the D02 route table against the app.

SC-01a promoted the frozen contract modules to `services/site_scene/`; the
prototype package keeps aliases so its tests run against production code. No
production module imports the prototype, and every frozen SC-01 scene route is
mounted exactly once, with chat disposition "none".
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


def test_frozen_routes_are_mounted_exactly_once():
    from main import app

    paths = app.openapi()["paths"]
    mounted = [(m.upper(), _shape(path)) for path, ops in paths.items() for m in ops]
    for route in contract.ROUTES:
        assert mounted.count((route["method"], _shape(route["path"]))) == 1, route
        assert route["chat"] == "none"
    # The deferred rows (sources: SC-06; exports/artifacts: SC-04) stay unmounted.
    assert not any("/scene/sources" in p or "/scene/exports" in p or "/scene/artifacts" in p for p in paths)
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
