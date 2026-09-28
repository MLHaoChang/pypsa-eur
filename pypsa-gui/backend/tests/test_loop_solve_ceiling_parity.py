"""
The loop panels state the solve ceiling in their pre-run copy. The number
they print is a frontend constant that mirrors `MAX_LOOP_SOLVES` in
`services/adequacy/coupling.py`, the cap the routes enforce. Two copies of
one number drift; this pins them equal, the way `brand.theme.test.ts` pins
the theme tokens.
"""
from __future__ import annotations

import re
from pathlib import Path

from services.adequacy.coupling import MAX_LOOP_SOLVES

_LOOP_PANEL = (
    Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "pages" / "results" / "LoopPanel.tsx"
)


def test_frontend_loop_ceiling_matches_the_backend_cap():
    source = _LOOP_PANEL.read_text(encoding="utf-8")
    match = re.search(r"export const MAX_LOOP_SOLVES = (\d+)", source)
    assert match, "LoopPanel.tsx no longer exports MAX_LOOP_SOLVES"
    assert int(match.group(1)) == MAX_LOOP_SOLVES
