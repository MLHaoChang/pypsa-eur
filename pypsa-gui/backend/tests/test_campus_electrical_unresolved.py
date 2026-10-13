"""The campus needs the investment run could not meet reach the investment case
(owner decision 2026-10-13: "report them; the IC flags the case").

``owner_assets_response`` gains, additively, a top-level ``unresolved``: a list
of ``{"need", "reason"}`` read from the investment table of the run the owner
assets come from (``_owner_source``: the joint optimisation when it is fresh
with a point of its own, else the least-cost run), one per distinct need and
reason of its ``status == "unresolved"`` rows, in table order; ``[]`` when
there are none and when that run has no investment. Every other key keeps its
meaning. ``get_state`` carries the count, ``owner_assets_unresolved``, beside
``owner_assets_count``.

The list is read from ``campus_investment.csv``, which ``campus_study_hash``
already hashes, so it never changes without the hash changing; the hash bytes
are unchanged.
"""
import hashlib
import json

import pandas as pd
import yaml

from services import campus_electrical_service as ce
from tests.test_campus_electrical_ic_source import milp_dir, run_dir, run_milp
from tests.test_campus_electrical_milp_job import (  # noqa: F401  (fixtures)
    SETTINGS, _clean_slot, hub)
from tests.test_campus_electrical_service import (  # noqa: F401  (fixtures)
    INVEST_COLUMNS, fake_investment, hash_by_the_contract, inv_row, purchase_rows, user_and_db)

KEYS = ["assets", "source_hash", "stale", "study_method", "unresolved"]
TAP = "campus_voltage: 0.88 pu at MV; a reactive escalation did not reduce the excursion. Needs a tap change"


def unresolved_rows():
    return [
        inv_row("transformer GRID_IMPORT", "TR_132_33_63", "transformer", 2, capex=5_000_000.0),
        inv_row("reactive", "CAP_33_10M", "capacitor_bank", 2, status="unresolved", reason=TAP),
        inv_row("switchgear GRID", "SG_132_31p5", "switchgear", 3, capex=1_350_000.0),
        inv_row("cable CBL_1", None, "none", 0, status="unresolved", reason="no further candidate in the library"),
        inv_row("cable CBL_2", None, "none", 0, status="not_needed", reason="within rating"),
        inv_row("transformer OLD", "TR_132_33_40", "transformer", 1, existing=True, status="kept", reason="kept"),
    ]


def write_table(directory, rows):
    pd.DataFrame(rows, columns=INVEST_COLUMNS).to_csv(directory / ce.cs.INVESTMENT_CSV, index=False)


# ── the response ────────────────────────────────────────────────────────────

def test_the_response_lists_the_unresolved_needs_of_the_run_with_their_reasons_in_table_order(hub):
    fake_investment(hub, unresolved_rows())
    out = ce.owner_assets_response(hub)
    assert list(out) == KEYS
    assert out["unresolved"] == [{"need": "reactive", "reason": TAP},
                                 {"need": "cable CBL_1", "reason": "no further candidate in the library"}]
    assert all(list(u) == ["need", "reason"] for u in out["unresolved"])


def test_every_other_key_keeps_its_meaning(hub):
    fake_investment(hub, unresolved_rows())
    out = ce.owner_assets_response(hub)
    assert out["assets"] == ce.extra_owner_assets(hub)
    assert [a["name"] for a in out["assets"]] == ["campus:TR_132_33_63#1", "campus:SG_132_31p5#1"]
    assert out["source_hash"] == ce.campus_study_hash(hub) and out["stale"] is False
    assert out["study_method"] == "least_cost"


def test_a_run_with_no_unresolved_need_gives_an_empty_list(hub):
    fake_investment(hub, purchase_rows())
    assert ce.owner_assets_response(hub)["unresolved"] == []


def test_no_investment_run_gives_an_empty_list(hub):
    assert ce.owner_assets_response(hub) == {"assets": [], "source_hash": None, "stale": False,
                                             "study_method": None, "unresolved": []}
    ce.run(hub, {**SETTINGS, "invest": False})
    assert ce.owner_assets_response(hub)["unresolved"] == []


def test_a_table_without_the_library_it_bought_from_is_no_investment_run(hub):
    """The same rule as the assets and the hash: a list only where there is a hash."""
    d = fake_investment(hub, unresolved_rows())
    (d / ce.USED_LIBRARY_FILE).unlink()
    out = ce.owner_assets_response(hub)
    assert out["source_hash"] is None and out["unresolved"] == []


def test_the_rows_of_one_need_with_one_reason_are_one_entry(hub):
    rows = [inv_row("transformer T", "TR_132_33_63", "transformer", 1, status="unresolved", reason="r1"),
            inv_row("transformer T", "SG_132_31p5", "switchgear", 2, status="unresolved", reason="r1"),
            inv_row("transformer T", "TR_132_33_40", "transformer", 1, status="unresolved",
                    reason="r1; replacement unpriced: no lifetime"),
            inv_row("reactive", "CAP_33_10M", "capacitor_bank", 1, status="unresolved", reason="r2")]
    fake_investment(hub, rows)
    assert ce.owner_assets_response(hub)["unresolved"] == [
        {"need": "transformer T", "reason": "r1"},
        {"need": "transformer T", "reason": "r1; replacement unpriced: no lifetime"},
        {"need": "reactive", "reason": "r2"}]


def test_the_need_and_reason_are_strings_even_when_the_table_left_the_reason_empty(hub):
    fake_investment(hub, [inv_row("reactive", None, "none", 0, status="unresolved", reason=None)])
    assert ce.owner_assets_response(hub)["unresolved"] == [{"need": "reactive", "reason": ""}]


def test_a_real_least_cost_run_reports_its_unresolved_rows_and_a_stale_one_still_does(hub):
    ce.run(hub, SETTINGS)
    assert ce.owner_assets_response(hub)["unresolved"] == []
    path = run_dir(hub) / ce.cs.INVESTMENT_CSV
    df = pd.read_csv(path)
    df.loc[0, ["status", "reason"]] = ["unresolved", "campus_voltage: needs a tap change"]
    df.to_csv(path, index=False)
    want = [{"need": df.loc[0, "need"], "reason": "campus_voltage: needs a tap change"}]
    assert ce.owner_assets_response(hub)["unresolved"] == want
    spec = yaml.safe_load(ce.get_state(hub)["campus_yaml"])
    spec["campus"]["pcc"]["sk_max_mva"]["value"] = 2500.0
    ce.save_campus(hub, yaml.safe_dump(spec))
    out = ce.owner_assets_response(hub)
    assert out["stale"] is True and out["unresolved"] == want


# ── the run actually used ───────────────────────────────────────────────────

def test_a_milp_made_stale_gives_way_to_the_least_cost_table_and_its_unresolved_rows(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    path = run_dir(hub) / ce.cs.INVESTMENT_CSV
    df = pd.read_csv(path)
    df.loc[0, ["status", "reason"]] = ["unresolved", "least cost could not"]
    df.to_csv(path, index=False)
    # the least-cost table changed, so the MILP made from it is stale: the least-cost run is the source
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "least_cost"
    assert out["unresolved"] == [{"need": df.loc[0, "need"], "reason": "least cost could not"}]


def test_a_fresh_milp_with_an_unresolved_row_reports_it(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    write_table(milp_dir(hub), [inv_row("transformer GRID_IMPORT", "TR_132_33_63", "transformer", 2),
                                inv_row("reactive", None, "none", 0, status="unresolved", reason="milp could not")])
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "milp"
    assert out["unresolved"] == [{"need": "reactive", "reason": "milp could not"}]


def test_a_fresh_milp_without_unresolved_rows_reports_none_while_the_least_cost_run_has_some(hub, monkeypatch):
    """The least-cost run had an unresolved need when the MILP was made from it
    (its basis records that table), and the MILP met it."""
    run_milp(hub, monkeypatch)
    path = run_dir(hub) / ce.cs.INVESTMENT_CSV
    df = pd.read_csv(path)
    df.loc[0, ["status", "reason"]] = ["unresolved", "least cost could not"]
    df.to_csv(path, index=False)
    basis_path = milp_dir(hub) / ce.MILP_BASIS_FILE
    basis = json.loads(basis_path.read_text())
    basis["inputs"][ce.cs.INVESTMENT_CSV] = hashlib.sha256(path.read_bytes()).hexdigest()
    basis_path.write_text(json.dumps(basis))
    assert ce.get_state(hub)["milp"]["stale"] is False
    out = ce.owner_assets_response(hub)
    assert out["study_method"] == "milp" and out["unresolved"] == []
    assert ce.get_state(hub)["owner_assets_unresolved"] == 0
    assert ce.get_state(hub)["results"]["unresolved"] == [{"need": df.loc[0, "need"],
                                                           "reason": "least cost could not"}]


# ── the hash ────────────────────────────────────────────────────────────────

def test_the_hash_bytes_are_unchanged_and_a_change_to_the_unresolved_set_changes_the_hash(hub):
    d = fake_investment(hub, unresolved_rows())
    before = ce.campus_study_hash(hub)
    assert before == hash_by_the_contract(d)                      # the least-cost contract, unchanged
    rows = unresolved_rows()
    rows[1]["reason"] = TAP + " (another)"
    write_table(d, rows)
    assert ce.campus_study_hash(hub) != before and ce.campus_study_hash(hub) == hash_by_the_contract(d)
    rows[1]["status"] = "chosen"
    write_table(d, rows)
    assert ce.owner_assets_response(hub)["unresolved"] == [
        {"need": "cable CBL_1", "reason": "no further candidate in the library"}]
    assert ce.campus_study_hash(hub) == hash_by_the_contract(d)


# ── the state and the copilot ───────────────────────────────────────────────

def test_the_state_counts_the_unresolved_needs_beside_the_owner_assets(hub):
    state = ce.get_state(hub)
    assert state["owner_assets_unresolved"] == 0
    keys = list(state)
    assert keys.index("owner_assets_unresolved") == keys.index("owner_assets_count") + 1
    fake_investment(hub, unresolved_rows())
    state = ce.get_state(hub)
    assert state["owner_assets_count"] == 2 and state["owner_assets_unresolved"] == 2


def test_the_state_counts_the_run_actually_used(hub, monkeypatch):
    run_milp(hub, monkeypatch)
    write_table(milp_dir(hub), [inv_row("transformer GRID_IMPORT", "TR_132_33_63", "transformer", 2),
                                inv_row("reactive", None, "none", 0, status="unresolved", reason="a"),
                                inv_row("cable X", None, "none", 0, status="unresolved", reason="b")])
    state = ce.get_state(hub)
    assert state["owner_assets_method"] == "milp" and state["owner_assets_unresolved"] == 2


def test_the_study_tool_describes_the_unresolved_count():
    from services.chat_tools_schema import TOOLS
    desc = {t["name"]: t["description"] for t in TOOLS}["campus_get_study"]
    assert "owner_assets_unresolved" in desc and "investment case" in desc
