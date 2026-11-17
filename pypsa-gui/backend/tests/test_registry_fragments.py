"""Per-route and per-tool registries take `.d/` fragments (RETRO-FRAG-1).

A new route or tool adds its own fragment file instead of a row in a shared
Python table. Each test copies a real base JSON into ``tmp_path`` and drives the
registry's loader with fragments there. The checks keep their strength: a key
defined twice, a fragment for an unknown route and a stale row all still fail.
"""

from __future__ import annotations

import json
import shutil

import pytest

from smoke import family_manifest as fm
from smoke.json_fragments import extend, extend_items, fragment_files, route_key
from services import not_via_chat as nvc
from tests import test_golden_coverage as golden
from tests import test_route_chat_disposition as disposition
from tests import test_write_surface_lock_policy as lock
from tests.test_workflow_tools import helpers_state  # noqa: F401  (the client fixture's state)


@pytest.fixture
def copy(tmp_path):
    """``copy(real_base)`` -> ``(base, fragment)``: the base copied into tmp with an
    empty ``.d/``, and ``fragment(name, data)`` writing one fragment there."""

    def make(real):
        base = tmp_path / real.name
        shutil.copyfile(real, base)
        base.with_suffix(".d").mkdir()

        def fragment(name, data):
            path = base.with_suffix(".d") / name
            path.write_text(data if isinstance(data, str) else json.dumps(data))
            return path

        return base, fragment

    return make


# ── the generic helpers ─────────────────────────────────────────────────────


@pytest.fixture
def table(tmp_path):
    base = tmp_path / "t.json"
    base.write_text(json.dumps({"rows": {}, "items": []}))
    base.with_suffix(".d").mkdir()
    return base


def _put(base, name, data):
    (base.with_suffix(".d") / name).write_text(json.dumps(data))


def test_extend_adds_fragment_rows_after_the_python_rows(table):
    _put(table, "b.json", {"rows": {"y": 2}})
    _put(table, "a.json", {"rows": {"x": 1}})
    assert list(extend({"w": 0}, table, "rows").items()) == [
        ("w", 0),
        ("x", 1),
        ("y", 2),
    ]


def test_extend_refuses_a_key_the_python_table_defines_and_names_the_file(table):
    _put(table, "claude-x.json", {"rows": {"w": 9}})
    with pytest.raises(
        ValueError, match=r"claude-x\.json: rows .w. is already defined"
    ):
        extend({"w": 0}, table, "rows")


def test_extend_refuses_two_spellings_of_one_converted_key(table):
    _put(table, "a.json", {"rows": {"GET /x": 1}})
    _put(table, "b.json", {"rows": {"GET  /x": 1}})
    with pytest.raises(ValueError, match=r"b\.json: rows"):
        extend({}, table, "rows", lambda k, v: (route_key(k.replace("  ", " ")), v))


def test_extend_reports_a_bad_row_against_its_file(table):
    _put(table, "claude-x.json", {"rows": {"post /x": 1}})
    with pytest.raises(
        ValueError, match=r"claude-x\.json: rows .post /x.: a route key"
    ):
        extend({}, table, "rows", lambda k, v: (route_key(k), v))


def test_extend_needs_the_section_in_the_base(table):
    with pytest.raises(ValueError, match="needs a dict .missing."):
        extend({}, table, "missing")
    _put(table, "a.json", {"unknown": {}})
    with pytest.raises(ValueError, match="unknown key"):
        extend({}, table, "rows")


def test_extend_items_keeps_base_then_file_name_order_and_refuses_a_known_id(table):
    _put(table, "b.json", {"items": [{"id": "c"}]})
    _put(table, "a.json", {"items": [{"id": "b"}]})
    assert [i["id"] for i in extend_items([{"id": "a"}], table, "items")] == [
        "a",
        "b",
        "c",
    ]
    _put(table, "c.json", {"items": [{"id": "a"}]})
    with pytest.raises(ValueError, match=r"c\.json: items 'a' is already defined"):
        extend_items([{"id": "a"}], table, "items")


@pytest.mark.parametrize("text", ["post /x", "POST x", "POST", "POST /a b", " /x"])
def test_a_route_key_is_method_space_path(text):
    with pytest.raises(ValueError):
        route_key(text)


# ── write-surface lock policy ───────────────────────────────────────────────

_ROUTE = ("POST", "/api/projects/{project_id}/activate")  # a base ROUTE_POLICY row


def test_lock_policy_fragment_rows_join_the_tables(copy):
    base, fragment = copy(lock.POLICY_FILE)
    fragment(
        "claude-x.json",
        {
            "routes": {
                "POST /api/new": {
                    "category": lock.NOT_PROJECT_DATA,
                    "reason": "a reason",
                }
            },
            "tools": {
                "new_tool": {"category": lock.CHAT_SESSION, "reason": "a reason"}
            },
            "known_gaps": {"gap_tool": "a defect"},
        },
    )
    routes, tools, gaps = lock.policy_tables(base)
    assert routes[("POST", "/api/new")] == (lock.NOT_PROJECT_DATA, "a reason")
    assert tools["new_tool"] == (lock.CHAT_SESSION, "a reason")
    assert gaps == {**lock.KNOWN_GAPS, "gap_tool": "a defect"}


def test_a_lock_policy_fragment_cannot_change_a_base_routes_category(copy):
    base, fragment = copy(lock.POLICY_FILE)
    fragment(
        "claude-x.json",
        {
            "routes": {
                f"{_ROUTE[0]} {_ROUTE[1]}": {
                    "category": lock.NOT_PROJECT_DATA,
                    "reason": "no lock needed after all",
                }
            }
        },
    )
    with pytest.raises(ValueError, match="already defined"):
        lock.policy_tables(base)


@pytest.mark.parametrize(
    "row",
    [
        {"category": "not-a-category", "reason": "r"},
        {"category": lock.CHAT_SESSION, "reason": ""},
        {"category": lock.CHAT_SESSION},
        [lock.CHAT_SESSION, "r"],
    ],
)
def test_a_malformed_lock_decision_fails(copy, row):
    base, fragment = copy(lock.POLICY_FILE)
    fragment("claude-x.json", {"tools": {"t": row}})
    with pytest.raises(ValueError, match=r"claude-x\.json: tools"):
        lock.policy_tables(base)


def test_a_lock_fragment_for_a_missing_route_is_stale(copy, monkeypatch):
    base, fragment = copy(lock.POLICY_FILE)
    fragment(
        "claude-x.json",
        {
            "routes": {
                "POST /api/no/such/route": {
                    "category": lock.NOT_PROJECT_DATA,
                    "reason": "r",
                }
            }
        },
    )
    monkeypatch.setattr(lock, "ROUTE_POLICY", lock.policy_tables(base)[0])
    with pytest.raises(AssertionError, match="/api/no/such/route"):
        lock.test_no_route_policy_entry_is_stale_or_redundant()


def test_lock_fragments_never_change_a_base_row():
    routes, tools, gaps = lock.policy_tables()
    for merged, python in zip((routes, tools, gaps), lock.PYTHON_TABLES):
        assert {k: merged[k] for k in python} == python
    assert (routes, tools, gaps) == (
        lock.ROUTE_POLICY,
        lock.TOOL_POLICY,
        lock.KNOWN_GAPS,
    )


# ── route chat disposition ──────────────────────────────────────────────────


def test_disposition_fragment_rows_join_the_tables(copy):
    base, fragment = copy(disposition.DISPOSITION_FILE)
    tool = sorted(disposition.SERVED_BY_TOOL.values())[0]
    fragment(
        "claude-x.json",
        {
            "served_by_tool": {"GET /api/new": tool},
            "ui_internal": {
                "new_bucket": {
                    "reason": "only the app calls it",
                    "routes": ["GET /api/ui"],
                }
            },
            "known_gaps": {"GET /api/aaa": "Some panel"},
        },
    )
    served, ui, gaps = disposition.disposition_tables(base)
    assert served[("GET", "/api/new")] == tool
    assert ui["new_bucket"] == {
        "reason": "only the app calls it",
        "routes": frozenset({("GET", "/api/ui")}),
    }
    assert gaps[("GET", "/api/aaa")] == "Some panel"
    assert list(gaps) == sorted(gaps)  # merged gaps stay sorted, as the base must be


def test_a_disposition_fragment_cannot_reassign_a_served_route(copy):
    base, fragment = copy(disposition.DISPOSITION_FILE)
    method, path = next(iter(disposition.SERVED_BY_TOOL))
    fragment(
        "claude-x.json", {"served_by_tool": {f"{method} {path}": "some_other_tool"}}
    )
    with pytest.raises(ValueError, match="already defined"):
        disposition.disposition_tables(base)


def test_a_disposition_fragment_for_a_missing_route_fails(copy, monkeypatch):
    base, fragment = copy(disposition.DISPOSITION_FILE)
    tool = sorted(disposition.SERVED_BY_TOOL.values())[0]
    fragment("claude-x.json", {"served_by_tool": {"GET /api/no/such/route": tool}})
    monkeypatch.setattr(
        disposition, "SERVED_BY_TOOL", disposition.disposition_tables(base)[0]
    )
    with pytest.raises(AssertionError, match="/api/no/such/route"):
        disposition.test_no_disposition_names_a_missing_route()


def test_a_fragment_known_gap_counts_against_the_ceiling(copy, monkeypatch):
    base, fragment = copy(disposition.DISPOSITION_FILE)
    fragment(
        "claude-x.json",
        {
            "known_gaps": {
                f"GET /api/extra/{i}": "Some panel"
                for i in range(disposition._KNOWN_GAPS_CEILING + 1)
            }
        },
    )
    monkeypatch.setattr(
        disposition, "KNOWN_GAPS", disposition.disposition_tables(base)[2]
    )
    with pytest.raises(AssertionError):
        disposition.test_known_gaps_only_shrink()


def test_disposition_fragments_never_change_a_base_row():
    merged = disposition.disposition_tables()
    for table, python in zip(merged, disposition.PYTHON_TABLES):
        assert {k: table[k] for k in python} == python
    assert merged == (
        disposition.SERVED_BY_TOOL,
        disposition.UI_INTERNAL,
        disposition.KNOWN_GAPS,
    )


def test_a_stale_fragment_known_gap_is_named_by_its_file(copy, monkeypatch):
    base, fragment = copy(disposition.DISPOSITION_FILE)
    route = next(iter(disposition.SERVED_BY_TOOL))
    path = fragment(
        "claude-x.json", {"known_gaps": {f"{route[0]} {route[1]}": "Some panel"}}
    )
    monkeypatch.setattr(disposition, "DISPOSITION_FILE", base)
    monkeypatch.setattr(
        disposition, "KNOWN_GAPS", disposition.disposition_tables(base)[2]
    )
    assert disposition.stale_known_gaps() == [route]
    assert disposition.known_gap_origin(route) == path


# ── not via chat ────────────────────────────────────────────────────────────


def _entry(ident, **over):
    return {
        "id": ident,
        "title": "A thing",
        "disposition": "out of chat",
        "reason": "This is done on its own page, which chat cannot reach for you.",
        "page": "Some page",
        "panel": None,
        "keywords": ["zzqx"],
        "routes": [["GET", "/api/zzqx"]],
        **over,
    }


def test_not_via_chat_fragment_entries_join_in_file_name_order(copy):
    base, fragment = copy(nvc.ENTRIES_FILE)
    fragment("claude-b.json", {"entries": [_entry("b_thing")]})
    fragment("claude-a.json", {"entries": [_entry("a_thing")]})
    entries = nvc.load_entries(base)
    assert [e["id"] for e in entries[-2:]] == ["a_thing", "b_thing"]
    assert entries[-1]["keywords"] == frozenset({"zzqx"})
    assert entries[-1]["routes"] == frozenset({("GET", "/api/zzqx")})


def test_tied_not_via_chat_matches_rank_base_then_fragment_file_name(copy, monkeypatch):
    base, fragment = copy(nvc.ENTRIES_FILE)
    fragment("claude-b.json", {"entries": [_entry("b_thing", title="B thing")]})
    fragment("claude-a.json", {"entries": [_entry("a_thing", title="A thing")]})
    monkeypatch.setattr(nvc, "NOT_VIA_CHAT", nvc.load_entries(base))
    assert [m["title"] for m in nvc.matches({"zzqx"})] == ["A thing", "B thing"]


@pytest.mark.parametrize(
    "over",
    [
        {"id": "admin_users_and_orgs"},  # a base id
        {"keywords": "zzqx"},
        {"routes": [["GET", "/api/x", "extra"]]},
        {"extra_field": 1},
    ],
)
def test_a_bad_not_via_chat_fragment_fails_the_import(copy, over):
    base, fragment = copy(nvc.ENTRIES_FILE)
    fragment("claude-x.json", {"entries": [_entry("x_thing", **over)]})
    with pytest.raises(ValueError, match=r"claude-x\.json"):
        nvc.load_entries(base)


def test_a_goal_must_name_a_known_entry(copy):
    base, fragment = copy(nvc.ENTRIES_FILE)
    fragment("claude-x.json", {"goals": {"Do the thing": "no_such_entry"}})
    with pytest.raises(ValueError, match=r"claude-x\.json.*no_such_entry"):
        nvc.load_goals(base)
    fragment(
        "claude-x.json",
        {"entries": [_entry("x_thing")], "goals": {"Do the thing": "x_thing"}},
    )
    assert nvc.load_goals(base) == {"Do the thing": "x_thing"}


def test_not_via_chat_fragments_never_change_a_base_row():
    assert nvc.load_entries()[: len(nvc.PYTHON_ENTRIES)] == nvc.PYTHON_ENTRIES
    assert nvc.load_entries() == nvc.NOT_VIA_CHAT


# ── family manifest ─────────────────────────────────────────────────────────


def _covered(vid, tool):
    return {
        "id": vid,
        "status": "covered",
        "oracle": "hand number",
        "tools": [tool],
        "tests": [fm.B + "test_x.py::test_y"],
    }


def _first(status):
    return next(v for v in fm.VARIANTS if v["status"] == status)


_PENDING = {"id": "LC-D01.zz_pending", "status": "pending", "reason": "not built yet"}


def _with_pending(fragment):
    """No base variant is pending today, so a fragment adds one to settle."""
    fragment("claude-a.json", {"variants": [_PENDING]})
    return _PENDING


def test_family_fragments_add_tools_variants_and_additions(copy):
    base, fragment = copy(fm.MANIFEST_FILE)
    family = sorted(fm.FAMILIES)[0]
    covered = _first("covered")
    fragment(
        "claude-x.json",
        {
            "tool_families": {"brand_new_tool": family},
            "variants": [_covered("LC-D01.brand_new", "brand_new_tool")],
            "variant_additions": [
                {
                    "id": covered["id"],
                    "tools": ["brand_new_tool"],
                    "tests": [fm.B + "test_x.py::test_z"],
                }
            ],
        },
    )
    tools, variants, settled = fm.merged_tables(base)
    assert "brand_new_tool" in tools[family]
    assert variants[-1]["id"] == "LC-D01.brand_new"
    grown = next(v for v in variants if v["id"] == covered["id"])
    assert grown["tools"][-1] == "brand_new_tool" and grown["tests"][-1].endswith(
        "::test_z"
    )
    assert settled == {}


@pytest.mark.parametrize(
    "data, match",
    [
        ({"tool_families": {"t": "no_such_family"}}, "no_such_family"),
        (
            lambda: {
                "tool_families": {
                    sorted(fm.TOOLS_BY_FAMILY[sorted(fm.FAMILIES)[0]])[0]: sorted(
                        fm.FAMILIES
                    )[1]
                }
            },
            "already in",
        ),
        (
            lambda: {"variants": [_covered(_first("covered")["id"], "t")]},
            "already defined",
        ),
        (
            {"variant_additions": [{"id": "LC-D01.no_such", "tests": []}]},
            "LC-D01.no_such",
        ),
        (
            {
                "variants": [_PENDING],
                "variant_additions": [{"id": _PENDING["id"], "tools": ["t"]}],
            },
            "not covered",
        ),
        (
            lambda: {
                "variant_additions": [
                    {
                        "id": _first("covered")["id"],
                        "tests": [_first("covered")["tests"][0]],
                    }
                ]
            },
            "already lists",
        ),
    ],
)
def test_a_bad_family_fragment_fails(copy, data, match):
    base, fragment = copy(fm.MANIFEST_FILE)
    fragment("claude-x.json", data() if callable(data) else data)
    with pytest.raises(ValueError, match=match):
        fm.merged_tables(base)


def test_a_fragment_settles_a_pending_variant_once(copy):
    base, fragment = copy(fm.MANIFEST_FILE)
    pending = _with_pending(fragment)
    tool = sorted(fm.inventory_tools())[0]
    path = fragment("claude-x.json", {"settles": [_covered(pending["id"], tool)]})
    _, variants, settled = fm.merged_tables(base)
    assert [v["status"] for v in variants if v["id"] == pending["id"]] == ["covered"]
    assert settled == {pending["id"]: path}
    assert [i["id"] for i in variants] == [v["id"] for v in fm.VARIANTS] + [
        pending["id"]
    ]  # in place
    assert any(
        pending["id"] in line and "claude-x.json" in line
        for line in fm.stale_rows(settled)
    )

    fragment(
        "claude-y.json",
        {"settles": [{**_covered(pending["id"], tool), "oracle": "another number"}]},
    )
    with pytest.raises(ValueError, match=r"claude-y\.json.*already settled"):
        fm.merged_tables(base)


@pytest.mark.parametrize("status", ["covered", "unavailable", None])
def test_only_a_pending_variant_can_be_settled(copy, status):
    base, fragment = copy(fm.MANIFEST_FILE)
    vid = _first(status)["id"] if status else "LC-D01.no_such"
    fragment("claude-x.json", {"settles": [_covered(vid, "t")]})
    with pytest.raises(ValueError, match="only a pending variant"):
        fm.merged_tables(base)


def test_manifest_problems_run_on_the_settled_view(copy, monkeypatch):
    base, fragment = copy(fm.MANIFEST_FILE)
    fragment(
        "claude-x.json",
        {"settles": [_covered(_with_pending(fragment)["id"], "not_an_inventory_tool")]},
    )
    tools, variants, _ = fm.merged_tables(base)
    problems = fm.manifest_problems(
        tools_by_family=tools, variants=variants, collect=False
    )
    assert any("not_an_inventory_tool is not an inventory tool" in p for p in problems)


def test_family_fragments_never_change_a_base_row():
    tools, variants, settled = fm.merged_tables()
    assert all(tools[f] >= names for f, names in fm.PYTHON_TOOLS_BY_FAMILY.items())
    assert set(tools) == set(fm.PYTHON_TOOLS_BY_FAMILY)
    for before, after in zip(fm.PYTHON_VARIANTS, variants):
        assert before["id"] == after["id"]
        if before["id"] in settled:
            assert before["status"] == "pending"
        elif (
            before != after
        ):  # only a covered variant grows, and only at the end of its lists
            assert after["tools"][: len(before["tools"])] == before["tools"]
            assert after["tests"][: len(before["tests"])] == before["tests"]
    assert (tools, variants, settled) == (fm.TOOLS_BY_FAMILY, fm.VARIANTS, fm.SETTLED)


# ── golden route surfaces ───────────────────────────────────────────────────


def test_golden_route_surface_fragments_join_and_cannot_redefine(copy):
    base, fragment = copy(golden.ROUTE_SURFACES_FILE)
    fragment("claude-x.json", {"routes": {"routers/results.py::brand_new": []}})
    assert (
        golden.route_surfaces(base)[("routers/results.py", "brand_new")] == frozenset()
    )
    file, fn = next(iter(golden.PYTHON_ROUTE_SURFACES))
    fragment("claude-y.json", {"routes": {f"{file}::{fn}": []}})
    with pytest.raises(ValueError, match="already defined"):
        golden.route_surfaces(base)


def test_a_golden_fragment_for_a_missing_handler_fails(copy, monkeypatch):
    base, fragment = copy(golden.ROUTE_SURFACES_FILE)
    fragment("claude-x.json", {"routes": {"routers/results.py::no_such_handler": []}})
    monkeypatch.setattr(golden, "ROUTE_SURFACES", golden.route_surfaces(base))
    with pytest.raises(AssertionError, match="no_such_handler"):
        golden.test_every_route_handler_is_declared_in_the_surfaces_allowlist()


def test_golden_fragments_never_change_a_base_row():
    merged = golden.route_surfaces()
    assert {
        k: merged[k] for k in golden.PYTHON_ROUTE_SURFACES
    } == golden.PYTHON_ROUTE_SURFACES
    assert merged == golden.ROUTE_SURFACES


# ── every registry folder ───────────────────────────────────────────────────

REGISTRY_FILES = (
    lock.POLICY_FILE,
    disposition.DISPOSITION_FILE,
    nvc.ENTRIES_FILE,
    fm.MANIFEST_FILE,
    golden.ROUTE_SURFACES_FILE,
)


@pytest.mark.parametrize("base", REGISTRY_FILES, ids=lambda p: p.name)
def test_every_registry_has_a_fragment_folder_with_a_readme(base):
    folder = base.with_suffix(".d")
    assert (folder / "README.md").is_file()
    fragment_files(base)  # only *.json and README.md, as json_fragments requires


@pytest.fixture
def discovery(client):
    from harness.session import ChatSession
    from services import chat_tools

    chat_tools.set_chat_session(ChatSession())
    return chat_tools


@pytest.mark.parametrize("goal, entry_id", sorted(nvc.load_goals().items()))
def test_every_fragment_goal_finds_its_entry_first(discovery, goal, entry_id):
    expected = next(e for e in nvc.NOT_VIA_CHAT if e["id"] == entry_id)
    assert (
        discovery.find_capabilities(goal)["not_via_chat"][0]["title"]
        == expected["title"]
    )


def test_the_frozen_app_ships_the_not_via_chat_json_and_its_folder():
    from tests.test_packaging_requirements import _check_bundle, _spec_backend_datas

    datas = _spec_backend_datas()
    assert datas.get("services/not_via_chat.json") == "services", datas
    assert datas.get("services/not_via_chat.d") == "services/not_via_chat.d", datas
    assert "not_via_chat.json" in _check_bundle().EXPECTED


def test_the_sweep_prunes_a_stale_fragment_known_gap_and_its_empty_file(
    copy, tmp_path, monkeypatch
):
    from smoke import prune_stale_declarations as prune

    base, fragment = copy(disposition.DISPOSITION_FILE)
    route = next(iter(disposition.SERVED_BY_TOOL))
    path = fragment(
        "claude-x.json", {"known_gaps": {f"{route[0]} {route[1]}": "Some panel"}}
    )
    source = tmp_path / "disposition.py"
    shutil.copyfile(prune.DISPOSITION, source)
    monkeypatch.setattr(prune, "DISPOSITION", source)
    monkeypatch.setattr(disposition, "DISPOSITION_FILE", base)
    monkeypatch.setattr(
        disposition, "KNOWN_GAPS", disposition.disposition_tables(base)[2]
    )

    assert prune.prune_known_gaps(apply=False) == [
        f"KNOWN_GAPS {route[0]} {route[1]} (claude-x.json)"
    ]
    assert path.exists()
    prune.prune_known_gaps(apply=True)
    assert not path.exists()  # its only row went, so the fragment went
    assert (
        source.read_text() == prune.DISPOSITION.read_text()
    )  # the Python rows are untouched
