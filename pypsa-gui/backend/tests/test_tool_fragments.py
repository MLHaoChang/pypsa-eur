"""Per-tool registry fragments for the chat catalogue (RETRO-FRAG-1, issue 02).

A tool is a pair `harness/catalogue_tools/<stem>.py` +
`services/chat_dispatchers/<stem>.py`, discovered at import and merged by
`harness/tool_fragments.py`. These tests hold the merge's refusals, the order
rule, the gates a fragment's dispatcher gets, the import boundaries and the
frozen-app collection.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from harness import catalogue, tool_fragments as F
from harness.tool_fragments import FragmentError, SchemaFragments

BACKEND = Path(__file__).resolve().parents[1]
SPEC = BACKEND.parent / "pypsa-gui.spec"


def _tool(name):
    return {
        "name": name,
        "description": "Probe. Safety: read.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }


def _mod(name, tools=(), routes=None, **attrs):
    mod = types.ModuleType(name)
    mod.TOOLS = [_tool(t) for t in tools]
    mod.ROUTES = (
        routes if routes is not None else {t: [("GET", "/api/x")] for t in tools}
    )
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def _schema(**mods):
    return SchemaFragments({stem: mod for stem, mod in mods.items()})


# ── the shipped fragments ───────────────────────────────────────────────────


def test_report_templates_is_a_fragment_spliced_at_its_old_place():
    names = [t["name"] for t in catalogue.TOOLS]
    i = names.index("list_report_templates")
    assert names[i - 1] == "delete_report"
    assert names[i : i + 6] == [
        "list_report_templates",
        "set_report_template",
        "get_report_template",
        "propose_report_mapping",
        "set_report_mapping",
        "list_report_roundtrips",
    ]
    assert catalogue._FRAGMENTS.spliced == {"report_templates"}


def test_every_schema_fragment_has_its_dispatcher_pair():
    schema = F.discover(F.SCHEMA_PACKAGE)
    dispatch = F.discover(F.DISPATCH_PACKAGE)
    assert schema and set(schema) == set(dispatch)
    from services import chat_tools

    for stem, mod in dispatch.items():
        for name, fn in mod.DISPATCHERS.items():
            got = chat_tools.DISPATCHERS[name]
            while got is not fn and hasattr(got, "__wrapped__"):
                got = got.__wrapped__
            assert got is fn, f"{stem}.{name} is not what DISPATCHERS dispatches"


def test_moved_dispatchers_keep_their_chat_tools_attribute():
    from services import chat_tools
    from services.chat_dispatchers import report_templates

    for name, fn in report_templates.DISPATCHERS.items():
        assert getattr(chat_tools, name) is fn


# ── merge refusals name both modules ────────────────────────────────────────


def test_a_tool_name_defined_twice_is_refused():
    frags = _schema(a=_mod("pkg.a", ["dup"], ORDER=1))
    tools = [_tool("dup")]
    frags.extend(tools)
    with pytest.raises(FragmentError, match=r"'dup'.*harness\.catalogue.*pkg\.a"):
        frags.merge(tools, {}, frozenset(), frozenset())


def test_a_tool_name_in_two_fragments_is_refused():
    frags = _schema(
        a=_mod("pkg.a", ["dup"], ORDER=1), b=_mod("pkg.b", ["dup"], ORDER=2)
    )
    tools: list = []
    frags.extend(tools)
    with pytest.raises(FragmentError, match=r"'dup'.*pkg\.a.*pkg\.b"):
        frags.merge(tools, {}, frozenset(), frozenset())


def test_a_route_key_already_in_tool_routes_is_refused():
    frags = _schema(a=_mod("pkg.a", ["t1"], ORDER=1))
    tools: list = []
    frags.extend(tools)
    with pytest.raises(
        FragmentError, match=r"route key 't1'.*harness\.catalogue.*pkg\.a"
    ):
        frags.merge(tools, {"t1": [("GET", "/api/y")]}, frozenset(), frozenset())


def test_routes_must_match_the_fragments_tools():
    frags = _schema(a=_mod("pkg.a", ["t1"], routes={}, ORDER=1))
    tools: list = []
    frags.extend(tools)
    with pytest.raises(FragmentError, match=r"pkg\.a: ROUTES keys"):
        frags.merge(tools, {}, frozenset(), frozenset())


def test_a_write_set_member_already_declared_is_refused():
    frags = _schema(a=_mod("pkg.a", ["t1"], ORDER=1, NETWORK_WRITES={"t1"}))
    tools: list = []
    frags.extend(tools)
    with pytest.raises(
        FragmentError, match=r"NETWORK_WRITES.*harness\.catalogue.*pkg\.a"
    ):
        frags.merge(tools, {}, frozenset({"t1"}), frozenset())


def test_a_dispatcher_already_in_dispatchers_is_refused():
    schema = {"a": _mod("pkg.cat.a", ["t1"])}
    disp = types.ModuleType("pkg.disp.a")
    disp.DISPATCHERS = {"t1": lambda: None}
    with pytest.raises(
        FragmentError, match=r"dispatcher 't1'.*services\.chat_tools.*pkg\.disp\.a"
    ):
        F.merge_dispatchers({"t1": print}, {}, {"a": disp}, schema)


def test_an_order_clash_is_refused():
    frags = _schema(
        a=_mod("pkg.a", ["t1"], ORDER=2026112701),
        b=_mod("pkg.b", ["t2"], ORDER=2026112701),
    )
    with pytest.raises(FragmentError, match=r"ORDER 2026112701.*pkg\.a.*pkg\.b"):
        frags.extend([])


def test_an_unspliced_fragment_without_order_is_refused():
    with pytest.raises(FragmentError, match=r"pkg\.a is not spliced.*ORDER"):
        _schema(a=_mod("pkg.a", ["t1"])).extend([])


def test_a_spliced_fragment_that_also_declares_order_is_refused():
    frags = _schema(a=_mod("pkg.a", ["t1"], ORDER=1))
    with pytest.raises(
        FragmentError,
        match=r"pkg\.a is spliced by harness\.catalogue and also declares ORDER",
    ):
        frags.splice("a")


def test_splicing_a_missing_fragment_is_refused():
    with pytest.raises(FragmentError, match=r"splices harness\.catalogue_tools\.gone"):
        _schema().splice("gone")


@pytest.mark.parametrize("missing", ["dispatch", "schema"])
def test_an_unpaired_fragment_is_refused(missing):
    schema = {"a": _mod("pkg.cat.a", ["t1"])}
    disp = types.ModuleType("pkg.disp.a")
    disp.DISPATCHERS = {"t1": lambda: None}
    mods, schemas = ({}, schema) if missing == "dispatch" else ({"a": disp}, {})
    have = "pkg.cat.a" if missing == "dispatch" else "pkg.disp.a"
    want = (
        "services.chat_dispatchers.a"
        if missing == "dispatch"
        else "harness.catalogue_tools.a"
    )
    with pytest.raises(FragmentError, match=rf"{have} has no pair: {want} is missing"):
        F.merge_dispatchers({}, {}, mods, schemas)


def test_a_dispatcher_fragment_must_cover_exactly_its_tools():
    schema = {"a": _mod("pkg.cat.a", ["t1", "t2"])}
    disp = types.ModuleType("pkg.disp.a")
    disp.DISPATCHERS = {"t1": lambda: None}
    with pytest.raises(
        FragmentError, match=r"pkg\.disp\.a DISPATCHERS.*pkg\.cat\.a TOOLS"
    ):
        F.merge_dispatchers({}, {}, {"a": disp}, schema)


def test_appended_fragments_follow_order_not_file_name():
    frags = _schema(
        a=_mod("pkg.a", ["late"], ORDER=2026112702),
        b=_mod("pkg.b", ["early"], ORDER=2026112701),
    )
    tools: list = [_tool("legacy")]
    frags.extend(tools)
    assert [t["name"] for t in tools] == ["legacy", "early", "late"]


def test_a_fragment_spliced_twice_is_refused():
    frags = _schema(a=_mod("pkg.a", ["t1"]))
    frags.splice("a")
    with pytest.raises(FragmentError, match=r"splices pkg\.a twice"):
        frags.splice("a")


@pytest.mark.parametrize("attr", ["NETWORK_WRITES", "NON_NETWORK_WRITES"])
def test_a_fragments_write_sets_are_merged(attr):
    frags = _schema(a=_mod("pkg.a", ["t1"], ORDER=1, **{attr: {"t1"}}))
    tools: list = []
    frags.extend(tools)
    net, non = frags.merge(tools, {}, frozenset({"x"}), frozenset({"y"}))
    got = net if attr == "NETWORK_WRITES" else non
    assert got == {"t1", "x" if attr == "NETWORK_WRITES" else "y"}


@pytest.mark.parametrize("attr", ["NETWORK_WRITES", "NON_NETWORK_WRITES"])
def test_a_write_set_naming_another_tool_is_refused(attr):
    frags = _schema(a=_mod("pkg.a", ["t1"], ORDER=1, **{attr: {"legacy_tool"}}))
    tools: list = []
    frags.extend(tools)
    with pytest.raises(
        FragmentError, match=rf"pkg\.a: {attr} names a tool it does not define"
    ):
        frags.merge(tools, {}, frozenset(), frozenset())


@pytest.mark.parametrize("attr", ["PRE_DISPATCH_VALIDATORS", "LOCK_GATED"])
def test_a_dispatcher_fragment_cannot_reach_another_tool(attr):
    schema = {"a": _mod("pkg.cat.a", ["t1"])}
    disp = types.ModuleType("pkg.disp.a")
    disp.DISPATCHERS = {"t1": lambda: None}
    setattr(
        disp, attr, {"legacy_tool": print} if attr != "LOCK_GATED" else {"legacy_tool"}
    )
    with pytest.raises(
        FragmentError, match=rf"pkg\.disp\.a: {attr} names a tool it does not define"
    ):
        F.merge_dispatchers({}, {}, {"a": disp}, schema)


def test_merge_dispatchers_returns_the_lock_gated_tools():
    schema = {"a": _mod("pkg.cat.a", ["t1", "t2"])}
    disp = types.ModuleType("pkg.disp.a")
    disp.DISPATCHERS = {"t1": print, "t2": print}
    disp.LOCK_GATED = {"t2"}
    assert F.merge_dispatchers({}, {}, {"a": disp}, schema) == {"t2"}


# ── discovery misses fail loudly ────────────────────────────────────────────


def test_a_spliced_fragment_dropped_from_discovery_fails_the_catalogue_import(
    monkeypatch,
):
    real = F.discover

    def without_report_templates(package):
        found = real(package)
        found.pop("report_templates", None)
        return found

    monkeypatch.setattr(F, "discover", without_report_templates)
    spec = importlib.util.spec_from_file_location(
        "_catalogue_probe", BACKEND / "harness" / "catalogue.py"
    )
    probe = importlib.util.module_from_spec(spec)
    with pytest.raises(
        FragmentError, match=r"harness\.catalogue_tools\.report_templates"
    ):
        spec.loader.exec_module(probe)


def test_discovery_finding_nothing_in_the_frozen_app_raises(tmp_path, monkeypatch):
    pkg = tmp_path / "zz_empty_fragments"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert F.discover("zz_empty_fragments") == {}
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(
        FragmentError, match="no fragment modules found in the frozen app"
    ):
        F.discover("zz_empty_fragments")


def test_discovery_in_the_frozen_app_with_fragments_present_succeeds(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "report_templates" in F.discover(F.SCHEMA_PACKAGE)


def test_a_dispatcher_fragment_imported_first_is_not_circular():
    code = (
        "import services.chat_dispatchers.report_templates as R\n"
        "from services import chat_tools as T\n"
        "assert all(n in T.DISPATCHERS for n in R.DISPATCHERS)\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stderr[-2000:]


def test_the_spec_collects_both_fragment_packages():
    """`pkgutil` discovery cannot be followed by PyInstaller's static analysis, so
    the spec lists every module of both packages as hidden imports, and writes
    the packages out beside the archive. The spec cannot be imported (PyInstaller
    injects its globals), so its listing function is run on its own."""
    text = SPEC.read_text(encoding="utf-8")
    assert (
        'FRAGMENT_PACKAGES = ("harness.catalogue_tools", "services.chat_dispatchers")'
        in text
    )
    for pkg in (F.SCHEMA_PACKAGE, F.DISPATCH_PACKAGE):
        assert f'"{pkg}": "pyz+py"' in text, pkg
    tree = ast.parse(text)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_fragment_modules"
    )
    scope = {"BACKEND": BACKEND}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(SPEC), "exec"), scope)
    for pkg in (F.SCHEMA_PACKAGE, F.DISPATCH_PACKAGE):
        listed = set(scope["_fragment_modules"](pkg))
        assert {f"{pkg}.{stem}" for stem in F.discover(pkg)} | {pkg} == listed, pkg


# ── a new fragment pair is gated like every other tool ──────────────────────

_PROBE_SCHEMA = """
from harness.catalogue_base import _SERVICE_CALL, _t

ORDER = 2099010101
TOOLS = [
    _t("zz_probe_edit", "Probe edit. Safety: write.", {"cls": {"type": "string"}}, ["cls"]),
    _t("zz_probe_apply", "Probe apply. Safety: write.", {}, []),
    _t("zz_probe_save", "Probe save. Safety: write.", {}, []),
]
ROUTES = {
    "zz_probe_edit": [("POST", "/api/network/components/{cls}")],
    "zz_probe_apply": _SERVICE_CALL,
    "zz_probe_save": _SERVICE_CALL,
}
NETWORK_WRITES = {"zz_probe_apply"}
NON_NETWORK_WRITES = {"zz_probe_save"}
"""

_PROBE_DISPATCH = """
def zz_probe_edit(cls):
    return {"cls": cls}


def zz_probe_apply():
    return {}


def zz_probe_save():
    return {}


def _check(args):
    return None


DISPATCHERS = {
    "zz_probe_edit": zz_probe_edit,
    "zz_probe_apply": zz_probe_apply,
    "zz_probe_save": zz_probe_save,
}
PRE_DISPATCH_VALIDATORS = {"zz_probe_edit": _check}
LOCK_GATED = {"zz_probe_save"}
"""

_PROBE_RUN = """
import json, sys
import harness.catalogue_tools, services.chat_dispatchers
harness.catalogue_tools.__path__.append(sys.argv[1])
services.chat_dispatchers.__path__.append(sys.argv[2])
from harness import catalogue as C
from services import chat_tools as T
from services.chat_dispatchers import zz_probe as P

def depth(fn):
    n = 0
    while hasattr(fn, "__wrapped__"):
        fn, n = fn.__wrapped__, n + 1
    return fn, n

out = {"last": [t["name"] for t in C.TOOLS[-3:]],
       "network_writes": "zz_probe_apply" in C._SERVICE_CALL_NETWORK_WRITES,
       "non_network_writes": "zz_probe_save" in C._SERVICE_CALL_NON_NETWORK_WRITES,
       "validator": T.PRE_DISPATCH_VALIDATORS.get("zz_probe_edit") is P._check}
for name in ("zz_probe_edit", "zz_probe_apply", "zz_probe_save"):
    raw, n = depth(T.DISPATCHERS[name])
    out[name] = {"raw": raw is getattr(P, name), "depth": n,
                 "lock": name in T._lock_gated_tool_names(),
                 "study": name in T._study_gated_tool_names(),
                 "undo": name in T.UNDO_CAPTURED_TOOLS}
print("PROBE" + json.dumps(out))
"""


def test_a_new_fragment_pairs_write_tools_get_every_wrapper(tmp_path):
    """Loads a synthetic pair through the real discovery in a fresh interpreter:
    it lands last in TOOLS, its validator and write sets are merged, its routed
    and NETWORK_WRITES tools are lock-gated, study-gated and undo-captured, and
    its LOCK_GATED project-folder write is lock-gated only. Loading the fragments after the
    gates wrap DISPATCHERS fails this."""
    schema, disp = tmp_path / "schema", tmp_path / "disp"
    schema.mkdir()
    disp.mkdir()
    (schema / "zz_probe.py").write_text(textwrap.dedent(_PROBE_SCHEMA))
    (disp / "zz_probe.py").write_text(textwrap.dedent(_PROBE_DISPATCH))
    run = subprocess.run(
        [sys.executable, "-c", _PROBE_RUN, str(schema), str(disp)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    line = next(x for x in run.stdout.splitlines() if x.startswith("PROBE"))
    out = json.loads(line[len("PROBE") :])
    assert out["last"] == ["zz_probe_edit", "zz_probe_apply", "zz_probe_save"]
    assert out["network_writes"] and out["non_network_writes"] and out["validator"]
    save = out["zz_probe_save"]
    assert save["raw"] and save["depth"] == 1 and save["lock"], save
    assert not save["study"] and not save["undo"], save
    for name in ("zz_probe_edit", "zz_probe_apply"):
        got = out[name]
        assert got["raw"] and got["depth"] == 2, (name, got)
        assert got["lock"] and got["study"] and got["undo"], (name, got)


# ── import boundaries ───────────────────────────────────────────────────────


def test_schema_fragments_import_only_the_catalogue_base():
    allowed = {"__future__", "harness.catalogue_base"}
    for path in sorted((BACKEND / "harness" / "catalogue_tools").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                bad = (
                    []
                    if node.module in allowed and not node.level
                    else [node.module or "."]
                )
            else:
                continue
            assert not bad, (
                f"{path.name} imports {bad}; a schema fragment imports only harness.catalogue_base"
            )


_LIGHT = """
import importlib, json, sys
importlib.import_module(sys.argv[1])
print("MODS" + json.dumps(sorted(m for m in sys.modules if m.startswith("services."))))
"""


@pytest.mark.parametrize(
    "module,no_services",
    [
        ("harness.protocol", True),
        ("harness.spill", True),
        ("harness.guards", False),
        ("harness.stub", False),
        ("harness.catalogue", False),
    ],
)
def test_light_importers_do_not_pull_in_the_dispatchers(module, no_services):
    run = subprocess.run(
        [sys.executable, "-c", _LIGHT, module],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    mods = json.loads(
        next(x for x in run.stdout.splitlines() if x.startswith("MODS"))[4:]
    )
    if no_services:
        assert mods == [], f"{module} imports services: {mods}"
    leaked = [
        m
        for m in mods
        if m.startswith(("services.chat_tools", "services.chat_dispatchers"))
    ]
    assert leaked == [], f"{module} imports the dispatchers: {leaked}"


def test_an_order_that_is_a_bool_is_refused():
    with pytest.raises(FragmentError, match=r"pkg\.a is not spliced.*ORDER"):
        _schema(a=_mod("pkg.a", ["t1"], ORDER=True)).extend([])


def test_validators_and_lock_gated_may_cover_every_tool_of_the_fragment():
    schema = {"a": _mod("pkg.cat.a", ["t1"])}
    disp = types.ModuleType("pkg.disp.a")
    disp.DISPATCHERS = {"t1": print}
    disp.PRE_DISPATCH_VALIDATORS = {"t1": print}
    disp.LOCK_GATED = {"t1"}
    validators: dict = {}
    assert F.merge_dispatchers({}, validators, {"a": disp}, schema) == {"t1"}
    assert validators == {"t1": print}
