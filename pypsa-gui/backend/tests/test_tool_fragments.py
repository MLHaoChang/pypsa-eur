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
    assert catalogue._FRAGMENTS.spliced == {
        "report_templates",
        "lv08_bcampus",
        "campus_electrical",
        "adequacy_studies",
        "explanation",
        "ui_control",
        "asset_results",
    }


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


# ── a moved family keeps its place in TOOL_ROUTES and DISPATCHERS ───────────


_REPORT_TEMPLATES = [
    "list_report_templates",
    "set_report_template",
    "get_report_template",
    "propose_report_mapping",
    "set_report_mapping",
]

# Every family moved out of the monolith, with the keys either side of its old
# place on the pre-move master, in TOOLS, TOOL_ROUTES and DISPATCHERS (a dict
# {table: (before, after)} when the three differ; after None = the table's end).
# Each move slice adds its row.
SPLICED_PLACES = {
    "report_templates": ("delete_report", "list_report_roundtrips"),
    "lv08_bcampus": {
        "TOOLS": ("create_evidence_report", "export_results_workbook"),
        "TOOL_ROUTES": ("create_evidence_report", "get_input_workbook"),
        "DISPATCHERS": ("get_grid_risk_production", None),
    },
    "campus_electrical": {
        "TOOLS": ("gridspine_compute_capacity", "gridspine_assess_connection"),
        "TOOL_ROUTES": ("gridspine_assess_connection", "list_library_items"),
        "DISPATCHERS": ("gridspine_assess_connection", "list_library_items"),
    },
    "adequacy_studies": {
        "TOOLS": ("get_eh_template", "solve_queue_enqueue"),
        "TOOL_ROUTES": ("get_eh_template", "solve_queue_enqueue"),
        "DISPATCHERS": ("get_reliability_goal", "solve_queue_enqueue"),
    },
    "explanation": {
        "TOOLS": ("record_asset_health", "get_adequacy_results"),
        "TOOL_ROUTES": ("record_asset_health", "get_adequacy_results"),
        "DISPATCHERS": ("get_aggregate_load", "create_component"),
    },
    "ui_control": {
        "TOOLS": ("undo_status", "compare_scenarios"),
        "TOOL_ROUTES": ("undo_status", "ui_set_snapshot"),
        "DISPATCHERS": ("undo_status", "ui_set_snapshot"),
    },
    "asset_results": {
        "TOOLS": ("set_active_profile", "gridspine_create_study"),
        "TOOL_ROUTES": ("clear_uploads", "export_results_workbook"),
        "DISPATCHERS": ("clear_uploads", "export_results_workbook"),
    },
}


def test_every_spliced_family_has_its_old_place_pinned():
    assert set(SPLICED_PLACES) == catalogue._FRAGMENTS.spliced


@pytest.mark.parametrize("table", ["TOOLS", "TOOL_ROUTES", "DISPATCHERS"])
@pytest.mark.parametrize("stem", sorted(SPLICED_PLACES))
def test_a_spliced_family_keeps_its_old_place(stem, table):
    from services import chat_tools

    place = SPLICED_PLACES[stem]
    before, after = place[table] if isinstance(place, dict) else place
    schema = F.discover(F.SCHEMA_PACKAGE)[stem]
    own, keys = {
        "TOOLS": (
            [t["name"] for t in schema.TOOLS],
            [t["name"] for t in catalogue.TOOLS],
        ),
        "TOOL_ROUTES": (list(schema.ROUTES), list(catalogue.TOOL_ROUTES)),
        "DISPATCHERS": (
            list(F.discover(F.DISPATCH_PACKAGE)[stem].DISPATCHERS),
            list(chat_tools.DISPATCHERS),
        ),
    }[table]
    # Fragments that are not spliced are appended after the monolith in ORDER
    # (contract item 2); the place pinned here is among the spliced keys.
    appended = {
        name
        for s, mod in F.discover(F.SCHEMA_PACKAGE).items()
        if s not in catalogue._FRAGMENTS.spliced
        for name in mod.ROUTES.keys() | {t["name"] for t in mod.TOOLS}
    }
    keys = [k for k in keys if k not in appended]
    i = keys.index(own[0])
    tail = [] if after is None else [after]
    assert keys[i - 1 : i + len(own) + 1] == [before, *own, *tail]
    assert after is not None or i + len(own) == len(keys)


def test_splicing_routes_of_a_family_whose_tools_are_not_spliced_is_refused():
    frags = _schema(a=_mod("pkg.cat.a", ["t1"]))
    with pytest.raises(
        FragmentError, match=r"ROUTES of harness\.catalogue_tools\.a.*not its TOOLS"
    ):
        frags.splice_routes("a")


def test_splicing_routes_twice_is_refused():
    frags = _schema(a=_mod("pkg.cat.a", ["t1"]))
    frags.splice("a")
    frags.splice_routes("a")
    with pytest.raises(FragmentError, match="twice"):
        frags.splice_routes("a")


def test_a_spliced_family_whose_routes_are_not_spliced_is_refused():
    frags = _schema(a=_mod("pkg.cat.a", ["t1"]))
    tools = frags.splice("a")
    with pytest.raises(FragmentError, match=r"pkg\.cat\.a.*TOOL_ROUTES.*splice_routes"):
        frags.merge(tools, {}, frozenset(), frozenset())


def test_spliced_routes_are_kept_where_they_were_spliced():
    frags = _schema(a=_mod("pkg.cat.a", ["t1"]), b=_mod("pkg.cat.b", ["t2"], ORDER=1))
    tools = [_tool("x"), *frags.splice("a")]
    frags.extend(tools)
    routes = {"x": [], **frags.splice_routes("a"), "y": []}
    frags.merge(tools, routes, frozenset(), frozenset())
    assert list(routes) == ["x", "t1", "y", "t2"]


def test_two_spliced_and_two_appended_fragments_keep_their_order():
    """Two families spliced at their places (in the opposite order to their
    file names) and two new tools appended in ORDER, not file name."""
    frags = _schema(
        a=_mod("pkg.cat.a", ["a1", "a2"]),
        b=_mod("pkg.cat.b", ["b1"]),
        y=_mod("pkg.cat.y", ["y1"], ORDER=2026112702),
        z=_mod("pkg.cat.z", ["z1"], ORDER=2026112701),
    )
    tools = [_tool("x"), *frags.splice("b"), _tool("m"), *frags.splice("a"), _tool("n")]
    frags.extend(tools)
    routes = {
        "x": [],
        **frags.splice_routes("b"),
        "m": [],
        **frags.splice_routes("a"),
        "n": [],
    }
    frags.merge(tools, routes, frozenset(), frozenset())
    order = ["x", "b1", "m", "a1", "a2", "n", "z1", "y1"]
    assert [t["name"] for t in tools] == order
    assert list(routes) == order
    disp = F.DispatchFragments(
        {s: _disp(f"pkg.disp.{s}", *(n for n in order if n[0] == s)) for s in "abyz"}
    )
    dispatchers = {
        "x": print,
        **disp.splice("b"),
        "m": print,
        **disp.splice("a"),
        "n": print,
    }
    disp.merge(dispatchers, {}, frags)
    assert list(dispatchers) == order


def test_a_spliced_route_removed_from_tool_routes_is_refused():
    frags = _schema(a=_mod("pkg.cat.a", ["t1"]))
    tools = frags.splice("a")
    frags.splice_routes("a")
    with pytest.raises(FragmentError, match=r"TOOL_ROUTES entry 't1'.*removed"):
        frags.merge(tools, {}, frozenset(), frozenset())


def test_a_spliced_route_replaced_in_tool_routes_is_refused():
    frags = _schema(a=_mod("pkg.cat.a", ["t1"]))
    tools = frags.splice("a")
    routes = {**frags.splice_routes("a")}
    routes["t1"] = [("GET", "/api/other")]
    with pytest.raises(FragmentError, match=r"TOOL_ROUTES entry 't1'.*replaced"):
        frags.merge(tools, routes, frozenset(), frozenset())


def _disp(name, *tools, **attrs):
    mod = types.ModuleType(name)
    mod.DISPATCHERS = {t: (lambda: None) for t in tools}
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_spliced_dispatchers_are_kept_where_they_were_spliced():
    schema = _schema(a=_mod("pkg.cat.a", ["t1"]), b=_mod("pkg.cat.b", ["t2"], ORDER=1))
    schema.splice("a")
    disp = F.DispatchFragments(
        {
            "a": _disp("pkg.disp.a", "t1", LOCK_GATED={"t1"}),
            "b": _disp("pkg.disp.b", "t2"),
        }
    )
    dispatchers = {"x": print, **disp.splice("a"), "y": print}
    assert disp.merge(dispatchers, {}, schema) == {"t1"}
    assert list(dispatchers) == ["x", "t1", "y", "t2"]


def test_a_dispatcher_family_spliced_on_one_side_only_is_refused():
    schema = _schema(a=_mod("pkg.cat.a", ["t1"]))
    disp = F.DispatchFragments({"a": _disp("pkg.disp.a", "t1")})
    dispatchers = {**disp.splice("a")}
    with pytest.raises(FragmentError, match=r"\['a'\] spliced by only one"):
        disp.merge(dispatchers, {}, schema)


def test_a_spliced_dispatcher_replaced_in_dispatchers_is_refused():
    schema = _schema(a=_mod("pkg.cat.a", ["t1"]))
    schema.splice("a")
    disp = F.DispatchFragments({"a": _disp("pkg.disp.a", "t1")})
    dispatchers = {**disp.splice("a")}
    dispatchers["t1"] = print
    with pytest.raises(FragmentError, match=r"DISPATCHERS entry 't1'.*replaced"):
        disp.merge(dispatchers, {}, schema)


def test_splicing_a_missing_or_twice_spliced_dispatcher_family_is_refused():
    disp = F.DispatchFragments({"a": _disp("pkg.disp.a", "t1")})
    with pytest.raises(
        FragmentError, match=r"chat_dispatchers\.b, which was not found"
    ):
        disp.splice("b")
    disp.splice("a")
    with pytest.raises(FragmentError, match="twice"):
        disp.splice("a")


# ── every shipped fragment tool gets exactly the gates its kind earns ───────


def _expected_gates(name, schema, disp):
    """(lock, study) for a fragment tool, derived from its own ROUTES, write
    sets and safety tier (not from chat_tools' gate-name functions): the
    lock gate covers a non-read tool with a write route under a gated prefix,
    a NETWORK_WRITES or a LOCK_GATED member; the study gate the network
    writes among those."""
    from harness.catalogue import safety_tier_for
    from services import chat_tools as T

    writes = [
        path
        for route in schema.ROUTES[name]
        if isinstance(route, tuple)
        for method, path in [route]
        if method.upper() in T._LOCK_GATE_WRITE_METHODS
    ]
    network = name in getattr(schema, "NETWORK_WRITES", ())
    lock = safety_tier_for(name) != "read" and (
        network
        or name in getattr(disp, "LOCK_GATED", ())
        or any(
            p.startswith(T._LOCK_GATE_PREFIXES) and p not in T._LOCK_GATE_EXEMPT_PATHS
            for p in writes
        )
    )
    study = (
        lock
        and name not in T._STUDY_GATE_SWAP_TOOLS
        and (network or any(p.startswith(T._STUDY_GATE_PREFIXES) for p in writes))
    )
    return lock, study


def gate_mismatches():
    """Every discovered fragment tool whose DISPATCHERS entry does not unwrap to
    the fragment's own callable through one wrapper per gate it earns; also the
    tools checked. Run in-process and in the probe interpreter below."""
    from services import chat_tools as T

    schemas = F.discover(F.SCHEMA_PACKAGE)
    lock_names, study_names = T._lock_gated_tool_names(), T._study_gated_tool_names()
    bad, checked = [], []
    for stem, disp in F.discover(F.DISPATCH_PACKAGE).items():
        for name, raw in disp.DISPATCHERS.items():
            lock, study = _expected_gates(name, schemas[stem], disp)
            fn, depth = T.DISPATCHERS[name], 0
            while fn is not raw and hasattr(fn, "__wrapped__"):
                fn, depth = fn.__wrapped__, depth + 1
            got = (fn is raw, depth, name in lock_names, name in study_names)
            if got != (True, lock + study, lock, study):
                bad.append((name, {"want": (lock, study), "got": got}))
            checked.append(name)
    return bad, checked


def test_every_fragment_tool_is_wrapped_once_per_gate_it_earns():
    bad, checked = gate_mismatches()
    assert bad == []
    assert set(_REPORT_TEMPLATES) <= set(checked)


# ── what a moved family copies or reaches in chat_tools ─────────────────────


def test_lv08_island_table_ids_equal_their_source():
    from harness.catalogue_tools import lv08_bcampus
    from services.campus_island_report import TABLE_IDS

    assert lv08_bcampus._ISLAND_TABLE_IDS == TABLE_IDS


def test_adequacy_studies_constants_equal_their_source():
    """The schema fragment imports only catalogue_base, so it copies these."""
    from harness.catalogue_tools import adequacy_studies as A
    from models.energy_hub import MAX_EH_BUDGET_SOLVES
    from services.adequacy.mc import MAX_DRAWS

    for name in (
        "ADEQUACY_STUDY_ENUM",
        "ADEQUACY_RESTORE_ENUM",
        "EH_ARCHETYPE_ENUM",
        "EH_STAGE_ENUM",
    ):
        assert getattr(A, name) == getattr(catalogue, name), name
    assert A._MAX_EH_BUDGET_SOLVES == MAX_EH_BUDGET_SOLVES
    assert A._MC_MAX_DRAWS == MAX_DRAWS


def test_the_campaign_writes_stay_lock_gated_and_non_network():
    """Membership only: the registry digest proves both merged sets equal master."""
    from services import chat_tools

    assert {
        "start_campaign",
        "end_campaign",
    } <= chat_tools._LOCK_GATE_SERVICE_CALL_MUTATORS
    assert {
        "start_campaign",
        "end_campaign",
    } <= catalogue._SERVICE_CALL_NON_NETWORK_WRITES
    assert "campaign_status" not in chat_tools._LOCK_GATE_SERVICE_CALL_MUTATORS


# Every inlined enum of the S5 schema fragments, by stem (catalogue keeps its own).
S5_CONSTANTS = {
    "explanation": ["INVESTMENT_CLASS_ENUM"],
    "ui_control": [
        "COMPONENT_CLASS_ENUM",
        "GUIDED_STEP_ENUM",
        "PANEL_SECTION_ENUM",
        "SAFETY_PANEL_ENUM",
        "RESULTS_TAB_ENUM",
        "BOTTOM_TAB_ENUM",
        "COMPARE_TAB_ENUM",
    ],
    "asset_results": [
        "ASSET_CATEGORY_ENUM",
        "ASSET_VIEW_MODE_ENUM",
        "ASSET_RESOLUTION_ENUM",
        "RESULTS_SOURCE_ENUM",
        "COMPONENT_CLASS_ENUM",
    ],
}


@pytest.mark.parametrize(
    "stem,name", [(s, n) for s, names in S5_CONSTANTS.items() for n in names]
)
def test_s5_constants_equal_catalogues(stem, name):
    mod = F.discover(F.SCHEMA_PACKAGE)[stem]
    assert getattr(mod, name) == getattr(catalogue, name)


def test_s5_constants_are_every_upper_case_name_of_the_fragment():
    for stem, names in S5_CONSTANTS.items():
        mod = F.discover(F.SCHEMA_PACKAGE)[stem]
        own = {
            n
            for n in vars(mod)
            if n.isupper() and not n.startswith("_") and n not in {"TOOLS", "ROUTES"}
        }
        assert own - {"NON_NETWORK_WRITES"} == set(names), stem


# Helpers and tables only their family uses, moved with it; chat_tools
# re-exports each, so both names are one object.
MOVED_HELPERS = {
    "explanation": ["_explain_kpis"],
    "ui_control": [
        "_PANEL_SECTIONS",
        "_PANEL_SECTION_ORDER",
        "_loadflow_navigation_args",
    ],
    "asset_results": ["_series_stats"],
}


@pytest.mark.parametrize(
    "stem,name", [(s, n) for s, names in MOVED_HELPERS.items() for n in names]
)
def test_a_moved_helper_is_the_chat_tools_object(stem, name):
    from services import chat_tools

    mod = F.discover(F.DISPATCH_PACKAGE)[stem]
    assert getattr(chat_tools, name) is getattr(mod, name)


def test_export_asset_results_stays_a_non_network_write():
    from harness.catalogue_tools import asset_results

    assert asset_results.NON_NETWORK_WRITES == {"export_asset_results"}
    assert "export_asset_results" in catalogue._SERVICE_CALL_NON_NETWORK_WRITES
    assert "get_asset_results" not in catalogue._SERVICE_CALL_NON_NETWORK_WRITES


def test_the_crud_attrs_table_is_read_from_chat_tools_at_call_time(monkeypatch):
    from services import chat_tools
    from services.chat_dispatchers import ui_control

    monkeypatch.setattr(chat_tools, "_GENERIC_CRUD_ATTRS", {"Probe": "probes"})
    assert ui_control._GENERIC_CRUD_ATTRS["Probe"] == "probes"
    assert "Bus" not in ui_control._GENERIC_CRUD_ATTRS


def _proxies():
    """(fragment, name) for each dispatcher-fragment helper that reaches a
    chat_tools helper of the same name through ``_ct()``."""
    from services import chat_tools

    return [
        (mod, name)
        for mod in F.discover(F.DISPATCH_PACKAGE).values()
        for name, fn in vars(mod).items()
        if name != "_ct"
        and isinstance(fn, types.FunctionType)
        and fn.__module__ == mod.__name__
        and fn.__code__.co_names == ("_ct", name)
        and hasattr(chat_tools, name)
    ]


def test_every_proxy_reaches_chat_tools_at_call_time(monkeypatch):
    """A proxy bound at import (``_x = _ct()._x``) would miss a later patch of
    ``chat_tools._x``, which tests rely on (test_lc27_island_report_table_arg)."""
    from services import chat_tools

    found = _proxies()
    assert {n for _, n in found} >= {
        "_campus_route",
        "_acting",
        "_b2b_fit",
        "_campus_check_lock",
        "_gridspine_project_for_write",
        "island_tool_args",
    }
    for mod, name in found:
        sentinel = object()
        monkeypatch.setattr(chat_tools, name, lambda *a, _s=sentinel, **k: _s)
        assert getattr(mod, name)(1, x=2) is sentinel, (mod.__name__, name)


# Every helper each dispatcher fragment reaches in chat_tools, by stem. A
# helper outside this list, or a proxy aimed at another helper (which
# `_proxies` skips), fails here rather than only in the lock and ACL tests.
PROXIES = {
    "report_templates": [],
    "lv08_bcampus": [
        "_acting",
        "_b2b_fit",
        "_campus_route",
        "_closing_uploads",
        "_cut",
        "_gridspine_project",
        "_gridspine_upload",
        "_with_sha",
    ],
    "campus_electrical": [
        "_acting",
        "_campus_check_lock",
        "_gridspine_project",
        "_gridspine_project_for_write",
        "island_tool_args",
    ],
    "adequacy_studies": [
        "_campaign_gated",
        "_resolve_adequacy_handler",
        "get_adequacy_results",
        "get_asset_health",
    ],
    "energization": [],
    "expert_catalogue": [],
    "explanation": ["get_asset_results", "get_results"],
    "ui_control": ["_acting"],
    "asset_results": ["_save_agent_export"],
}


def test_every_dispatcher_fragments_proxies_are_pinned():
    found = {}
    for mod, name in _proxies():
        found.setdefault(mod.__name__.rsplit(".", 1)[1], []).append(name)
    helpers = {
        stem: sorted(
            name
            for name, fn in vars(mod).items()
            if name != "_ct"
            and isinstance(fn, types.FunctionType)
            and fn.__module__ == mod.__name__
            and name not in mod.DISPATCHERS
            and name not in MOVED_HELPERS.get(stem, ())
        )
        for stem, mod in F.discover(F.DISPATCH_PACKAGE).items()
    }
    assert helpers == PROXIES
    assert {stem: sorted(found.get(stem, [])) for stem in PROXIES} == PROXIES


def test_the_adequacy_tables_are_read_from_chat_tools_at_call_time(monkeypatch):
    """review_eh_study and abort_adequacy_study read two chat_tools dicts
    through a call-time view, so a patched dict is the one they see."""
    from routers import results as R
    from services import chat_tools
    from services.adequacy import eh_review
    from services.chat_dispatchers import adequacy_studies as A

    views = {
        name
        for mod in F.discover(F.DISPATCH_PACKAGE).values()
        for name, v in vars(mod).items()
        if type(v).__name__ == "_CtTable"
    }
    assert views == {
        "_ADEQUACY_NO_DATA_HINTS",
        "_ADEQUACY_ABORT_HANDLER_NAMES",
        "_GENERIC_CRUD_ATTRS",
    }

    monkeypatch.setattr(
        chat_tools, "_ADEQUACY_NO_DATA_HINTS", {"eh_reference_design": "patched"}
    )
    monkeypatch.setattr(R, "get_eh_study", lambda: None)
    monkeypatch.setattr(
        eh_review,
        "review_latest",
        lambda state, record, no_data_message: no_data_message,
    )
    assert A.review_eh_study() == "patched"

    monkeypatch.setattr(
        chat_tools, "_ADEQUACY_ABORT_HANDLER_NAMES", {"probe": "_probe_abort"}
    )
    monkeypatch.setattr(R, "_probe_abort", lambda: "stopped", raising=False)
    assert A.abort_adequacy_study("probe") == "stopped"
    with pytest.raises(Exception, match="Known: probe"):
        A.abort_adequacy_study("frontier")


def test_a_moved_dispatcher_calls_the_patched_chat_tools_helper(monkeypatch):
    from services import chat_tools
    from services.chat_dispatchers import lv08_bcampus

    monkeypatch.setattr(chat_tools, "_campus_route", lambda *a, **k: {"job": "patched"})
    assert lv08_bcampus.campus_milp_status("P")["job"] == {"job": "patched"}


def test_a_campus_electrical_dispatcher_calls_the_patched_public_helper(monkeypatch):
    from services import chat_tools
    from services.chat_dispatchers import campus_electrical

    class Patched(Exception):
        pass

    def refuse(*a, **k):
        raise Patched

    monkeypatch.setattr(chat_tools, "island_tool_args", refuse)
    with pytest.raises(Patched):
        campus_electrical.campus_set_island_config("P", config={})


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


def _dispatcher_stems():
    """The dispatcher fragments on disk, listed without importing them."""
    folder = BACKEND / "services" / "chat_dispatchers"
    return sorted(p.stem for p in folder.glob("[!_]*.py"))


def test_the_circular_import_cases_are_exactly_the_discovered_fragments():
    stems = _dispatcher_stems()
    assert stems == [
        "adequacy_studies",
        "asset_results",
        "campus_electrical",
        "energization",
        "expert_catalogue",
        "explanation",
        "lv08_bcampus",
        "report_templates",
        "ui_control",
    ]
    assert stems == sorted(F.discover(F.DISPATCH_PACKAGE))


@pytest.mark.parametrize("stem", _dispatcher_stems())
def test_every_dispatcher_fragment_imported_first_is_not_circular(stem):
    """chat_tools discovers the dispatcher fragments part-way through its own
    import, so a fragment that imports a chat_tools name at its top would fail
    whichever module a fresh interpreter imports first."""
    code = (
        f"import services.chat_dispatchers.{stem} as R\n"
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


def _check_bundle():
    sys.path.insert(0, str(BACKEND / "smoke"))
    try:
        import check_bundle
    finally:
        sys.path.pop(0)
    return check_bundle


def _fake_bundle(root, skip=None):
    """A onedir-shaped tree: alembic.ini at the frozen root, every fragment
    module written beside it as the spec's "pyz+py" collection does."""
    frozen = root / "_internal"
    frozen.mkdir(parents=True)
    (frozen / "alembic.ini").write_text("")
    for pkg in (F.SCHEMA_PACKAGE, F.DISPATCH_PACKAGE):
        rel = Path(*pkg.split("."))
        (frozen / rel).mkdir(parents=True)
        for src in (BACKEND / rel).glob("[!_]*.py"):
            if (rel / src.name).as_posix() != skip:
                (frozen / rel / src.name).write_text("")
    return root


def test_the_bundle_check_reads_the_spec_fragment_packages():
    packages = _check_bundle().FRAGMENT_PACKAGES
    assert packages == (F.SCHEMA_PACKAGE, F.DISPATCH_PACKAGE)
    assert f"FRAGMENT_PACKAGES = {packages!r}".replace("'", '"') in SPEC.read_text(
        encoding="utf-8"
    )


def test_the_bundle_check_main_refuses_a_bundle_missing_a_fragment(tmp_path):
    """End to end through ``main``: an otherwise clean bundle passes, and the
    same bundle less one fragment module is refused."""
    import plistlib

    cb = _check_bundle()

    def bundle(root, skip=None):
        _fake_bundle(root, skip=skip)
        (root / "Contents").mkdir()
        with (root / "Contents" / "Info.plist").open("wb") as fh:
            plistlib.dump({k: "Used for chat." for k in cb.REQUIRED_PLIST_KEYS}, fh)
        return str(root)

    assert cb.main(["check_bundle.py", bundle(tmp_path / "whole")]) == 0
    skip = "services/chat_dispatchers/report_templates.py"
    assert cb.main(["check_bundle.py", bundle(tmp_path / "less", skip=skip)]) == 1


def test_the_bundle_check_refuses_when_the_source_tree_has_no_fragments(
    tmp_path, monkeypatch
):
    cb = _check_bundle()
    monkeypatch.setattr(cb, "_BACKEND", tmp_path / "nowhere")
    problems = cb.check_fragments(_fake_bundle(tmp_path / "bundle"))
    assert len(problems) == 2 and "no fragment modules" in problems[0], problems


def test_the_bundle_check_passes_a_bundle_with_every_fragment(tmp_path):
    assert _check_bundle().check_fragments(_fake_bundle(tmp_path)) == []


@pytest.mark.parametrize(
    "skip",
    [
        "harness/catalogue_tools/report_templates.py",
        "services/chat_dispatchers/report_templates.py",
    ],
)
def test_the_bundle_check_refuses_a_bundle_missing_a_fragment(tmp_path, skip):
    cb = _check_bundle()
    problems = cb.check_fragments(_fake_bundle(tmp_path, skip=skip))
    assert len(problems) == 1 and skip in problems[0], problems
    assert cb.check_fragments(tmp_path / "_internal" / "harness") != []


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

from tests.test_tool_fragments import gate_mismatches
bad, checked = gate_mismatches()
out = {"last": [t["name"] for t in C.TOOLS[-3:]],
       "gate_mismatches": bad, "gate_checked": checked,
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
    assert out["gate_mismatches"] == [], out["gate_mismatches"]
    assert {"zz_probe_edit", "zz_probe_apply", "zz_probe_save"} <= set(
        out["gate_checked"]
    )
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
