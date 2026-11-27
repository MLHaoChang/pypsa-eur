"""Template display names, the builds (expert-catalogue 01a-2b; GK B2 D2, D7).

Every component of every build that changes a template's component set has
a name, and every row names a component some build has. Split from
``test_template_display_names.py`` because the builds are slow.
"""

from __future__ import annotations

import pytest

import project_templates.eh_templates as eh
from project_templates import eh_site
from services import template_display_names as tdn

from tests.test_template_display_names import COVERED


# ── D2 + D7: the table matches every build that changes the component set ───

def _ids(n) -> set[tuple[str, str]]:
    return {(cls, str(cid)) for cls, attr in tdn.COMPONENT_LISTS.items()
            for cid in getattr(n, attr).index}


def _dc_hybrid(tid):
    return eh.BUILDERS[tid](load_spec=eh.dc_load_spec(tid, {"backup": {"mode": "hybrid"}}))


def _planned(tid):
    return eh.BUILDERS[tid](planning=eh.planning_spec(tid, {"start_year": 2030,
                                                             "end_year": 2060}))


_SITE_FULL = {
    "existing": {"gensets": {"count": eh_site.MAX_GENSETS, "unit_mw": 1.0, "fuel": "diesel"},
                 "pv_mw": 1.0, "battery": {"mw": 1.0, "mwh": 2.0},
                 "ups": {"mw": 10.0, "minutes": 15.0}},
}


def _builds():
    """Every build that adds, removes or renames components:
    - each template at its defaults;
    - each planned template over the widest span (2030–2060), which sets the
      per-period suffixes (the create route offers no other option to the
      concentrator: its ``years``/``time_base`` are builder-only);
    - the data centers' hybrid backup, which adds the backup batteries
      ("bess" only removes gensets and is refused by the default load's peak
      check; "genset" is the default);
    - each site type, and a site with every existing option at its maximum."""
    for tid in sorted(eh.BUILDERS):
        yield f"{tid}:default", tid, lambda tid=tid: eh.BUILDERS[tid]()
        if tid in eh.NATIVE_PLANNING:
            yield f"{tid}:planned", tid, lambda tid=tid: _planned(tid)
        if tid.startswith("eh_datacenter"):
            yield f"{tid}:hybrid", tid, lambda tid=tid: _dc_hybrid(tid)
    for st in eh_site.SITE_TYPES:
        yield f"eh_site:{st}", "eh_site", (
            lambda st=st: eh_site.build_eh_site(eh_site.site_params({"site_type": st})))
    yield "eh_site:full", "eh_site", (
        lambda: eh_site.build_eh_site(eh_site.site_params(_SITE_FULL)))


_BUILDS = list(_builds())


@pytest.fixture(scope="module")
def built() -> dict[str, tuple[str, set[tuple[str, str]]]]:
    return {label: (tid, _ids(make())) for label, tid, make in _BUILDS}


def test_the_builds_cover_every_covered_template(built):
    assert {tid for tid, _ in built.values()} == COVERED


@pytest.mark.parametrize("label", [label for label, _, _ in _BUILDS])
def test_every_component_of_every_build_has_a_name(built, label):
    tid, ids = built[label]
    missing = sorted(f"{cls}:{cid}" for cls, cid in ids if tdn.name_for(tid, cls, cid) is None)
    assert not missing, f"{label}: components with no display name: {missing}"


def _row_matches(cls: str, cid: str, ids: set[tuple[str, str]]) -> bool:
    found = tdn._pattern(cid)
    if found is None:
        return (cls, cid) in ids
    rx, _ = found
    return any(c == cls and rx.fullmatch(i) for c, i in ids)


@pytest.mark.parametrize("tid", sorted(COVERED))
def test_every_row_names_a_component_some_build_has(built, tid):
    ids = set().union(*(s for t, s in built.values() if t == tid))
    stale = sorted(f"{cls}:{cid}" for cls, cid in tdn.DISPLAY_NAMES[tid]
                   if not _row_matches(cls, cid, ids))
    assert not stale, f"{tid}: rows naming no component: {stale}"


@pytest.mark.parametrize("tid", sorted(COVERED))
def test_the_table_has_the_pinned_shape(tid):
    """D7: the shape, not the words (those are UX's)."""
    classes = set(tdn.COMPONENT_LISTS)
    for (cls, cid), name in tdn.DISPLAY_NAMES[tid].items():
        assert cls in classes, (cls, cid)
        assert isinstance(name, str) and name.strip() == name and name, (cls, cid)
        assert not name.startswith("__"), (cls, cid)
        assert name != cid, (cls, cid)
        for token in ("{y}", "{n}"):
            assert (token in cid) == (token in name), (cls, cid, name)
        assert sum(t in cid for t in ("{y}", "{n}")) <= 1, (cls, cid)
