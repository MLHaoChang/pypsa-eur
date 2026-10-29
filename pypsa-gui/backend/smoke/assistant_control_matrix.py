"""Regenerate/check the editable-control and family-journey matrix (issue 08).

Reads CreationForm FIELD_MAP and attributeCatalog editability/closed sets without
importing the React app. Maps each control to create_component / update_component
or an explicit gap. Resolves the requested AR alias against real inventory IDs.
Each coverage family's journey is a reference to the LC-06 family manifest
(``family_manifest.py``): its suites and their variants' status, read from the
manifest itself, so coverage is counted in one ledger.

``--check`` builds the matrix in memory, with the inventory and route parity it
summarises, and applies the CI guards; ``--write`` also saves it for reading. It
is not committed, so tool PRs never regenerate it. Route parity imports the app,
so this check runs in the assistant-route-parity workflow.
"""
from __future__ import annotations

import argparse
import functools
import json
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    from smoke import assistant_inventory, assistant_route_parity, family_manifest
except ImportError:  # run as a script: smoke/ itself is on sys.path
    import assistant_inventory
    import assistant_route_parity
    import family_manifest

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = ROOT / '.scratch/live-voice/capability-matrix.json'
CREATION_FORM = ROOT / 'pypsa-gui/frontend/src/layout/CreationForm.tsx'
ATTR_CATALOG = ROOT / 'pypsa-gui/frontend/src/utils/attributeCatalog.ts'

# Process-unit form fields that are not Link attributes (utils/processUnit.ts derives the attributes).
PROCESS_UNIT_HELPER = re.compile(r'^(product_per_t|el_kwh_per_t|in[1-3]_(bus|rate))$')

# Palette id → PyPSA component_class accepted by create_component / update_component.
PALETTE_TO_CLASS: dict[str, str] = {
    'bus': 'Bus',
    'line': 'Line',
    'transformer': 'Transformer',
    'electrolyzer': 'Link',
    'fuel_cell': 'Link',
    'power_to_heat': 'Link',
    'chp': 'Link',
    'process_unit': 'Link',
    'thermal': 'Generator',
    'renewable': 'Generator',
    'battery': 'StorageUnit',
    'psh': 'StorageUnit',
    'hydrogen': 'StorageUnit',
    'caes': 'StorageUnit',
    'flywheel': 'StorageUnit',
    'thermal_storage': 'Store',
    'load_elec': 'Load',
    'load_h2': 'Load',
    'load_heat': 'Load',
}

# Each coverage family's journey is its row in the LC-06 family manifest
# (`smoke/family_manifest.py`): the suites SUITES_BY_FAMILY assigns it and their
# variants by status. A suite shared by two families counts in both.
MANIFEST_SOURCE = 'pypsa-gui/backend/smoke/family_manifest.py'
# The suites LC-06 leaves out (R3 of the LV 08 comparison); each keeps its own blocker.
R3_SUITES = tuple(f'LC-D{i:02d}' for i in (1, 2, 20, 21, 22, 23, 24))
R3_ISSUE = '.scratch/live-copilot/issues/32-uncovered-families-d01-d02-d20-d24.md'


def family_journeys(variants: list[dict] | None = None) -> list[dict]:
    """One journey row per family, read from the manifest's variants (or `variants`)."""
    variants = family_manifest.VARIANTS if variants is None else variants
    rows = []
    for slug, family in family_manifest.FAMILIES.items():
        suites = [f'LC-D{i:02d}' for i in family_manifest.SUITES_BY_FAMILY[slug]]
        by_status = {s: [v['id'] for suite in suites for v in variants if v['suite'] == suite and v['status'] == s]
                     for s in ('covered', 'pending', 'unavailable')}
        rows.append({
            'family': family,
            'journey_id': f'manifest:{slug}',
            'source': MANIFEST_SOURCE,
            'suites': suites,
            'variants': by_status,
            'status': 'pending' if by_status['pending'] else 'covered',
        })
    return rows


FAMILY_JOURNEYS: list[dict] = family_journeys()

# Blockers that stay until a human closes them with evidence; never derived away.
STANDING_BLOCKERS: list[dict[str, str]] = [
    {'id': 'ui-local-control-parity-unaudited', 'issue': '08',
     'summary': 'Client-only panel/subview/filter controls (no backend call) are not mapped to a navigation tool or a declared gap; controls that call the backend are audited in route_parity.'},
    {'id': 'hosted-gates-pending', 'issue': '05',
     'summary': 'Hosted profile, device and latency gates and the prolonged mixed-domain conversation are not run; an architecture review does not certify production behavior.'},
]


@functools.cache
def _route_parity() -> dict:
    """Summarise the GUI-route to typed-tool parity matrix, built in memory; its problems fail closed."""
    data, problems = assistant_route_parity.build()
    if problems:
        raise ValueError('route parity fails; run assistant_route_parity.py --check: ' + '; '.join(problems))
    needs: dict[str, list[str]] = {}
    reasons: dict[str, int] = {}
    held: dict[str, list[str]] = {}
    for row in data['rows']:
        if row['class'] == 'needs-tool':
            needs.setdefault(row['family'], []).append(f'{row["method"]} {row["path"]}')
        elif row['class'] == 'limitation':
            reasons[row['reason']] = reasons.get(row['reason'], 0) + 1
            if row['reason'] == 'held-by-spec':
                held.setdefault(row['issue'], []).append(f'{row["method"]} {row["path"]}')
    return {
        'source': 'pypsa-gui/backend/smoke/assistant_route_parity.py (built in memory)',
        'counts': data['counts'],
        'limitations_by_reason': dict(sorted(reasons.items())),
        'needs_tool_by_family': {f: sorted(r) for f, r in sorted(needs.items())},
        'held_by_issue': {i: sorted(r) for i, r in sorted(held.items())},
    }


def _full_coverage(journeys: list[dict], route_parity: dict | None = None,
                   variants: list[dict] | None = None) -> dict:
    """The explicit limitation block: whole-application coverage is NOT claimed
    until every manifest variant of every family is covered and no blocker remains."""
    variants = family_manifest.VARIANTS if variants is None else variants
    pending = [j for j in journeys if j['variants']['pending']]
    blockers = []
    for j in pending:
        lc06 = [v for v in j['variants']['pending'] if v.split('.')[0] not in R3_SUITES]
        if lc06:
            blockers.append({'id': f'family-manifest:{j["journey_id"].removeprefix("manifest:")}', 'issue': 'LC-06',
                             'family': j['family'], 'pending_variants': lc06,
                             'summary': f'{len(lc06)} LC-06 variants of this family are pending ({MANIFEST_SOURCE}).'})
    for suite in R3_SUITES:
        open_ = sorted({v for j in pending for v in j['variants']['pending'] if v.split('.')[0] == suite})
        if open_:
            blockers.append({'id': f'r3-uncovered:{suite}', 'issue': 'LC-32', 'pointer': R3_ISSUE,
                             'pending_variants': open_,
                             'summary': f'LC-06 leaves {suite} out; no behavioral fixture covers it.'})
    covered_tools = {t for v in variants if v['status'] == 'covered' for t in v.get('tools', [])}
    uncovered = len(set(family_manifest.inventory_tools()) - covered_tools)
    if uncovered:
        blockers.append({'id': 'manifest-tools-uncovered', 'issue': 'LC-06',
                         'summary': f'{uncovered} assistant tools are listed by no covered manifest variant.'})
    blockers += STANDING_BLOCKERS
    if route_parity is not None:
        if route_parity['counts'].get('unclassified'):
            blockers.append({'id': 'route-parity:unclassified', 'issue': '08',
                             'summary': f'{route_parity["counts"]["unclassified"]} GUI routes are unclassified.'})
        blockers += [
            {'id': f'route-parity:needs-tool:{family}', 'issue': '08', 'family': family,
             'summary': f'{len(routes)} GUI routes have no typed assistant tool (route_parity.needs_tool_by_family).'}
            for family, routes in route_parity['needs_tool_by_family'].items()
        ]
        # A held route is a limitation, never a tool: its issue stays open until it serves them.
        blockers += [
            {'id': f'route-parity:held:{issue}', 'issue': '08', 'pointer': issue,
             'summary': f'{len(routes)} GUI routes are held for this issue (route_parity.held_by_issue).'}
            for issue, routes in route_parity.get('held_by_issue', {}).items()
        ]
    return {
        'claimed': not blockers,
        'status': 'established' if not blockers else 'not_established',
        'rule': (
            'Whole-application coverage may be claimed only when every variant of every family in '
            'the LC-06 family manifest is covered and no blocker remains. Load flow, '
            'sensitivity and GridSpine fixtures are examples, not the scope.'
        ),
        'journeys_total': len(journeys),
        'journeys_pending': len(pending),
        'blockers': blockers,
        'permitted_statement': (
            'Tested subsets only, labelled accordingly; no whole-application coverage claim.'
        ),
    }


# User-requested "AR" label → real modules/tools (never invent "Ardat").
AR_RESOLUTION = {
    'label': 'AR',
    'status': 'mapped',
    'expansion': 'Asset Reliability',
    'maps_to': {
        'tools': [
            'get_asset_health',
            'record_asset_health',
            'get_adequacy_results',
            'get_fmea_worksheet',
            'run_fmea_sweep',
        ],
        'result_tabs': ['adequacy', 'fmea'],
        'modules': [
            'pypsa-gui/backend/services/adequacy/asset_health.py',
            'pypsa-gui/backend/services/adequacy/',
        ],
    },
    'note': (
        'Requested AR label maps to Asset Reliability (health assumptions) plus '
        'adequacy/FMEA result surfaces. No separate module exists for that acronym.'
    ),
}


def _parse_fields_from_block(block: str, *, carrier: str | None = None, hours: str | None = None) -> list[dict]:
    """Parse `{ key, label, type, ... }` objects; single- or multi-line."""
    fields = []
    for fm in re.finditer(
        r"\{\s*key:\s*'([^']+)'\s*,\s*label:\s*'((?:\\'|[^'])*)'\s*,\s*type:\s*'([^']+)'"
        r"(.*?)\}",
        block,
        re.S,
    ):
        rest = fm.group(4)
        unit_m = re.search(r"unit:\s*'([^']+)'", rest)
        default_m = re.search(r"defaultValue:\s*([^,\n}]+)", rest)
        default = None
        if default_m:
            raw = default_m.group(1).strip()
            if raw in ('carrier',) and carrier is not None:
                default = carrier
            elif raw in ('hours',) and hours is not None:
                default = hours
            elif raw.startswith("'") and raw.endswith("'"):
                default = raw[1:-1]
        fields.append({
            'key': fm.group(1),
            'label': fm.group(2).replace("\\'", "'"),
            'parameter_type': fm.group(3),
            'unit': unit_m.group(1) if unit_m else None,
            'default': default,
        })
    return fields


def _helper_fields(source: str, name: str, carrier: str, hours: str | None = None) -> list[dict]:
    m = re.search(
        rf'const {re.escape(name)}\s*=\s*\([^)]*\)\s*:\s*[^\n]*=>\s*\[(.*?)\n\]',
        source,
        re.S,
    )
    if m is None:
        raise ValueError(f'Cannot find helper {name}; update the control-matrix reader.')
    return _parse_fields_from_block(m.group(1), carrier=carrier, hours=hours)


def _parse_field_map(source: str) -> dict[str, list[dict]]:
    """Extract palette_id → field specs from CreationForm FIELD_MAP."""
    start = source.find('const FIELD_MAP')
    if start < 0:
        raise ValueError('Cannot find FIELD_MAP; update the control-matrix reader.')
    brace = source.find('{', start)
    depth = 0
    end = None
    for i, ch in enumerate(source[brace:], brace):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        raise ValueError('Cannot close FIELD_MAP; update the control-matrix reader.')
    body = source[brace + 1:end]
    out: dict[str, list[dict]] = {}
    # Digits allowed (load_h2); indent-2 keys only so comments/fields are skipped.
    for m in re.finditer(r'\n  ([a-z0-9_]+):\s*(.+)', body):
        key, rhs = m.group(1), m.group(2).strip()
        if rhs.startswith('['):
            # Inline array until the matching close at indent 2
            rest = body[m.end(2) - len(rhs):]
            depth = 0
            close = None
            for i, ch in enumerate(rest):
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        close = i
                        break
            if close is None:
                raise ValueError(f'Cannot close FIELD_MAP[{key!r}]; update the reader.')
            fields = _parse_fields_from_block(rest[1:close])
        elif rhs.startswith('genFields('):
            args = re.findall(r"'([^']+)'", rhs)
            fields = _helper_fields(source, 'genFields', carrier=args[0])
        elif rhs.startswith('storFields('):
            args = re.findall(r"'([^']+)'", rhs)
            hours = args[1] if len(args) > 1 else '4'
            fields = _helper_fields(source, 'storFields', carrier=args[0], hours=hours)
        else:
            raise ValueError(f'Unsupported FIELD_MAP[{key!r}] rhs {rhs!r}; update the reader.')
        if not fields:
            raise ValueError(f'FIELD_MAP[{key!r}] has no fields; update the reader.')
        out[key] = fields
    if not out:
        raise ValueError('FIELD_MAP has no palette entries; update the reader.')
    return out


def _parse_string_record(source: str, name: str) -> dict[str, str | list]:
    """Read a TypeScript `export const NAME: Record<...> = { ... }` object."""
    m = re.search(rf'export const {re.escape(name)}\s*[^=]*=\s*\{{(.*?)\n\}}', source, re.S)
    if m is None:
        raise ValueError(f'Cannot find {name}; update the control-matrix reader.')
    body = m.group(1)
    # Arrays: 'Key': ['a', 'b']
    arrays = {
        k: re.findall(r"'([^']+)'", vals)
        for k, vals in re.findall(r"'([^']+)':\s*\[(.*?)\]", body, re.S)
    }
    scalars = dict(re.findall(r"'([^']+)':\s*'([^']+)'", body))
    # Prefer arrays when both match a key (CLOSED_SETS)
    out: dict[str, str | list] = dict(scalars)
    out.update(arrays)
    if not out:
        raise ValueError(f'{name} is empty; update the control-matrix reader.')
    return out


def _inventory_tool_names() -> set[str]:
    return {t['name'] for t in assistant_inventory.build_inventory()['tools']}


def build_matrix() -> dict:
    form_src = CREATION_FORM.read_text(encoding='utf-8')
    attr_src = ATTR_CATALOG.read_text(encoding='utf-8')
    field_map = _parse_field_map(form_src)
    route_parity = _route_parity()
    overrides = _parse_string_record(attr_src, 'EDITABILITY_OVERRIDES')
    closed = _parse_string_record(attr_src, 'CLOSED_SETS')
    tools = _inventory_tool_names()
    for name in AR_RESOLUTION['maps_to']['tools']:
        if name not in tools:
            raise ValueError(f'AR maps_to tool {name!r} missing from inventory; update AR_RESOLUTION.')

    controls = []
    gaps = []
    for palette_id, fields in sorted(field_map.items()):
        component_class = PALETTE_TO_CLASS.get(palette_id)
        if component_class is None:
            gaps.append({
                'ui_surface': 'creation_form',
                'palette_id': palette_id,
                'reason': 'no palette→component_class mapping',
            })
            continue
        for field in fields:
            # Form-only helpers that are not PyPSA attrs (voltage-step preset).
            if palette_id == 'transformer' and field['key'] == 'type':
                tool = None
                gap = 'ui_helper_not_pypsa_attr'
            elif palette_id == 'process_unit' and PROCESS_UNIT_HELPER.match(field['key']):
                # Rates per tonne: derived into efficiency / efficiency2.. and bus2.. on submit.
                tool = None
                gap = 'ui_helper_not_pypsa_attr'
            elif field['key'].endswith('_per_km'):
                # Submitted as absolute r/x/b via SUBMIT_TRANSFORM — covered by create_component attrs.
                tool = 'create_component'
                gap = None
            else:
                tool = 'create_component'
                gap = None
            controls.append({
                'family': 'Network/topology/asset parameters',
                'ui_surface': 'creation_form',
                'ui_target': f'palette:{palette_id}.{field["key"]}',
                'palette_id': palette_id,
                'field_key': field['key'],
                'label': field['label'],
                'parameter_type': field['parameter_type'],
                'unit': field['unit'],
                'component_class': component_class,
                'tool': tool,
                'attrs_path': None if tool is None else f'attrs.{field["key"]}',
                'eligibility': {
                    'project_kinds': ['capacity_expansion', None],
                    'ui_modes': ['expert', 'guided'],
                    'requires_project': True,
                },
                'gap': gap,
                'behavioral_fixture': 'manifest:network-topology',
            })

    edit_rows = []
    for key, status in sorted(overrides.items()):
        if not isinstance(status, str):
            continue
        component_class, _, column = key.partition('.')
        tool = 'update_component' if status == 'editable' else None
        edit_rows.append({
            'family': 'Network/topology/asset parameters',
            'ui_surface': 'attribute_grid',
            'ui_target': f'catalog:{key}',
            'component_class': component_class,
            'field_key': column,
            'editability': status,
            'tool': tool,
            'eligibility': {
                'project_kinds': ['capacity_expansion', None],
                'ui_modes': ['expert'],
                'requires_project': True,
            },
            'behavioral_fixture': 'manifest:network-topology',
        })

    closed_rows = []
    for key, values in sorted(closed.items()):
        if not isinstance(values, list):
            continue
        component_class, _, column = key.partition('.')
        closed_rows.append({
            'ui_target': f'catalog:{key}',
            'component_class': component_class,
            'field_key': column,
            'parameter_type': 'enum',
            'values': values,
            'tool': 'update_component',
        })

    return {
        'format_version': 1,
        'generated_at': datetime.now(timezone.utc).date().isoformat(),
        'status': (
            'control/parameter matrix for Network/topology; '
            'behavioral journeys remain pending on issue 05'
        ),
        'sources': [
            'pypsa-gui/frontend/src/layout/CreationForm.tsx',
            'pypsa-gui/frontend/src/utils/attributeCatalog.ts',
            'pypsa-gui/backend/smoke/assistant_inventory.py (built in memory)',
            '.scratch/live-voice/application-coverage.md',
            'pypsa-gui/backend/smoke/assistant_route_parity.py (built in memory)',
            MANIFEST_SOURCE,
        ],
        'controls': controls,
        'editability_overrides': edit_rows,
        'closed_sets': closed_rows,
        'control_gaps': gaps,
        'ar_resolution': AR_RESOLUTION,
        'family_journeys': FAMILY_JOURNEYS,
        'route_parity': route_parity,
        'full_coverage': _full_coverage(FAMILY_JOURNEYS, route_parity),
        'counts': {
            'creation_controls': len(controls),
            'editability_overrides': len(edit_rows),
            'closed_sets': len(closed_rows),
            'family_journeys': len(FAMILY_JOURNEYS),
            'unmapped_palette_ids': len(gaps),
        },
    }


def _full_coverage_problem(matrix: dict) -> str | None:
    """CI guard: a claim must be backed by covered manifest variants and zero blockers.
    Pending is recomputed from the manifest, never read from the matrix's own copy."""
    block = matrix.get('full_coverage')
    if not isinstance(block, dict):
        return 'full_coverage limitation block is missing'
    pending = [j for j in family_journeys() if j['variants']['pending']]
    if block.get('claimed') is True and (pending or block.get('blockers')):
        return 'full_coverage.claimed is true while family journeys are pending or blockers remain'
    if pending and (block.get('claimed') is not False or block.get('status') != 'not_established'):
        return 'full_coverage must be claimed=false/not_established while manifest variants are pending'
    if block.get('journeys_pending') != len(pending):
        return 'full_coverage.journeys_pending disagrees with family_manifest'
    return None


def matrix_problem(matrix: dict) -> str | None:
    """The CI guards; None when the matrix may stand."""
    if matrix.get('ar_resolution', {}).get('status') != 'mapped':
        return 'AR alias is still unresolved'
    if len(matrix.get('family_journeys', [])) != 14:
        return 'family_journeys must assign all 14 coverage families'
    problem = _full_coverage_problem(matrix)
    if problem:
        return problem
    if any(c.get('tool') is None and c.get('gap') is None for c in matrix.get('controls', [])):
        return 'every creation control needs a tool or an explicit gap'
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Build in memory; fail on any guard.')
    mode.add_argument('--write', action='store_true', help='Also write the matrix (not committed) for reading.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        built = build_matrix()
    except ValueError as exc:
        print(exc)
        return 1
    if args.write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(built, indent=2) + '\n', encoding='utf-8')
        print(f'wrote {args.output} controls={built["counts"]["creation_controls"]}')
    problem = matrix_problem(built)
    if problem:
        print(problem)
        return 1
    print(
        'ok',
        f'controls={built["counts"]["creation_controls"]}',
        f'families={built["counts"]["family_journeys"]}',
        'ar=mapped',
        f'full_coverage={built["full_coverage"]["status"]}',
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
