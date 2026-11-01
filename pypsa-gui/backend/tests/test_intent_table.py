"""AS-10: the preference comparison table (one sizing loop per design intent)."""
import pytest

from models.design_intent import DesignIntentSpec
from services.compare import intent_table as it


def _result(status="converged", failing=(), unresolved=(), cost=1.0e6):
    checks = [{"name": "transformer", "ok": "transformer" not in failing, "lever": "cut"},
              {"name": "escr", "ok": "escr" not in failing, "lever": "none" if "escr" in unresolved else "cut"},
              {"name": "harmonics", "ok": True, "skipped": True, "lever": "none"}]
    final = {"checks": checks, "failing": list(failing), "unresolved": list(unresolved), "not_run": ["harmonics"],
             "total_cost_eur": cost, "cost_eur": cost,
             "sizes": {"campus_poc": 90.0, "q_ss": 12.0, "q_dyn": 8.0, "p_sm": 5.0, "e_sm": 2.0,
                       "store:bess": 320.0, "charger:bess": 80.0, "s_x:bess": 10.0}}
    return {"status": status, "verdict": f"v-{status}", "final": final, "failing": list(failing),
            "unresolved": list(unresolved), "not_run": ["harmonics"], "library": {"transformer": 100.0},
            "basis_note": "same LP; UC/MIP options not reproduced", "warnings": []}


def _scenario(by_intent, calls):
    """A (prepare, runner) pair: prepare tags the scenario with its intent; the runner reads the tag."""
    def prepare(spec):
        return spec.intent, None, None

    def run(network, config, inp):
        calls.append(network)
        return by_intent[network]
    return prepare, run


def test_two_intents_run_two_scenarios_and_give_two_rows():
    calls = []
    prepare, runner = _scenario({"A": _result(), "B": _result()}, calls)
    out = it.compare_intents(prepare, [DesignIntentSpec(intent="A"), DesignIntentSpec(intent="B")],
                             critical_mw=80.0, profile={}, runner=runner)
    assert calls == ["A", "B"]
    assert [r["intent"] for r in out["rows"]] == ["A", "B"]


def test_requires_two_distinct_intents():
    with pytest.raises(ValueError):
        it.compare_intents(*_scenario({}, [])[:1], [DesignIntentSpec(intent="A")], critical_mw=80.0, profile={},
                           runner=None)
    with pytest.raises(ValueError):
        it.compare_intents(*_scenario({}, [])[:1], [DesignIntentSpec(intent="A")] * 2, critical_mw=80.0,
                           profile={}, runner=None)


@pytest.mark.parametrize("status,failing,unresolved,want", [
    ("converged", (), (), "pass"),
    ("converged", ("transformer",), (), "fail"),
    ("not_converged", (), (), "not_converged"),
    ("unresolved", ("escr",), ("escr",), "unresolved"),
    ("failed", (), (), "failed"),
    ("aborted", (), (), "aborted"),
])
def test_only_a_clean_converged_loop_is_a_pass(status, failing, unresolved, want):
    row = it.build_row(DesignIntentSpec(intent="B"), _result(status, failing, unresolved), 80.0, {})
    assert row["outcome"] == want
    assert row["status"] == status


def test_not_converged_with_all_checks_ok_is_still_not_a_pass():
    row = it.build_row(DesignIntentSpec(intent="B"), _result("not_converged"), 80.0, {})
    assert row["outcome"] != "pass"
    assert all(c["state"] in ("ok", "not_run") for c in row["checks"])


def test_row_units_and_not_run():
    row = it.build_row(DesignIntentSpec(intent="A"), _result(), 80.0, {})
    assert row["cost_eur"] == 1.0e6
    assert row["assets"]["store:bess"] == {"value": 320.0, "unit": "MWh"}
    assert row["assets"]["charger:bess"]["unit"] == "MW"
    assert row["assets"]["q_ss"]["unit"] == "Mvar"
    assert row["transformer_mva"] == 100.0
    assert row["statcom_mvar"] == 20.0
    assert {c["name"]: c["state"] for c in row["checks"]}["harmonics"] == "not_run"
    assert "UC/MIP" in row["basis_note"]


def test_intent_a_autonomy_and_intent_d_warning():
    a = it.build_row(DesignIntentSpec(intent="A", backup_autonomy_h=6.0), _result(), 80.0, {})
    assert a["backup_autonomy_h"] == 6.0 and a["backup_energy_mwh"] == 480.0
    b = it.build_row(DesignIntentSpec(intent="B"), _result(), 80.0, {})
    assert b["backup_autonomy_h"] is None and b["warnings"] == []
    d = it.build_row(DesignIntentSpec(intent="D"), _result(), 80.0, {})
    assert d["warnings"] and "ride-through" in d["warnings"][0]
