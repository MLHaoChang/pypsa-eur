"""
template-e2e 10 — the reliability study never changes the user's grid
connection limit without an accept (owner decision TE2E-10-ASK-1).

Before Run, ``archetypes.planned_changes`` lists every user value the study
pack would change, in plain words (no raw ids). A weak-grid study keeps the
user's connection limit unless the request says ``connection_limit: accept``.
One case per weak-grid template family (data centre, concentrator, ammonia).
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

from models.energy_hub import (
    default_off_grid_pack,
    default_strong_grid_pack,
    default_weak_flexible_pack,
)
from services.adequacy import archetypes as A
from services.adequacy import eh_study_runner as RUN

TPL_DIR = pathlib.Path(__file__).resolve().parents[1] / "project_templates"
sys.path.insert(0, str(TPL_DIR))
import eh_templates as T  # noqa: E402

#: Words a user never reads in this list: link ids, attribute and pack names.
_RAW = re.compile(r"grid_import|subsea_tie|p_nom|p_max_pu|weak_flexible|off_grid|"
                  r"import_p_nom_mw|\bLink\b|'")

# (template, connection limit the user built, the cap the study would use)
FAMILIES = [
    ("eh_concentrator_staged", 35.0, 50.0),   # raised
    ("eh_ammonia_staged", 200.0, 50.0),       # lowered
    ("eh_datacenter_staged", 55.0, 55.0),     # same limit, expansion bound only
]


def _weak_pack(tid: str):
    po = (T.TEMPLATE_META.get(tid) or {}).get("pack_overrides") or {}
    keep = {k: v for k, v in po.items() if k == "import_p_nom_mw"}
    return RUN.apply_pack_overrides(default_weak_flexible_pack(), keep or None)


def _limits(n, links):
    return {k: (float(n.links.at[k, "p_nom"]), float(n.links.at[k, "p_nom_max"]))
            for k in links}


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_planned_changes_list_the_connection_change_in_plain_words(tid, built, cap):
    n = T.BUILDERS[tid]()
    rows = A.planned_changes(n, _weak_pack(tid))
    kinds = {r["kind"]: r for r in rows}
    if built != cap:
        row = kinds["connection_limit"]
        assert (row["before_mw"], row["after_mw"]) == (built, cap)
        assert f"{cap:g} MW" in row["text"] and f"{built:g} MW" in row["text"]
    else:
        assert "connection_limit" not in kinds
        assert kinds["expansion_limit"]["after_mw"] == cap
    assert rows and all(r["needs_accept"] for r in rows)
    for r in rows:
        assert not _RAW.search(r["text"]), r["text"]


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_planned_changes_do_not_touch_the_network(tid, built, cap):
    n = T.BUILDERS[tid]()
    links = A.select_import_links(n, _weak_pack(tid).import_overlay)
    before = _limits(n, links)
    A.planned_changes(n, _weak_pack(tid))
    assert _limits(n, links) == before


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_keep_leaves_the_connection_as_built(tid, built, cap):
    n = T.BUILDERS[tid]()
    pack, keep = RUN.resolve_connection_choice(n, _weak_pack(tid), None)
    assert keep is True
    links = A.select_import_links(n, pack.import_overlay)
    before = _limits(n, links)
    res = A.apply_archetype_pack_detailed(n, pack, keep_connection=keep)
    assert _limits(n, links) == before
    # The study's cap is the user's own limit, so the report states it.
    assert pack.import_overlay.import_p_nom_mw == pytest.approx(built)
    assert res.warnings and all(not _RAW.search(w) for w in res.warnings)
    assert any(f"{built:g} MW" in w for w in res.warnings)


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_accept_applies_the_study_cap_with_plain_warnings(tid, built, cap):
    n = T.BUILDERS[tid]()
    pack, keep = RUN.resolve_connection_choice(n, _weak_pack(tid), "accept")
    assert keep is False
    links = A.select_import_links(n, pack.import_overlay)
    res = A.apply_archetype_pack_detailed(n, pack, keep_connection=keep)
    assert sum(float(n.links.at[k, "p_nom"]) for k in links) == pytest.approx(cap)
    assert res.warnings and all(not _RAW.search(w) for w in res.warnings)
    res.undo()
    assert sum(float(n.links.at[k, "p_nom"]) for k in links) == pytest.approx(built)


def test_explicit_keep_is_the_same_as_no_choice():
    n = T.BUILDERS["eh_concentrator_staged"]()
    pack = _weak_pack("eh_concentrator_staged")
    a = RUN.resolve_connection_choice(n, pack, None)
    b = RUN.resolve_connection_choice(n, pack, "keep")
    assert (a[0].model_dump(), a[1]) == (b[0].model_dump(), b[1])


def test_a_bad_choice_is_refused():
    n = T.BUILDERS["eh_concentrator_staged"]()
    with pytest.raises(ValueError):
        RUN.resolve_connection_choice(n, _weak_pack("eh_concentrator_staged"), "maybe")


def test_strong_grid_plans_no_change():
    n = T.BUILDERS["eh_ammonia_staged"]()
    assert A.planned_changes(n, default_strong_grid_pack()) == []


def test_off_grid_lists_the_closed_connection_in_plain_words():
    n = T.BUILDERS["eh_microgrid_staged"]()
    rows = A.planned_changes(n, default_off_grid_pack())
    assert [r["kind"] for r in rows] == ["connection_closed"]
    assert rows[0]["after_mw"] == 0.0
    assert not _RAW.search(rows[0]["text"])
