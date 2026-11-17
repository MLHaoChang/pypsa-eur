"""The pypsa cage: which gridspine modules may import pypsa (owner ruling 2026-11-17)."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRIDSPINE = ROOT / "gridspine"


def _allowed(path: Path) -> bool:
    rel = path.relative_to(GRIDSPINE)
    if rel.parts[0] == "producers":
        return True
    return (
        rel.parts[0] == "ingest"
        and len(rel.parts) == 2
        and rel.name.endswith("_import.py")
    )


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _pypsa_imports(tree: ast.AST) -> list[int]:
    """Line numbers of pypsa imports, skipping `if TYPE_CHECKING:` bodies."""
    found: list[int] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            names = []
        if any(n == "pypsa" or n.startswith("pypsa.") for n in names):
            found.append(node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def test_only_producers_and_ingest_importers_import_pypsa():
    bad = [
        f"{path.relative_to(ROOT)}:{line}"
        for path in sorted(GRIDSPINE.rglob("*.py"))
        if not _allowed(path)
        for line in _pypsa_imports(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert not bad, f"pypsa imported outside producers/ and ingest/*_import.py: {bad}"


def test_the_cage_pattern():
    assert _allowed(GRIDSPINE / "producers" / "pypsa_nodal.py")
    assert _allowed(GRIDSPINE / "ingest" / "psse_import.py")
    assert not _allowed(GRIDSPINE / "ingest" / "import_result.py")
    assert not _allowed(GRIDSPINE / "ingest" / "campus.py")
    assert not _allowed(GRIDSPINE / "ingest" / "sub" / "x_import.py")
    assert not _allowed(GRIDSPINE / "static" / "x_import.py")


def test_the_scan_sees_nested_imports_and_skips_type_checking():
    tree = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    import pypsa\nelse:\n    import pypsa.io\n"
        "def f():\n    from pypsa import Network\n"
        "import pypsatools\n"
    )
    assert _pypsa_imports(tree) == [5, 7]
