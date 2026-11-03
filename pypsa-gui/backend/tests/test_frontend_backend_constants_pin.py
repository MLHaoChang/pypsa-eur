"""Pin the two backend constants the frontend repeats by hand (AS-10 follow-up)."""
import re
from pathlib import Path

from models.design_intent import BACKUP_AUTONOMY_DEFAULT_H
from models.energy_hub import EH_IMPORT_ROLES

PAGES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"


def test_frontend_autonomy_default_matches_backend():
    src = (PAGES / "IntentComparisonSection.tsx").read_text(encoding="utf-8")
    m = re.search(r"^const AUTONOMY_DEFAULT_H = ([0-9.]+)\s*$", src, re.M)
    assert m, "AUTONOMY_DEFAULT_H not found in IntentComparisonSection.tsx"
    assert float(m.group(1)) == BACKUP_AUTONOMY_DEFAULT_H


def test_frontend_import_roles_match_backend():
    src = (PAGES / "intentDefaults.ts").read_text(encoding="utf-8")
    m = re.search(r"^const IMPORT_ROLES = \[([^\]]*)\]", src, re.M)
    assert m, "IMPORT_ROLES not found in intentDefaults.ts"
    assert re.findall(r"'([^']*)'", m.group(1)) == list(EH_IMPORT_ROLES)
