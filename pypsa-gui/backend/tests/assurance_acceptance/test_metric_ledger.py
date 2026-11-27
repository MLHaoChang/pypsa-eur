"""AA-10 S5 (A7.7; GK B G5): the per-metric mapping ledger.

`metric_ledger.d/` holds one JSON file per checked-answer profile, merged by
`smoke/json_fragments.py`. Each row maps one (profile, metric) of
`harness.calculations.PROFILES` to its formula, validity guards, tolerance,
oracle test and per-surface status (format: `metric_ledger.d/README.md`).

The ledger and `PROFILES` (read here, never copied) must match row for row.
Oracle node ids resolve statically with `ast`; nothing here collects or runs
another test. Every failure message ends with the verified/pending counts.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sys
from numbers import Real
from pathlib import Path

import pytest

from harness import calculations
from smoke import json_fragments

HERE = Path(__file__).resolve().parent
BASE = HERE / "metric_ledger.json"
REPO = HERE.parents[3]
SECTION = "profiles"
SURFACES = ("answer", "route", "workbook", "pdf", "download_meta")
STATUSES = ("verified", "pending")
ROW_KEYS = frozenset(
    {
        "metric",
        "formula",
        "validity",
        "tolerance",
        "oracle",
        "status",
        "domain_review",
        "surfaces",
    }
)
# GK B's per-metric domain review (issue 10 R5): "pending", or the full head SHA of
# the PR that carries the GK B OK. A pending row may carry GK B's one-line reason.
DOMAIN_REVIEW = re.compile(r"gk-b:[0-9a-f]{40}")
ROW_OPTIONAL = frozenset({"domain_review_note"})
SURFACE_KEYS = frozenset({"status", "test"})
OPTIONAL = frozenset({"ticket"})
NAMED_TOLERANCE = re.compile(
    r"([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*):([A-Z][A-Z0-9_]*)"
)
NODE_NAME = re.compile(r"([A-Za-z_]\w*(?:::[A-Za-z_]\w*)?)(?:\[(.+)\])?")
TICKET_22 = ".scratch/assistant-assurance/issues/22-workbook-mean-price-unweighted.md"
TICKET_23 = (
    ".scratch/assistant-assurance/issues/23-emissions-route-static-efficiency.md"
)


def slug(profile: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", profile.lower()).strip("-")


def load(base: Path = BASE) -> tuple[dict, list[str]]:
    """{(profile, metric): (row, file)} from the merged view, and every load problem.

    Each fragment holds exactly one profile under its own slug; a (profile,
    metric) two files define is named with both. `json_fragments.load_merged`
    then applies its own rules (unknown key, unreadable file, clash)."""
    rows, problems = {}, []
    for path in json_fragments.fragment_files(base):
        try:
            data = json_fragments._read_object(
                path
            )  # the module's one reader, so its errors read alike
        except ValueError as exc:
            problems.append(str(exc))
            continue
        profiles = data.get(SECTION)
        if not isinstance(profiles, dict) or len(profiles) != 1:
            problems.append(
                f"{path.name}: holds exactly one profile under {SECTION!r}, "
                f"not {sorted(profiles) if isinstance(profiles, dict) else profiles!r}"
            )
            continue
        [(profile, items)] = profiles.items()
        if path.stem != slug(profile):
            problems.append(
                f"{path.name}: profile {profile!r} belongs in {slug(profile)}.json"
            )
        if not isinstance(items, list):
            problems.append(f"{path.name}: {profile} must be a list of rows")
            continue
        for row in items:
            metric = row.get("metric") if isinstance(row, dict) else None
            if not isinstance(metric, str):
                problems.append(
                    f"{path.name}: {profile}: a row without a metric name: {row!r}"
                )
                continue
            if (profile, metric) in rows:
                problems.append(
                    f"{profile}/{metric} is defined in both {rows[profile, metric][1].name} "
                    f"and {path.name}"
                )
                continue
            rows[profile, metric] = (row, path)
    try:
        merged = json_fragments.load_merged(base)[SECTION]
    except ValueError as exc:
        problems.append(str(exc))
    else:
        if set(merged) != {p for p, _ in rows}:
            problems.append(f"the merged view holds {sorted(merged)}")
    return rows, problems


def summary(rows: dict) -> str:
    """Verified/pending counts, in total and per surface, for every failure message."""
    values = [row for row, _ in rows.values() if isinstance(row, dict)]

    def count(items):
        return (
            f"{sum(i.get('status') == 'verified' for i in items)} verified / "
            f"{sum(i.get('status') == 'pending' for i in items)} pending"
        )

    per = []
    for surface in SURFACES:
        listed = [
            r["surfaces"][surface]
            for r in values
            if isinstance(r.get("surfaces"), dict)
            and isinstance(r["surfaces"].get(surface), dict)
        ]
        per.append(f"{surface} {count(listed)}")
    return f"[ledger: {len(values)} rows, {count(values)}; " + "; ".join(per) + "]"


def _resolve_name(tree: ast.Module, node: ast.expr) -> ast.expr:
    """A module-level name's assigned value; a literal is returned as it is."""
    if not isinstance(node, ast.Name):
        return node
    for stmt in tree.body:
        targets = (
            stmt.targets
            if isinstance(stmt, ast.Assign)
            else [stmt.target]
            if isinstance(stmt, ast.AnnAssign) and stmt.value
            else []
        )
        if any(isinstance(t, ast.Name) and t.id == node.id for t in targets):
            return stmt.value
    raise ValueError(f"{node.id} is not a module-level assignment")


def _is_mark(node: ast.expr, name: str) -> bool:
    func = node.func if isinstance(node, ast.Call) else None
    return isinstance(func, ast.Attribute) and func.attr == name


def _id(value) -> str:
    """The id pytest gives a literal argvalue; anything else is not guessed."""
    if isinstance(value, str) and value.isascii():
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    raise ValueError(f"no static id for {value!r}")


def param_ids(mark: ast.Call, tree: ast.Module) -> list[str]:
    """The ids one `pytest.mark.parametrize` gives, from literals only."""
    kwargs = {k.arg: k.value for k in mark.keywords}
    args = list(mark.args) + [
        kwargs[k] for k in ("argnames", "argvalues")[len(mark.args) :] if k in kwargs
    ]
    if len(args) < 2:
        raise ValueError("parametrize without argnames and argvalues")
    names = ast.literal_eval(_resolve_name(tree, args[0]))
    names = (
        [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
    )
    if "ids" in kwargs:
        ids = ast.literal_eval(_resolve_name(tree, kwargs["ids"]))
        if not (
            isinstance(ids, (list, tuple)) and all(isinstance(i, str) for i in ids)
        ):
            raise ValueError("ids is not a literal list of strings")
        return list(ids)
    values = _resolve_name(tree, args[1])
    if not isinstance(values, (ast.List, ast.Tuple)):
        raise ValueError("argvalues is not a literal list")
    ids = []
    for element in values.elts:
        if _is_mark(element, "param"):
            given = {k.arg: k.value for k in element.keywords}
            if "id" in given:
                ids.append(ast.literal_eval(given["id"]))
                continue
            value = tuple(ast.literal_eval(a) for a in element.args)
            value = value[0] if len(names) == 1 else value
        else:
            value = ast.literal_eval(element)
        parts = [value] if len(names) == 1 else list(value)
        ids.append("-".join(_id(v) for v in parts))
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate ids {ids}: pytest renames them")
    return ids


def node_problem(node) -> str | None:
    """Why a repository-relative pytest node id does not resolve, statically; None when it does."""
    if not isinstance(node, str):
        return f"a node id is a string, not {node!r}"
    file_part, sep, name = node.partition("::")
    match = NODE_NAME.fullmatch(name) if sep else None
    if match is None:
        return f"{node!r} is not path.py::test[param]"
    path = (REPO / file_part).resolve()
    if REPO not in path.parents or path.suffix != ".py" or not path.is_file():
        return f"{file_part} is not a test file in the repository"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    *owner, func_name = match.group(1).split("::")
    body = tree.body
    if owner:
        cls = next(
            (s for s in body if isinstance(s, ast.ClassDef) and s.name == owner[0]),
            None,
        )
        if cls is None:
            return f"{file_part} has no class {owner[0]}"
        body = cls.body
    func = next(
        (
            s
            for s in body
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            and s.name == func_name
        ),
        None,
    )
    if func is None or not func_name.startswith("test"):
        return f"{file_part} has no test {match.group(1)}"
    param = match.group(2)
    if param is None:
        return None
    marks = [d for d in func.decorator_list if _is_mark(d, "parametrize")]
    if len(marks) != 1:
        return f"{match.group(1)} has {len(marks)} parametrize decorators; [{param}] is not resolved statically"
    try:
        ids = param_ids(marks[0], tree)
    except (ValueError, SyntaxError, TypeError, KeyError) as exc:
        return f"{match.group(1)}: param ids not resolvable statically ({exc})"
    return (
        None
        if param in ids
        else f"{match.group(1)} has no param id {param!r}; it has {ids}"
    )


def _ticket_problem(ticket) -> str | None:
    if (
        not isinstance(ticket, str)
        or not ticket.startswith(".scratch/")
        or ".." in Path(ticket).parts
    ):
        return f"ticket {ticket!r} is not a path under .scratch/"
    return None if (REPO / ticket).is_file() else f"ticket {ticket} does not exist"


def _tolerance_problem(tolerance) -> str | None:
    if tolerance == "exact" or (
        isinstance(tolerance, str) and re.fullmatch(r"ulp:[1-9][0-9]*", tolerance)
    ):
        return None
    match = NAMED_TOLERANCE.fullmatch(tolerance) if isinstance(tolerance, str) else None
    if match is None:
        return f"tolerance {tolerance!r} is not exact, ulp:<n> or module.path:NAME"
    try:
        value = getattr(importlib.import_module(match.group(1)), match.group(2))
    except (ImportError, AttributeError):
        return f"tolerance {tolerance} does not resolve"
    if isinstance(value, bool) or not isinstance(value, Real):
        return f"tolerance {tolerance} is not a number"
    return None


def _status_problems(entry: dict, test_key: str) -> list[str]:
    status, test = entry.get("status"), entry.get(test_key)
    if status not in STATUSES:
        return [f"status {status!r} is not one of {STATUSES}"]
    if status == "verified" and test is None:
        return [f"verified needs a {test_key}"]
    if status == "pending" and test is not None:
        return [f"pending has {test_key} null, not {test!r}"]
    return []


def domain_review_problem(value) -> str | None:
    if value == "pending" or (
        isinstance(value, str) and DOMAIN_REVIEW.fullmatch(value)
    ):
        return None
    return f"domain_review {value!r} is not 'pending' or gk-b:<full sha>"


def row_problems(profile: str, row, path: Path) -> list[str]:
    """Every closed-schema breach of one row, each naming the file and the row."""
    name = row.get("metric") if isinstance(row, dict) else repr(row)
    where = f"{path.name} {profile}/{name}"
    if not isinstance(row, dict):
        return [f"{where}: a row is an object"]
    found = []
    keys = set(row)
    if ROW_KEYS - keys or keys - ROW_KEYS - OPTIONAL - ROW_OPTIONAL:
        found.append(
            f"keys missing {sorted(ROW_KEYS - keys)}, "
            f"unknown {sorted(keys - ROW_KEYS - OPTIONAL - ROW_OPTIONAL)}"
        )
    found += filter(None, [domain_review_problem(row.get("domain_review"))])
    for key in ("formula", "validity"):
        if not (isinstance(row.get(key), str) and row[key].strip()):
            found.append(f"{key} must be non-empty text")
    found += filter(None, [_tolerance_problem(row.get("tolerance"))])
    found += _status_problems(row, "oracle")
    if row.get("oracle") is not None:
        found += filter(None, [node_problem(row["oracle"])])
    if "ticket" in row:
        found += filter(None, [_ticket_problem(row["ticket"])])
    surfaces = row.get("surfaces")
    if not isinstance(surfaces, dict) or not surfaces:
        found.append("surfaces must be a non-empty object")
        surfaces = {}
    for surface, entry in surfaces.items():
        if surface not in SURFACES:
            found.append(f"surface {surface!r} is not one of {SURFACES}")
            continue
        if not isinstance(entry, dict):
            found.append(f"surface {surface} must be an object")
            continue
        keys = set(entry)
        if SURFACE_KEYS - keys or keys - SURFACE_KEYS - OPTIONAL:
            found.append(
                f"surface {surface}: keys missing {sorted(SURFACE_KEYS - keys)}, "
                f"unknown {sorted(keys - SURFACE_KEYS - OPTIONAL)}"
            )
        found += [f"surface {surface}: {p}" for p in _status_problems(entry, "test")]
        if entry.get("test") is not None:
            found += [
                f"surface {surface}: {p}"
                for p in filter(None, [node_problem(entry["test"])])
            ]
        if "ticket" in entry:
            found += [
                f"surface {surface}: {p}"
                for p in filter(None, [_ticket_problem(entry["ticket"])])
            ]
        if entry.get("status") == "verified" and row.get("status") != "verified":
            found.append(f"surface {surface} is verified on a pending row")
    return [f"{where}: {p}" for p in found]


@pytest.fixture(scope="module")
def ledger():
    return load()


def expected_pairs() -> set:
    return {
        (profile, metric)
        for profile, metrics in calculations.PROFILES.items()
        for metric in metrics
    }


def test_every_fragment_holds_one_profile_and_loads_through_json_fragments(ledger):
    rows, problems = ledger
    assert not problems, "\n".join(problems) + "\n" + summary(rows)


def test_no_profile_metric_is_missing_a_row(ledger):
    rows, _ = ledger
    missing = sorted(expected_pairs() - set(rows))
    assert not missing, (
        f"no ledger row for {[f'{p}/{m}' for p, m in missing]} {summary(rows)}"
    )


def test_no_row_is_an_orphan(ledger):
    rows, _ = ledger
    orphans = sorted(set(rows) - expected_pairs())
    assert not orphans, (
        f"rows with no metric in harness.calculations.PROFILES: "
        f"{[f'{p}/{m} ({rows[p, m][1].name})' for p, m in orphans]} {summary(rows)}"
    )


def test_every_row_follows_the_closed_schema_and_its_tests_resolve(ledger):
    rows, _ = ledger
    problems = [
        p
        for (profile, _), (row, path) in sorted(rows.items())
        for p in row_problems(profile, row, path)
    ]
    assert not problems, "\n".join(problems) + "\n" + summary(rows)


@pytest.mark.parametrize(
    "profile,metric,surfaces,ticket",
    [
        ("bus-price/1", "mean_price", ("workbook",), TICKET_22),
        ("network-emissions/1", "total_emissions", ("route", "workbook"), TICKET_23),
    ],
)
def test_the_known_surface_defects_stay_pending_with_their_tickets(
    ledger, profile, metric, surfaces, ticket
):
    rows, _ = ledger
    row = rows[profile, metric][0]
    for surface in surfaces:
        assert row["surfaces"].get(surface) == {
            "status": "pending",
            "test": None,
            "ticket": ticket,
        }, f"{profile}/{metric} {surface} {summary(rows)}"


# ── the loader's and resolver's own failures ──────────────────────────────


ROW = {
    "metric": "mean_price",
    "formula": "f",
    "validity": "v",
    "tolerance": "exact",
    "oracle": None,
    "status": "pending",
    "domain_review": "pending",
    "surfaces": {"answer": {"status": "pending", "test": None}},
}


@pytest.fixture
def base(tmp_path):
    path = tmp_path / "metric_ledger.json"
    path.write_text(json.dumps({SECTION: {}}))
    (tmp_path / "metric_ledger.d").mkdir()
    return path


def _fragment(base, name, profiles):
    (base.with_suffix(".d") / name).write_text(json.dumps({SECTION: profiles}))


def test_a_metric_two_files_define_names_both(base):
    _fragment(base, "bus-price-1.json", {"bus-price/1": [ROW]})
    _fragment(base, "zz.json", {"bus-price/1": [ROW]})
    _, problems = load(base)
    assert (
        "bus-price/1/mean_price is defined in both bus-price-1.json and zz.json"
        in problems
    )


def test_a_file_with_two_profiles_or_another_profiles_name_fails(base):
    _fragment(base, "bus-price-1.json", {"bus-price/1": [ROW], "lost-load/1": []})
    _fragment(base, "lost-load.json", {"lost-load/1": []})
    problems = "\n".join(load(base)[1])
    assert "bus-price-1.json: holds exactly one profile" in problems
    assert (
        "lost-load.json: profile 'lost-load/1' belongs in lost-load-1.json" in problems
    )


def test_an_unknown_key_or_value_names_the_file_and_the_row():
    path = Path("bus-price-1.json")
    bad = {
        **ROW,
        "note": "x",
        "status": "done",
        "surfaces": {"email": {"status": "pending", "test": None}},
    }
    problems = "\n".join(row_problems("bus-price/1", bad, path))
    assert (
        "bus-price-1.json bus-price/1/mean_price: keys missing [], unknown ['note']"
        in problems
    )
    assert (
        "status 'done' is not one of" in problems
        and "surface 'email' is not one of" in problems
    )
    assert row_problems(
        "bus-price/1", {**ROW, "tolerance": "harness.price_calculations:NO_SUCH"}, path
    )
    assert row_problems(
        "bus-price/1", {**ROW, "status": "verified"}, path
    )  # verified with a null oracle
    assert not row_problems(
        "bus-price/1",
        {**ROW, "tolerance": "harness.price_calculations:VOLL_TOLERANCE"},
        path,
    )


def test_node_ids_resolve_statically_including_a_param_id():
    tests = "pypsa-gui/backend/tests/test_result_profiles_assurance.py::"
    assert node_problem(tests + "test_curtailment_of_a_variable_generator[G1]") is None
    assert node_problem(tests + "test_curtailment_of_a_variable_generator[G9]") == (
        "test_curtailment_of_a_variable_generator has no param id 'G9'; it has ['G1', 'network']"
    )
    assert "has no test" in node_problem(tests + "test_no_such_test")
    assert "not a test file" in node_problem("pypsa-gui/backend/tests/nope.py::test_x")
    # float("nan") is not a literal: pytest's generated id is not guessed.
    assert "not resolvable statically" in node_problem(
        "pypsa-gui/backend/tests/test_assurance_metrics.py::"
        "test_missing_or_out_of_bounds_dispatch_never_becomes_full_horizon[bad0]"
    )


# ── per-metric domain review (issue 10 R5, GK B plan GO) ─────────────────

R1_OK = "gk-b:bf0eed8e63449828d3c91967f75a8c35e2775a2a"  # #2036
R2_OK = "gk-b:e0be8aa78175a29b88c93d1c46b51c0f6189307c"  # #2037
R3_OK = "gk-b:ccad4aaf72c1a6a8b5599b61703e945575171f9b"  # #2044
R4_OK = "gk-b:1f98625c8302e01590af4612b987d01752076a11"  # #2049
# GK B (73) on #2057: the 41 rows its R5 table passed, and the two energy-balance
# rows re-confirmed at SERVED_TOL (they leave R2's sha, which saw BUS_TOL_MW).
R5_OK = "gk-b:41f32cf0bbe4de6e9df232044d48858467be85f8"  # #2057
# Exact set: a row whose domain_review is not "pending" is listed here with its value.
REVIEWED = {
    **{
        pair: R5_OK
        for pair in (
            ("ac-branch-loading/1", "ac_branch_rating"),
            ("ac-branch-loading/1", "ac_peak_apparent_flow"),
            ("ac-branch-loading/1", "ac_peak_branch_utilisation"),
            ("branch-loading/1", "branch_rating"),
            ("branch-loading/1", "peak_branch_flow"),
            ("branch-loading/1", "peak_branch_utilisation"),
            ("bus-price/1", "max_price"),
            ("bus-price/1", "mean_price"),
            ("bus-price/1", "min_price"),
            ("capacity/1", "capacity_change"),
            ("capacity/1", "optimal_capacity"),
            ("carrier-energy/1", "carrier_generator_energy"),
            ("carrier-energy/1", "carrier_peak_generator_output"),
            ("energy-balance/1", "export_losses_and_conversion"),
            ("energy-balance/1", "storage_change"),
            ("generator-cost/1", "allocated_fixed_cost"),
            ("generator-cost/1", "annual_fixed_cost"),
            ("generator-cost/1", "dispatch_energy"),
            ("generator-cost/1", "marginal_expenditure"),
            ("generator-cost/1", "peak_dispatch"),
            ("generator-cost/1", "total_cost"),
            ("generator-dispatch/1", "dispatch_energy"),
            ("generator-dispatch/1", "marginal_expenditure"),
            ("generator-dispatch/1", "peak_dispatch"),
            ("generator-emissions/1", "generator_emissions"),
            ("grid-readiness/1", "not_checked"),
            ("lost-load/1", "lost_load_cost"),
            ("lost-load/1", "lost_load_energy"),
            ("lost-load/1", "peak_shed"),
            ("network-emissions/1", "co2_cap_shadow_price"),
            ("network-emissions/1", "total_emissions"),
            ("network-linear-cost/1", "total_cost"),
            ("network-loading/1", "max_branch_utilisation"),
            ("site-import/1", "export_energy"),
            ("site-import/1", "grid_import_energy"),
            ("site-import/1", "peak_grid_import"),
            ("storage-unit-cost/1", "allocated_fixed_cost"),
            ("storage-unit-cost/1", "annual_fixed_cost"),
            ("storage-unit-cost/1", "storage_discharge_expenditure"),
            ("storage-unit-cost/1", "storage_holding_expenditure"),
            ("storage-unit-cost/1", "storage_operating_expenditure"),
            ("storage-unit-cost/1", "total_cost"),
            ("time-to-power/1", "time_to_power_months"),
        )
    },
    **{
        ("renewable-share/1", m): R4_OK
        for m in ("renewable_share", "nonrenewable_energy", "electricity_used")
    },
    **{
        ("system-cost-breakdown/1", m): R3_OK
        for m in ("system_capex", "system_opex", "system_cost", "objective_residual")
    },
    **{
        ("branch-losses/1", m): R1_OK
        for m in ("loss_energy", "peak_loss", "demand_energy", "loss_pct_of_demand")
    },
    **{
        pair: R2_OK
        for pair in (
            ("grid-readiness/1", "checked_pass"),
            ("grid-readiness/1", "checked_warn"),
            ("grid-readiness/1", "checked_fail"),
            ("network-linear-cost/1", "dispatch_energy"),
            ("network-linear-cost/1", "marginal_expenditure"),
            ("network-linear-cost/1", "peak_dispatch"),
            ("network-linear-cost/1", "annual_fixed_cost"),
            ("network-linear-cost/1", "allocated_fixed_cost"),
        )
    },
}


def test_the_reviewed_rows_are_exactly_the_listed_set(ledger):
    rows, _ = ledger
    reviewed = {
        pair: row["domain_review"]
        for pair, (row, _) in rows.items()
        if row.get("domain_review") != "pending"
    }
    assert reviewed == REVIEWED, summary(rows)


def test_only_a_verified_row_can_carry_a_domain_review(ledger):
    rows, _ = ledger
    assert all(rows[pair][0]["status"] == "verified" for pair in REVIEWED)


@pytest.mark.parametrize(
    "value",
    [
        "gk-b:bf0eed8",
        "gk-a:bf0eed8e63449828d3c91967f75a8c35e2775a2a",
        "gk-b:BF0EED8E63449828D3C91967F75A8C35E2775A2A",
        "gk-b:bf0eed8e63449828d3c91967f75a8c35e2775a2a ",
        "ok",
        None,
    ],
)
def test_a_malformed_domain_review_is_refused(value):
    problems = row_problems("bus-price/1", {**ROW, "domain_review": value}, Path("x"))
    assert any("domain_review" in p for p in problems)
    assert not row_problems("bus-price/1", {**ROW, "domain_review": R1_OK}, Path("x"))


def test_a_row_without_domain_review_is_refused():
    row = {k: v for k, v in ROW.items() if k != "domain_review"}
    assert any(
        "keys missing ['domain_review']" in p
        for p in row_problems("bus-price/1", row, Path("x"))
    )


def test_domain_review_feeds_no_tally_and_no_gate(ledger):
    """Changing every row's domain_review leaves the verified tally equal, and no
    application module opens the ledger, so rollout and pilot gating cannot see it."""
    rows, _ = ledger
    flipped = {
        pair: ({**row, "domain_review": R1_OK}, path)
        for pair, (row, path) in rows.items()
    }
    assert summary(flipped) == summary(rows)
    backend = HERE.parents[1]
    readers = [
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if "tests" not in path.relative_to(backend).parts
        and re.search(r"\bmetric_ledger\b", path.read_text(errors="ignore"))
    ]
    assert readers == []


def test_independent_domain_approval_stays_false():
    from harness import assurance_contracts, rollout

    assert rollout.capabilities()["independent_domain_approval"] is False
    assert "independent_domain_approval" in assurance_contracts.PENDING


# GK B (73) R5 ruling C, tightened by issue 29: a verified row's named tolerance
# constant must be the one its oracle compares at, not just a name in the file.
TOLERANCE_KEYWORDS = frozenset({"rel", "abs", "rtol", "atol"})


def _functions(tree: ast.Module) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _names(node: ast.AST, constant: str) -> bool:
    return any(
        (isinstance(n, ast.Name) and n.id == constant)
        or (isinstance(n, ast.Attribute) and n.attr == constant)
        for n in ast.walk(node)
    )


def compares_at(oracle: str, constant: str) -> bool:
    """True when the oracle function, or a helper it calls in its module, uses
    `constant` as a rel/abs/rtol/atol keyword or inside a comparison."""
    path, _, name = oracle.partition("::")
    tree = ast.parse((REPO / path).read_text())
    functions = _functions(tree)
    todo, seen = [name.split("[")[0]], set()
    while todo:
        current = todo.pop()
        if current in seen or current not in functions:
            continue
        seen.add(current)
        for node in ast.walk(functions[current]):
            if isinstance(node, ast.Compare) and _names(node, constant):
                return True
            if isinstance(node, ast.Call):
                if any(
                    k.arg in TOLERANCE_KEYWORDS and _names(k.value, constant)
                    for k in node.keywords
                ):
                    return True
                if isinstance(node.func, ast.Name):
                    todo.append(node.func.id)
    return False


def unpinned_reviews(rows) -> list[str]:
    """Verified rows whose named tolerance constant their oracle does not compare at."""
    off = []
    for (profile, metric), (row, _) in sorted(rows.items()):
        if row["status"] != "verified" or row["oracle"] is None:
            continue
        match = NAMED_TOLERANCE.fullmatch(row["tolerance"])
        if match is None:
            continue  # "exact" or ulp:<n>: no constant to name
        if not compares_at(row["oracle"], match[2]):
            off.append(
                f"{profile}/{metric}: {match[2]} is not a tolerance keyword "
                f"in {row['oracle'].split('::')[-1]}"
            )
    return off


def test_a_verified_row_names_the_tolerance_its_oracle_compares_at(ledger):
    """Every verified row, reviewed or pending, names a constant its oracle asserts at."""
    off = unpinned_reviews(ledger[0])
    assert off == [], "\n".join(off)


def test_the_check_proves_the_comparison_not_the_name(tmp_path, monkeypatch):
    """A constant that is only imported, or used by another test, does not pin the row."""
    source = tmp_path / "test_x.py"
    source.write_text(
        "from m import TOL\n"
        "def helper(a):\n    assert a == approx(1, rel=TOL)\n"
        "def test_imports_only():\n    assert 1 == approx(1, abs=1e-6)\n"
        "def test_other():\n    assert 1 == approx(1, abs=TOL)\n"
        "def test_helper():\n    helper(1)\n"
    )
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert not compares_at("test_x.py::test_imports_only", "TOL")
    assert compares_at("test_x.py::test_other", "TOL")
    assert compares_at("test_x.py::test_helper", "TOL")


def test_a_pending_row_says_why_and_a_reviewed_row_does_not(ledger):
    rows, _ = ledger
    off = []
    for (profile, metric), (row, _) in sorted(rows.items()):
        note = row.get("domain_review_note")
        if row["domain_review"] == "pending" and row["status"] == "verified":
            if not (isinstance(note, str) and note.strip()):
                off.append(f"{profile}/{metric}: pending with no domain_review_note")
        elif row["domain_review"] != "pending" and note is not None:
            off.append(f"{profile}/{metric}: reviewed but carries a domain_review_note")
    assert off == [], "\n".join(off)


def test_a_row_naming_a_constant_its_oracle_does_not_use_is_refused(ledger):
    rows, _ = ledger
    pair = ("investment-case/1", "project_irr")
    row, path = rows[pair]
    assert row["tolerance"] == "harness.investment_calculations:IRR_TOLERANCE"
    wrong = {**row, "tolerance": "harness.investment_calculations:LCOE_TOLERANCE"}
    assert unpinned_reviews({**rows, pair: (wrong, path)}) == [
        "investment-case/1/project_irr: LCOE_TOLERANCE is not a tolerance keyword "
        "in test_project_irr_of_one_sign_change_is_the_hand_root"
    ]


# ── pending metrics with no checked profile (issue 10 R5) ────────────────

PENDING = HERE / "metric_ledger_pending.json"
PENDING_NAMES = {
    "fmea",
    "monte_carlo_adequacy",
    "adequacy_loops",
    "billing",
    "cfe_score",
    "lcoh",
    "unit_commitment",
    "bus_voltages",
    "business_case_npv",
    "objective_decomposition",
}


def _pending_rows():
    return json.loads(PENDING.read_text())["pending"]


def test_the_pending_metrics_are_exactly_the_named_set():
    rows = _pending_rows()
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)) and set(names) == PENDING_NAMES
    for row in rows:
        assert set(row) == {
            "name",
            "implementation",
            "what",
            "status",
            "oracle",
            "reason",
        }, row
        assert row["status"] == "pending" and row["oracle"] is None, row


@pytest.mark.parametrize("row", _pending_rows(), ids=lambda r: r["name"])
def test_each_pending_implementation_resolves(row):
    """A rename of the function breaks this, so the row cannot point at nothing."""
    import importlib

    module, function = row["implementation"].split(":")
    assert callable(getattr(importlib.import_module(module), function))


def test_no_pending_metric_is_counted_as_verified(ledger):
    rows, _ = ledger
    profiles = {profile for profile, _ in rows}
    names = {metric for _, metric in rows}
    assert not PENDING_NAMES & (profiles | names)
    assert "0 verified" in summary(
        {(r["name"], r["name"]): (r, PENDING) for r in _pending_rows()}
    )
