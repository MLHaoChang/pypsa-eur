"""
template-e2e 10 — the reliability study never changes the user's grid
connection limit without an accept (owner decision TE2E-10-ASK-1).

Before Run, ``archetypes.planned_changes`` lists every user value the study
pack would change, in plain words (no raw ids), from the same writes the
apply makes; ``keep_connection`` leaves the user's connection as built.
The runner's keep/accept choice lands in a follow-up PR; this pins the
engine. One case per weak-grid template family (data centre, concentrator, ammonia).
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
_RAW = re.compile(
    r"grid_import|subsea_tie|p_nom|p_max_pu|weak_flexible|off_grid|"
    r"import_p_nom_mw|\bLink\b|'"
)

# (template, connection limit the user built, the cap the study would use)
FAMILIES = [
    ("eh_concentrator_staged", 35.0, 50.0),  # raised
    ("eh_ammonia_staged", 200.0, 50.0),  # lowered
    ("eh_datacenter_staged", 55.0, 55.0),  # same limit, expansion bound only
]


_BUILT: dict = {}


def _build(tid: str):
    """A fresh copy of the template network (each template built once)."""
    if tid not in _BUILT:
        _BUILT[tid] = T.BUILDERS[tid]()
    return _BUILT[tid].copy()


def _weak_pack(tid: str):
    po = (T.TEMPLATE_META.get(tid) or {}).get("pack_overrides") or {}
    keep = {k: v for k, v in po.items() if k == "import_p_nom_mw"}
    return RUN.apply_pack_overrides(default_weak_flexible_pack(), keep or None)


def _limits(n, links):
    return {
        k: (float(n.links.at[k, "p_nom"]), float(n.links.at[k, "p_nom_max"]))
        for k in links
    }


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_planned_changes_list_the_connection_change_in_plain_words(tid, built, cap):
    n = _build(tid)
    rows = A.planned_changes(n, _weak_pack(tid))
    kinds = {r["kind"]: r for r in rows}
    if built != cap:
        row = kinds["connection_limit"]
        assert (row["before_mw"], row["after_mw"]) == (built, cap)
        assert row["text"] == (
            f"The study would set your grid connection limit to "
            f"{cap:g} MW (your project: {built:g} MW)."
        )
    else:
        assert "connection_limit" not in kinds
        assert kinds["expansion_limit"]["after_mw"] == cap
    assert rows and all(r["needs_accept"] for r in rows)
    for r in rows:
        assert not _RAW.search(r["text"]), r["text"]


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_planned_changes_do_not_touch_the_network(tid, built, cap):
    n = _build(tid)
    links = A.select_import_links(n, _weak_pack(tid).import_overlay)
    before = _limits(n, links)
    A.planned_changes(n, _weak_pack(tid))
    assert _limits(n, links) == before


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_keep_leaves_the_connection_as_built(tid, built, cap):
    n = _build(tid)
    pack = _weak_pack(tid)
    links = A.select_import_links(n, pack.import_overlay)
    before = _limits(n, links)
    res = A.apply_archetype_pack_detailed(n, pack, keep_connection=True)
    assert _limits(n, links) == before
    assert res.warnings and all(not _RAW.search(w) for w in res.warnings)
    assert res.warnings == [
        f"The study kept your grid connection limit of {built:g} MW."
    ]


@pytest.mark.parametrize("tid,built,cap", FAMILIES)
def test_accept_applies_the_study_cap_with_plain_warnings(tid, built, cap):
    n = _build(tid)
    pack = _weak_pack(tid)
    links = A.select_import_links(n, pack.import_overlay)
    res = A.apply_archetype_pack_detailed(n, pack)
    assert sum(float(n.links.at[k, "p_nom"]) for k in links) == pytest.approx(cap)
    assert res.warnings and all(not _RAW.search(w) for w in res.warnings)
    res.undo()
    assert sum(float(n.links.at[k, "p_nom"]) for k in links) == pytest.approx(built)


def test_strong_grid_plans_no_change():
    n = _build("eh_ammonia_staged")
    assert A.planned_changes(n, default_strong_grid_pack()) == []


@pytest.mark.parametrize("chosen", [True, False])
def test_off_grid_lists_the_closed_connection_in_plain_words(chosen):
    """The off-grid site type counts as the accept only when it is the user's
    own saved choice (GK A); a template default or a model's pick needs one."""
    n = _build("eh_concentrator_staged")
    rows = A.planned_changes(n, default_off_grid_pack(), off_grid_chosen=chosen)
    assert [r["kind"] for r in rows] == ["connection_closed"]
    assert (rows[0]["before_mw"], rows[0]["after_mw"]) == (35.0, 0.0)
    assert rows[0]["needs_accept"] is (not chosen)
    assert not _RAW.search(rows[0]["text"])


def test_an_already_closed_connection_plans_no_change():
    n = _build("eh_microgrid_staged")
    assert A.planned_changes(n, default_off_grid_pack()) == []


# ── C1: the preview is the apply ────────────────────────────────────────────

_STATIC = ("p_nom", "p_nom_max", "p_max_pu", "p_min_pu")


def _diff(before, after) -> list[tuple]:
    """Every user-facing value the apply changed: (link, attr, before, after)."""
    out = []
    for name in after.links.index:
        for attr in _STATIC:
            if attr in after.links.columns:
                a, b = before.links.at[name, attr], after.links.at[name, attr]
                if not (a == b or (a != a and b != b)):
                    out.append((name, attr, float(a), float(b)))
        for attr in ("p_max_pu", "p_min_pu"):
            tb, ta = getattr(before.links_t, attr), getattr(after.links_t, attr)
            if name in ta.columns and not ta[name].equals(tb[name]):
                out.append((name, f"t_{attr}", None, 0.0))
    return sorted(out, key=str)


def _planned(rows) -> list[tuple]:
    return sorted(
        (
            (w["link"], w["attr"], w["before"], w["after"])
            for r in rows
            for w in r["writes"]
        ),
        key=str,
    )


_PACKS = {
    "strong_grid": default_strong_grid_pack,
    "weak_flexible": default_weak_flexible_pack,
    "off_grid": default_off_grid_pack,
}


@pytest.mark.parametrize("keep", [False, True])
@pytest.mark.parametrize("arch", list(_PACKS))
@pytest.mark.parametrize("tid", list(T.BUILDERS))
def test_planned_changes_equal_what_the_apply_writes(tid, arch, keep):
    n = _build(tid)
    pack = _PACKS[arch]()
    if arch == "weak_flexible":
        pack = _weak_pack(tid)
    rows = A.planned_changes(n, pack, keep_connection=keep)
    before = n.copy()
    A.apply_archetype_pack_detailed(n, pack, keep_connection=keep)
    assert _diff(before, n) == _planned(rows)
    if keep and arch == "weak_flexible":
        assert rows == []


def _two_link_network():
    import pandas as pd
    import pypsa

    n = pypsa.Network()
    n.set_snapshots(pd.date_range("2030-01-01", periods=2, freq="h"))
    n.add("Carrier", "AC")
    n.add("Bus", "grid", carrier="AC")
    n.add("Bus", "hub", carrier="AC")
    n.add("Load", "demand", bus="hub", p_set=40.0)
    n.add(
        "Link", "feeder_a", bus0="grid", bus1="hub", p_nom=30.0, eh_role="grid_import"
    )
    n.add(
        "Link", "feeder_b", bus0="grid", bus1="hub", p_nom=20.0, eh_role="grid_import"
    )
    return n


def test_two_links_on_one_connection_share_the_cap_evenly():
    """Under accept the summed cap is split evenly: 60 MW over two Links is
    30 MW each, so only the 20 MW Link is written (pinned)."""
    n = _two_link_network()
    pack = RUN.apply_pack_overrides(
        default_weak_flexible_pack(), {"import_p_nom_mw": 60.0}
    )
    rows = A.planned_changes(n, pack)
    row = next(r for r in rows if r["kind"] == "connection_limit")
    assert (row["before_mw"], row["after_mw"]) == (50.0, 60.0)
    assert [(w["link"], w["before"], w["after"]) for w in row["writes"]] == [
        ("feeder_b", 20.0, 30.0)
    ]
    assert "2 connections" in row["text"] and not _RAW.search(row["text"])


def test_an_equal_sum_with_a_different_split_is_still_listed():
    n = _two_link_network()
    pack = RUN.apply_pack_overrides(
        default_weak_flexible_pack(), {"import_p_nom_mw": 50.0}
    )
    row = next(r for r in A.planned_changes(n, pack) if r["kind"] == "connection_limit")
    assert (row["before_mw"], row["after_mw"]) == (50.0, 50.0)
    assert row["text"] == (
        "The study would share your 50 MW grid connection limit "
        "evenly over its 2 connections (25 MW each)."
    )
    res = A.apply_archetype_pack_detailed(n, pack)
    assert res.warnings[0] == row["text"].replace("would share", "shared")


def test_a_cap_equal_only_after_rounding_is_a_change():
    n = _build("eh_concentrator_staged")
    pack = RUN.apply_pack_overrides(
        default_weak_flexible_pack(), {"import_p_nom_mw": 35.0000001}
    )
    kinds = [r["kind"] for r in A.planned_changes(n, pack)]
    assert "connection_limit" in kinds


def test_a_non_mw_import_bus_is_refused():
    n = _two_link_network()
    n.buses.at["grid", "unit"] = "t"
    pack = RUN.apply_pack_overrides(
        default_weak_flexible_pack(), {"import_p_nom_mw": 60.0}
    )
    with pytest.raises(A.ArchetypePackError, match="not MW"):
        A.planned_changes(n, pack)
    with pytest.raises(A.ArchetypePackError, match="not MW"):
        A.apply_archetype_pack_detailed(n, pack)


def test_a_network_without_an_import_link_plans_no_change():
    n = _two_link_network()
    n.remove("Link", ["feeder_a", "feeder_b"])
    assert A.planned_changes(n, default_weak_flexible_pack()) == []


def test_an_unknown_archetype_is_refused_before_any_write():
    n = _two_link_network()
    pack = default_weak_flexible_pack().model_copy(update={"archetype": "mystery"})
    with pytest.raises(A.ArchetypePackError, match="unknown archetype"):
        A.apply_archetype_pack_detailed(n, pack)
    assert (n.links.at["feeder_a", "p_nom"], n.links.at["feeder_b", "p_nom"]) == (
        30.0,
        20.0,
    )


def test_off_grid_writes_zero_flow_on_each_link_and_keeps_its_rating():
    n = _two_link_network()
    n.links.at["feeder_a", "p_min_pu"] = -0.5
    rows = A.planned_changes(n, default_off_grid_pack())
    assert _planned(rows) == sorted(
        [
            ("feeder_a", "p_max_pu", 1.0, 0.0),
            ("feeder_a", "p_min_pu", -0.5, 0.0),
            ("feeder_b", "p_max_pu", 1.0, 0.0),
        ],
        key=str,
    )
    A.apply_archetype_pack_detailed(n, default_off_grid_pack())
    assert list(n.links.p_max_pu) == [0.0, 0.0] and list(n.links.p_nom) == [30.0, 20.0]


def test_a_weak_pack_without_a_cap_is_refused():
    n = _two_link_network()
    pack = default_weak_flexible_pack()
    pack = pack.model_copy(
        update={
            "import_overlay": pack.import_overlay.model_copy(
                update={"import_p_nom_mw": None}
            )
        }
    )
    with pytest.raises(A.ArchetypePackError, match="requires import_overlay"):
        A.planned_changes(n, pack)


def test_a_floor_above_each_links_share_is_named_per_connection():
    n = _two_link_network()
    pack = RUN.apply_pack_overrides(
        default_weak_flexible_pack(), {"import_p_nom_mw": 60.0}
    )
    assert not any(
        "minimum size" in w
        for w in A.apply_archetype_pack_detailed(n.copy(), pack).warnings
    )
    n.links.at["feeder_a", "p_nom_min"] = 40.0
    res = A.apply_archetype_pack_detailed(n, pack)
    assert (
        "Your grid connection has a minimum size of 40 MW, above the study's cap "
        "of 30 MW per connection; the study may find no solution."
    ) in res.warnings
