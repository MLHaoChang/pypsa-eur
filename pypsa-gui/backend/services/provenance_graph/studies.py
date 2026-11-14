"""
Studies, reports and the investment case on the graph (slice 05).

A decision report is a `derived` node read from its study's run (`run:study:<id>`), which was built from the
assumptions ledger, the intake and each option fork's run (`run:fork:<uuid>`: its `network.nc` and engine inputs).
The run's own record (a ledger or intake edited during the run, a copied record) is pinned too. Each compared value
is a `pins.Pin`, so a read that raises is stale with its reason, and a recorded None never matches (ADR-0001).
`study.report.stale_reasons` is this verdict's broken edges named in today's codes, in today's order.

The investment case is `results:investment_case` computed in `run:investment_case`, pinned on the assumptions hash
and on each per-input digest it recorded; `investment_case_runner.staleness` reads its `stale`, `changed` and `reason`
from it. A value-flow hub is pinned on the group links and fixed-share keys it was saved with.

`needs_run` on a broken edge: the edge feeds a run (its source is a `run` node), so only running again clears it;
any other broken edge clears by assembling again.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from services.provenance_graph import pins
from services.provenance_graph.types import Broken, Edge, Node, Verdict

NO_HASHES = "report_has_no_findings_hashes"
EARLIER_VERSION = "run_by_an_earlier_version"
LEDGER = "ledger_changed_since_findings"
INTAKE = "intake_changed_since_findings"
FORK = "fork_changed_since_findings"
ENGINE = "engine_inputs_changed_since_findings"


def report_id(study_id: str) -> str:
    return f"report:{study_id}"


def study_run_id(study_id: str) -> str:
    return f"run:study:{study_id}"


def fork_run_id(fork_uuid: str) -> str:
    return f"run:fork:{fork_uuid}"


def _field(hashes: Any, key: str) -> Any:
    return (
        hashes.get(key) if isinstance(hashes, Mapping) else getattr(hashes, key, None)
    )


def report_verdict(
    report,
    study,
    ledger,
    fork_hash_now: Callable[[str], str | None],
    *,
    fork_engine_now: Callable[[str], str | None],
) -> tuple[Verdict, list[str]]:
    """The report's verdict and its broken edges as today's reason codes (`study.report.stale_reasons`).

    Order: the run record's own reasons, then no recorded hashes (nothing else compares), an earlier version, the
    ledger, the intake, then per fork (sorted by uuid) its network or else its engine inputs."""
    from services.provenance_graph.queries import Builder, staleness
    from services.study import packs, run_hashes

    sid = str(study.study_id)
    rep, run = report_id(sid), study_run_id(sid)
    b = Builder(None)
    b.node(Node("derived", rep))
    b.node(Node("run", run))
    b.edge(Edge("derived_from", rep, run))
    # The run's own record: the reasons it was marked stale with (edited during the run, a copied record).
    own = list(study.stale_reasons) if study.stale else []
    _pin(
        b, run, f"input:study:{sid}/run_record", pins.Pin("run record", [], lambda: own)
    )
    hashes = report.hashes_at_findings
    forks: dict = {}
    if hashes is None:
        _pin(
            b,
            rep,
            f"input:{rep}/findings_hashes",
            pins.Pin("findings hashes", None, lambda: None),
        )
    else:
        compiled = _field(hashes, "compiled_hash") or None
        # Whether the run recorded its engine inputs: only a missing record breaks it.
        _pin(
            b,
            run,
            f"input:study:{sid}/engine_recorded",
            pins.Pin("engine inputs recorded", compiled, lambda: compiled),
        )
        _pin(
            b,
            run,
            "input:ledger",
            pins.Pin(
                "ledger",
                run_hashes.recorded_ledger_hash(hashes),
                lambda: packs.ledger_hash(ledger),
            ),
        )
        _pin(
            b,
            run,
            f"input:study:{sid}/intake",
            pins.Pin(
                "intake",
                _field(hashes, "intake_hash"),
                lambda: run_hashes.intake_hash(study.intake),
            ),
        )
        appendix = report.sections.get("appendix")
        forks = ((appendix.payload if appendix is not None else None) or {}).get(
            "option_forks"
        ) or {}
        engines = _field(hashes, "option_compiled_hashes") or {}
        for fork_uuid, recorded in sorted(hashes.option_network_hashes.items()):
            fr = fork_run_id(fork_uuid)
            b.node(Node("run", fr))
            b.edge(Edge("derived_from", run, fr))
            _pin(
                b,
                fr,
                f"input:{fr}/network",
                pins.Pin(
                    f"fork network:{fork_uuid}",
                    recorded,
                    lambda u=fork_uuid: fork_hash_now(u),
                ),
            )
            if compiled:
                _pin(
                    b,
                    fr,
                    f"input:{fr}/engine",
                    pins.Pin(
                        f"fork engine inputs:{fork_uuid}",
                        engines.get(str(fork_uuid)),
                        lambda u=fork_uuid: fork_engine_now(u),
                    ),
                )
    v = staleness(b.build(), rep)
    return v, _codes(v, sid, forks, own)


def _pin(b, src: str, dst: str, pin: pins.Pin) -> None:
    b.node(Node("input", dst))
    b.edge(Edge("derived_from", src, dst, pin))


def _codes(v: Verdict, sid: str, forks: dict, own: list[str]) -> list[str]:
    """Each broken edge in today's reason code; a fork whose network changed names no engine change too."""
    out: list[str] = []
    networks = {x.src for x in v.broken if x.dst.endswith("/network")}
    for x in v.broken:
        if x.dst == f"input:study:{sid}/run_record":
            out += own
        elif x.dst.endswith("/findings_hashes"):
            out.append(NO_HASHES)
        elif x.dst.endswith("/engine_recorded"):
            out.append(EARLIER_VERSION)
        elif x.dst == "input:ledger":
            out.append(LEDGER)
        elif x.dst == f"input:study:{sid}/intake":
            out.append(INTAKE)
        else:
            fork_uuid = x.src.removeprefix("run:fork:")
            name = forks.get(fork_uuid, fork_uuid)
            if x.dst.endswith("/network"):
                out.append(f"{FORK}:{name}")
            elif x.src not in networks:
                out.append(f"{ENGINE}:{name}")
    return list(dict.fromkeys(out))


def needs_run(x: Broken) -> bool:
    """The broken edge feeds a run: only running again clears it."""
    return x.src.startswith("run:")


def verdict_wire(v: Verdict) -> dict:
    """A verdict as the report and investment-case routes send it: its state and each broken edge, with
    `needs_run`, so the client never reads the reason text."""
    return {
        "state": v.state,
        "broken": [
            {"edge": x.dst, **x.wire(), "needs_run": needs_run(x)} for x in v.broken
        ],
    }


# ── the investment case ──────────────────────────────────────────────────────

IC = "results:investment_case"
IC_RUN = "run:investment_case"


def investment_case_verdict(
    stored_hash: Any, stored_parts: Mapping[str, Any], current: tuple[str, dict] | None
) -> Verdict:
    """`results:investment_case` -computed_in-> `run:investment_case`, pinned on the assumptions hash, and
    `derived_from` one input per assumption part when the report recorded its parts (a part it did not record reads
    as the value None, as `stored_parts.get` did). A current key that cannot be computed is unreadable: stale."""
    from services.provenance_graph.queries import Builder, staleness

    cur_hash, cur_parts = current if current is not None else (pins.UNREADABLE, {})
    b = Builder(None)
    b.node(Node("derived", IC))
    b.node(Node("run", IC_RUN))
    b.edge(
        Edge(
            "computed_in",
            IC,
            IC_RUN,
            pins.Pin("assumptions_hash", stored_hash, lambda: cur_hash),
        )
    )
    if stored_parts:  # an unreadable key has no current parts
        for k in cur_parts:
            dst = f"input:investment_case/{k}"
            b.node(Node("input", dst))
            # The part as a value: None is a part's legitimate value ("no study applies"), so it is wrapped.
            b.edge(
                Edge(
                    "derived_from",
                    IC,
                    dst,
                    pins.Pin(
                        k,
                        ("value", stored_parts.get(k)),
                        lambda k=k: ("value", cur_parts.get(k)),
                    ),
                )
            )
    return staleness(b.build(), IC)


# ── a value-flow hub ─────────────────────────────────────────────────────────


def hub_verdict(hub, vf) -> Verdict:
    """The saved hub against the group: the member links, and for `fixed_shares` the share keys (casefolded)
    against the members. Neither recorded list is None, so both always compare."""
    from services.provenance_graph.queries import pin_verdict

    members = sorted(m.link for m in vf.hub_members)
    pin_list = [pins.Pin("group_links", sorted(hub.group_links), lambda: members)]
    if vf.allocation.basis == "fixed_shares":
        keys = sorted(k.strip().casefold() for k in (vf.allocation.shares or {}))
        pin_list.append(
            pins.Pin(
                "fixed_shares",
                keys,
                lambda: sorted(
                    m.participant.strip().casefold() for m in vf.hub_members
                ),
            )
        )
    return pin_verdict("hub", pin_list)
