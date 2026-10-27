"""Regenerate/check the GUI-route to typed-tool parity matrix (live-voice issue 08).

Run from any directory: python <this-file> --write or --check.

Every backend route the GUI reaches (axios ``client`` calls, ``fetch``,
``EventSource``, ``sendBeacon`` and raw ``/api/`` URLs under
``pypsa-gui/frontend/src``) is classified exactly once:

* ``tool``: an assistant tool covers it, through a declared ``TOOL_ROUTES``
  HTTP entry or through a service function the tool handler and the route
  handler both call (direct callees, same-module helpers followed);
* ``needs-tool``: declared in the declarations file, a coverage gap;
* ``limitation``: declared with a reason from ``LIMITATION_REASONS``.

``--check`` fails closed: an unresolvable GUI binding, a binding matching no
route, more than one route or the wrong method, an unclassified GUI route, or a
stale declaration. The matrix is a source contract, not a behavioral test.

The app is imported, not served: ``PYPSAGUI_APP_DATA_DIR`` and
``PYPSAGUI_PROJECTS_ROOT`` point at a temporary directory first, no lifespan
runs, and routes are read from the router objects only.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / 'pypsa-gui/backend'
FRONTEND_SRC = ROOT / 'pypsa-gui/frontend/src'
DEFAULT_OUTPUT = ROOT / '.scratch/live-voice/route-parity.json'
DECLARATIONS = Path(__file__).with_name('assistant_route_parity_declarations.json')

LIMITATION_REASONS = (
    'account-auth',          # sign-in, session, CSRF, password: the user's own identity
    'admin',                 # user/workspace administration: an operator action
    'assistant-self-config', # the assistant's own settings, keys, profiles, MCP servers
    'conversation-transport',# the chat/voice stream itself, not an application capability
    'app-shell',             # health, changelog polling, local desktop settings
    'ui-local-state',        # layout/view persistence with no domain effect
    'binary-transfer',       # raw file bytes the assistant reaches via typed upload/export tools
)
MUTATING_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# ─── frontend bindings ──────────────────────────────────────────────────────

SCANNER = ROOT / 'pypsa-gui/frontend/scripts/api-bindings.mjs'


def scan_frontend(src_root: Path = FRONTEND_SRC) -> dict:
    """Run the TypeScript-checker scanner; it needs ``npm ci`` in pypsa-gui/frontend."""
    proc = subprocess.run(
        ['node', str(SCANNER), str(src_root)], capture_output=True, text=True, check=False, timeout=300,
    )
    if proc.returncode:
        raise RuntimeError(f'{SCANNER.name} failed (run npm ci in pypsa-gui/frontend?): {proc.stderr[-800:]}')
    return json.loads(proc.stdout)


def apply_overrides(scan: dict, overrides: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """Attribute declared call sites the scanner cannot resolve statically.

    An override names the file and either the start of the scanner's call text
    (unresolved calls) or the exact method and URL (a binding whose match is
    ambiguous), and lists the routes the call reaches; an empty list declares a non-request
    literal (a base URL, a prefix test). Returns (bindings, problems, ambiguity
    overrides, stale-override problems).
    """
    used, problems, extra, bindings = set(), [], [], []
    for b in scan['bindings']:
        hit = [i for i, o in enumerate(overrides)
               if o['file'] == b['file'] and o.get('url') == b['url'] and o.get('method') == b['method']]
        if not hit:
            bindings.append(b)
            continue
        used.update(hit)
        for method, path in overrides[hit[0]]['routes']:
            extra.append({'file': b['file'], 'kind': 'override', 'method': method, 'route': [method, path]})
    for p in scan['problems']:
        hit = [i for i, o in enumerate(overrides)
               if o['file'] == p['file'] and 'call' in o and p['call'].startswith(o['call'])]
        if not hit:
            problems.append(p)
            continue
        used.update(hit)
        for method, path in overrides[hit[0]]['routes']:
            extra.append({'file': p['file'], 'kind': 'override', 'method': method, 'route': [method, path]})
    stale = [
        f'stale binding override {o["file"]}: {o.get("call") or o.get("url")!r}: nothing matches it'
        for i, o in enumerate(overrides) if i not in used
    ]
    return bindings, problems, extra, stale


# ─── route matching ─────────────────────────────────────────────────────────


def _segments(path: str) -> list[str]:
    return [s for s in path.strip('/').split('/') if s]


def _segment_matches(gui: str, route: str, loose: bool = False) -> bool:
    if route.startswith('{') and route.endswith('}'):
        return True  # route parameter, includes {path:path} single segment
    if '{}' in gui:
        return loose and re.fullmatch(re.escape(gui).replace(r'\{\}', '[^/]*'), route) is not None
    return gui == route


def _path_matches(gui: list[str], path: str, loose: bool = False) -> bool:
    segs = _segments(path)
    if segs and segs[-1].endswith(':path}'):
        return len(gui) >= len(segs) and all(_segment_matches(g, r, loose) for g, r in zip(gui, segs[:-1]))
    return len(segs) == len(gui) and all(_segment_matches(g, r, loose) for g, r in zip(gui, segs))


def match_binding(binding: dict, routes: list[tuple[str, str]]) -> tuple[tuple[str, str] | None, str | None]:
    """Resolve like Starlette: routes in registration order, first full match wins.

    A literal GUI URL needs no guess. A URL with a GUI parameter that fits more
    than one distinct route path for its method is ambiguous and fails (V2).
    """
    gui = _segments(binding['url'])
    hits = [(m, p) for m, p in routes if _path_matches(gui, p)]
    if not hits:
        return None, 'no route matches'
    same = [h for h in hits if h[0] == binding['method']]
    if not same:
        return None, f'route exists for {sorted({h[0] for h in hits})} but GUI uses {binding["method"]}'
    # A GUI parameter could carry a value equal to an earlier route's literal
    # segment; that route would then serve the call instead (V2).
    chosen = routes.index(same[0])
    loose = sorted({
        p for m, p in routes[:chosen]
        if m == binding['method'] and _path_matches(gui, p, True)
        and any('{}' in g and not r.startswith('{') for g, r in zip(gui, _segments(p)))
    })
    if loose:
        return None, f'ambiguous: {same[0][1]} or {loose}'
    return same[0], None


# ─── backend routes, tools and service calls ────────────────────────────────


def _isolated_app():
    tmp = tempfile.mkdtemp(prefix='route-parity-')
    os.environ.setdefault('PYPSAGUI_APP_DATA_DIR', tmp)
    os.environ.setdefault('PYPSAGUI_PROJECTS_ROOT', str(Path(tmp) / 'projects'))
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    import main  # noqa: PLC0415 - import after isolation
    return main.app


def app_routes(app) -> dict[tuple[str, str], object]:
    """Flatten every HTTP route in dispatch order, including schema-hidden ones."""
    out: dict[tuple[str, str], object] = {}

    def walk(routes, prefix=''):
        for r in routes:
            effective = getattr(r, 'effective_route_contexts', None)
            if callable(effective):  # FastAPI >= 0.16x wraps included routers
                walk(effective())
                continue
            path, methods = getattr(r, 'path', None), getattr(r, 'methods', None)
            endpoint = getattr(r, 'endpoint', None)
            if methods and endpoint is not None and path is not None:
                for m in sorted(methods - {'HEAD', 'OPTIONS'}):
                    out.setdefault((m, prefix + path), endpoint)

    walk(app.routes)
    schema = {(m.upper(), p) for p, ops in app.openapi()['paths'].items() for m in ops}
    if missing := schema - out.keys():
        raise RuntimeError(f'route flattening missed {len(missing)} schema routes, e.g. {sorted(missing)[:3]}')
    return out


def _qualname(fn) -> str:
    return f'{fn.__module__}:{fn.__qualname__}'


def service_leaves(fn, depth: int = 3, _seen=None) -> set[str]:
    """Service functions ``fn`` calls directly, following same-module helpers."""
    seen = set() if _seen is None else _seen
    fn = inspect.unwrap(fn)
    if not callable(fn) or id(fn) in seen or depth < 0:
        return set()
    seen.add(id(fn))
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return set()
    home, leaves = getattr(fn, '__module__', ''), set()
    glb = dict(getattr(fn, '__globals__', {}))
    glb.update(_local_imports(tree, home))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _resolve_callee(node.func, glb)
        if target is None or not hasattr(target, '__module__'):
            continue
        mod = target.__module__ or ''
        name = _qualname(target) if hasattr(target, '__qualname__') else None
        if name is None:
            continue
        if mod == home:
            leaves |= service_leaves(target, depth - 1, seen)
        elif mod.startswith(('services.', 'harness.')):
            leaves.add(name)
    return leaves


def _local_imports(tree, home: str) -> dict:
    """Names bound by imports inside the function body (lazy service imports)."""
    names = {}
    for node in ast.walk(tree):
        try:
            if isinstance(node, ast.ImportFrom) and node.module:
                base = node.module
                if node.level:
                    base = importlib.util.resolve_name('.' * node.level + node.module, home.rpartition('.')[0])
                mod = importlib.import_module(base)
                for alias in node.names:
                    value = getattr(mod, alias.name, None)
                    if value is None:
                        value = importlib.import_module(f'{base}.{alias.name}')
                    names[alias.asname or alias.name] = value
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod = importlib.import_module(alias.name)
                    names[alias.asname or alias.name.split('.')[0]] = (
                        mod if alias.asname else importlib.import_module(alias.name.split('.')[0]))
        except ImportError:
            continue
    return names


def _resolve_callee(node, glb):
    chain = []
    while isinstance(node, ast.Attribute):
        chain.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in glb:
        return None
    obj = glb[node.id]
    for attr in reversed(chain):
        obj = inspect.getattr_static(obj, attr, None)
        if isinstance(obj, (staticmethod, classmethod)):
            obj = obj.__func__
        if obj is None:
            return None
    return obj if inspect.isfunction(obj) else None


# ─── matrix ─────────────────────────────────────────────────────────────────


def load_declarations(path: Path = DECLARATIONS) -> dict:
    return json.loads(path.read_text())


def build(app=None, declarations: dict | None = None, frontend: tuple | None = None) -> tuple[dict, list[str]]:
    """Return (matrix, problems)."""
    app = app or _isolated_app()
    decl = declarations if declarations is not None else load_declarations()
    from harness.catalogue import TOOL_ROUTES, safety_tier_for  # noqa: PLC0415
    from services.chat_tools import DISPATCHERS  # noqa: PLC0415

    routes = app_routes(app)
    scan = frontend if frontend is not None else scan_frontend()
    bindings, scan_problems, overridden, problems = apply_overrides(scan, decl.get('binding_overrides', []))
    problems += [f'binding {p["file"]}: {p["call"]!r}: {p["reason"]}' for p in scan_problems]
    route_set = set(routes)
    route_order = [k for k in routes if k[1].startswith('/api/')]  # the SPA catch-all serves no API

    gui_routes: dict[tuple[str, str], set[str]] = {}
    for b in bindings:
        hit, why = match_binding(b, route_order)
        if hit is None:
            problems.append(f'binding {b["file"]}: {b["method"]} {b["url"]}: {why}')
            continue
        gui_routes.setdefault(hit, set()).add(b['file'])
    for o in overridden:
        key = tuple(o['route'])
        if key not in route_set:
            problems.append(f'binding override {o["file"]}: route {key} does not exist')
            continue
        gui_routes.setdefault(key, set()).add(o['file'])

    non_mutating = {(d['method'], d['path']) for d in decl.get('non_mutating_routes', [])}
    allow = {d['service'] for d in decl.get('service_matches', [])}

    def tier_allows(tool: str, key: tuple[str, str]) -> bool:
        return not (safety_tier_for(tool) == 'read' and key[0] in MUTATING_METHODS and key not in non_mutating)

    by_http: dict[tuple[str, str], set[str]] = {}
    for tool, entries in TOOL_ROUTES.items():
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                by_http.setdefault((entry[0], entry[1]), set()).add(tool)
    tool_leaves = {
        t: (service_leaves(DISPATCHERS[t]) | {_qualname(inspect.unwrap(DISPATCHERS[t]))}) & allow
        for t, r in TOOL_ROUTES.items() if r == ['_service_call_'] and t in DISPATCHERS
    }

    limitations = {(d['method'], d['path']): d for d in decl.get('limitations', [])}
    needs = {(d['method'], d['path']): d for d in decl.get('needs_tool', [])}
    for key, d in limitations.items():
        if d.get('reason') not in LIMITATION_REASONS:
            problems.append(f'limitation {key}: reason {d.get("reason")!r} not in {LIMITATION_REASONS}')
    for key in limitations.keys() & needs.keys():
        problems.append(f'route {key} declared both needs-tool and limitation')
    for key in non_mutating - route_set:
        problems.append(f'stale non_mutating_routes entry {key}: route gone')

    used_services: set[str] = set()
    rows = []
    for key in sorted(gui_routes, key=lambda k: (k[1], k[0])):
        tools = {t for t in by_http.get(key, ()) if tier_allows(t, key)}
        via = 'tool_routes' if tools else None
        endpoint = routes.get(key)
        if not tools and endpoint is not None:
            leaves = service_leaves(endpoint) & allow
            tools = {t for t, tl in tool_leaves.items() if tl & leaves and tier_allows(t, key)}
            used_services |= {x for t in tools for x in tool_leaves[t] & leaves}
            via = 'service_call' if tools else None
        row = {'method': key[0], 'path': key[1], 'gui_files': sorted(gui_routes[key])}
        if tools:
            row |= {'class': 'tool', 'tools': sorted(tools), 'via': via}
            if key in limitations or key in needs:
                problems.append(f'stale declaration {key}: route is now tool-covered by {sorted(tools)}')
        elif key in needs:
            row |= {'class': 'needs-tool', 'family': needs[key].get('family')}
        elif key in limitations:
            row |= {'class': 'limitation', 'reason': limitations[key]['reason']}
        else:
            row |= {'class': 'unclassified'}
            problems.append(f'unclassified GUI route {key[0]} {key[1]}: add a tool or declare it')
        rows.append(row)
    for key in sorted((limitations.keys() | needs.keys()) - gui_routes.keys(), key=lambda k: (k[1], k[0])):
        why = 'route gone' if key not in route_set else 'no GUI binding reaches it'
        problems.append(f'stale declaration {key}: {why}')

    for service in sorted(allow - used_services):
        problems.append(f'stale service_matches entry {service}: no GUI route and tool share it')
    counts = {c: sum(1 for r in rows if r['class'] == c) for c in ('tool', 'needs-tool', 'limitation', 'unclassified')}
    matrix = {
        'format_version': 1,
        'status': 'source contract; behavioral fixtures remain pending on issue 05',
        'sources': [
            'pypsa-gui/backend/main.py (flattened app routes)',
            'pypsa-gui/frontend/src/** (client/fetch/EventSource/sendBeacon/raw /api/ URLs)',
            'pypsa-gui/backend/harness/catalogue.py (TOOL_ROUTES, safety tiers)',
            'pypsa-gui/backend/services/chat_tools.py (DISPATCHERS)',
            'pypsa-gui/backend/smoke/assistant_route_parity_declarations.json',
        ],
        'counts': {'backend_routes': len(routes), 'gui_routes': len(rows), **counts},
        'rows': rows,
    }
    return matrix, problems


def _dump(matrix: dict) -> str:
    return json.dumps(matrix, indent=1, sort_keys=True) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--write', action='store_true')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    matrix, problems = build()
    if args.write:
        args.output.write_text(_dump(matrix))
        print(f'wrote {args.output}: {matrix["counts"]}')
    else:
        if not args.output.exists():
            problems.append(f'missing {args.output}; run --write')
        elif args.output.read_text() != _dump(matrix):
            problems.append(f'{args.output} is stale; run --write')
    for p in problems:
        print('FAIL', p)
    if not problems:
        print(f'ok: {matrix["counts"]}')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
