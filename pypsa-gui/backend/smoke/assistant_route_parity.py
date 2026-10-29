"""Regenerate/check the GUI-route to typed-tool parity matrix (live-voice issue 08).

Run from any directory: python <this-file> --check (or --write to also save the
matrix for reading; it is not committed, so tool PRs never regenerate it).

Every backend route the GUI reaches (axios ``client`` calls, ``fetch``,
``EventSource``, ``sendBeacon`` and raw ``/api/`` URLs under
``pypsa-gui/frontend/src``) is classified exactly once:

* ``tool``: an assistant tool covers it, through a declared ``TOOL_ROUTES``
  HTTP entry or through a service function the tool handler and the route
  handler both call (direct callees, same-module helpers followed);
* ``needs-tool``: declared in the declarations file, a coverage gap that
  ``--check`` fails on (LV08 R2: none is left);
* ``limitation``: declared with a reason from ``LIMITATION_REASONS``; a
  ``held-by-spec`` one names the open issue that holds it (``issue``).

Declarations are ``assistant_route_parity_declarations.json`` plus the fragments in
``assistant_route_parity_declarations.d/`` (``smoke/json_fragments.py``).

``--check`` fails closed: an unresolvable GUI binding, a binding matching no
route, more than one route or the wrong method, an unclassified GUI route, or a
declaration whose route is gone or unreached. A declaration whose route a tool
now serves is reported as stale but passes; the coordinator's sweep deletes it
(``smoke/prune_stale_declarations.py``). The matrix is a source contract, not a
behavioral test.

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

try:
    from smoke.json_fragments import load_merged
except ImportError:  # run as a script: smoke/ itself is on sys.path
    from json_fragments import load_merged

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
    'app-shell',             # health, local desktop settings, the project edit-lock heartbeat
    'live-stream',           # server-sent log streams; the assistant reads the same logs via snapshot tools
    'ui-local-state',        # layout/view persistence with no domain effect
    'binary-transfer',       # raw file bytes the assistant reaches via typed upload/export tools
    'held-by-spec',          # a frozen spec owns this route's chat disposition; ``issue`` names it (HELD_ISSUE_RE)
)
# A ``held-by-spec`` limitation's ``issue``: a repo-relative issue file that names
# the route as ``METHOD path`` and is not closed (its ``Status:`` is not done or
# wontfix). Each held issue is one full_coverage blocker (assistant_control_matrix).
HELD_ISSUE_RE = re.compile(r'\.scratch/[\w./-]+/issues/[\w.-]+\.md')
CLOSED_STATUSES = ('done', 'wontfix')
# The issue-05 coverage families (capability-matrix.json ``family_journeys``).
FAMILIES = (
    'projects', 'network-topology', 'timeseries-horizon', 'solver-simulation', 'all-results',
    'reliability-adequacy', 'gridspine-campus', 'economics-investment', 'experiments',
    'import-export', 'reports', 'audit-undo', 'harness-workflows', 'ui-monitoring',
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


def match_binding(
    binding: dict, routes: list[tuple[str, str]], acknowledged: frozenset[str] = frozenset(),
) -> tuple[tuple[str, str] | None, str | None]:
    """Resolve like Starlette: routes in registration order, first full match wins.

    A literal GUI URL needs no guess. A URL with a GUI parameter that fits more
    than one distinct route path for its method is ambiguous and fails (V2),
    unless ``acknowledged`` names exactly that set of earlier shadowing paths
    (a pre-existing route-order shadow the declarations accept).
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
    if set(loose) != acknowledged:
        if loose:
            return None, f'ambiguous: {same[0][1]} or {loose}'
        return None, f'stale acknowledged shadows {sorted(acknowledged)}: nothing shadows {same[0][1]} now'
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
    from starlette.concurrency import run_in_threadpool

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
        if target is run_in_threadpool and node.args:
            # `await run_in_threadpool(service_fn, ...)`: the call is service_fn's.
            target = _resolve_callee(node.args[0], glb)
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


def _held_problems(key: tuple[str, str], issue) -> list[str]:
    """Why a ``held-by-spec`` limitation's ``issue`` does not hold the route (empty when it does)."""
    where = f'held-by-spec limitation {key}'
    if not isinstance(issue, str) or not HELD_ISSUE_RE.fullmatch(issue):
        return [f'{where}: issue {issue!r} is not a .scratch/<feature>/issues/<file>.md path']
    path = ROOT / issue
    if not path.is_file():
        return [f'{where}: issue file {issue} does not exist']
    text = path.read_text(encoding='utf-8')
    problems = []
    if f'{key[0]} {key[1]}' not in text:
        problems.append(f'{where}: {issue} does not name the route as "{key[0]} {key[1]}"')
    status = re.search(r'^Status:\s*([\w-]+)', text, re.M)
    if status is None or status.group(1).lower() in CLOSED_STATUSES:
        problems.append(f'{where}: {issue} is {status.group(1) if status else "without a Status: line"}; '
                        'a closed issue holds nothing: build the tool or declare another reason')
    return problems


def load_declarations(path: Path = DECLARATIONS) -> dict:
    """The base declarations merged with ``assistant_route_parity_declarations.d/*.json``."""
    return load_merged(path)


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

    shadows = {(d['method'], d['url']): frozenset(d['shadows']) for d in decl.get('acknowledged_shadows', [])}
    gui_routes: dict[tuple[str, str], set[str]] = {}
    used_shadows: set[tuple[str, str]] = set()
    for b in bindings:
        ack = shadows.get((b['method'], b['url']), frozenset())
        hit, why = match_binding(b, route_order, ack)
        if hit is not None and ack:
            used_shadows.add((b['method'], b['url']))
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

    for key in sorted(shadows.keys() - used_shadows):
        problems.append(f'stale acknowledged_shadows entry {key}: no GUI binding matches through exactly those shadows')

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
    for name, declared in (('limitations', limitations), ('needs_tool', needs)):
        if len(declared) != len(decl.get(name, [])):
            problems.append(f'{name}: a route is declared more than once (base or a .d/ fragment)')
    for key, d in limitations.items():
        if d.get('reason') not in LIMITATION_REASONS:
            problems.append(f'limitation {key}: reason {d.get("reason")!r} not in {LIMITATION_REASONS}')
        elif d.get('reason') == 'held-by-spec':
            problems += _held_problems(key, d.get('issue'))
    for key, d in needs.items():
        if d.get('family') not in FAMILIES:
            problems.append(f'needs-tool {key}: family {d.get("family")!r} not in {FAMILIES}')
    for key in limitations.keys() & needs.keys():
        problems.append(f'route {key} declared both needs-tool and limitation')
    for key in non_mutating - route_set:
        problems.append(f'stale non_mutating_routes entry {key}: route gone')

    used_services: set[str] = set()
    stale: list[dict] = []
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
                # Not a failure: the PR that serves a route leaves its declaration,
                # so two such PRs never edit the same lines.
                # `smoke/prune_stale_declarations.py --apply` deletes it.
                stale.append({'method': key[0], 'path': key[1], 'tools': sorted(tools)})
        elif key in needs:
            row |= {'class': 'needs-tool', 'family': needs[key].get('family')}
            if 'note' in needs[key]:
                row['note'] = needs[key]['note']
        elif key in limitations:
            row |= {'class': 'limitation', 'reason': limitations[key]['reason']}
            if 'issue' in limitations[key]:
                row['issue'] = limitations[key]['issue']
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
            'pypsa-gui/backend/smoke/assistant_route_parity_declarations.json (+ .d/*.json)',
        ],
        'counts': {'backend_routes': len(routes), 'gui_routes': len(rows), **counts},
        'rows': rows,
        'stale_declarations': stale,
    }
    return matrix, problems


def _dump(matrix: dict) -> str:
    return json.dumps(matrix, indent=1, sort_keys=True) + '\n'


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Build in memory; fail on any problem.')
    mode.add_argument('--write', action='store_true', help='Also write the matrix (not committed) for reading.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    matrix, problems = build()
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(_dump(matrix))
        print(f'wrote {args.output}: {matrix["counts"]}')
    for s in matrix['stale_declarations']:
        print(f'stale (sweep prunes) {s["method"]} {s["path"]}: now served by {s["tools"]}')
    # A needs-tool row is a coverage gap, and the gap is closed (LV08 R2): a new
    # GUI route gets a tool or a limitation in the same PR.
    problems += [f'needs-tool GUI route {r["method"]} {r["path"]} ({r["family"]}): add a typed tool '
                 'or declare a limitation' for r in matrix['rows'] if r['class'] == 'needs-tool']
    for p in problems:
        print('FAIL', p)
    if not problems:
        print(f'ok: {matrix["counts"]}')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
