"""Study harness 12 (GK A I6): on an off-grid project the SCR row and every
row built from the PoC's entered S_k'' leave the scorecard with the reason."""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from types import SimpleNamespace as types_ns

import pytest

from services.adequacy import grid_readiness as G
from tests.test_grid_readiness import PROFILE
from tests.test_grid_readiness_motor_start import _site
from tests.test_grid_readiness_voltage_stability import IDS as VS_IDS
from tests.test_grid_readiness_voltage_stability import _template, _write


#: The module's readers of the PoC's entered fault level.
_SK_READERS = {"_poc_sks", "_poc_sk_mva", "_poc_sk_min", "_governing_poc"}


def _calls(fn) -> set:
    """The names ``fn`` calls or refers to, from its source."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def _reads_the_poc_fault_level(build) -> bool:
    """True when ``build`` reaches a fault-level reader directly or through the module's own helpers
    (followed transitively), or reads the ``eh_sk_mva`` column itself."""
    seen, todo = set(), [build]
    while todo:
        fn = todo.pop()
        if fn in seen:
            continue
        seen.add(fn)
        names = _calls(fn)
        if names & _SK_READERS or "eh_sk_mva" in inspect.getsource(fn):
            return True
        todo += [
            f
            for name in names
            if inspect.isfunction(f := getattr(G, name, None))
            and f.__module__ == G.__name__
            and f not in G._BUILDERS
        ]
    return False


def test_a_builder_that_reads_the_fault_level_through_a_helper_is_caught(monkeypatch):
    def _helper(inp):
        return G._poc_sk_mva(inp.n)

    def _new_row(inp):
        return {"id": "new", "value": _helper(inp)}

    _helper.__module__ = _new_row.__module__ = G.__name__
    monkeypatch.setattr(G, "_helper", _helper, raising=False)
    assert _reads_the_poc_fault_level(_new_row)
    assert not _reads_the_poc_fault_level(G._lole_row)


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
    # The voltage-stability rows name the governing PoC; they leave an off-grid scorecard by their own rule.
    assert G._vs_rows in derived
    derived -= {G._vs_rows}
    assert derived == set(G.SK_BUILDERS)
    named = {b.__name__ for b in G.SK_BUILDERS}
    assert named == {
        "_scr_rows",
        "_rvc_row",
        "_rvc_infrequent_row",
        "_harmonic_row",
        "_tdd_row",
        "_flicker_row",
        "_motor_start_rows",
        "_unbalance_row",
    }


def test_every_scorecard_builder_is_listed_once():
    builders = {
        name
        for name, f in vars(G).items()
        if inspect.isfunction(f)
        and name.startswith("_")
        and name.endswith(("_row", "_rows"))
        and list(inspect.signature(f).parameters) == ["inp"]
    }
    assert builders == {b.__name__ for b in G._BUILDERS}
    assert len(G._BUILDERS) == len(set(G._BUILDERS))


def test_off_grid_excludes_scr_and_every_fault_level_row_with_the_reason(tmp_path):
    _template(tmp_path, "off_grid")
    _write(tmp_path)
    sk = _sk_ids(tmp_path)
    assert {
        "scr",
        "rapid_voltage_change",
        "rapid_voltage_change_infrequent",
        "harmonic_distortion",
        "current_distortion",
        "flicker",
        "voltage_unbalance",
    } <= sk
    assert any(i == "motor_start" or i.startswith("motor_start:") for i in sk)
    out = G.grid_readiness(tmp_path, _site(), PROFILE)
    excluded = {e["id"]: e for e in out["excluded"]}
    assert set(excluded) == sk | set(VS_IDS)
    assert all(
        excluded[i]["reason"] == G.SK_OFF_GRID and excluded[i]["requirement"]
        for i in sk
    )
    assert G.SK_OFF_GRID == (
        "off-grid: the tie is open, so the PoC's fault level does not apply; "
        "the island's fault level comes from its own units"
    )
    assert not sk & {r["id"] for r in out["rows"]}
    # The voltage-stability rows also read the PoC's fault level and leave by their own rule.
    assert not set(VS_IDS) & {r["id"] for r in out["rows"]}
    assert all(excluded[i]["reason"] == G.VS_OFF_GRID for i in VS_IDS) and VS_IDS
    # An excluded row is never counted, as a pass or as anything else.
    assert sum(out["summary"][v] for v in G.VERDICTS) == len(out["rows"])
    for i in sk:
        fact = G.readiness_fact(out, i)
        assert fact.value == "excluded" and fact.reason == f"excluded: {G.SK_OFF_GRID}"


def _conn_ids(card) -> set:
    """The ramp, oscillation and energization-peak ids on a scorecard, kept or excluded."""
    ids = {r["id"] for r in card["rows"]} | {e["id"] for e in card["excluded"]}
    return {
        i
        for i in ids
        if i in ("ramp_rate", "oscillation") or i.split(":")[0] == "energization_peak"
    }


def _open_tie(n):
    """The site with its import Link held at 0 MW in every hour: a normally open tie."""
    n.links.at["grid_import", "p_max_pu"] = 0.0
    return n


def _off_grid(tmp_path):
    _template(tmp_path, "off_grid")
    _write(tmp_path)
    return tmp_path


def _assert_conn_excluded(out, conn):
    excluded = {e["id"]: e for e in out["excluded"]}
    assert conn <= set(excluded)
    assert not conn & {r["id"] for r in out["rows"]}
    assert all(
        excluded[i]["reason"] == G.CONN_OFF_GRID and excluded[i]["requirement"]
        for i in conn
    )
    assert sum(out["summary"][v] for v in G.VERDICTS) == len(out["rows"])
    for i in conn:
        fact = G.readiness_fact(out, i)
        assert (
            fact.value == "excluded" and fact.reason == f"excluded: {G.CONN_OFF_GRID}"
        )


def test_off_grid_with_an_open_tie_excludes_the_ramp_oscillation_and_peak_rows(
    tmp_path,
):
    # SH41-OFFGRID-1: the tie is open, so the connection's limits do not judge the island.
    out = G.grid_readiness(_off_grid(tmp_path), _open_tie(_site()), PROFILE)
    conn = _conn_ids(out)
    assert {"ramp_rate", "oscillation"} <= conn
    assert any(i.split(":")[0] == "energization_peak" for i in conn)
    _assert_conn_excluded(out, conn)
    assert G.CONN_OFF_GRID == (
        "off-grid: the tie is open, so the island draws nothing through the connection; "
        "the connection's ramp, oscillation and import limits do not apply"
    )
    # The other rows leave by their own rules, unchanged.
    reasons = {e["reason"] for e in out["excluded"]}
    assert reasons == {G.SK_OFF_GRID, G.VS_OFF_GRID, G.CONN_OFF_GRID}


def test_off_grid_excludes_every_energization_period_it_emitted(tmp_path, monkeypatch):
    # GK A O2: the ids are the ones the builders emitted on this project, every period.
    from services.adequacy import site_peak as SP

    periods = [2030, 2035, 2040]
    monkeypatch.setattr(
        SP,
        "peak_check",
        lambda n, island, unset=None: [
            {"period": p, "unset": ["load"], "energized_max_mw": 10.0} for p in periods
        ],
    )
    out = G.grid_readiness(_off_grid(tmp_path), _open_tie(_site()), PROFILE)
    peak = {f"energization_peak:{p}" for p in periods}
    assert {i for i in _conn_ids(out) if i.startswith("energization_peak")} == peak
    _assert_conn_excluded(out, peak | {"ramp_rate", "oscillation"})


def test_a_label_alone_keeps_the_rows_judged_with_a_note(tmp_path):
    # GK A O1: off-grid by label, but the saved connection can import.
    out = G.grid_readiness(_off_grid(tmp_path), _site(), PROFILE)
    rows = {r["id"]: r for r in out["rows"]}
    conn = _conn_ids(out)
    assert {"ramp_rate", "oscillation"} <= conn <= set(rows)
    assert not conn & {e["id"] for e in out["excluded"]}
    assert all(rows[i]["note"].endswith(G.CONN_LABEL_ONLY) for i in conn)
    assert G.CONN_LABEL_ONLY == "labelled off-grid, but the connection can import"


def _no_link(n):
    n.remove("Link", "grid_import")
    return n


def _kept(out):
    conn = _conn_ids(out)
    assert {"ramp_rate", "oscillation"} <= conn <= {r["id"] for r in out["rows"]}
    assert G.CONN_OFF_GRID not in {e["reason"] for e in out["excluded"]}
    return {r["id"]: r for r in out["rows"]}, conn


def test_off_grid_with_no_import_link_keeps_the_rows(tmp_path):
    # GK A (#1516): no connection element found is not an open tie.
    _kept(G.grid_readiness(_off_grid(tmp_path), _no_link(_site()), PROFILE))


def test_off_grid_with_a_line_to_the_grid_keeps_the_rows(tmp_path):
    # The tie Link is open, but a Line joins the PCC to the campus, so the island can import.
    n = _open_tie(_site())
    n.add("Line", "grid_line", bus0="grid", bus1="mv", x=0.1, r=0.01, s_nom=60.0)
    rows, conn = _kept(G.grid_readiness(_off_grid(tmp_path), n, PROFILE))
    assert all(rows[i]["note"].endswith(G.CONN_LABEL_ONLY) for i in conn)


def test_off_grid_with_a_grid_generator_at_the_poc_and_no_link_keeps_the_rows(tmp_path):
    n = _no_link(_site())
    n.add("Generator", "grid_supply", bus="grid", carrier="grid", p_nom=60.0)
    _kept(G.grid_readiness(_off_grid(tmp_path), n, PROFILE))


@pytest.mark.parametrize("unmark", ("none_marked", "no_column"))
def test_off_grid_with_no_poc_marked_keeps_the_rows(tmp_path, unmark):
    # An open tie, but no bus is the PoC: nothing shows the PCC open.
    n = _open_tie(_site())
    if unmark == "none_marked":
        n.buses["eh_poc"] = False
    else:
        n.buses = n.buses.drop(columns="eh_poc")
    out = G.grid_readiness(_off_grid(tmp_path), n, PROFILE)
    assert {"ramp_rate", "oscillation"} <= {r["id"] for r in out["rows"]}
    assert G.CONN_OFF_GRID not in {e["reason"] for e in out["excluded"]}


def test_off_grid_with_a_conducting_import_link_away_from_the_poc_keeps_the_rows(
    tmp_path,
):
    # The PoC's tie is open, but another tagged import Link still conducts.
    n = _open_tie(_site())
    n.add("Bus", "grid2", v_nom=20.0)
    n.add("Link", "import2", bus0="grid2", bus1="mv", p_nom=10.0)
    n.links["eh_role"] = [
        "" if x != "import2" else "grid_import" for x in n.links.index
    ]
    _kept(G.grid_readiness(_off_grid(tmp_path), n, PROFILE))


def test_off_grid_with_no_saved_network_keeps_the_rows(tmp_path):
    out = G.grid_readiness(_off_grid(tmp_path), None, PROFILE)
    rows = {r["id"]: r for r in out["rows"]}
    assert {"ramp_rate", "oscillation", "energization_peak"} <= set(rows)
    assert G.CONN_OFF_GRID not in {e["reason"] for e in out["excluded"]}
    # Nothing is known of the connection, so the rows carry no "can import" note either.
    assert not any(G.CONN_LABEL_ONLY in (r["note"] or "") for r in out["rows"])


def test_a_gate_on_ramp_rate_reads_excluded_never_pass_off_grid(tmp_path):
    # GK A O3: a rule naming the row (the DC profile's dc.ramp_rate) is not evaluated.
    from services import run_gates

    import types

    out = G.grid_readiness(_off_grid(tmp_path), _open_tie(_site()), PROFILE)
    env = types.SimpleNamespace(cache={"scorecard": out})
    for rid in ("ramp_rate", "oscillation"):
        fact = run_gates._scorecard_reader(run_gates.SCORECARD + rid)(env)
        assert isinstance(fact, run_gates.NoEvidence) and fact.value == "excluded", rid
        assert fact.reason == f"excluded: {G.CONN_OFF_GRID}"
    # The open tie's one peak row has no period, so the DC block family finds no member.
    fam = run_gates._scorecard_reader(run_gates.SCORECARD + "energization_peak:")(env)
    assert isinstance(fam, run_gates.NoEvidence)


def test_a_family_with_one_excluded_period_is_never_passed_on_the_rest():
    from services import run_gates

    card = {
        "rows": [{"id": "energization_peak:2030", "verdict": "pass", "assumed": False}],
        "excluded": [
            {"id": "energization_peak:2035", "requirement": "r", "reason": "why"}
        ],
    }
    env = types_ns(cache={"scorecard": card}, ctx=types_ns(network=None))
    fact = run_gates._scorecard_reader(run_gates.SCORECARD + "energization_peak:")(env)
    assert isinstance(fact, run_gates.NoEvidence) and fact.value == "excluded"
    # A card without an excluded list (an older one) is read on its rows.
    del card["excluded"]
    fact = run_gates._scorecard_reader(run_gates.SCORECARD + "energization_peak:")(env)
    assert not isinstance(fact, run_gates.NoEvidence)


def test_a_grid_project_judges_ramp_oscillation_and_peak_against_the_connection(
    tmp_path,
):
    _write(tmp_path)
    out = G.grid_readiness(tmp_path, _site(), PROFILE)
    assert out["excluded"] == []
    rows = {r["id"]: r for r in out["rows"]}
    conn = _conn_ids(out)
    assert {"ramp_rate", "oscillation"} <= conn <= set(rows)
    assert any(i.split(":")[0] == "energization_peak" for i in conn)
    assert not any(G.CONN_LABEL_ONLY in (rows[i]["note"] or "") for i in conn)
    assert rows["ramp_rate"]["setting"]["item"] == "connection_ramp"
    assert rows["oscillation"]["setting"]["item"] == "oscillation"


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


@pytest.mark.parametrize(
    "tid", ("eh_datacenter", "eh_datacenter_staged", "eh_h2_hub", "eh_h2_hub_staged")
)
def test_a_grid_template_card_is_unchanged_by_the_off_grid_connection_rule(
    tmp_path, monkeypatch, tid
):
    # GK A O4: on a grid-connected template the SH 41 rule never runs, so the whole card is the
    # card without it, byte for byte.
    from tests.test_grid_readiness import _project

    d, n = _project(tmp_path, tid)
    card = json.dumps(G.grid_readiness(d, n, PROFILE), sort_keys=True, default=str)

    def boom(inp):
        raise AssertionError("the tie is read on a grid-connected project")

    monkeypatch.setattr(G, "_tie_open", boom)
    monkeypatch.setattr(G, "CONN_BUILDERS", frozenset())
    assert (
        json.dumps(G.grid_readiness(d, n, PROFILE), sort_keys=True, default=str) == card
    )
