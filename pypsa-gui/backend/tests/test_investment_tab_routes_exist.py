"""
Every route the Investment tab's API clients call exists in the backend
(UX assessment Q2): on master the tab called `GET /results/value_flows`
before the route landed and every load raised two 503 "Frontend not built"
toasts (the SPA fallback answering an unknown `/api` path).

The clients are scanned, not listed by hand, so a new call without its route
fails here instead of in the browser.
"""
from __future__ import annotations

import pathlib
import re

import main

_FRONTEND_API = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src" / "api"
_CLIENTS = ("commercial.ts", "finance.ts")
# client.get<T>('/x/y' …) / client.put(`/x/${k}` …) — the method and the path literal.
_CALL = re.compile(r"client\.(get|post|put|delete|patch)(?:<[^>]*>)?\(\s*(['`])(/[^'`]*)\2")
# A path built on the base URL without a method (an <a href> download link).
_BASE_URL = re.compile(r"baseURL \?\? ''\}(/[^`]*)`")


def _calls() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for name in _CLIENTS:
        text = (_FRONTEND_API / name).read_text()
        out |= {(m.group(1).upper(), m.group(3)) for m in _CALL.finditer(text)}
        out |= {("GET", m.group(1)) for m in _BASE_URL.finditer(text)}
    return out


def _route_patterns() -> list[tuple[set[str], re.Pattern]]:
    """(methods, path regex) of every /api route, from the app's OpenAPI schema
    (the routers are mounted through wrappers, so `app.routes` is not flat)."""
    pats = []
    for path, ops in main.app.openapi()["paths"].items():
        if not path.startswith("/api/"):
            continue
        rx = re.sub(r"\\\{[^}]+\\\}", "[^/]+", re.escape(path))
        pats.append(({m.upper() for m in ops}, re.compile(f"^{rx}$")))
    return pats


def test_the_scan_finds_the_investment_tab_calls():
    calls = _calls()
    assert ("GET", "/results/value_flows") in calls
    assert ("POST", "/results/investment_case") in calls
    assert ("GET", "/results/investment_case/export.xlsx") in calls
    assert len(calls) >= 20


def test_every_investment_tab_call_has_a_route():
    pats = _route_patterns()
    missing = []
    for method, path in sorted(_calls()):
        concrete = "/api" + re.sub(r"\$\{[^}]+\}", "x", path.split("?")[0])
        if not any(method in ms and rx.match(concrete) for ms, rx in pats):
            missing.append(f"{method} {path}")
    assert not missing, f"Investment-tab calls with no backend route: {missing}"
