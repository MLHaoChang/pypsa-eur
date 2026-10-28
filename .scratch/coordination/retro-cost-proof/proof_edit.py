"""Apply proof branch A, B or C edits inside a worktree (argv: worktree, variant)."""
import json
import sys
from pathlib import Path

wt, variant = Path(sys.argv[1]), sys.argv[2]
B = wt / 'pypsa-gui/backend'


def edit(path, old, new, count=1):
    s = path.read_text()
    assert s.count(old) == count, (path, old, s.count(old))
    path.write_text(s.replace(old, new))


def add_tool(name, route, after_tool, kind, params, args, body_anchor):
    cat = B / 'harness/catalogue.py'
    line = next(l for l in cat.read_text().splitlines() if l.startswith(f'    _t("{after_tool}",'))
    edit(cat, line + '\n', line + '\n' + f'    _t("{name}", "Proof tool {name} for the RETRO-COST-1 merge-order test. '
         f'Safety: read.", {params}, {list(json.loads(params))}),\n')
    rline = next(l for l in cat.read_text().splitlines() if l.startswith(f'    "{after_tool}": ['))
    edit(cat, rline + '\n', rline + '\n' + f'    "{name}": [{route!r}],\n')
    ct = B / 'services/chat_tools.py'
    edit(ct, f'\ndef {body_anchor}(', f'\ndef {name}({args}) -> dict:\n'
         f'    return {{"error": "proof", "error_kind": "{kind}"}}\n\n\ndef {body_anchor}(')
    dline = next(l for l in ct.read_text().splitlines() if l.startswith(f'    "{after_tool}": '))
    edit(ct, dline + '\n', dline + '\n' + f'    "{name}": {name},\n')
    frag = wt / f'pypsa-gui/tool-error-kinds.d/claude-proof-{variant.lower()}.json'
    frag.write_text(json.dumps({'kinds': {kind: {
        'surface': 'inline', 'why': f'Proof kind for {name}; the assistant reports the detail and stops.'}}}) + '\n')


if variant == 'A':
    # A new GUI route without a tool yet (needs_tool fragment + a known gap), a tool and a kind.
    edit(B / 'routers/results.py', '@results_router.get("/eh_readiness")\n',
         '@results_router.get("/proof_a_status")\ndef get_proof_a_status() -> dict:\n    return {"ok": True}\n\n\n'
         '@results_router.get("/eh_readiness")\n')
    sim = wt / 'pypsa-gui/frontend/src/api/simulation.ts'
    edit(sim, '/** Read-only preflight from GET /results/eh_readiness (P14). */\n',
         "/** Proof (RETRO-COST-1): a GUI route with no tool yet. */\n"
         "export const getProofAStatus = () => client.get('/results/proof_a_status')\n\n"
         '/** Read-only preflight from GET /results/eh_readiness (P14). */\n')
    edit(B / 'tests/test_route_chat_disposition.py',
         "    ('GET', '/api/results/process_view'): 'Results panel',\n",
         "    ('GET', '/api/results/process_view'): 'Results panel',\n"
         "    ('GET', '/api/results/proof_a_status'): 'Results panel',\n")
    (B / 'smoke/assistant_route_parity_declarations.d/claude-proof-a.json').write_text(json.dumps(
        {'needs_tool': [{'method': 'GET', 'path': '/api/results/proof_a_status', 'family': 'all-results'}]}) + '\n')
    add_tool('proof_a_tool', ('GET', '/api/results/eh_readiness'), 'get_eh_readiness', 'proof_a_kind',
             '{"archetype": {"type": "string"}}', 'archetype: str', 'get_eh_readiness')
elif variant == 'B':
    # Serves a declared needs-tool route that is also a KNOWN_GAPS row; leaves both (stale).
    add_tool('proof_b_island_report', ('GET', '/api/campus-electrical/{name}/island/report'),
             'create_evidence_report', 'proof_b_kind', '{"name": {"type": "string"}}', 'name: str',
             'create_evidence_report')
elif variant == 'C':
    # Like B, but appended right after the same last tool as B: the "both at the end" case.
    add_tool('proof_c_tool', ('GET', '/api/results/eh_readiness'), 'create_evidence_report', 'proof_c_kind',
             '{"archetype": {"type": "string"}}', 'archetype: str', 'create_evidence_report')
