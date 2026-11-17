"""`services.compare.intent_inputs` on the DC template's network, in memory (walkthrough bugs 05/06): each rung of
the ladder (request, project, assumed) and each refusal, in the form's words."""

from __future__ import annotations

import math

import pytest

from services.compare import intent_inputs as ii
from services.solver.pcs_statcom import tag_pq_link

ENV = {
    "phases": [{"swing_mw": 1.0}],
    "max": {
        "swing_mw": 1.0,
        "oscillation_amplitude_mw": 0.5,
        "oscillation_period_s": 2.0,
    },
}
RAMP = {
    "id": "ramp_rate",
    "limit": 4.0,
    "source": "Template assumption: 10%",
    "assumed": True,
}
TAGS = dict(
    x_pu=0.1,
    pf=0.9,
    s_n_mva=50.0,
    p_cap_mw=45.0,
    pf_req=0.92,
    statcom_capital_cost=9000.0,
)


@pytest.fixture
def n():
    from project_templates.eh_templates import build_eh_datacenter

    return build_eh_datacenter()


def _resolve(n, req, **kw):
    args = dict(ramp_row=RAMP, envelope=ENV, rate=0.07) | kw
    return ii.resolve(n, req, **args)


def _refused(fn, *a, **kw) -> str:
    with pytest.raises(ii.IntentInputsRefusal) as exc:
        fn(*a, **kw)
    return str(exc.value)


def test_project_values_fill_what_the_request_leaves_out(n):
    inputs, src = _resolve(n, {"pq": {}})
    assert inputs["link"] == "grid_import" and src["link"] == "project"
    assert inputs["ramp_mw_per_min"] == 4.0 and src["ramp_mw_per_min"] == RAMP["source"]
    assert (
        inputs["envelope"] is ENV
        and src["envelope"] == "the DC load's dynamic envelope"
    )
    assert inputs["pq"]["x_pu"] == ii.ASSUMED_X_PU and src["pq"]["x_pu"].startswith(
        "assumed"
    )


def test_the_request_wins_and_says_so(n):
    req = {
        "link": "grid_import",
        "ramp_mw_per_min": 2.0,
        "envelope": {"x": 1},
        "pq": {"pf": 0.9},
    }
    inputs, src = _resolve(n, req)
    assert (inputs["ramp_mw_per_min"], inputs["envelope"], inputs["pq"]["pf"]) == (
        2.0,
        {"x": 1},
        0.9,
    )
    assert (
        src["link"]
        == src["ramp_mw_per_min"]
        == src["envelope"]
        == src["pq"]["pf"]
        == "request"
    )
    assert src["pq"]["x_pu"].startswith("assumed")
    assert req == {
        "link": "grid_import",
        "ramp_mw_per_min": 2.0,
        "envelope": {"x": 1},
        "pq": {"pf": 0.9},
    }


def test_a_fully_tagged_link_runs_on_its_tags_without_pq(n):
    tag_pq_link(n, "grid_import", **TAGS)
    inputs, src = _resolve(n, {})
    assert inputs["pq"] == TAGS and set(src["pq"].values()) == {"project"}


def test_a_partly_tagged_link_takes_its_set_tags_and_assumes_the_rest(n):
    tag_pq_link(n, "grid_import", **TAGS)
    n.links.loc["grid_import", "pq_pf"] = math.nan
    inputs, src = _resolve(n, {"pq": {}})
    assert inputs["pq"]["x_pu"] == 0.1 and src["pq"]["x_pu"] == "project"
    assert inputs["pq"]["pf"] == ii.ASSUMED_PF and src["pq"]["pf"].startswith("assumed")
    assert "load power factor" in _refused(_resolve, n, {})


def test_another_links_tags_are_not_this_links(n):
    tag_pq_link(n, "site_transformer", **TAGS)
    _, src = _resolve(n, {"pq": {}})
    assert all(v.startswith("assumed") for v in src["pq"].values())


def test_the_rating_follows_the_links_reach(n):
    n.links.loc["grid_import", "p_max_pu"] = 1.5
    inputs, _ = _resolve(n, {"pq": {}})
    assert inputs["pq"]["p_cap_mw"] == inputs["pq"]["s_n_mva"] == 60.0


@pytest.mark.parametrize(
    "req,kw,label",
    [
        ({"pq": {}}, {"ramp_row": None}, "Grid ramp limit:"),
        ({"pq": {}}, {"ramp_row": {**RAMP, "limit": None}}, "Grid ramp limit:"),
        (
            {"pq": {}},
            {"envelope": None},
            "Load swing, oscillation amplitude and period:",
        ),
        (
            {"link": "nowhere", "pq": {}},
            {},
            "Campus link: nowhere is not in the network",
        ),
        ({"pq": [1, 2]}, {}, "Point of connection: the values must be given by name"),
        ({"pq": {"surprise": 1}}, {}, "Point of connection: unknown value(s) surprise"),
        (
            {},
            {},
            "Point of connection: the project holds no transformer reactance (p.u.), load power factor",
        ),
    ],
)
def test_each_refusal_opens_with_its_field(n, req, kw, label):
    assert _refused(_resolve, n, req, **kw).startswith(label)


def test_no_marked_grid_import_is_refused_by_name(n):
    n.links["eh_role"] = ""
    assert _refused(_resolve, n, {"pq": {}}).startswith(
        "Campus link: no link is marked as the grid import"
    )
    n.links.drop(columns="eh_role", inplace=True)
    assert ii.poc_link(n) is None


def test_defaults_without_a_ramp_or_envelope_say_why(n):
    for ramp in (None, {**RAMP, "limit": None}):
        d = ii.defaults(n, ramp_row=ramp, envelope=None, rate=0.07)
        assert d["ramp_mw_per_min"]["value"] is None and d["ramp_mw_per_min"]["reason"]
        assert d["envelope"]["value"] is None and d["envelope"]["reason"]


@pytest.mark.parametrize(
    "pq,words",
    [
        ({"pf": "high"}, "every value must be a number"),
        ({"x_pu": math.inf}, "transformer reactance (p.u.) must be a finite number"),
        ({"pf": 1.5}, "load power factor out of range"),
        ({"pf_req": 0.0}, "required power factor out of range"),
        ({"p_cap_mw": 10.0}, "import cap (MW) out of range"),
    ],
)
def test_apply_pq_refuses_in_the_forms_words(n, pq, words):
    inputs, _ = _resolve(n, {"pq": pq})
    text = _refused(ii.apply_pq, n, inputs)
    assert text.startswith("Point of connection: ") and words in text
    assert "pq_" not in text and "statcom_capital_cost" not in text


def test_apply_pq_names_a_check_without_a_field_plainly(n):
    n.add("Bus", "empty_bus")
    n.add("Link", "spur", bus0="grid", bus1="empty_bus", p_nom=5.0)
    inputs, _ = _resolve(n, {"link": "spur", "pq": {"p_cap_mw": 5.0, "s_n_mva": 5.0}})
    text = _refused(ii.apply_pq, n, inputs)
    assert text.startswith(
        "Point of connection: link spur: bus1 empty_bus carries no Load"
    )


def test_apply_pq_tags_the_given_network_and_returns_the_sizing_fields(n):
    inputs, _ = _resolve(n, {"pq": {}})
    rest = ii.apply_pq(n, inputs)
    assert "pq" not in rest and rest["link"] == "grid_import"
    assert n.links.at["grid_import", "pq_x_pu"] == ii.ASSUMED_X_PU


def test_an_unbounded_extendable_link_reads_its_p_nom(n):
    n.links.loc["grid_import", ["p_nom_extendable", "p_nom_max"]] = [True, math.inf]
    inputs, _ = _resolve(n, {"pq": {}})
    assert inputs["pq"]["s_n_mva"] == n.links.at["grid_import", "p_nom"]


def test_a_link_with_no_finite_rating_asks_for_it(n):
    n.links.loc["grid_import", ["p_nom_extendable", "p_nom_max", "p_nom"]] = [
        True,
        math.inf,
        0.0,
    ]
    held = ii.defaults(n, ramp_row=RAMP, envelope=ENV, rate=0.07)["pq"]
    assert held["s_n_mva"]["value"] is None and held["s_n_mva"]["reason"]
    text = _refused(_resolve, n, {"pq": {"p_cap_mw": 40.0}})
    assert text.startswith("Point of connection: transformer rating (MVA):")
    inputs, _ = _resolve(n, {"pq": {"p_cap_mw": 40.0, "s_n_mva": 40.0}})
    assert inputs["pq"]["s_n_mva"] == 40.0


def test_apply_pq_moves_the_tags_to_the_chosen_link_in_the_copy(n):
    tag_pq_link(n, "site_transformer", **TAGS)
    n.links.loc["site_transformer", "pq_q_margin_mvar"] = 1.0
    inputs, _ = _resolve(n, {"pq": {}})
    ii.apply_pq(n, inputs)
    assert n.links.loc["site_transformer", ["pq_x_pu", "pq_q_margin_mvar"]].isna().all()
    assert n.links.at["grid_import", "pq_x_pu"] == ii.ASSUMED_X_PU


def test_a_fixed_link_reads_its_p_nom_not_its_p_nom_max(n):
    n.links.loc["grid_import", ["p_nom_extendable", "p_nom_max"]] = [False, 999.0]
    inputs, _ = _resolve(n, {"pq": {}})
    assert inputs["pq"]["s_n_mva"] == n.links.at["grid_import", "p_nom"]


def test_an_unbounded_fixed_link_has_no_assumed_rating(n):
    n.links.loc["grid_import", ["p_nom_extendable", "p_nom"]] = [False, math.inf]
    held = ii.defaults(n, ramp_row=RAMP, envelope=ENV, rate=0.07)["pq"]
    assert held["s_n_mva"]["value"] is None


def test_the_statcom_cost_is_the_packs_overnight_per_mvar_as_an_annuity_plus_fom():
    from services.finance.replacements import annuity

    value, _ = ii.statcom_cost(0.07)
    assert value == pytest.approx(450_000 / 5 * (annuity(0.07, 20) + 0.03), rel=1e-3)
    assert value == pytest.approx(11_195.36, rel=1e-4)


def test_defaults_name_the_marked_grid_import(n):
    d = ii.defaults(n, ramp_row=RAMP, envelope=ENV, rate=0.07)
    assert d["link"] == {
        "value": "grid_import",
        "source": ii.SOURCE_PROJECT,
        "kind": "project",
    }
