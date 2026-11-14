"""PG-05a: study reports, the investment case and a value-flow hub read their verdict on the provenance graph.

The legacy checks are kept here, verbatim, as the oracle (GK B V1, V3): over every combination the new reasons, the
staleness dict and the hub bool equal theirs with `==`, and the verdict reads stale exactly when they did.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

from services.provenance_graph import studies as S
from services.study import packs, run_hashes
from services.study import report as R
from tests.study_s4_support import INTAKE
from tests.test_study_decision_report import _hashed_report, _study

NET, ENG = "h" * 16, "e" * 16
COPIED = "copied_record_findings_computed_on_the_origin_forks"


# ── the legacy checks (master before PG-05), the oracle ─────────────────────


def _legacy_reasons(report, study, ledger, fork_hash_now, *, fork_engine_now):
    reasons = list(study.stale_reasons) if study.stale else []
    hashes = report.hashes_at_findings
    if hashes is None:
        reasons.append("report_has_no_findings_hashes")
        return list(dict.fromkeys(reasons))
    if not run_hashes.engine_recorded(hashes):
        reasons.append("run_by_an_earlier_version")
    if not run_hashes.ledger_matches(ledger, hashes):
        reasons.append("ledger_changed_since_findings")
    if not run_hashes.intake_matches(study.intake, hashes):
        reasons.append("intake_changed_since_findings")
    forks = (
        (report.sections.get("appendix") or R.ReportSection(status="ok")).payload or {}
    ).get("option_forks") or {}
    for fork_uuid, recorded in sorted(hashes.option_network_hashes.items()):
        if not run_hashes.fork_matches(recorded, fork_hash_now(fork_uuid)):
            reasons.append(
                f"fork_changed_since_findings:{forks.get(fork_uuid, fork_uuid)}"
            )
        elif run_hashes.engine_recorded(hashes) and not run_hashes.compiled_matches(
            hashes, fork_uuid, fork_engine_now(fork_uuid)
        ):
            reasons.append(
                f"engine_inputs_changed_since_findings:{forks.get(fork_uuid, fork_uuid)}"
            )
    return list(dict.fromkeys(reasons))


def _legacy_staleness(stored, current, *, reason=None):
    if not stored:
        return {"present": False, "stale": None, "changed": [], "reason": None}
    stored_hash = stored.get("assumptions_hash")
    prov = (
        ((stored.get("sections") or {}).get("project") or {}).get("payload") or {}
    ).get("provenance") or {}
    stored_parts = prov.get("inputs") or {}
    if current is None:
        return {
            "present": True,
            "stale": True,
            "changed": [],
            "reason": reason or "current_assumptions_not_established",
            "assumptions_hash": stored_hash,
            "current_hash": None,
        }
    cur_hash, cur_parts = current
    changed = (
        sorted(k for k in cur_parts if stored_parts.get(k) != cur_parts.get(k))
        if stored_parts
        else []
    )
    stale = cur_hash != stored_hash
    return {
        "present": True,
        "stale": stale,
        "changed": changed if stale else [],
        "reason": None
        if not stale
        else ("inputs_changed" if changed else "hash_differs"),
        "assumptions_hash": stored_hash,
        "current_hash": cur_hash,
    }


def _legacy_stale_hub(hub, vf):
    links = sorted(m.link for m in vf.hub_members)
    if sorted(hub.group_links) != links:
        return True
    if vf.allocation.basis == "fixed_shares":
        keys = sorted(k.strip().casefold() for k in (vf.allocation.shares or {}))
        return keys != sorted(m.participant.strip().casefold() for m in vf.hub_members)
    return False


# ── the report ───────────────────────────────────────────────────────────────


def _edited_ledger(ledger):
    rows = [
        r.model_copy(
            update={"value": 150.0, "status": "customised", "provenance": "user"}
        )
        if r.key == "battery_storage_eur_per_kwh"
        else r
        for r in ledger.rows
    ]
    return ledger.model_copy(update={"rows": rows})


def _edited_intake(study):
    return study.model_copy(
        update={"intake": {**INTAKE, "load": {**INTAKE["load"], "annual_mwh": 9000.0}}}
    )


def _case(hashes, own, ledger_edit, intake_edit):
    if hashes == "none":
        report, ledger = _hashed_report()
        report = report.model_copy(update={"hashes_at_findings": None})
    elif hashes in ("no appendix", "appendix without payload", "no fork names"):
        report, ledger = _hashed_report()
        sections = dict(report.sections)
        if hashes == "no appendix":
            sections.pop("appendix")
        elif hashes == "appendix without payload":
            sections["appendix"] = R.ReportSection(status="ok")
        else:
            sections["appendix"] = R.ReportSection(
                status="ok", payload={"option_forks": None}
            )
        report = report.model_copy(update={"sections": sections})
    elif (
        "option_compiled_hashes" in hashes and hashes["option_compiled_hashes"] is None
    ):
        report, ledger = _hashed_report()
        report = report.model_copy(
            update={
                "hashes_at_findings": report.hashes_at_findings.model_copy(
                    update={"option_compiled_hashes": None}
                )
            }
        )
    else:
        report, ledger = _hashed_report(**hashes)
    study = _study(stale=bool(own), stale_reasons=own) if own else _study()
    if ledger_edit:
        ledger = _edited_ledger(ledger)
    if intake_edit:
        study = _edited_intake(study)
    return report, study, ledger


HASHES = {
    "one fork": {},
    "none": "none",
    "earlier version": {"compiled_hash": None, "option_compiled_hashes": {}},
    "two forks": {
        "option_network_hashes": {"f1": NET, "f2": NET},
        "option_compiled_hashes": {"f1": ENG, "f2": ENG},
    },
    "fork unrecorded": {"option_network_hashes": {"f1": None}},
    "engine unrecorded": {"option_compiled_hashes": {}},
    "no intake hash": {"intake_hash": None},
    "empty compiled hash": {"compiled_hash": ""},
    "no engine map": {"option_compiled_hashes": None},
    "no appendix": "no appendix",
    "appendix without payload": "appendix without payload",
    "no fork names": "no fork names",
}
OWN = [[], [COPIED], ["ledger_changed_during_run", "intake_changed_during_run"]]
NETS = [NET, "x" * 16, None]
ENGS = [ENG, "y" * 16, None]


@pytest.mark.parametrize("hashes", list(HASHES))
def test_reasons_and_state_equal_the_legacy_check_on_every_combination(hashes):
    """V1/V3: `==` on the codes and their order; the verdict is stale exactly when a reason was given."""
    for own, led, intk, net, eng, net2 in itertools.product(
        OWN, (False, True), (False, True), NETS, ENGS, (NET, None)
    ):
        report, study, ledger = _case(HASHES[hashes], own, led, intk)
        nets = {"f1": net, "f2": net2}
        args = (report, study, ledger, lambda u: nets[u])
        kw = {"fork_engine_now": lambda u: eng}
        want = _legacy_reasons(*args, **kw)
        v, got = S.report_verdict(*args, **kw)
        assert got == want, (hashes, own, led, intk, net, eng, net2)
        assert R.stale_reasons(*args, **kw) == want
        assert (v.state == "stale") == bool(want)
        assert v.state in ("stale", "fresh")


def _verdict(report, study, ledger, net=NET, eng=ENG):
    return S.report_verdict(
        report, study, ledger, lambda u: net, fork_engine_now=lambda u: eng
    )


def _broken(v):
    return [x.dst for x in v.broken]


def test_fresh_compares_every_edge():
    report, ledger = _hashed_report()
    v, codes = _verdict(report, _study(), ledger)
    assert (v.state, codes, v.uncompared) == ("fresh", [], ())


# V4: each input breaks its own edge and nothing else.
@pytest.mark.parametrize(
    "change, edge, code, run",
    [
        ("ledger", "input:ledger", "ledger_changed_since_findings", True),
        ("intake", "input:study:{sid}/intake", "intake_changed_since_findings", True),
        (
            "network",
            "input:run:fork:f1/network",
            "fork_changed_since_findings:bess_2h",
            True,
        ),
        (
            "engine",
            "input:run:fork:f1/engine",
            "engine_inputs_changed_since_findings:bess_2h",
            True,
        ),
        ("own", "input:study:{sid}/run_record", COPIED, True),
        (
            "earlier",
            "input:study:{sid}/engine_recorded",
            "run_by_an_earlier_version",
            True,
        ),
        (
            "no hashes",
            "input:report:{sid}/findings_hashes",
            "report_has_no_findings_hashes",
            False,
        ),
    ],
)
def test_each_input_breaks_exactly_its_own_edge(change, edge, code, run):
    """V4 one killing case per pin; V5 `needs_run` exactly on edges a run reads."""
    report, ledger = _hashed_report(
        **(
            {"compiled_hash": None, "option_compiled_hashes": {}}
            if change == "earlier"
            else {}
        )
    )
    study = _study(stale=True, stale_reasons=[COPIED]) if change == "own" else _study()
    net, eng = NET, ENG
    if change == "ledger":
        ledger = _edited_ledger(ledger)
    elif change == "intake":
        study = _edited_intake(study)
    elif change == "network":
        net = "x" * 16
    elif change == "engine":
        eng = "y" * 16
    elif change == "no hashes":
        report = report.model_copy(update={"hashes_at_findings": None})
    v, codes = _verdict(report, study, ledger, net, eng)
    sid = str(study.study_id)
    assert _broken(v) == [edge.format(sid=sid)]
    assert codes == [code]
    (wire,) = S.verdict_wire(v)["broken"]
    assert wire["needs_run"] is run
    assert wire["edge"] == edge.format(sid=sid)


def test_a_changed_network_also_breaks_the_engine_edge_but_names_the_network_only():
    report, ledger = _hashed_report()
    v, codes = _verdict(report, _study(), ledger, "x" * 16, "y" * 16)
    assert _broken(v) == ["input:run:fork:f1/network", "input:run:fork:f1/engine"]
    assert codes == ["fork_changed_since_findings:bess_2h"]


def test_no_hashes_never_reads_fresh():
    """V2: a report with nothing recorded compares nothing it could match."""
    report, ledger = _hashed_report()
    report = report.model_copy(update={"hashes_at_findings": None})
    v, codes = _verdict(report, _study(), ledger)
    assert v.state == "stale" and codes == ["report_has_no_findings_hashes"]


def test_a_recorded_none_never_matches_a_current_none():
    """V6: a fork with no recorded hash, and a gone fork (None now), is stale."""
    report, ledger = _hashed_report(
        option_network_hashes={"f1": None}, option_compiled_hashes={"f1": None}
    )
    v, codes = _verdict(report, _study(), ledger, None, None)
    assert v.state == "stale"
    assert codes == ["fork_changed_since_findings:bess_2h"]
    assert {x.reason for x in v.broken} == {"not recorded"}


@pytest.mark.parametrize("what", ["network", "engine", "ledger", "intake"])
def test_a_raising_current_read_is_stale_with_its_reason(what, monkeypatch):
    """V6 (#1390 F2): a read that raises is stale with its reason, never a raise out of the route."""
    report, ledger = _hashed_report()

    def boom(*_a, **_k):
        raise OSError("disk gone")

    net = boom if what == "network" else (lambda u: NET)
    eng = boom if what == "engine" else (lambda u: ENG)
    if what == "ledger":
        monkeypatch.setattr(packs, "ledger_hash", boom)
    if what == "intake":
        monkeypatch.setattr(run_hashes, "intake_hash", boom)
    v, codes = S.report_verdict(report, _study(), ledger, net, fork_engine_now=eng)
    assert v.state == "stale"
    assert (
        codes
        == {
            "network": ["fork_changed_since_findings:bess_2h"],
            "engine": ["engine_inputs_changed_since_findings:bess_2h"],
            "ledger": ["ledger_changed_since_findings"],
            "intake": ["intake_changed_since_findings"],
        }[what]
    )
    assert [x.reason for x in v.broken] == ["cannot be read now: disk gone"]


def test_the_route_sends_the_verdict_beside_the_reasons():
    from routers.studies import _report_out

    report, ledger = _hashed_report()
    read = _verdict(report, _study(), ledger, "x" * 16)
    out = _report_out(report, read)
    assert out["stale"] is True
    assert out["stale_reasons"] == ["fork_changed_since_findings:bess_2h"]
    assert out["verdict"]["state"] == "stale"
    assert [(b["edge"], b["needs_run"]) for b in out["verdict"]["broken"]] == [
        ("input:run:fork:f1/network", True)
    ]


# ── the investment case ──────────────────────────────────────────────────────

PARTS = {
    "solver_config": "s",
    "finance": "f",
    "dispatch": "d",
    "owner_assets": None,
    "extra_assets": None,
}


def _stored(hash_, parts):
    out = {"assumptions_hash": hash_}
    if parts is not None:
        out["sections"] = {"project": {"payload": {"provenance": {"inputs": parts}}}}
    return out


def _ic_cases():
    for stored_hash in ("H", None):
        for stored_parts in (PARTS, None, {}, {k: v for k, v in PARTS.items() if v}):
            for current in (
                None,
                ("H", PARTS),
                ("H2", PARTS),
                ("H2", {**PARTS, "finance": "f2"}),
                ("H2", {**PARTS, "extra_assets": "x"}),
                ("H2", {**PARTS, "finance": "f2", "dispatch": "d2"}),
            ):
                for reason in (None, "no_dispatch"):
                    yield _stored(stored_hash, stored_parts), current, reason


def test_the_ic_dict_equals_the_legacy_check_on_every_combination():
    """V1/V3: `==` on the dict minus `verdict`; the verdict is stale exactly when `stale`."""
    from services.finance.investment_case_runner import staleness

    for stored, current, reason in _ic_cases():
        got = staleness(stored, current, reason=reason)
        verdict = got.pop("verdict")
        assert got == _legacy_staleness(stored, current, reason=reason), (
            stored,
            current,
        )
        assert (verdict["state"] == "stale") is got["stale"]
    assert staleness(None, ("H", PARTS)) == _legacy_staleness(None, ("H", PARTS))


@pytest.mark.parametrize("part", list(PARTS))
def test_each_ic_part_breaks_exactly_its_own_edge(part):
    """V4: every assumption part has its own edge; the hash breaks with it."""
    from services.finance.investment_case_runner import staleness

    got = staleness(_stored("H", PARTS), ("H2", {**PARTS, part: "changed"}))
    assert got["changed"] == [part] and got["reason"] == "inputs_changed"
    edges = [(b["edge"], b["needs_run"]) for b in got["verdict"]["broken"]]
    # The hash's edge and this part's: the run's inputs, so only a new run clears either (V5).
    assert edges == [
        ("input:investment_case:assumptions_hash", True),
        (f"input:investment_case/{part}", True),
    ]


def test_a_part_changed_under_an_equal_hash_now_reads_stale():
    """V1, the one row that moves: a record whose hash still matches but whose recorded part differs (an
    inconsistent record) read fresh on master; the part's edge breaks now. Nothing moves toward fresh."""
    from services.finance.investment_case_runner import staleness

    stored, current = _stored("H", PARTS), ("H", {**PARTS, "finance": "f2"})
    assert _legacy_staleness(stored, current)["stale"] is False
    got = staleness(stored, current)
    assert (got["stale"], got["changed"], got["reason"]) == (
        True,
        ["finance"],
        "inputs_changed",
    )


def test_an_ic_key_that_cannot_be_computed_is_stale_never_fresh():
    """V2/V6: no current key compares nothing it could match."""
    from services.finance.investment_case_runner import staleness

    got = staleness(_stored("H", PARTS), None, reason="no_dispatch")
    assert got["stale"] is True and got["reason"] == "no_dispatch"
    assert got["verdict"]["state"] == "stale"
    assert [b["reason"] for b in got["verdict"]["broken"]] == ["cannot be read now"]


def test_an_unrecorded_ic_hash_never_matches():
    from services.finance.investment_case_runner import staleness

    got = staleness(_stored(None, PARTS), (None, PARTS))
    assert got["stale"] is True
    assert got["verdict"]["broken"][0]["reason"] == "not recorded"


# ── a value-flow hub ─────────────────────────────────────────────────────────


def _hub(links):
    return SimpleNamespace(group_links=links)


def _vf(members, basis="pro_rata", shares=None):
    return SimpleNamespace(
        hub_members=[SimpleNamespace(link=link, participant=p) for link, p in members],
        allocation=SimpleNamespace(basis=basis, shares=shares),
    )


MEMBERS = [("l1", "Alice"), ("l2", "Bob")]


def test_the_hub_bool_equals_the_legacy_check():
    """V3: `==` over links and shares combinations."""
    from services.commercial.participants import _stale_hub

    for links, basis, shares in itertools.product(
        (["l1", "l2"], ["l2", "l1"], ["l1"], ["l1", "l2", "l3"]),
        ("pro_rata", "fixed_shares"),
        (None, {"alice": 1, " BOB ": 1}, {"alice": 1}, {"alice": 1, "carol": 1}),
    ):
        hub, vf = _hub(links), _vf(MEMBERS, basis, shares)
        assert _stale_hub(hub, vf) == _legacy_stale_hub(hub, vf), (links, basis, shares)


def test_each_hub_pin_breaks_exactly_its_own_edge():
    """V4: the group links and the fixed-share keys each break their own edge."""
    ok = {"alice": 1, "bob": 1}
    assert (
        S.hub_verdict(_hub(["l1", "l2"]), _vf(MEMBERS, "fixed_shares", ok)).state
        == "fresh"
    )
    links = S.hub_verdict(_hub(["l1"]), _vf(MEMBERS, "fixed_shares", ok))
    assert [x.field for x in links.broken] == ["group_links"]
    shares = S.hub_verdict(
        _hub(["l1", "l2"]), _vf(MEMBERS, "fixed_shares", {"alice": 1})
    )
    assert [x.field for x in shares.broken] == ["fixed_shares"]
