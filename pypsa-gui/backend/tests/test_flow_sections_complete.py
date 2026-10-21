"""Every code `validate_for_run` can emit has a row in the flow's code → section
table (project-workflow B1; design §3.1), so a new check never files to
"other" unnoticed.

The codes are collected from the SOURCE, as the release review did: the
`_err` / `_warn` calls of `validation_service`, the `(severity, code, …)`
findings of the modules it delegates to, and the non-finite categories. The
f-string codes are expanded over what they can be.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from services import flow_sections as F

SERVICES = pathlib.Path(__file__).resolve().parent.parent / "services"
VALIDATOR = SERVICES / "validation_service.py"
DELEGATED = (
    SERVICES / "timeseries_qa.py",
    SERVICES / "topology_analyzer.py",
    SERVICES / "commercial" / "preflight.py",
    SERVICES / "study" / "pre_u2.py",
)
SEVERITIES = {"error", "warning", "info"}
# What `f"{comp.lower()}_…"` is called with in validation_service.
COMPONENTS = ("Generator", "StorageUnit", "Store", "Link", "Line", "Transformer")


def _const(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _nonfinite_categories(tree) -> set[str]:
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_nonfinite_category")
    return {_const(r.value.elts[0]) for r in ast.walk(fn)
            if isinstance(r, ast.Return) and isinstance(r.value, ast.Tuple)} - {None}


def _validator_codes() -> tuple[set[str], set[str]]:
    """(literal codes, f-string templates) of validation_service's _err/_warn."""
    tree = ast.parse(VALIDATOR.read_text("utf-8"))
    literal, templates = set(), set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("_err", "_warn") and node.args):
            continue
        arg = node.args[0]
        if _const(arg) is not None:
            literal.add(_const(arg))
        else:
            templates.add(ast.unparse(arg))
    categories = _nonfinite_categories(tree)
    expanded: set[str] = set()
    for t in templates:
        if t == "code":                              # the non-finite categories
            expanded |= categories
        elif t == "f'{code}_partial_coverage'":
            expanded |= {f"{c}_partial_coverage" for c in categories}
        elif t.startswith("f'{comp.lower()}_"):
            suffix = t[len("f'{comp.lower()}_"):-1]
            for comp in COMPONENTS:
                for s in ([suffix.replace("{prefix}", p) for p in ("p_nom", "e_nom", "s_nom")]
                          if "{prefix}" in suffix else [suffix]):
                    expanded.add(f"{comp.lower()}_{s}")
        else:
            raise AssertionError(f"unexpanded code template {t}: teach this test it")
    return literal, expanded


def _delegated_codes() -> set[str]:
    """`(severity, code, …)` tuples and `Issue(severity, code, …)` calls."""
    out: set[str] = set()
    for path in DELEGATED:
        tree = ast.parse(path.read_text("utf-8"))
        # Module constants (`CODE = "study_network_tariff_in_links"`).
        consts = {t.id: _const(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  for t in n.targets if isinstance(t, ast.Name) and _const(n.value)}
        for node in ast.walk(tree):
            elts = (node.elts if isinstance(node, ast.Tuple)
                    else node.args if isinstance(node, ast.Call) else None)
            if not elts or len(elts) < 2 or _const(elts[0]) not in SEVERITIES:
                continue
            if _const(elts[1]) is not None:
                out.add(_const(elts[1]))
            elif isinstance(elts[1], ast.Name) and elts[1].id in consts:
                out.add(consts[elts[1].id])
            elif isinstance(elts[1], ast.JoinedStr) and "island_" in ast.unparse(elts[1]):
                out |= {"island_no_supply", "island_under_capacity"}
            elif isinstance(elts[1], ast.JoinedStr):
                raise AssertionError(f"{path.name}: unexpanded code {ast.unparse(elts[1])}")
        # Codes named in a `for code in ("commercial.…", …)` refusal loop.
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                out |= {c for c in map(_const, node.iter.elts) if c and "." in c}
    return out


def test_the_collector_finds_codes():
    literal, expanded = _validator_codes()
    assert {"snapshots_empty", "voll_no_loads", "rolling_with_sclopf"} <= literal
    assert {"generator_p_nom_bounds", "link_efficiency_invalid",
            "nonfinite_cost_partial_coverage"} <= expanded
    assert {"network_islanded", "commercial.binding_invalid",
            "study_network_tariff_in_links"} <= _delegated_codes()


def test_every_validator_code_has_an_explicit_row():
    """validation_service's own literal codes each have a CODE_SECTION row."""
    literal, _ = _validator_codes()
    missing = sorted(c for c in literal if c not in F.CODE_SECTION)
    assert not missing, f"add these to flow_sections.CODE_SECTION: {missing}"


@pytest.mark.parametrize("code", sorted(set().union(*_validator_codes(), _delegated_codes())))
def test_every_emittable_code_files_to_a_section(code):
    section = F.section_for_code(code)
    assert section in (*F.INPUT_SECTIONS, F.BY_COMPONENT), (code, section)


def test_rows_name_only_known_sections():
    assert set(F.CODE_SECTION.values()) <= {*F.INPUT_SECTIONS, F.BY_COMPONENT}
    assert {s for _, s in F.CODE_PREFIX_SECTION} <= set(F.INPUT_SECTIONS)
    assert set(F.COMPONENT_SECTION.values()) <= set(F.INPUT_SECTIONS)
    assert set(F.SECTION_PANEL) == set(F.SECTION_IDS)


def test_sections_are_the_ten_in_flow_order():
    assert F.SECTION_IDS == ("project", "carriers", "connection", "loads", "assets",
                             "profiles", "commercial", "scenarios", "readiness", "results")


@pytest.mark.parametrize(("code", "cls", "name", "expected"), [
    ("generator_p_nom_bounds", "Generator", "pv", "assets"),
    ("link_p_nom_bounds", "Link", "grid_import", "connection"),
    ("link_p_nom_bounds", "Link", "electrolyser", "assets"),
    ("bus_ref_unknown", "Load", "it_load", "loads"),
    ("nonfinite_cost", "Line", "l1", "connection"),
    ("carrier_something_new", "", "", "carriers"),
    ("a_code_nobody_mapped", "", "", "readiness"),
])
def test_file_issue(code, cls, name, expected):
    assert F.file_issue(code, cls, name, frozenset({"grid_import"})) == expected
