"""Regenerate/check source coverage without credentials, models or application I/O.

Run from any directory: python <this-file> --write or --check.
The inventory is a source contract, never a certification of tool behavior.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / '.scratch/live-voice/capability-inventory.json'


def _literal_constant(path: Path, name: str):
    """Read schema caps without importing their scientific/Pydantic modules."""
    for node in ast.parse(path.read_text(), filename=str(path)).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return ast.literal_eval(node.value)
    raise ValueError(f'Cannot read literal schema constant {name} in {path}; update the reader.')


def _metadata(relative: str) -> dict:
    """Evaluate repository-owned declarations, replacing only imported literals.

    This executes the actual catalogue factories/assembly, not a second registry.
    Application modules/handlers are never imported. Unsupported imports or
    computed caps fail closed and require an explicit reader update.
    """
    path = ROOT / relative
    tree = ast.parse(path.read_text(), filename=str(path))
    namespace = {'__name__': 'assistant_metadata', '__file__': str(path)}
    retained = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module not in ('__future__', 'typing'):
            if node.level or not node.module:
                raise ValueError('Cannot read relative metadata imports; update the reader.')
            source = ROOT / 'pypsa-gui/backend' / (node.module.replace('.', '/') + '.py')
            for alias in node.names:
                namespace[alias.asname or alias.name] = _literal_constant(source, alias.name)
        elif isinstance(node, ast.Import):
            raise ValueError('Cannot read module imports in metadata; update the reader.')
        else:
            retained.append(node)
    tree.body = retained
    exec(compile(tree, str(path), 'exec'), namespace)
    return namespace


def build_inventory() -> dict:
    """Read authoritative tool declarations and independent frontend target IDs."""
    catalogue = _metadata('pypsa-gui/backend/harness/catalogue.py')
    toolsets = _metadata('pypsa-gui/backend/harness/toolsets.py')
    TOOLS, TOOL_ROUTES = catalogue['TOOLS'], catalogue['TOOL_ROUTES']
    RESULTS_TAB_ENUM, SAFETY_PANEL_ENUM = catalogue['RESULTS_TAB_ENUM'], catalogue['SAFETY_PANEL_ENUM']
    safety_tier_for = catalogue['safety_tier_for']
    DOMAINS, filter_tools = toolsets['DOMAINS'], toolsets['filter_tools']

    results = (ROOT / 'pypsa-gui/frontend/src/pages/Results.tsx').read_text()
    tab_block = re.search(r'\bconst TABS:.*?=\s*\[(.*?)\n\]', results, re.S)
    if tab_block is None:
        raise ValueError('Cannot enumerate Results TABS; update the inventory reader.')
    rows = re.findall(r'''\{\s*id:\s*(['"])(.*?)\1,\s*label:\s*(['"])(.*?)\3''', tab_block.group(1))
    if len(rows) != len(re.findall(r'\bid\s*:', tab_block.group(1))):
        raise ValueError('Cannot enumerate every Results target; update the inventory reader.')
    tabs = [{'id': row[1], 'label': row[3]} for row in rows]
    ui = (ROOT / 'pypsa-gui/frontend/src/store/uiStore.ts').read_text()
    panel_type = re.search(r'''export\s+type\s+SlidePanel\s*=\s*((?:\s*\|?\s*['"][^'"]+['"])+)''', ui)
    if panel_type is None:
        raise ValueError('Cannot enumerate SlidePanel; update the inventory reader.')
    if ui[panel_type.end():].lstrip().startswith('|'):
        raise ValueError('Cannot enumerate every SlidePanel target; update the inventory reader.')
    panels = re.findall(r'''['"]([^'"]+)['"]''', panel_type.group(1))
    if not tabs or not panels or len({t['id'] for t in tabs}) != len(tabs) or len(set(panels)) != len(panels):
        raise ValueError('Empty or duplicate frontend targets; inventory cannot certify navigation.')
    domains = {d: {t['name'] for t in filter_tools(TOOLS, d)} for d in DOMAINS if d != 'all'}
    return {
        'format_version': 1,
        'generated_at': datetime.now(timezone.utc).date().isoformat(),
        'status': 'source inventory; behavioral coverage and GUI parameter mapping pending implementation',
        'sources': [
            'pypsa-gui/backend/harness/catalogue.py', 'pypsa-gui/backend/harness/toolsets.py',
            'pypsa-gui/frontend/src/pages/Results.tsx', 'pypsa-gui/frontend/src/store/uiStore.ts',
            'pypsa-gui/backend/models/energy_hub.py', 'pypsa-gui/backend/services/adequacy/mc.py',
        ],
        'tool_count': len(TOOLS),
        'tools': [{
            'name': tool['name'],
            'toolsets': [d for d, names in domains.items() if tool['name'] in names],
            'route': TOOL_ROUTES[tool['name']],
            'input_keys': list(tool['input_schema'].get('properties', {})),
            'required': tool['input_schema'].get('required', []),
            'safety_tier': safety_tier_for(tool['name']),
            'schema_sha256': hashlib.sha256(json.dumps(tool['input_schema'], sort_keys=True).encode()).hexdigest(),
            'behavioral_fixture': 'pending',
        } for tool in TOOLS],
        'result_tabs': tabs,
        'tool_result_tabs': RESULTS_TAB_ENUM,
        'result_tabs_not_in_tool_enum': [t['id'] for t in tabs if t['id'] not in RESULTS_TAB_ENUM],
        'slide_panels': panels,
        'tool_panel_enum': SAFETY_PANEL_ENUM,
        'slide_panels_not_in_tool_enum': [p for p in panels if p not in SAFETY_PANEL_ENUM],
        'requested_aliases': {
            'AR': 'unresolved label; map against actual UI/module vocabulary during coverage audit, do not infer expansion',
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Reject source drift or unoffered navigation targets.')
    mode.add_argument('--write', action='store_true', help='Regenerate the source inventory; behavior remains unverified.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        # Normalize tuples used in route declarations to their JSON representation.
        current = json.loads(json.dumps(build_inventory()))
    except (OSError, ValueError, KeyError, SyntaxError) as exc:
        print(f'Inventory generation failed: {exc}')
        return 1
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(current, indent=2) + '\n')
        print(f'Wrote {current["tool_count"]} tools; behavioral/GUI parameter coverage still pending.')
        return 0
    try:
        stored = json.loads(args.output.read_text())
    except (OSError, ValueError):
        print('Inventory missing or invalid; run with --write to regenerate.')
        return 1
    stored.pop('generated_at', None)
    current.pop('generated_at', None)
    if stored != current:
        print('Inventory stale; run with --write, review changes and commit the updated source contract.')
        return 1
    missing = current['result_tabs_not_in_tool_enum'] + current['slide_panels_not_in_tool_enum']
    if missing:
        print('GUI navigation targets absent from shared tool schema: ' + ', '.join(missing))
        return 1
    print(f'Inventory and GUI navigation schemas match: {current["tool_count"]} tools, '
          f'{len(current["result_tabs"])} result tabs, {len(current["slide_panels"])} panels. '
          'Behavioral/GUI parameter coverage still pending.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
