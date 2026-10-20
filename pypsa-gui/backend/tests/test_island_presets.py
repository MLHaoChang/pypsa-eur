"""Control presets through pypsa-gui (data-centre grid readiness, ticket 04).

The presets are gridspine's (``gridspine/schema/island.py``); pypsa-gui only
selects one (``campus_electrical_service.select_island_preset``) and reports
what the project then offers (``get_island``'s ``services`` and
``warnings``). Oracles:

* selecting a preset writes the sidecar through gridspine and the island
  study reads it: under ``grid_forming_support`` the I2c hand case still buys
  its 10 MW of seamless headroom from the BESS (cost 1000); under
  ``grid_following`` the BESS is not credited, so the genset (1000/MWh)
  runs 10 MW over the 4 hours instead: objective 40 000 for 1 400;
* an extendable BESS has no rating before sizing, so FFR leaves it out and
  says why;
* every committed template sidecar takes each of the four presets
  project-wide: it validates and its island study runs, or it refuses with a
  typed reason; an island left with no grid former raises
  ``preset_leaves_no_grid_former`` (gatekeeper, 2026-10-20).
"""
import json

import pytest
from fastapi import HTTPException

from gridspine.schema.island import PRESETS, offered_services, select_preset, validate_island_config
from services import campus_electrical_service as ce
from services.adequacy.island_config import NO_GRID_FORMER, PRESET_NOT_APPLIED, island_warnings
from services.solver.island import IslandRefusal, island_study
from tests.test_campus_island_service import _config, _sidecar_path, project, user_and_db  # noqa: F401
from tests.test_island_config import T


# ── selecting a preset in a project ────────────────────────────────────────

def test_get_island_lists_the_presets_and_what_the_project_offers(project):  # noqa: F811
    ce.save_island(project, _config())
    out = ce.get_island(project)
    assert set(out["presets"]) == set(PRESETS)
    assert out["services"]["preset"] is None
    assert "grid_forming" in out["services"]["units"]["bess"]["services"]
    assert out["warnings"] == []


def test_selecting_grid_forming_support_is_written_and_the_study_reads_it(project):  # noqa: F811
    ce.save_island(project, _config())
    out = ce.select_island_preset(project, "grid_forming_support")
    assert out["skipped"] == []
    saved = json.loads(_sidecar_path(project).read_text())
    assert saved["preset"] == "grid_forming_support"
    assert out["services"]["units"]["bess"]["control"] == "gfm_vsm"
    study = ce.run_island_study(project)
    assert study["cost_of_seamless"] == pytest.approx(1000.0, abs=1e-4)


def test_under_grid_following_the_bess_is_not_credited_and_the_genset_runs(project):  # noqa: F811
    ce.save_island(project, _config())
    out = ce.select_island_preset(project, "grid_following")
    assert out["services"]["services"] == []
    assert out["warnings"] == []                       # the genset still forms the island
    study = ce.run_island_study(project)
    assert study["variants"]["seamless"]["objective"] == pytest.approx(10.0 * 4 * 1000.0, rel=1e-6)
    assert study["cost_of_seamless"] == pytest.approx(40000.0 - 400.0, rel=1e-6)


def test_ffr_leaves_an_extendable_bess_out_and_says_why(project):  # noqa: F811
    ce.save_island(project, _config())
    out = ce.select_island_preset(project, "fast_frequency_response")
    assert len(out["skipped"]) == 1 and out["skipped"][0].startswith("bess:")
    assert "fast_frequency_response" not in out["services"]["services"]
    later = ce.get_island(project)["warnings"]               # the reason outlives the response
    assert [w["code"] for w in later] == [PRESET_NOT_APPLIED]
    assert "bess" in later[0]["message"] and "rating" in later[0]["message"]


def test_clearing_a_selected_preset_saves_and_keeps_the_island(project):  # noqa: F811
    ce.save_island(project, _config())
    selected = ce.select_island_preset(project, "grid_forming_support")
    cleared = ce.select_island_preset(project, None)
    assert "preset" not in cleared["config"]
    assert cleared["config"]["units"]["bess"]["control"] == "gfm_vsm"
    assert cleared["services"]["units"]["bess"]["services"] == selected["services"]["units"]["bess"]["services"]


def test_an_unknown_preset_is_422_and_keeps_the_sidecar(project):  # noqa: F811
    ce.save_island(project, _config())
    with pytest.raises(HTTPException) as exc:
        ce.select_island_preset(project, "grid_magic")
    assert exc.value.status_code == 422 and "grid_magic" in exc.value.detail
    assert json.loads(_sidecar_path(project).read_text()) == _config()


def test_selecting_a_preset_without_a_sidecar_is_422(project):  # noqa: F811
    with pytest.raises(HTTPException) as exc:
        ce.select_island_preset(project, "grid_following")
    assert exc.value.status_code == 422 and "island_config" in exc.value.detail


# ── every committed template sidecar, under every preset ──────────────────

def _fixed(n):
    su = n.storage_units
    return {x: float(su.at[x, "p_nom"]) for x in su.index if not bool(su.at[x, "p_nom_extendable"])}


@pytest.mark.slow
@pytest.mark.parametrize("template", sorted(T.ISLAND_CONFIG))
@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_template_sidecar_takes_every_preset(template, preset):
    """Each validates, reports its services and warnings, and its island
    study runs; a staged (multi-period) template is refused by the study
    with its typed reason, as without a preset."""
    n = T.BUILDERS[template]()
    raw, skipped = select_preset(T.ISLAND_CONFIG[template], preset, _fixed(n))
    cfg = validate_island_config(raw)
    assert cfg["preset"] == preset
    offered = offered_services(cfg)
    assert offered["preset"] == preset
    for reason in skipped:
        assert preset == "fast_frequency_response" and "rating" in reason
    codes = {w["code"] for w in island_warnings(cfg)}
    assert codes <= {NO_GRID_FORMER, PRESET_NOT_APPLIED}
    assert (PRESET_NOT_APPLIED in codes) == bool(skipped)
    try:
        out = island_study(n, cfg)
    except IslandRefusal as exc:
        assert "island_study_multi_period_unsupported" in str(exc)
        return
    assert out["variants"]["baseline"]["status"] == "ok"


def test_the_h2_hub_under_grid_following_has_no_grid_former():
    """The H2 hub islands on its batteries alone (no genset): under
    grid_following nothing forms its voltage."""
    raw, _ = select_preset(T.ISLAND_CONFIG["eh_h2_hub"], "grid_following")
    warnings = island_warnings(validate_island_config(raw))
    assert [w["code"] for w in warnings] == [NO_GRID_FORMER]
    assert "grid_following" in warnings[0]["message"]
    assert island_warnings(validate_island_config(T.ISLAND_CONFIG["eh_h2_hub"])) == []
