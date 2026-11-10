"""Study harness 12 (GK A I6): on an off-grid project the SCR row and every
row built from the PoC's entered S_k'' leave the scorecard with the reason."""

from __future__ import annotations

import inspect
import json

from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE
from tests.test_grid_readiness_motor_start import _site
from tests.test_grid_readiness_voltage_stability import IDS as VS_IDS
from tests.test_grid_readiness_voltage_stability import _template, _write


def _reads_the_poc_fault_level(build) -> bool:
    src = inspect.getsource(build)
    return "_poc_sk_mva(" in src or "_poc_sk_min(" in src


def _sk_ids(d) -> set:
    profile, error = G._load_profile(PROFILE, ())
    inp = G._Inputs(d, _site(), profile, error, None, None)
    out = set()
    for build in G.SK_BUILDERS:
        rows = build(inp)
        out |= {r["id"] for r in (rows if isinstance(rows, list) else [rows])}
    return out


def test_the_fault_level_builders_are_read_from_the_code():
    """A new row judged against the PoC's S_k'' joins the off-grid exclusion
    or this fails: the set is every builder reading the PoC's fault level,
    plus the SCR row (which reads it through the SCR gate)."""
    derived = {b for b in G._BUILDERS if _reads_the_poc_fault_level(b)} | {G._scr_rows}
    assert derived == set(G.SK_BUILDERS)
    named = {b.__name__ for b in G.SK_BUILDERS}
    assert named == {"_scr_rows", "_rvc_row", "_rvc_infrequent_row", "_harmonic_row", "_tdd_row",
                     "_flicker_row", "_motor_start_rows", "_unbalance_row"}


def test_every_scorecard_builder_is_listed_once():
    builders = {name for name, f in vars(G).items()
                if inspect.isfunction(f) and name.startswith("_") and name.endswith(("_row", "_rows"))
                and list(inspect.signature(f).parameters) == ["inp"]}
    assert builders == {b.__name__ for b in G._BUILDERS}
    assert len(G._BUILDERS) == len(set(G._BUILDERS))


def test_off_grid_excludes_scr_and_every_fault_level_row_with_the_reason(tmp_path):
    _template(tmp_path, "off_grid")
    _write(tmp_path)
    sk = _sk_ids(tmp_path)
    assert {"scr", "rapid_voltage_change", "rapid_voltage_change_infrequent", "harmonic_distortion",
            "current_distortion", "flicker", "voltage_unbalance"} <= sk
    assert any(i == "motor_start" or i.startswith("motor_start:") for i in sk)
    out = G.grid_readiness(tmp_path, _site(), PROFILE)
    excluded = {e["id"]: e for e in out["excluded"]}
    assert set(excluded) == sk | set(VS_IDS)
    assert all(excluded[i]["reason"] == G.SK_OFF_GRID and excluded[i]["requirement"] for i in sk)
    assert G.SK_OFF_GRID == ("off-grid: the tie is open, so the PoC's fault level does not apply; "
                             "the island's fault level comes from its own units")
    assert not sk & {r["id"] for r in out["rows"]}
    # An excluded row is never counted, as a pass or as anything else.
    assert sum(out["summary"][v] for v in G.VERDICTS) == len(out["rows"])
    for i in sk:
        fact = G.readiness_fact(out, i)
        assert fact.value == "excluded" and fact.reason == f"excluded: {G.SK_OFF_GRID}"


def test_a_grid_project_keeps_every_row_in_the_scorecard_order(tmp_path):
    _write(tmp_path)
    grid = G.grid_readiness(tmp_path, _site(), PROFILE)
    assert grid["excluded"] == []
    off = tmp_path / "off"
    off.mkdir()
    _template(off, "off_grid")
    _write(off)
    island = G.grid_readiness(off, _site(), PROFILE)
    ids = [r["id"] for r in grid["rows"]]
    assert _sk_ids(tmp_path) <= set(ids)
    gone = {e["id"] for e in island["excluded"]}
    assert [i for i in ids if i not in gone] == [r["id"] for r in island["rows"]]
    assert [i for i in ids if i in gone] == [e["id"] for e in island["excluded"]]
    json.dumps(grid)  # the card stays plain JSON
