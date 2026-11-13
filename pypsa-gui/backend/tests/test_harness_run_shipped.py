# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""Study harness 14, S-B1 (GK B (56) C3-C5, C10): admission of the shipped cases of
`eh_ipp_hybrid`: the project's cases.json read and checked, every case shown with
all its changes, the allow-list checked against the built template, the digest, and
the pick. Nothing runs here."""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import project_templates.eh_ipp  # noqa: F401 -- registers the template
from harness import run_shipped as S
from project_templates import eh_templates as T

TID = "eh_ipp_hybrid"
TPL = pathlib.Path(__file__).resolve().parents[1] / "project_templates" / TID
CAP = 4


@pytest.fixture(scope="module")
def built():
    return T.solver_config(TID), T.BUILDERS[TID]()


@pytest.fixture
def project(tmp_path):
    """A project dir holding its own copy of the shipped cases.json."""
    d = tmp_path / "project"
    d.mkdir()
    shutil.copy(TPL / "cases.json", d / "cases.json")
    return d


@pytest.fixture
def tpl(tmp_path):
    """A private copy of the template's price_series, so a test may bend it."""
    d = tmp_path / "tpl"
    shutil.copytree(TPL / "price_series", d / "price_series")
    return d


def _load(project, tpl, built):
    cfg, network = built
    return S.load(project, TID, template_dir=tpl, cfg=cfg, network=network)


def _edit(project, fn):
    path = project / "cases.json"
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))
    return data


def _kind(fn):
    with pytest.raises(S.ShippedError) as err:
        fn()
    return err.value.kind


def _change(path, old, new, kind="number"):
    return {"path": path, "value_type": kind, "old": old, "new": new}


def _one(project, tpl, built, change):
    """The status of a lone case carrying `change`."""
    _edit(
        project,
        lambda d: d.update(
            cases=[
                {
                    "id": "x",
                    "label": "x",
                    "answers": "x",
                    "reason": "x",
                    "changes": [change],
                }
            ]
        ),
    )
    (case,) = _load(project, tpl, built)["cases"]
    return case


# ── what the file shows (C3) ─────────────────────────────────────────────────


def test_every_case_of_the_file_is_shown_with_all_its_changes(project, tpl, built):
    shipped = _load(project, tpl, built)
    file = json.loads((project / "cases.json").read_text())["cases"]
    assert shipped["total"] == len(file) == 8
    assert [c["id"] for c in shipped["cases"]] == [c["id"] for c in file]
    for shown, given in zip(shipped["cases"], file):
        assert shown["changes"] == given["changes"]


def test_the_two_cases_outside_the_allow_list_are_not_run_with_their_reason(
    project, tpl, built
):
    shipped = _load(project, tpl, built)
    by = {c["id"]: c for c in shipped["cases"]}
    assert {i for i, c in by.items() if c["status"] == "not_run"} == {
        "tight_connection",
        "faster_degradation",
    }
    assert "Link.grid_export.p_nom" in by["tight_connection"]["why"]
    assert "finance.degradation_by_asset.pv_plant" in by["faster_degradation"]["why"]
    assert len(shipped["admissible"]) == 6
    assert all(by[i]["why"] is None for i in shipped["admissible"])


# ── a malformed file refuses with a typed code, never a crash (#1390 F2) ─────


def _cases_with(**case):
    base = {
        "id": "a",
        "label": "a",
        "answers": "a",
        "reason": "a",
        "changes": [_change("commercial.contracts.ppa_wind.price", 1, 2)],
    }
    return {"version": 1, "template": TID, "cases": [{**base, **case}]}


@pytest.mark.parametrize(
    "data",
    [
        [],
        "text",
        None,
        {"version": 2, "template": TID, "cases": []},
        {"version": 1, "template": "another", "cases": [{}]},
        {"version": 1, "template": TID, "cases": {}},
        {"version": 1, "template": TID, "cases": []},
        {"version": 1, "template": TID, "cases": ["a"]},
        _cases_with(id=5),
        _cases_with(id="bad id"),
        _cases_with(label=None),
        _cases_with(changes=[]),
        _cases_with(changes="x"),
        _cases_with(changes=["x"]),
        _cases_with(changes=[{"value_type": "number", "old": 1, "new": 2}]),
        _cases_with(changes=[{"path": "", "value_type": "number", "old": 1, "new": 2}]),
        _cases_with(
            changes=[{"path": "commercial.a", "value_type": "text", "old": 1, "new": 2}]
        ),
        _cases_with(
            changes=[{"path": "commercial.a", "value_type": "number", "old": 1}]
        ),
        {
            "version": 1,
            "template": TID,
            "cases": [_cases_with()["cases"][0], _cases_with()["cases"][0]],
        },
    ],
)
def test_a_malformed_file_refuses_with_its_typed_code(project, tpl, built, data):
    (project / "cases.json").write_text(json.dumps(data))
    assert _kind(lambda: _load(project, tpl, built)) == "shipped_cases_invalid"


def test_a_duplicate_id_names_the_id(project, tpl, built):
    case = _cases_with()["cases"][0]
    (project / "cases.json").write_text(
        json.dumps({"version": 1, "template": TID, "cases": [case, case]})
    )
    with pytest.raises(S.ShippedError, match="share the id a"):
        _load(project, tpl, built)


def test_text_that_is_not_json_refuses_with_its_typed_code(project, tpl, built):
    (project / "cases.json").write_text("{not json")
    assert _kind(lambda: _load(project, tpl, built)) == "shipped_cases_invalid"


def test_a_project_with_no_cases_file_refuses_with_its_typed_code(project, tpl, built):
    (project / "cases.json").unlink()
    assert _kind(lambda: _load(project, tpl, built)) == "shipped_cases_missing"


# ── the allow-list, checked against the built template (C4) ──────────────────


def _walk(node, prefix):
    """Every leaf path of the built commercial block; a list of records is addressed by
    each record's id, as the cases do."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{prefix}.{key}")
    elif (
        isinstance(node, list)
        and all(isinstance(i, dict) and "id" in i for i in node)
        and node
    ):
        for item in node:
            yield from _walk(
                {k: v for k, v in item.items() if k != "id"}, f"{prefix}.{item['id']}"
            )
    else:
        yield prefix


def test_the_allow_list_admits_exactly_the_price_and_contract_fields_of_the_built_template(
    built,
):
    cfg, network = built
    leaves = list(_walk(cfg["commercial"], "commercial"))
    admitted = set()
    for path in leaves:
        kind = "string" if path.endswith(".id") else "number"
        value = "ipp_da_base" if kind == "string" else 1.0
        if S.refusal(_change(path, value, value, kind), cfg, network) is None:
            admitted.add(path)
    contracts = [c["id"] for c in cfg["commercial"]["contracts"]]
    expected = {"commercial.export_price_ref.id", "commercial.import_price_ref.id"}
    for cid in contracts:
        expected |= {
            f"commercial.contracts.{cid}.reference_price.id",
            f"commercial.contracts.{cid}.price",
            f"commercial.contracts.{cid}.share",
            f"commercial.contracts.{cid}.tenor_years",
        }
    assert admitted == expected
    # None of what the wildcard would have taken that is a never-lever is admitted.
    never = (
        "export_link",
        "poc_link",
        "connection",
        "timezone",
        "demand_items",
        "provenance",
    )
    assert not [p for p in admitted if any(word in p for word in never)]


@pytest.mark.parametrize(
    "path",
    [
        "commercial.export_link",
        "commercial.poc_link",
        "commercial.timezone",
        "commercial.connection.limit_mw",
        "commercial.provenance",
        "commercial.demand_items",
        "commercial.contracts.ppa_wind.changes_dispatch",
        "commercial.contracts.ppa_wind.asset_ids",
        "commercial.contracts.ppa_wind.seller",
        "commercial.contracts.ppa_wind.library_ref",
        "commercial.contracts.ppa_wind.kind",
        "commercial.contracts.ppa_wind.reference_price.hash",
        "commercial.contracts.ppa_wind",
        "commercial.export_price_ref",
        "commercial.export_price_ref.hash",
        "commercial",
    ],
)
def test_a_commercial_field_outside_the_list_is_refused_each(project, tpl, built, path):
    case = _one(project, tpl, built, _change(path, 1, 2))
    assert case["status"] == "not_run" and path in case["why"]


@pytest.mark.parametrize(
    "path",
    [
        "StorageUnit.bess.p_nom",
        "StorageUnit.bess.p_nom_max",
        "StorageUnit.bess.max_hours",
        "StorageUnit.bess.p_set",
        "StorageUnit.other.inv_power_overnight",
        "StorageUnit.bess",
        "Link.grid_export.p_nom",
        "Generator.wind_farm.p_nom",
        "finance.degradation_by_asset.pv_plant",
        "solver.tolerance",
        "grid_code.limit",
    ],
)
def test_a_field_that_is_not_a_battery_cost_or_a_commercial_one_is_refused_each(
    project, tpl, built, path
):
    case = _one(project, tpl, built, _change(path, 1, 2))
    assert case["status"] == "not_run" and path in case["why"]


@pytest.mark.parametrize("field", S.BESS_FIELDS)
def test_each_battery_cost_field_is_admitted(project, tpl, built, field):
    assert (
        _one(project, tpl, built, _change(f"StorageUnit.bess.{field}", 2.0, 1.0))[
            "status"
        ]
        == "admissible"
    )


def test_a_contract_the_template_does_not_have_is_refused(project, tpl, built):
    case = _one(project, tpl, built, _change("commercial.contracts.nope.price", 1, 2))
    assert case["status"] == "not_run" and "contract" in case["why"]


@pytest.mark.parametrize(
    "change",
    [
        _change("commercial.contracts.ppa_wind.price", 1, "x", "string"),
        _change("commercial.contracts.ppa_wind.price", 1, float("nan")),
        _change("commercial.contracts.ppa_wind.price", 1, float("inf")),
        _change("commercial.contracts.ppa_wind.price", 1, True),
        _change("commercial.contracts.ppa_wind.price", "a", 2),
        _change("commercial.export_price_ref.id", "ipp_da_base", 5, "number"),
        _change("commercial.export_price_ref.id", "ipp_da_base", "", "string"),
        _change("commercial.export_price_ref.id", "", "ipp_da_low", "string"),
    ],
)
def test_a_value_of_the_wrong_kind_is_refused(project, tpl, built, change):
    assert _one(project, tpl, built, change)["status"] == "not_run"


def test_a_price_series_the_template_does_not_ship_is_not_run(project, tpl, built):
    change = _change(
        "commercial.export_price_ref.id", "ipp_da_base", "ipp_da_unknown", "string"
    )
    case = _one(project, tpl, built, change)
    assert case["status"] == "not_run" and "ipp_da_unknown" in case["why"]


# ── the source of the series (C10) and the digest (C5) ───────────────────────


def test_a_template_series_dir_with_no_hashes_refuses_before_any_case_is_admitted(
    project, tpl, built
):
    meta = tpl / "price_series" / "meta.json"
    body = json.loads(meta.read_text())
    for entry in body["series"]:
        entry.pop("sha256")
    meta.write_text(json.dumps(body))
    assert _kind(lambda: _load(project, tpl, built)) == "shipped_series_unverified"


def test_a_changed_series_file_refuses_before_any_case_is_admitted(project, tpl, built):
    first = sorted((tpl / "price_series").glob("*.csv"))[0]
    first.write_bytes(first.read_bytes() + b"x")
    assert _kind(lambda: _load(project, tpl, built)) == "shipped_series_unverified"


def test_the_digest_is_stable_and_binds_the_series_the_cases_name(project, tpl, built):
    a, b = _load(project, tpl, built), _load(project, tpl, built)
    assert a["digest"] == b["digest"]
    # A series hash a case names moves the digest: re-hash one referenced series.
    meta = tpl / "price_series" / "meta.json"
    body = json.loads(meta.read_text())
    entry = next(e for e in body["series"] if e["name"] == "ipp_da_low")
    csv = tpl / "price_series" / entry["file"]
    csv.write_bytes(csv.read_bytes() + b"\n")
    import hashlib

    entry["sha256"] = hashlib.sha256(csv.read_bytes()).hexdigest()
    meta.write_text(json.dumps(body))
    assert _load(project, tpl, built)["digest"] != a["digest"]


def _approved(project, tpl, built):
    return _load(project, tpl, built)["digest"]


@pytest.mark.parametrize(
    "edit",
    [
        lambda d: d["cases"][0]["changes"][0].update(new="ipp_da_cannibalised"),
        lambda d: d["cases"][2]["changes"][0].update(new=1.0),
        lambda d: d["cases"][6]["changes"][0].update(new=1.0),  # a case that is not run
        lambda d: d["cases"][7].update(label="renamed"),  # a case that is not run
        lambda d: d.update(note="edited"),
        lambda d: d.update(base="edited"),
        lambda d: d["cases"].pop(),
        lambda d: d["cases"].append({**d["cases"][0], "id": "added"}),
        lambda d: d["cases"].reverse(),
    ],
)
def test_any_edit_of_the_file_after_approval_refuses(project, tpl, built, edit):
    approved = _approved(project, tpl, built)
    cfg, network = built
    kwargs = dict(template_dir=tpl, cfg=cfg, network=network)
    S.require_current(approved, project, TID, **kwargs)
    _edit(project, edit)
    assert (
        _kind(lambda: S.require_current(approved, project, TID, **kwargs))
        == "shipped_cases_changed"
    )


def test_an_unedited_file_is_current(project, tpl, built):
    cfg, network = built
    shipped = S.require_current(
        _approved(project, tpl, built),
        project,
        TID,
        template_dir=tpl,
        cfg=cfg,
        network=network,
    )
    assert shipped["total"] == 8


# ── the pick (C3, #1072) ─────────────────────────────────────────────────────


def test_the_admissible_cases_that_fit_the_cap_all_run(project, tpl, built):
    shipped = _load(project, tpl, built)
    assert S.choose(shipped, 6) == shipped["admissible"]


def test_more_admissible_cases_than_the_cap_need_a_pick_and_the_count_is_the_true_total(
    project, tpl, built
):
    shipped = _load(project, tpl, built)
    with pytest.raises(S.ShippedError, match="6 of 8") as err:
        S.choose(shipped, CAP)
    assert err.value.kind == "shipped_cases_over_budget"


def test_a_pick_runs_exactly_the_picked_cases_never_the_first_ones(project, tpl, built):
    shipped = _load(project, tpl, built)
    last = shipped["admissible"][-CAP:]
    assert S.choose(shipped, CAP, last) == last
    assert last != shipped["admissible"][:CAP]


def test_a_pick_is_deduplicated_in_order(project, tpl, built):
    shipped = _load(project, tpl, built)
    a, b = shipped["admissible"][:2]
    assert S.choose(shipped, CAP, [b, a, b]) == [b, a]


@pytest.mark.parametrize(
    "picked", [["tight_connection"], ["nope"], ["low_price", "faster_degradation"]]
)
def test_a_pick_of_a_case_that_cannot_run_refuses(project, tpl, built, picked):
    assert (
        _kind(lambda: S.choose(_load(project, tpl, built), CAP, picked))
        == "shipped_case_not_runnable"
    )


@pytest.mark.parametrize("picked", [[], {}, "low_price", [1]])
def test_a_pick_that_is_empty_or_malformed_refuses(project, tpl, built, picked):
    kind = _kind(lambda: S.choose(_load(project, tpl, built), CAP, picked))
    assert kind in ("shipped_cases_over_budget", "shipped_case_not_runnable")


def test_a_pick_over_the_cap_refuses(project, tpl, built):
    shipped = _load(project, tpl, built)
    assert (
        _kind(lambda: S.choose(shipped, 2, shipped["admissible"][:3]))
        == "shipped_cases_over_budget"
    )


# ── what the allow-list refuses whatever the kind of value (mutant survivors) ─


@pytest.mark.parametrize("kind", ["number", "string"])
@pytest.mark.parametrize(
    "path",
    [
        "commercial.export_price_ref.hash",
        "commercial.other.ppa_wind.reference_price.id",
        "commercial.contracts.ppa_wind.reference_price.version",
        "commercial.contracts.ppa_wind.x.y",
        "commercial.other.ppa_wind.price",
        "commercial.contracts.ppa_wind.price.x",
        "commercial.contracts.ppa_wind.indexation_pct_per_year",
    ],
)
def test_a_path_that_only_looks_like_an_allowed_one_is_refused_for_either_kind(
    project, tpl, built, path, kind
):
    old, new = (1.0, 2.0) if kind == "number" else ("ipp_da_base", "ipp_da_low")
    case = _one(project, tpl, built, _change(path, old, new, kind))
    assert case["status"] == "not_run"
    assert (
        "is a string field" not in case["why"]
        and "is a number field" not in case["why"]
    )


@pytest.mark.parametrize(
    "path, expected",
    [
        ("commercial.export_price_ref.id", True),
        ("commercial.import_price_ref.id", True),
        ("commercial.contracts.ppa_wind.reference_price.id", True),
        ("commercial.contracts.ppa_wind.price", False),
        ("commercial.export_price_ref.hash", False),
        ("commercial.other_ref.id", False),
        ("commercial.contracts.ppa_wind.reference_price.hash", False),
        ("commercial.contracts.ppa_wind.other.id", False),
        ("commercial.contracts.ppa_wind.reference_price", False),
        ("a.b.c.d.e", False),
    ],
)
def test_which_paths_name_a_price_series(path, expected):
    assert S._is_ref(path) is expected


def test_a_battery_field_the_template_battery_lacks_is_refused(project, tpl, built):
    cfg, network = built
    change = _change("StorageUnit.bess.inv_power_overnight", 2.0, 1.0)
    assert S.refusal(change, cfg, network) is None
    no_column = network.copy()
    no_column.storage_units = no_column.storage_units.drop(
        columns=["inv_power_overnight"]
    )
    no_unit = network.copy()
    no_unit.storage_units = no_unit.storage_units.rename(index={"bess": "other"})
    for missing in (no_column, no_unit, None):
        assert "not on the template's battery" in S.refusal(change, cfg, missing)


@pytest.mark.parametrize(
    "cfg",
    [
        None,
        {},
        {"commercial": None},
        {"commercial": {"contracts": None}},
        {"commercial": {"contracts": [None, "x", {"price": 1}, {"id": ""}]}},
    ],
)
def test_a_template_with_no_contract_list_leaves_no_contract_to_change(cfg, built):
    _, network = built
    change = _change("commercial.contracts.ppa_wind.price", 1, 2)
    assert "contract the template does not have" in S.refusal(change, cfg, network)


@pytest.mark.parametrize(
    "old, new",
    [
        ("ipp_da_base", 5),
        (5, "ipp_da_low"),
        (["x"], "ipp_da_low"),
        ("ipp_da_base", ["x"]),
        (None, "ipp_da_low"),
        ("ipp_da_base", None),
        ("", "ipp_da_low"),
        ("ipp_da_base", ""),
    ],
)
def test_a_series_name_must_be_text_on_both_sides(project, tpl, built, old, new):
    case = _one(
        project,
        tpl,
        built,
        _change("commercial.export_price_ref.id", old, new, "string"),
    )
    assert case["status"] == "not_run"


def test_the_digest_binds_the_template_the_file_is_for():
    data = {"version": 1}
    assert S._digest(data, "a", {}) != S._digest(data, "b", {})
    assert S._digest(data, "a", {}) != S._digest(data, "a", {"s": "1"})
    assert S._digest(data, "a", {}) != S._digest({"version": 2}, "a", {})


# ── the last survivors ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "change",
    [
        _change("commercial.contracts.ppa_wind.price", 1.0, 2.0, "string"),
        _change("StorageUnit.bess.inv_power_overnight", 1.0, 2.0, "string"),
        _change(
            "commercial.export_price_ref.id", "ipp_da_base", "ipp_da_low", "number"
        ),
        _change(
            "commercial.contracts.ppa_wind.reference_price.id",
            "ipp_da_base",
            "ipp_da_low",
            "number",
        ),
    ],
)
def test_a_value_kind_that_does_not_fit_the_field_is_refused_whatever_the_values(
    built, change
):
    cfg, network = built
    assert S.refusal(change, cfg, network) is not None


@pytest.mark.parametrize(
    "old, new",
    [
        ("", "ipp_da_low"),
        ("ipp_da_base", ""),
        (5, "ipp_da_low"),
        ("ipp_da_base", 5),
        (None, "x"),
        ("x", None),
    ],
)
def test_a_series_name_is_refused_on_its_own_not_only_through_the_series_lookup(
    built, old, new
):
    cfg, network = built
    assert S.refusal(
        _change("commercial.export_price_ref.id", old, new, "string"), cfg, network
    )


@pytest.mark.parametrize("cases", [{"a": 1}, "abc", 5])
def test_cases_that_are_not_a_list_are_refused_for_that_reason(
    project, tpl, built, cases
):
    (project / "cases.json").write_text(
        json.dumps({"version": 1, "template": TID, "cases": cases})
    )
    with pytest.raises(S.ShippedError, match="non-empty list of cases"):
        _load(project, tpl, built)


def test_changes_that_are_not_a_list_are_refused_for_that_reason(project, tpl, built):
    (project / "cases.json").write_text(json.dumps(_cases_with(changes="abc")))
    with pytest.raises(S.ShippedError, match="needs a list of changes"):
        _load(project, tpl, built)


def test_a_pick_that_is_a_bare_string_is_refused_as_not_a_list_of_ids(
    project, tpl, built
):
    with pytest.raises(S.ShippedError, match="Pick cases by id"):
        S.choose(_load(project, tpl, built), CAP, "low_price")


def test_a_contract_with_no_id_is_not_a_contract_to_change(built):
    cfg, network = built
    odd = {"commercial": {"contracts": [{"id": ""}, {"id": None}, {"price": 1}]}}
    change = _change("commercial.contracts..price", 1, 2)
    assert "contract the template does not have" in S.refusal(change, odd, network)
