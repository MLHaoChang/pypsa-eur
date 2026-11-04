"""Pin the two backend constants the frontend repeats by hand (AS-10 follow-up).

The frontend literals are read tolerantly: a formatter may add a semicolon, a type
annotation, `export`, double quotes, a trailing comma or line breaks, and none of
that changes the value. A changed value still fails.
"""
import re
from pathlib import Path

from models.design_intent import BACKUP_AUTONOMY_DEFAULT_H
from models.energy_hub import EH_IMPORT_ROLES

PAGES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"

_AUTONOMY = re.compile(
    r"\bconst\s+AUTONOMY_DEFAULT_H\s*(?::\s*number\s*)?=\s*"
    r"([0-9][0-9_]*(?:\.[0-9_]+)?)\s*(?:as\s+const\s*)?;?"
)
_ROLES = re.compile(r"\bconst\s+IMPORT_ROLES\s*(?::[^=\n]*)?=\s*\[([^\]]*)\]")
_STRING = re.compile(r"""(['"`])(.*?)\1""")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)


def frontend_autonomy_default(src: str) -> float | None:
    m = _AUTONOMY.search(src)
    return float(m.group(1).replace("_", "")) if m else None


def frontend_import_roles(src: str) -> list[str] | None:
    m = _ROLES.search(src)
    if not m:
        return None
    return [v for _, v in _STRING.findall(_COMMENT.sub("", m.group(1)))]


def test_frontend_autonomy_default_matches_backend():
    src = (PAGES / "IntentComparisonSection.tsx").read_text(encoding="utf-8")
    value = frontend_autonomy_default(src)
    assert value is not None, "AUTONOMY_DEFAULT_H not found in IntentComparisonSection.tsx"
    assert value == BACKUP_AUTONOMY_DEFAULT_H


def test_frontend_import_roles_match_backend():
    src = (PAGES / "intentDefaults.ts").read_text(encoding="utf-8")
    roles = frontend_import_roles(src)
    assert roles is not None, "IMPORT_ROLES not found in intentDefaults.ts"
    assert roles == list(EH_IMPORT_ROLES)


def test_the_readers_survive_a_reformat_and_still_see_a_changed_value():
    assert frontend_autonomy_default("export const AUTONOMY_DEFAULT_H: number = 4;\n") == 4
    assert frontend_autonomy_default("const AUTONOMY_DEFAULT_H = 4.5 // h\n") == 4.5
    assert frontend_autonomy_default("const AUTONOMY_DEFAULT_H = 5\n") == 5
    assert frontend_autonomy_default("const OTHER = 4\n") is None
    spread = 'const IMPORT_ROLES = [\n  "grid_import", // a\n  "eh_import",\n  "import",\n]\n'
    assert frontend_import_roles(spread) == ["grid_import", "eh_import", "import"]
    assert frontend_import_roles("const IMPORT_ROLES: string[] = ['a']") == ["a"]
    assert frontend_import_roles("const IMPORT_ROLES = ['grid_import', 'x', 'import']") != list(EH_IMPORT_ROLES)
