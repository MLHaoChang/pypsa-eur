"""
LC-26 — weak_flexible replaces an import Link's p_nom; the run says so.

The fixture's import Link is rated 100 MW; the pack cap is 50 MW (default) or
the override. The apply step must name the Link, its rating and the cap used,
and the warning must reach the ``apply_pack`` stage note in the report.
"""
from __future__ import annotations

import json
import time

import pytest

from services import chat_tools as T
from services.adequacy import archetypes as A
from models.energy_hub import default_weak_flexible_pack

STAGES = ["apply_pack", "ens_solve", "assemble"]


def _weak_net():
    from tests.test_energy_hub_mvp_b import _weak_mvp_b_network
    return _weak_mvp_b_network()


def _json(out):
    return json.loads(out) if isinstance(out, str) else out


def _run_through_chat(install_network, **overrides) -> dict:
    install_network(_weak_net())
    T.update_solver_config({"solver_name": "highs", "voll": 5000.0})
    T.run_eh_study(archetype="weak_flexible", stages=STAGES,
                   pack_overrides={"ens_cap_permyriad": 5000.0, **overrides})
    deadline = time.time() + 180.0
    while time.time() < deadline:
        if _json(T.get_adequacy_results("eh_study")).get("status") != "running":
            break
        time.sleep(0.1)
    return _json(T.get_adequacy_results("eh_reference_design"))


def _apply_note(report: dict) -> str:
    rec = next(r for r in report["pipeline"]["stages"] if r["stage"] == "apply_pack")
    return rec.get("note") or ""


def test_apply_warns_when_the_pack_cap_differs_from_the_link_rating():
    n = _weak_net()
    res = A.apply_archetype_pack_detailed(n, default_weak_flexible_pack())
    assert len(res.warnings) == 2          # p_nom and the finite p_nom_max
    w = res.warnings[0]
    assert "lowered" in w and "import_poc" in w and "100" in w and "50" in w
    assert "p_nom_max" in res.warnings[1]
    assert n.links.at["import_poc", "p_nom"] == 50.0


def test_apply_says_raised_when_the_cap_is_above_the_rating():
    n = _weak_net()
    n.links.at["import_poc", "p_nom"] = 30.0
    res = A.apply_archetype_pack_detailed(n, default_weak_flexible_pack())
    assert "raised" in res.warnings[0] and "30" in res.warnings[0]
    assert "50" in res.warnings[0]


def test_extendable_link_discloses_p_nom_max_and_a_floor_above_the_cap():
    n = _weak_net()
    n.links.at["import_poc", "p_nom_extendable"] = True
    n.links.at["import_poc", "p_nom"] = 50.0          # equals the cap
    n.links.at["import_poc", "p_nom_min"] = 70.0
    res = A.apply_archetype_pack_detailed(n, default_weak_flexible_pack())
    text = " | ".join(res.warnings)
    assert "p_nom_max from 100 MW to 50 MW" in text   # only p_nom_max changed
    assert "p_nom_min 70 MW" in text and "infeasible" in text


def test_two_links_each_name_their_own_rating_and_the_split():
    n = _weak_net()
    n.add("Link", "import_b", bus0="grid", bus1="crit", p_nom=20.0,
          p_nom_extendable=False, efficiency=1.0, carrier="AC")
    n.links.at["import_b", "eh_role"] = "grid_import"
    res = A.apply_archetype_pack_detailed(n, default_weak_flexible_pack())
    a = next(w for w in res.warnings if "'import_poc'" in w and "p_nom from" in w)
    b = next(w for w in res.warnings if "'import_b'" in w)
    assert "from 100 MW to 25 MW" in a and "over 2 Links" in a
    assert "raised" in b and "from 20 MW to 25 MW" in b


def test_apply_is_silent_when_the_cap_equals_the_link_rating():
    n = _weak_net()
    pack = default_weak_flexible_pack()
    pack = pack.model_copy(update={"import_overlay": pack.import_overlay.model_copy(
        update={"import_p_nom_mw": 100.0})})
    assert A.apply_archetype_pack_detailed(n, pack).warnings == []


@pytest.mark.live_solve
def test_chat_run_reports_the_replaced_rating_in_the_apply_pack_note(
        install_network):
    note = _apply_note(_run_through_chat(install_network))
    assert "import_poc" in note and "100" in note and "50" in note


@pytest.mark.live_solve
def test_chat_run_report_notes_carry_the_warning(install_network):
    report = _run_through_chat(install_network)
    assert any("import_poc" in x and "lowered" in x and "100" in x
               for x in report["notes"])


@pytest.mark.live_solve
def test_chat_run_names_the_override_cap(install_network):
    note = _apply_note(_run_through_chat(
        install_network, import_p_nom_mw=80.0))
    assert "import_poc" in note and "100" in note and "80" in note


def test_run_eh_study_description_says_what_weak_flexible_sets():
    from services.chat_tools_schema import TOOLS
    desc = next(t for t in TOOLS if t["name"] == "run_eh_study")["description"]
    default = default_weak_flexible_pack().import_overlay.import_p_nom_mw
    assert "import_p_nom_mw" in desc and f"{default:g} MW" in desc
    assert "divided by the number of import Links" in desc
    assert "p_nom_max" in desc
