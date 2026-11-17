"""DC grid-readiness walkthrough 01: a project created with Batteries or Hybrid
backup assesses its own backup. ``dc_backup.genset_form`` turns the built
campus back into genset form for the assessment only (GK A G1-G8)."""

import copy

import pandas as pd
import pytest

from project_templates import eh_templates as T
from services.adequacy import dc_backup as B

TID = "eh_datacenter"


def _spec(mode, it=30.0):
    return T.dc_load_spec(TID, {"backup": {"mode": mode}, "phases": [{"cod": "2030-01-01", "it_mw": it}]})


def _campus(mode, it=30.0):
    spec = _spec(mode, it)
    return T.BUILDERS[TID](load_spec=spec), T.island_config(TID, None, load_spec=spec)


def _genset(it=30.0):
    return _campus("genset", it)


def _same_network(a, b):
    for c in ("Generator", "StorageUnit", "Load", "Link", "Bus"):
        sa, sb = a.static(c), b.static(c)
        assert sorted(sa.index) == sorted(sb.index), c
        cols = [k for k in sa.columns if k in sb.columns]
        pd.testing.assert_frame_equal(sa.loc[sorted(sa.index), cols], sb.loc[sorted(sb.index), cols],
                                      check_dtype=False, obj=c)
        ta, tb = a.dynamic(c), b.dynamic(c)
        for attr in set(ta) | set(tb):
            fa, fb = ta.get(attr, pd.DataFrame()), tb.get(attr, pd.DataFrame())
            if fa.empty and fb.empty:
                continue
            assert sorted(fa.columns) == sorted(fb.columns), (c, attr)
            pd.testing.assert_frame_equal(fa[sorted(fa.columns)], fb[sorted(fb.columns)],
                                          check_dtype=False, obj=f"{c}.{attr}")


@pytest.mark.parametrize("mode", ["bess", "hybrid"])
def test_a_built_campus_round_trips_to_genset_form(mode):
    n_b, i_b = _campus(mode)
    n_g, i_g = _genset()
    m, island, notes, strings = B.genset_form(n_b, i_b, mode, n_g, i_g)
    assert strings and all(s.startswith(B.STRING_PREFIX) for s in strings)
    _same_network(m, n_g)
    assert island == i_g
    assert any("not assessed as built" in x for x in notes)
    assert any("example's genset fleet" in x for x in notes) is (mode == "bess")


def test_it_leaves_its_inputs_as_they_are():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    before = (sorted(n_b.storage_units.index), sorted(n_b.generators.index), copy.deepcopy(i_b))
    B.genset_form(n_b, i_b, "bess", n_g, i_g)
    assert before == (sorted(n_b.storage_units.index), sorted(n_b.generators.index), i_b)


def test_a_user_unit_sharing_the_prefix_is_kept_and_a_user_edit_survives():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    bus = n_b.storage_units.bus.iloc[0]
    n_b.add("StorageUnit", B.STRING_PREFIX + "mine", bus=bus, p_nom=1.0)
    n_b.generators.loc["rooftop_pv", "p_nom"] = 7.5
    m, island, _notes, strings = B.genset_form(n_b, i_b, "bess", n_g, i_g)
    assert B.STRING_PREFIX + "mine" in m.storage_units.index and B.STRING_PREFIX + "mine" not in strings
    assert m.generators.at["rooftop_pv", "p_nom"] == 7.5


def test_a_restored_genset_never_overwrites_a_project_component():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    n_b.add("Generator", "genset_1", bus=n_b.buses.index[0], p_nom=3.0)
    with pytest.raises(B.DcBackupError, match="already has genset_1"):
        B.genset_form(n_b, i_b, "bess", n_g, i_g)


def test_an_edited_critical_load_is_said():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    crit = B.critical_loads(i_g)[0]
    n_b.loads_t.p_set[crit] = n_b.loads_t.p_set[crit] * 1.2
    _m, _i, notes, _s = B.genset_form(n_b, i_b, "bess", n_g, i_g)
    assert any("sized for the spec, not the edited load" in x for x in notes)
