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


def test_the_digest_binds_the_projects_own_library_refs(project, tpl, built):
    cfg, network = built
    base = S.load(project, TID, template_dir=tpl, cfg=cfg, network=network)

    def with_ref(h):
        c = json.loads(json.dumps(cfg))
        c.setdefault("commercial", {})["export_price_ref"] = {
            "id": "ipp_da_base",
            "version": 1,
            "hash": h,
        }
        return S.load(project, TID, template_dir=tpl, cfg=c, network=network)["digest"]

    a, b = with_ref("a" * 64), with_ref("b" * 64)
    assert a != base["digest"] and a != b
    assert with_ref("a" * 64) == a


def _digest_with(project, tpl, built, patch):
    cfg, network = built
    c = json.loads(json.dumps(cfg))
    patch(c)
    return S.load(project, TID, template_dir=tpl, cfg=c, network=network)["digest"]


def _named_series(project):
    """A series name a shipped case names on a contract's reference price."""
    for case in json.loads((project / "cases.json").read_text())["cases"]:
        for ch in case["changes"]:
            if ch["path"].endswith(".reference_price.id"):
                return ch["path"].split(".")[2], ch["old"]
    raise AssertionError("no contract reference price in the shipped cases")


def test_the_digest_binds_a_contract_reference_price_ref(project, tpl, built):
    cid, name = _named_series(project)

    def ref(h):
        def patch(c):
            for k in c["commercial"]["contracts"]:
                if k["id"] == cid:
                    k["reference_price"] = {"id": name, "version": 1, "hash": h}

        return _digest_with(project, tpl, built, patch)

    assert ref("a" * 64) != ref("b" * 64)
    assert ref("a" * 64) == ref("a" * 64)
    cfg, _ = built
    assert ref("a" * 64) != _digest_with(project, tpl, built, lambda c: None)


def test_the_digest_binds_the_import_price_ref(project, tpl, built):
    def ref(h):
        return _digest_with(
            project,
            tpl,
            built,
            lambda c: c["commercial"].__setitem__(
                "import_price_ref", {"id": "ipp_da_base", "version": 1, "hash": h}
            ),
        )

    assert ref("a" * 64) != ref("b" * 64)


@pytest.mark.parametrize("commercial", ["not a dict", None, 7])
def test_a_project_config_with_no_commercial_block_still_digests(
    project, tpl, built, commercial
):
    base = _digest_with(project, tpl, built, lambda c: None)
    got = _digest_with(
        project, tpl, built, lambda c: c.__setitem__("commercial", commercial)
    )
    assert isinstance(got, str) and len(got) == 64 and got != base


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


LP_IDS = ["low_price", "cannibalised", "cheap_battery"]
FINANCE_IDS = ["ppa_price_minus_10", "merchant_heavy", "longer_ppa"]


def test_the_cases_split_three_lp_and_three_finance(project, tpl, built):
    shipped = _load(project, tpl, built)
    kinds = {c["id"]: c["kind"] for c in shipped["cases"]}
    assert [i for i in shipped["admissible"] if kinds[i] == S.LP] == LP_IDS
    assert [i for i in shipped["admissible"] if kinds[i] == S.FINANCE] == FINANCE_IDS


def test_more_lp_cases_than_the_cap_need_a_pick_and_the_count_is_the_true_total(
    project, tpl, built
):
    shipped = _load(project, tpl, built)
    with pytest.raises(S.ShippedError, match="3 of 8") as err:
        S.choose(shipped, 2)
    assert err.value.kind == "shipped_cases_over_budget"


def test_finance_cases_cost_no_solve_so_three_lp_fit_a_cap_of_three(
    project, tpl, built
):
    # P3: kills the mutant that counts finance cases against the cap.
    shipped = _load(project, tpl, built)
    assert S.choose(shipped, 3) == shipped["admissible"]


def test_a_cap_of_zero_admits_no_lp_case_and_still_runs_the_finance_ones(
    project, tpl, built
):
    assert S.choose(_load(project, tpl, built), 0) == FINANCE_IDS


def test_a_pick_runs_exactly_the_picked_lp_cases_and_every_finance_case(
    project, tpl, built
):
    shipped = _load(project, tpl, built)
    ids = S.choose(shipped, 2, ["cheap_battery", "cannibalised"])
    want = {"cannibalised", "cheap_battery", *FINANCE_IDS}
    assert ids == [i for i in shipped["admissible"] if i in want]  # file order
    assert "low_price" not in ids  # never the first ones


@pytest.mark.parametrize(
    "picked, kind",
    [
        ({}, "shipped_pick_invalid"),
        ("low_price", "shipped_pick_invalid"),
        ([1], "shipped_pick_invalid"),
        ([], "shipped_pick_empty"),
        (["low_price", "low_price"], "shipped_pick_duplicate"),
        (["nope"], "shipped_pick_unknown"),
        (["tight_connection"], "shipped_case_not_runnable"),
        (["low_price", "faster_degradation"], "shipped_case_not_runnable"),
        (["merchant_heavy"], "shipped_pick_finance"),
        (["low_price", "longer_ppa"], "shipped_pick_finance"),
        (LP_IDS, "shipped_cases_over_budget"),
    ],
)
def test_each_refused_pick_has_its_own_kind(project, tpl, built, picked, kind):
    assert _kind(lambda: S.choose(_load(project, tpl, built), 2, picked)) == kind


def test_a_pick_at_the_cap_runs(project, tpl, built):
    # The over-budget guard is `>`, not `>=`.
    assert "cheap_battery" in S.choose(
        _load(project, tpl, built), 2, ["low_price", "cheap_battery"]
    )


def test_load_names_each_series_hash_and_project_ref(project, tpl, built):
    shipped = _load(project, tpl, built)
    assert set(shipped["series"]) >= {
        "ipp_da_base",
        "ipp_da_low",
        "ipp_da_cannibalised",
    }
    assert all(len(h) == 64 for h in shipped["series"].values())
    assert isinstance(shipped["refs"], dict)


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


# ── S-B2a: classify, apply, the admission-time validation ────────────────────


def _ref(name, h="a" * 64):
    return {"id": name, "version": 1, "hash": h, "source": "template"}


def _project_cfg(cfg):
    """The template's solver_config as a created project holds it: minted refs."""
    c = json.loads(json.dumps(cfg))
    com = c["commercial"]
    com["export_price_ref"] = _ref("ipp_da_base")
    com["import_price_ref"] = _ref("ipp_da_base")
    for k in com["contracts"]:
        k["reference_price"] = _ref("ipp_da_base")
    return c


def _by_id(project, tpl, built):
    return {c["id"]: c for c in _load(project, tpl, built)["cases"]}


def test_each_shipped_case_is_classified_by_what_it_moves(project, tpl, built):
    kinds = {k: c["kind"] for k, c in _by_id(project, tpl, built).items()}
    assert kinds == {
        "low_price": "lp",
        "cannibalised": "lp",
        "ppa_price_minus_10": "finance",
        "merchant_heavy": "finance",
        "longer_ppa": "finance",
        "cheap_battery": "lp",
        "tight_connection": "unknown",
        "faster_degradation": "unknown",
    }


def test_a_contract_number_is_lp_only_for_a_dispatch_ppa(built):
    cfg, _ = built
    change = _change("commercial.contracts.ppa_wind.price", 65.0, 58.5)
    assert S.classify(change, cfg) == "finance"
    dispatch = json.loads(json.dumps(cfg))
    wind = next(k for k in dispatch["commercial"]["contracts"] if k["id"] == "ppa_wind")
    wind["settlement"] = "physical"
    wind["changes_dispatch"] = True
    wind["suspend_on_negative_price"] = None
    assert S.classify(change, dispatch) == "lp"
    other = _change("commercial.contracts.ppa_pv.price", 50.0, 45.0)
    assert S.classify(other, dispatch) == "finance"
    assert S.classify(change, {"commercial": "broken"}) == "unknown"


def test_a_reference_price_change_only_settles_and_a_price_ref_moves_the_lp(built):
    cfg, _ = built
    ref = _change("commercial.contracts.ppa_pv.reference_price.id", "a", "b", "string")
    assert S.classify(ref, cfg) == "finance"
    for k in ("export_price_ref", "import_price_ref"):
        assert (
            S.classify(_change(f"commercial.{k}.id", "a", "b", "string"), cfg) == "lp"
        )
    assert S.case_kind([ref], cfg) == "finance"
    assert (
        S.case_kind([ref, _change("StorageUnit.bess.inv_power_overnight", 1, 2)], cfg)
        == "lp"
    )
    unknown = _change("Link.x.p_nom", 1, 2)
    battery = _change("StorageUnit.bess.inv_power_overnight", 1, 2)
    assert S.case_kind([ref, unknown], cfg) == "unknown"  # finance + unknown
    assert S.case_kind([battery, unknown], cfg) == "unknown"  # lp + unknown
    assert S.case_kind([unknown, battery, ref], cfg) == "unknown"  # order-free


def _resolver(name):
    return _ref(name, "b" * 64)


def _case_changes(project, case_id):
    data = json.loads((project / "cases.json").read_text())
    return next(c for c in data["cases"] if c["id"] == case_id)["changes"]


def test_apply_patches_a_copy_and_returns_the_battery_changes(project, built):
    cfg = _project_cfg(built[0])
    before = json.dumps(cfg, sort_keys=True)
    out, network = S.apply(cfg, _case_changes(project, "low_price"), _resolver)
    assert json.dumps(cfg, sort_keys=True) == before  # never mutates
    assert out["commercial"]["export_price_ref"] == _ref("ipp_da_low", "b" * 64)
    assert out["commercial"]["contracts"][0]["reference_price"]["id"] == "ipp_da_low"
    assert network == []
    out, network = S.apply(cfg, _case_changes(project, "cheap_battery"), _resolver)
    assert out == cfg and [c["path"] for c in network] == [
        "StorageUnit.bess.inv_power_overnight",
        "StorageUnit.bess.inv_energy_overnight",
    ]


def test_apply_writes_each_contract_number(project, built):
    cfg = _project_cfg(built[0])
    out, _ = S.apply(cfg, _case_changes(project, "ppa_price_minus_10"), _resolver)
    prices = {k["id"]: k["price"] for k in out["commercial"]["contracts"]}
    assert prices == {"ppa_wind": 58.5, "ppa_pv": 45.0}


@pytest.mark.parametrize("case_id", ["ppa_price_minus_10", "low_price"])
def test_an_old_value_that_differs_from_the_forks_refuses(project, built, case_id):
    cfg = _project_cfg(built[0])
    if case_id == "low_price":
        cfg["commercial"]["export_price_ref"] = _ref("someone_elses_series")
    else:
        cfg["commercial"]["contracts"][0]["price"] = 99.0
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, _case_changes(project, case_id), _resolver)
    assert exc.value.kind == "shipped_case_stale"


def test_a_missing_contract_or_ref_is_stale_not_a_crash(project, built):
    cfg = _project_cfg(built[0])
    cfg["commercial"]["contracts"] = []
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, _case_changes(project, "ppa_price_minus_10"), _resolver)
    assert exc.value.kind == "shipped_case_stale"
    cfg = _project_cfg(built[0])
    cfg["commercial"]["export_price_ref"] = None
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, _case_changes(project, "low_price"), _resolver)
    assert exc.value.kind == "shipped_case_stale"


def test_a_series_with_no_ref_refuses_unresolved(project, built):
    cfg = _project_cfg(built[0])
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, _case_changes(project, "low_price"), lambda name: None)
    assert exc.value.kind == "shipped_series_unresolved"


def test_a_patched_block_that_does_not_validate_refuses_with_the_validators_reason(
    built,
):
    cfg = _project_cfg(built[0])
    bad = _change("commercial.contracts.ppa_wind.share", 0.7, 7.0)
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, [bad], _resolver)
    assert exc.value.kind == "shipped_cases_invalid"
    assert "share" in str(exc.value)


def test_a_project_with_no_commercial_block_cannot_apply(built):
    cfg = json.loads(json.dumps(built[0]))
    cfg.pop("commercial")
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, [_change("commercial.contracts.ppa_wind.price", 1, 2)], _resolver)
    assert exc.value.kind == "shipped_case_not_runnable"


def test_an_invalid_patched_case_is_not_run_at_admission_with_the_reason(
    project, tpl, built
):
    _edit(
        project,
        lambda d: d["cases"][3]["changes"][0].update(new=7.0),  # merchant_heavy share
    )
    case = _by_id(project, tpl, built)["merchant_heavy"]
    assert case["status"] == "not_run" and "share" in case["why"]
    assert case["id"] not in _load(project, tpl, built)["admissible"]


# ── GK B (58) K1-K4 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "StorageUnit.bess.max_hours",  # another storage field
        "StorageUnit.other.inv_power_overnight",  # another unit
        "Store.bess.capital_cost",  # a Store cost
        "Link.bess.capital_cost",  # a Link cost
        "commercial.some_new_key",
        "commercial.connection.id",  # the right shape, not a price ref
        "commercial.x.c1.reference_price.id",  # not under contracts
        "commercial.x.ppa_wind.price",  # not under contracts
        "commercial.export_price_ref.hash",
        "commercial.contracts.ppa_wind.reference_price.hash",
        "commercial.connection.p_nom",
        "commercial.contracts.ppa_wind.some_new_field",
        "finance.degradation_by_asset.pv_plant",
    ],
)
def test_a_change_the_classifier_does_not_recognise_is_unknown_never_lp_or_finance(
    built, path
):
    assert S.classify(_change(path, 1.0, 2.0), built[0]) == "unknown"


def test_an_unknown_case_is_never_run_at_admission(project, tpl, built):
    cfg, network = built
    bad = json.loads(json.dumps(cfg))
    bad["commercial"] = "broken"  # nothing about the contracts is known
    shipped = S.load(project, TID, template_dir=tpl, cfg=bad, network=network)
    case = next(c for c in shipped["cases"] if c["id"] == "ppa_price_minus_10")
    assert case["kind"] == "unknown" and case["status"] == "not_run"
    assert case["why"] and "ppa_price_minus_10" not in shipped["admissible"]


def test_the_project_contract_not_the_templates_decides_the_kind(project, tpl, built):
    cfg, network = built
    mine = json.loads(json.dumps(cfg))
    wind = next(k for k in mine["commercial"]["contracts"] if k["id"] == "ppa_wind")
    wind.update(settlement="physical", changes_dispatch=True)
    wind["suspend_on_negative_price"] = None
    kinds = {
        c["id"]: c["kind"]
        for c in S.load(project, TID, template_dir=tpl, cfg=mine, network=network)[
            "cases"
        ]
    }
    # The shipped file changes both contracts' price, and the project's wind PPA
    # moves the LP; the template's own would not.
    assert kinds["ppa_price_minus_10"] == "lp"
    assert _by_id(project, tpl, built)["ppa_price_minus_10"]["kind"] == "finance"


def test_apply_never_mutates_the_config_including_nested_lists(project, built):
    cfg = _project_cfg(built[0])
    before = json.loads(json.dumps(cfg))
    for case_id in ("low_price", "ppa_price_minus_10", "cheap_battery"):
        out, _ = S.apply(cfg, _case_changes(project, case_id), _resolver)
        assert cfg == before
        out["commercial"]["contracts"].append({"id": "x"})
        assert cfg == before  # the copy is deep


def test_the_validators_reason_is_what_the_case_reads(built):
    cfg = _project_cfg(built[0])
    with pytest.raises(S.ShippedError) as exc:
        S.apply(
            cfg, [_change("commercial.contracts.ppa_wind.share", 0.7, 7.0)], _resolver
        )
    assert str(exc.value).startswith("The changed commercial block does not validate:")
    assert "share" in str(exc.value)


def _fingerprint(cfg, network):
    from services import workflow_tools as W
    from services.pypsa_service import PyPSAService

    ctx = PyPSAService.build_context()
    ctx.network = network.copy()
    ctx.solver_state["solver_config"] = cfg
    return W._network_input_fingerprint(ctx)


def _applied(project, built, case_id):
    cfg = _project_cfg(built[0])
    out, battery = S.apply(cfg, _case_changes(project, case_id), _resolver)
    network = built[1].copy()
    for change in battery:
        network.storage_units.loc["bess", change["path"].split(".")[2]] = change["new"]
    return cfg, out, network


@pytest.mark.parametrize("case_id", ["low_price", "cannibalised"])
def test_a_price_ref_case_moves_the_fingerprint(project, built, case_id):
    cfg, out, network = _applied(project, built, case_id)
    assert _fingerprint(out, network) != _fingerprint(cfg, built[1])


@pytest.mark.parametrize(
    "case_id", ["ppa_price_minus_10", "merchant_heavy", "longer_ppa"]
)
def test_a_finance_case_also_moves_the_fingerprint_so_it_cannot_judge_alone(
    project, built, case_id
):
    """The fingerprint hashes the whole solver_config, contracts included, so it
    does not tell a finance-only case from an LP one (the judge keys on the kind)."""
    cfg, out, network = _applied(project, built, case_id)
    assert _fingerprint(out, network) != _fingerprint(cfg, built[1])


def test_the_battery_case_moves_the_lp_and_the_fingerprint(project, built):
    """The overnight cost fields are the parts the solve stages into the capital
    cost (`periodized_costs`); since TCU 08 (#1533) the fingerprint hashes the
    composite parts too, so even this raw edit moves it."""
    cfg, out, network = _applied(project, built, "cheap_battery")
    assert _fingerprint(out, network) != _fingerprint(cfg, built[1])
    changed = [
        network.storage_units.loc["bess", f] != built[1].storage_units.loc["bess", f]
        for f in S.BESS_FIELDS
    ]
    assert all(changed)


def _diff(a, b, path=""):
    """Every leaf path where two JSON-like values differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += _diff(x, y, f"{path}.{i}")
        return out
    return [] if a == b else [path]


@pytest.mark.parametrize(
    "case_id,fields",
    [
        ("ppa_price_minus_10", {"price"}),
        ("merchant_heavy", {"share"}),
        ("longer_ppa", {"tenor_years"}),
    ],
)
def test_a_finance_case_differs_from_the_base_only_in_the_fields_it_names(
    project, built, case_id, fields
):
    """No LP column, no price ref and no other config key rides along."""
    cfg, out, network = _applied(project, built, case_id)
    leaves = _diff(cfg, out)
    assert leaves and all(
        leaf.startswith("commercial.contracts.") and leaf.rsplit(".", 1)[1] in fields
        for leaf in leaves
    )
    assert (
        _diff(
            json.loads(network.storage_units.to_json()),
            json.loads(built[1].storage_units.to_json()),
        )
        == []
    )


@pytest.mark.parametrize("case_id", ["low_price", "cannibalised"])
def test_a_price_ref_case_changes_the_ref_ids_and_nothing_else(project, built, case_id):
    cfg, out, _ = _applied(project, built, case_id)
    leaves = _diff(cfg, out)
    # Exactly the replaced refs (id and the minted hash), nothing else.
    refs = (
        "commercial.export_price_ref",
        "commercial.import_price_ref",
        "commercial.contracts.0.reference_price",
        "commercial.contracts.1.reference_price",
    )
    assert set(leaves) == {f"{r}.{f}" for r in refs for f in ("id", "hash")}


def test_check_old_false_does_not_compare_the_old_value(project, built):
    cfg = _project_cfg(built[0])
    cfg["commercial"]["contracts"][0]["price"] = 99.0
    cfg["commercial"]["export_price_ref"] = _ref("someone_elses_series")
    out, _ = S.apply(
        cfg,
        _case_changes(project, "ppa_price_minus_10")
        + _case_changes(project, "low_price")[:1],
        _resolver,
        check_old=False,
    )
    assert out["commercial"]["contracts"][0]["price"] == 58.5
    assert out["commercial"]["export_price_ref"]["id"] == "ipp_da_low"


@pytest.mark.parametrize("contracts", [None, ["not a dict", 7]])
def test_a_contracts_list_that_is_missing_or_malformed_is_stale_not_a_crash(
    project, built, contracts
):
    cfg = _project_cfg(built[0])
    cfg["commercial"]["contracts"] = contracts
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, _case_changes(project, "ppa_price_minus_10"), _resolver)
    assert exc.value.kind == "shipped_case_stale"


def test_a_missing_current_number_is_stale_not_a_crash(project, built):
    cfg = _project_cfg(built[0])
    cfg["commercial"]["contracts"][0]["price"] = None
    with pytest.raises(S.ShippedError) as exc:
        S.apply(cfg, _case_changes(project, "ppa_price_minus_10"), _resolver)
    assert exc.value.kind == "shipped_case_stale"


def test_an_invalid_project_block_that_the_case_would_fix_is_still_not_run(
    project, tpl, built
):
    """merchant_heavy sets both shares to 0.3, so a project whose wind share is
    7.0 validates after the patch: only the unknown-kind guard stops the case."""
    cfg, network = built
    bad = json.loads(json.dumps(cfg))
    bad["commercial"]["contracts"][0]["share"] = 7.0
    shipped = S.load(project, TID, template_dir=tpl, cfg=bad, network=network)
    case = next(c for c in shipped["cases"] if c["id"] == "merchant_heavy")
    assert case["kind"] == "unknown" and case["status"] == "not_run"
    assert case["why"] == "What this case moves is not known, so it is not run."
    assert "merchant_heavy" not in shipped["admissible"]
