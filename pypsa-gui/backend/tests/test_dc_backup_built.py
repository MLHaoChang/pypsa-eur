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
    return T.dc_load_spec(
        TID, {"backup": {"mode": mode}, "phases": [{"cod": "2030-01-01", "it_mw": it}]}
    )


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
        pd.testing.assert_frame_equal(
            sa.loc[sorted(sa.index), cols],
            sb.loc[sorted(sb.index), cols],
            check_dtype=False,
            obj=c,
        )
        ta, tb = a.dynamic(c), b.dynamic(c)
        for attr in set(ta) | set(tb):
            fa, fb = ta.get(attr, pd.DataFrame()), tb.get(attr, pd.DataFrame())
            if fa.empty and fb.empty:
                continue
            assert sorted(fa.columns) == sorted(fb.columns), (c, attr)
            pd.testing.assert_frame_equal(
                fa[sorted(fa.columns)],
                fb[sorted(fb.columns)],
                check_dtype=False,
                obj=f"{c}.{attr}",
            )


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
    # The load is the spec's: no edited-load note.
    assert not any("not the edited load" in x for x in notes)


def test_it_leaves_its_inputs_as_they_are():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    before = (
        sorted(n_b.storage_units.index),
        sorted(n_b.generators.index),
        copy.deepcopy(i_b),
    )
    B.genset_form(n_b, i_b, "bess", n_g, i_g)
    assert before == (
        sorted(n_b.storage_units.index),
        sorted(n_b.generators.index),
        i_b,
    )


def test_a_user_unit_sharing_the_prefix_is_kept_and_a_user_edit_survives():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    bus = n_b.storage_units.bus.iloc[0]
    n_b.add("StorageUnit", B.STRING_PREFIX + "mine", bus=bus, p_nom=1.0)
    n_b.generators.loc["rooftop_pv", "p_nom"] = 7.5
    m, island, _notes, strings = B.genset_form(n_b, i_b, "bess", n_g, i_g)
    assert (
        B.STRING_PREFIX + "mine" in m.storage_units.index
        and B.STRING_PREFIX + "mine" not in strings
    )
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


def _listed(n_b, i_b, name, kind, prefixed):
    """A StorageUnit the sidecar lists as ride-through storage, with ``kind``."""
    unit = (B.STRING_PREFIX if prefixed else "user_") + name
    n_b.add("StorageUnit", unit, bus=n_b.storage_units.bus.iloc[0], p_nom=1.0)
    i_b["requirements"]["ride_through_storage"].append(unit)
    i_b["units"][unit] = {"kind": kind}
    return unit


@pytest.mark.parametrize(
    "kind,prefixed", [("ups", True), ("bess", False)], ids=["not-bess", "no-prefix"]
)
def test_a_listed_unit_is_a_built_string_only_with_the_kind_and_the_prefix(
    kind, prefixed
):
    """GK A G2: each part of the provenance test has its own killing case."""
    n_b, i_b = _campus("bess")
    unit = _listed(n_b, i_b, "x", kind, prefixed)
    assert unit not in B.built_strings(n_b, i_b)
    assert B.built_strings(n_b, i_b)


def test_other_time_steps_than_the_example_are_refused():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    n_b.set_snapshots(n_b.snapshots[:-1])
    with pytest.raises(B.DcBackupError, match="time steps differ"):
        B.genset_form(n_b, i_b, "bess", n_g, i_g)


@pytest.mark.parametrize("mode", ["bess", "hybrid"])
def test_a_sidecar_that_lists_no_string_is_refused_in_words(mode):
    """A project created before its create wrote its own sidecar holds the
    example's genset sidecar: its strings cannot be found, so it is refused
    with the way out, never assessed with them left in."""
    n_b, _i = _campus(mode)
    n_g, i_g = _genset()
    with pytest.raises(
        B.DcBackupError, match="does not list the battery strings"
    ) as exc:
        B.genset_form(n_b, copy.deepcopy(i_g), mode, n_g, i_g)
    assert f"created with {B.MODE_WORD[mode]} backup" in str(exc.value)


def test_the_staged_example_refuses_batteries_at_its_default_pue():
    """Pins the create form's staged line (STAGED_BESS_LINE in
    frontend dcLoadForm.ts): at the example's default PUE, Batteries does not
    fit the build plan's import. If this starts to pass, drop the line."""
    tid = "eh_datacenter_staged"
    from services.dc_load_profile import DcLoadError

    with pytest.raises(DcLoadError, match="peak is"):
        T.BUILDERS[tid](load_spec=T.dc_load_spec(tid, {"backup": {"mode": "bess"}}))


@pytest.mark.parametrize("island", [None, {}, {"requirements": {}}, {"units": {}}])
def test_a_sidecar_without_its_keys_lists_no_string(island):
    n_b, _i = _campus("bess")
    assert B.built_strings(n_b, island) == []


def test_a_listed_string_not_in_the_network_is_not_one():
    n_b, i_b = _campus("bess")
    gone = B.STRING_PREFIX + "gone"
    i_b["requirements"]["ride_through_storage"].append(gone)
    i_b["units"][gone] = {"kind": "bess"}
    assert gone not in B.built_strings(n_b, i_b)


def test_the_note_gives_the_built_strings_power_and_energy():
    n_b, i_b = _campus("bess")
    n_g, i_g = _genset()
    strings = B.built_strings(n_b, i_b)
    su = n_b.storage_units.loc[strings]
    _m, _i, notes, _s = B.genset_form(n_b, i_b, "bess", n_g, i_g)
    want = f"({su.p_nom.sum():.4g} MW, {(su.p_nom * su.max_hours).sum():.4g} MWh)"
    assert any(want in x for x in notes), (want, notes)


@pytest.mark.parametrize("mode", ["bess", "hybrid"])
def test_a_genset_sidecar_without_units_or_requirements_restores_none(mode):
    """A genset-form sidecar with neither key restores nothing, never fails."""
    n_b, i_b = _campus(mode)
    n_g, _i = _genset()
    m, island, _notes, strings = B.genset_form(n_b, i_b, mode, n_g, {})
    assert strings and not set(strings) & set(m.storage_units.index)
    assert "sustained_h" not in island["requirements"]
