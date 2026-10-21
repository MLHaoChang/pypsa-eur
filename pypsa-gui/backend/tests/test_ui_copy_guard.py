"""Server-side UI copy guard: text the user reads carries no internal ids.

The frontend half is ``frontend/src/uiCopyGuard.test.ts``. The server writes
much of the flow copy (item messages, section labels, scope labels and reasons,
readiness notes). This scans the string literals of those modules with the same
rules; comments and docstrings are not scanned, so a section reference in a
comment is fine.
"""

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Modules whose string literals reach the user. A module that is not on the
# branch yet is skipped, so the guard covers it the day it lands.
MODULES = [
    "services/flow_scope.py",
    "services/flow_service.py",
    "services/flow_sections.py",
    "services/adequacy/grid_readiness.py",
]

RULES = {
    "section sign": re.compile(r"§"),
    "workstream id": re.compile(r"\bW\d+[a-z]?\b"),
    "question id": re.compile(r"\bQ\d+\b"),
    "ticket id": re.compile(r"\b(?:LC-\d+|DC-?\d+|GS-)"),
    "raw PyPSA class path": re.compile(
        r"\b(?:Generator|Link|Store|StorageUnit|Line|Transformer|Bus|Load)\s?(?:›|>)"
    ),
}

# Intended words that look like an id: (module, exact text, why).
ALLOW: list[tuple[str, str, str]] = []


def copy_strings(source: str):
    """(line, text) of each string literal that is not a docstring."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            yield node.lineno, node.value


def findings(module: str, source: str):
    hits = []
    for line, text in copy_strings(source):
        for _, allowed, _why in (a for a in ALLOW if a[0] == module):
            text = text.replace(allowed, "")
        for name, rule in RULES.items():
            m = rule.search(text)
            if m:
                hits.append(f"{module}:{line} {name}: {m.group(0)!r} in {text[:80]!r}")
    return hits


def test_each_rule_fires_and_comments_and_docstrings_are_skipped():
    sample = (
        '"""See §4 and W3a."""\n'
        "# §5, LC-12 in a comment\n"
        "A = 'Ready for W3a'\n"
        "B = 'See § 4'\n"
        "C = 'Answer Q17'\n"
        "D = 'Tracked as LC-12'\n"
        "E = 'Link › grid_import'\n"
        "F = 'Generator>'\n"
        "def f():\n"
        '    """Docstring with GS-2."""\n'
        "    return 'fine words'\n"
    )
    found = findings("sample.py", sample)
    assert [h.split(" ")[1] for h in found] == [
        "workstream", "section", "question", "ticket", "raw", "raw",
    ]


@pytest.mark.parametrize("module", MODULES)
def test_server_copy_has_no_internal_ids(module):
    path = BACKEND / module
    if not path.exists():
        pytest.skip(f"{module} is not on this branch yet")
    assert findings(module, path.read_text()) == []
