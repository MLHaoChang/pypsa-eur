"""
provenance-graph 03, "why this number": the assistant's read tool over `why(node)`.

The acceptance example (a PV cost priced from the pack through its DC/AC ratio), each step state, the pack-price
arithmetic, the target refusals, the ledger map, the step cap, and that the tool reads only (COPILOT-1). GK B (42)
conditions X1–X6 are named where each is tested.
"""

from __future__ import annotations

import copy
import json
import math

import pypsa
import pytest

from harness import catalogue
from services import chat_tools
from services.asset_schema import provenance as P
from services.provenance_graph import explain as E
from services.provenance_graph import money
from services.provenance_graph.explain import TargetUnknown, explain, explain_ledger_row
from tests.test_typed_cost_units_stale import (
    _clear_caches,  # noqa: F401 - the autouse fixture
    _fc,
    _pv,
    _strip_scaled_by,
)

RATIO = "input:Generator/g/dc_ac_ratio"
URL = "/api/network/asset_schema/{}/provenance/{}/why/{}"


def _snapshot(n) -> str:
    return json.dumps(
        {
            "meta": n.meta,
            "generators": n.generators.to_dict(),
            "links": n.links.to_dict(),
            "storage_units": n.storage_units.to_dict(),
        },
        sort_keys=True,
        default=str,
    )


def _scale(n, value):
    """Overwrite the recorded scale on both money fields, as a damaged or foreign record would hold it."""
    for field in ("overnight_cost", "fom_cost"):
        P._root(n)["Generator"]["g"][field]["scaled_by"]["value"] = value


# ── the acceptance example ──────────────────────────────────────────────────


def test_why_a_pv_cost_walks_to_the_pack_price_and_the_ratio_with_their_stamps(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    out = explain(n, "Generator", "g", "overnight_cost")
    assert next(iter(out)) == "outcome"  # X5: the outcome comes first
    assert out["outcome"] == {
        "state": "fresh",
        "uncompared": [],
        "complete": True,
        "truncated": False,
        "steps_total": 2,
    }
    assert out["node"] == money.derived_id("Generator", "g", "overnight_cost")
    cost, ratio = out["steps"]
    assert cost["value"] == pytest.approx(n.generators.at["g", "overnight_cost"])
    assert cost["unit"] == "EUR/MW"
    stamp = P.get(n, "Generator", "g")["overnight_cost"]
    for key in ("status", "source", "source_year", "currency_year"):
        assert cost["stamp"][key] == stamp[key], key
    assert cost["stamp"]["status"] == "default"
    # The pack price it was scaled from: arithmetic, the value ÷ the ratio pricing recorded.
    pack = cost["pack_price"]
    assert pack["status"] == "established"
    assert pack["value"] == pytest.approx(cost["value"] / 1.2)
    assert pack["source"] == stamp["source"] and pack["basis"] == E.ARITHMETIC
    assert ratio["node"] == RATIO and ratio["value"] == pytest.approx(1.2)
    assert ratio["via"]["edge"] == "priced_through"
    assert ratio["via"]["recorded"]["column"] == "dc_ac_ratio"
    assert ratio["via"]["recorded"]["value"] == pytest.approx(1.2)
    assert (ratio["state"], ratio["broken"]) == ("fresh", None)


def test_why_says_when_the_ratio_has_changed_since_pricing(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.35
    out = explain(n, "Generator", "g", "overnight_cost")
    assert out["outcome"]["state"] == "stale"
    cost, ratio = out["steps"]
    assert cost["state"] == "stale"
    # Still worked back through the ratio it was priced with, not today's.
    assert cost["pack_price"]["value"] == pytest.approx(cost["value"] / 1.2)
    assert ratio["value"] == pytest.approx(1.35)
    assert ratio["state"] == "stale"
    assert ratio["broken"] == {
        "compared": {
            "field": "dc_ac_ratio",
            "recorded": pytest.approx(1.2),
            "current_field": "dc_ac_ratio",
            "current": pytest.approx(1.35),
        },
        "uncompared": [],
        "reason": "scale input changed",
    }


def test_the_economics_annualised_cost_walks_both_money_fields_to_the_ratio(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    out = explain(n, "Generator", "g", "capital_cost")
    assert [
        (s["node"], s["via"] and s["via"]["edge"], s["state"]) for s in out["steps"]
    ] == [
        (money.derived_id("Generator", "g", "capital_cost"), None, "stale"),
        (money.derived_id("Generator", "g", "overnight_cost"), "derived_from", "stale"),
        (RATIO, "priced_through", "stale"),
        (money.derived_id("Generator", "g", "fom_cost"), "derived_from", "stale"),
    ]
    annual = out["steps"][0]
    assert annual["value"] == float(n.generators.at["g", "capital_cost"])  # X4
    assert annual["unit"] == "EUR/MW/yr" and annual["stamp"] is None
    assert "pack_price" not in annual  # nothing scaled the annualised cost itself


def test_a_link_cost_walks_to_its_output_efficiency():
    n = _fc()
    n.links.at["fc", "efficiency"] = 0.4
    out = explain(n, "Link", "fc", "overnight_cost")
    assert out["steps"][1]["node"] == "input:Link/fc/efficiency"
    assert out["steps"][1]["broken"]["compared"]["current"] == pytest.approx(0.4)
    assert out["outcome"]["state"] == "stale"


# ── X2: one test per state ──────────────────────────────────────────────────


def test_state_fresh_only_where_the_comparator_compared(tmp_path, monkeypatch):
    out = explain(_pv(tmp_path, monkeypatch), "Generator", "g", "fom_cost")
    assert [s["state"] for s in out["steps"]] == ["fresh", "fresh"]
    assert out["steps"][1]["via"]["recorded"]["value"] == pytest.approx(1.2)


def test_state_stale_when_the_current_value_cannot_be_read(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = float("nan")
    ratio = explain(n, "Generator", "g", "overnight_cost")["steps"][1]
    assert ratio["state"] == "stale"
    assert ratio["broken"]["compared"] == {"field": "dc_ac_ratio", "recorded": 1.2}
    assert ratio["broken"]["uncompared"] == ["current_field", "current"]
    assert ratio["broken"]["reason"].startswith("cannot be priced now")


def test_state_broken_record_reads_stale_and_compares_nothing(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    P._root(n)["Generator"]["g"]["overnight_cost"]["scaled_by"] = {"column": "dc_ac_ratio"}
    out = explain(n, "Generator", "g", "overnight_cost")
    scale = out["steps"][1]
    assert out["outcome"]["state"] == "stale" and scale["state"] == "stale"
    assert scale["broken"] == {
        "compared": {},
        "uncompared": ["field", "recorded", "current_field", "current"],
        "reason": "scale input record malformed",
    }


def test_state_uncompared_never_fresh(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    out = explain(n, "Generator", "g", "lifetime")
    assert out["outcome"]["state"] == "uncompared"
    assert out["outcome"]["uncompared"] == [money.input_id("Generator", "g", "lifetime")]
    (step,) = out["steps"]
    assert step["state"] == "uncompared" and step["broken"] is None
    assert step["stamp"]["status"] == "default"


def test_a_typed_over_cost_is_its_own_input_and_uncompared(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    P.mark_customised(n, "Generator", "g", ["overnight_cost"])
    out = explain(n, "Generator", "g", "overnight_cost")
    assert out["node"] == money.input_id("Generator", "g", "overnight_cost")
    assert [s["state"] for s in out["steps"]] == ["uncompared"]
    assert out["steps"][0]["stamp"]["status"] == "customised"


# ── X3: the pack price is arithmetic, and not established on a bad scale ────


def test_an_unrecorded_scale_reads_stale_and_its_pack_price_not_established(
    tmp_path, monkeypatch
):
    n = _pv(tmp_path, monkeypatch)
    _strip_scaled_by(n, "Generator", "g")
    cost, scale = explain(n, "Generator", "g", "overnight_cost")["steps"]
    assert cost["pack_price"]["status"] == E.NOT_ESTABLISHED
    assert "value" not in cost["pack_price"]
    assert scale["via"]["recorded"] == {"unrecorded": money.NOT_RECORDED}
    assert scale["state"] == "stale" and scale["broken"]["reason"] == money.NOT_RECORDED


@pytest.mark.parametrize(
    "bad", [0, 0.0, float("inf"), float("-inf"), float("nan"), "1.2", True, None, [1.2]]
)
def test_a_bad_scale_never_gives_a_pack_price(tmp_path, monkeypatch, bad):
    n = _pv(tmp_path, monkeypatch)
    _scale(n, bad)
    for field in ("overnight_cost", "fom_cost"):
        pack = explain(n, "Generator", "g", field)["steps"][0]["pack_price"]
        assert pack["status"] == E.NOT_ESTABLISHED and pack["basis"] == E.ARITHMETIC
        assert "value" not in pack


def test_a_missing_priced_value_gives_no_pack_price(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "overnight_cost"] = float("nan")
    cost = explain(n, "Generator", "g", "overnight_cost")["steps"][0]
    assert cost["value"] is None
    assert cost["pack_price"]["status"] == E.NOT_ESTABLISHED


def test_a_good_pack_price_is_finite_and_non_zero(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    _scale(n, 1.5)
    pack = explain(n, "Generator", "g", "overnight_cost")["steps"][0]["pack_price"]
    assert math.isfinite(pack["value"]) and pack["value"] > 0
    assert pack["value"] == pytest.approx(n.generators.at["g", "overnight_cost"] / 1.5)


def test_stamp_gaps_are_named_never_filled(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    cost = explain(n, "Generator", "g", "overnight_cost")["steps"][0]
    # The EU pack records no currency of its own: the unit stays the schema's, and the gap is named.
    assert cost["stamp_missing"] == ["currency"] and cost["unit"] == "EUR/MW"
    entry = P._root(n)["Generator"]["g"]["overnight_cost"]
    entry["currency_year"] = None
    entry["currency"] = "GBP"
    cost = explain(n, "Generator", "g", "overnight_cost")["steps"][0]
    assert cost["stamp_missing"] == ["currency_year"]
    assert cost["unit"] == "GBP/MW" and cost["pack_price"]["unit"] == "GBP/MW"
    assert "currency_year" not in cost["stamp"] or cost["stamp"]["currency_year"] is None


# ── X4: targets ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "component, name, field, status",
    [
        ("Nope", "g", "overnight_cost", 422),
        ("Generator", "missing", "overnight_cost", 404),
        ("Generator", "g", "no_such_field", 404),
        ("Generator", "g", "p_nom", 404),  # a column with no provenance
    ],
)
def test_an_unknown_target_is_refused(tmp_path, monkeypatch, component, name, field, status):
    n = _pv(tmp_path, monkeypatch)
    with pytest.raises(TargetUnknown) as exc:
        explain(n, component, name, field)
    assert exc.value.status == status and exc.value.error_kind == "why_target_unknown"


def test_every_ledger_key_resolves_and_an_unknown_key_refuses():
    from services.study.asset_view import LEDGER_FIELDS

    keys = {m.key for m in LEDGER_FIELDS}
    for key in keys:
        got = E.ledger_fields(key)
        assert got and {m.key for m in got} == {key}
        assert got == [m for m in LEDGER_FIELDS if m.key == key]
    assert len(E.ledger_fields("battery_round_trip_efficiency")) == 2
    rooftop = E.ledger_fields("pv_rooftop_eur_per_kw", pv_kind="utility")
    assert rooftop == []  # the PV kind the pack did not read is left out
    with pytest.raises(TargetUnknown):
        E.ledger_fields("no_such_row")


def _ledger_pv(tmp_path, monkeypatch):
    """A pack-priced PV named as the Guided study names it (`asset_view.PV`)."""
    n = _pv(tmp_path, monkeypatch)
    g = n.generators.loc["g"]
    n.add("Generator", "pv", **{c: g[c] for c in ("bus", "carrier", "p_nom", "lifetime", "overnight_cost",
                                                  "fom_cost", "capital_cost", "dc_ac_ratio")})
    n.remove("Generator", "g")
    root = P._root(n)["Generator"]
    root["pv"] = root.pop("g")
    n.meta["decision_study_pack"] = {"pv_kind": "utility"}
    return n


def test_a_ledger_row_walks_the_field_it_compiles_to(tmp_path, monkeypatch):
    n = _ledger_pv(tmp_path, monkeypatch)
    n.generators.at["pv", "dc_ac_ratio"] = 1.3
    out = explain_ledger_row(n, "pv_utility_eur_per_kw")
    assert next(iter(out)) == "outcome"
    assert out["outcome"] == {"state": "stale", "complete": True, "truncated": False}
    (field,) = out["fields"]
    assert field == explain(n, "Generator", "pv", "overnight_cost")
    assert field["steps"][1]["node"] == "input:Generator/pv/dc_ac_ratio"
    # The PV kind the pack did not read, and a row with no asset on this network, refuse.
    for key in ("pv_rooftop_eur_per_kw", "battery_storage_eur_per_kwh", "nope"):
        with pytest.raises(TargetUnknown):
            explain_ledger_row(n, key)


def test_a_ledger_row_reads_uncompared_when_a_field_compared_nothing(tmp_path, monkeypatch):
    n = _ledger_pv(tmp_path, monkeypatch)
    out = explain_ledger_row(n, "pv_utility_lifetime_years")
    assert out["outcome"]["state"] == "uncompared"


# ── X5: the step cap ────────────────────────────────────────────────────────


def test_a_capped_walk_is_marked_truncated_and_never_complete(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    monkeypatch.setattr(E, "MAX_STEPS", 2)
    out = explain(n, "Generator", "g", "capital_cost")
    assert out["outcome"]["truncated"] is True and out["outcome"]["complete"] is False
    assert out["outcome"]["steps_total"] == 4 and len(out["steps"]) == 2
    monkeypatch.setattr(E, "MAX_STEPS", 1)
    row = explain_ledger_row(_ledger_pv(tmp_path / "pv", monkeypatch), "pv_utility_eur_per_kw")
    assert row["outcome"]["complete"] is False and row["outcome"]["truncated"] is True


# ── X1: reads only (COPILOT-1) ──────────────────────────────────────────────


def _no_writes(monkeypatch):
    """From here on, any provenance write or assurance record save fails the test."""
    from services import assurance_records

    def refuse(*_a, **_k):
        raise AssertionError("why_this_number wrote")

    for mod, name in (
        (P, "record"),
        (P, "annotate"),
        (P, "mark_customised"),
        (P, "rename"),
        (P, "drop"),
        (assurance_records, "save"),
    ):
        monkeypatch.setattr(mod, name, refuse)


def test_explain_changes_nothing_on_the_network(tmp_path, monkeypatch):
    n = _pv(tmp_path, monkeypatch)
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    before, meta = _snapshot(n), copy.deepcopy(n.meta)
    fields = ("overnight_cost", "fom_cost", "capital_cost", "lifetime")
    monkeypatch.setattr(P, "record", lambda *a, **k: pytest.fail("wrote"))
    monkeypatch.setattr(P, "annotate", lambda *a, **k: pytest.fail("wrote"))
    for field in fields:
        explain(n, "Generator", "g", field)
    assert _snapshot(n) == before and n.meta == meta


# ── X6: the route and the tool ──────────────────────────────────────────────


def test_the_tool_is_registered_read_only_on_its_get_routes():
    names = {t["name"] for t in catalogue.TOOLS}
    assert "why_this_number" in names and "why_this_number" in chat_tools.DISPATCHERS
    assert catalogue.safety_tier_for("why_this_number") == "read"
    assert catalogue.TOOL_ROUTES["why_this_number"] == [
        ("GET", "/api/network/asset_schema/{component}/provenance/{name}/why/{field}"),
        ("GET", "/api/projects/{name}/studies/{study_id}/ledger/{key}/why"),
    ]
    assert not catalogue.tool_edits_network("why_this_number")
    assert "why_this_number" not in chat_tools.UNDO_CAPTURED_TOOLS
    assert "why_this_number" not in chat_tools._lock_gated_tool_names()


def test_the_route_and_the_tool_answer_the_same_and_write_nothing(
    tmp_path, monkeypatch, client, install_network
):
    from services.pypsa_service import PyPSAService

    n = install_network(_pv(tmp_path, monkeypatch))
    n.generators.at["g", "dc_ac_ratio"] = 1.3
    ctx = PyPSAService.get_active_context()
    before = (_snapshot(n), json.dumps(ctx.user_ts_meta, sort_keys=True, default=str))
    revision = ctx.network_revision
    _no_writes(monkeypatch)
    via_tool = chat_tools.DISPATCHERS["why_this_number"](
        component="Generator", name="g", field="overnight_cost"
    )
    ctx = PyPSAService.get_active_context()
    assert ctx.network_revision == revision
    assert json.dumps(ctx.user_ts_meta, sort_keys=True, default=str) == before[1]
    # The request runs in the test client's own session context (the active context reads another one
    # afterwards), so across the route it is the network the route read that is compared: the same `n`.
    r = client.get(URL.format("Generator", "g", "overnight_cost"))
    assert r.status_code == 200, r.text
    assert r.json() == json.loads(json.dumps(via_tool))
    assert r.json()["outcome"]["state"] == "stale"
    assert r.json()["steps"][1]["broken"]["compared"]["current"] == pytest.approx(1.3)
    assert _snapshot(n) == before[0]


@pytest.mark.parametrize(
    "component, name, field, code",
    [
        ("Nope", "g", "overnight_cost", 422),
        ("Generator", "missing", "overnight_cost", 404),
        ("Generator", "g", "no_such_field", 404),
        ("Generator", "g", "p_nom", 404),
    ],
)
def test_the_route_refuses_an_unknown_target_with_its_kind(
    install_network, client, component, name, field, code
):
    n = pypsa.Network()
    n.add("Bus", "b")
    n.add("Generator", "g", bus="b")
    install_network(n)
    r = client.get(URL.format(component, name, field))
    assert r.status_code == code
    assert r.json()["detail"]["error_kind"] == "why_target_unknown"


def test_the_tool_refuses_a_mixed_or_partial_target():
    from fastapi import HTTPException

    for args in (
        {},
        {"component": "Generator", "name": "g"},
        {"component": "Generator", "name": "g", "field": "f", "ledger_key": "k"},
        {"project": "p", "study_id": "s", "ledger_key": "k"},
    ):
        with pytest.raises(HTTPException) as exc:
            chat_tools.DISPATCHERS["why_this_number"](**args)
        assert exc.value.status_code == 422, args
        assert exc.value.detail["error_kind"] == "invalid_tool_args"


# ── the ledger route, through a real study ──────────────────────────────────


@pytest.fixture
def studies_on(monkeypatch):
    from tests.study_s4_support import enable_studies

    yield from enable_studies(monkeypatch)


def test_the_ledger_route_and_the_tool_walk_the_option_fork_and_write_nothing(
    client, api_project, monkeypatch, project_storage_dir, studies_on
):
    """The PV price row on the option fork, after its ratio changed: the route and the tool both walk to the
    ratio and say it changed; neither writes the fork, the study or its ledger."""
    from services import solver_service
    from services.pypsa_service import PyPSAService
    from tests.study_s4_support import create_pack_study, wait_run
    from tests.test_provenance_graph import _price_pv_through_its_ratio
    from tests.test_study_expert_edits_routes import INTAKE
    from tests.test_study_runner import FakeSolver

    monkeypatch.setattr(solver_service, "run_simulation", FakeSolver())
    name = "pg03-why"
    api_project(f"{name}-src")
    r = create_pack_study(
        client, f"{name}-src", name, intake={**INTAKE, "pv": {"enabled": True, "kind": "rooftop"}}
    )
    assert r.status_code == 201, r.text
    sid = r.json()["study_id"]
    assert client.post(f"/api/projects/{name}/studies/{sid}/run", json={}).status_code == 202
    assert wait_run(client, name, sid)["status"] == "done"

    path = project_storage_dir(f"{name}-opt-bess_pv_2h") / "network.nc"
    n = pypsa.Network()
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.import_network_from_netcdf(n, path)
    _price_pv_through_its_ratio(n)
    n.generators.at["pv", "dc_ac_ratio"] = 1.3
    with PyPSAService.get_netcdf_io_lock():
        PyPSAService.export_network_to_netcdf(n, path)

    fork_bytes = path.read_bytes()
    study_file = project_storage_dir(name) / "studies" / f"{sid}.json"
    study_bytes = study_file.read_bytes()
    url = f"/api/projects/{name}/studies/{sid}/ledger/pv_rooftop_eur_per_kw/why"
    r = client.get(url, params={"option_id": "bess_pv_2h"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert next(iter(got)) == "outcome" and got["outcome"]["state"] == "stale"
    assert got["target"] == {"ledger_key": "pv_rooftop_eur_per_kw", "study_id": sid, "option_id": "bess_pv_2h"}
    (field,) = got["fields"]
    assert field["target"] == {"component": "Generator", "name": "pv", "field": "overnight_cost"}
    ratio = field["steps"][1]
    assert ratio["node"] == "input:Generator/pv/dc_ac_ratio"
    assert ratio["broken"]["compared"]["current"] == pytest.approx(1.3)

    via_tool = chat_tools.DISPATCHERS["why_this_number"](
        project=name, study_id=sid, ledger_key="pv_rooftop_eur_per_kw", option_id="bess_pv_2h"
    )
    assert json.loads(json.dumps(via_tool)) == got

    for key, option, code in (
        ("no_such_row", "bess_pv_2h", 404),
        ("pv_rooftop_eur_per_kw", "no_such_option", 404),
        ("pv_utility_eur_per_kw", "bess_pv_2h", 404),  # the PV kind this study's pack did not read
    ):
        r = client.get(f"/api/projects/{name}/studies/{sid}/ledger/{key}/why", params={"option_id": option})
        assert r.status_code == code, (key, option)
        assert r.json()["detail"]["error_kind"] == "why_target_unknown"
    assert path.read_bytes() == fork_bytes and study_file.read_bytes() == study_bytes
