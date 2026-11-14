# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""The shipped cases' engine (study harness 14, S-B2b; GK B (58) D1-D7,
GK B (59) G1-G7): the explore stage's third engine, after NETWORK and STUDY.

**Step.** One runner-native action, `shipped_cases` (`run_actions.REGISTRY`,
explore stage). It runs only on its card's digest (`needs_card`): the card
is S-B2c, so until then no profile names the action. Its record is frozen at
stage entry (`freeze`): every case of the project's cases.json with its
status, the cases picked to run and their digest (`run_shipped.load`).

**Admission** (`dispatch`). All cases go into `run.cases` with `engine:
shipped`. A case outside the allow-list is `not_run` with its reason; an
admissible case not picked is `not_run`; a finance case (it moves only the
settlement on the solved dispatch) is `not_evaluated` with no fork and no
solve; an LP case reserves its one solve on the envelope before it forks (D6).

**Fork** (`run_case`, the only runner entry: the admitted case id and the
approved digest). `require_current` before the fork (D4); the fork is named
by the harness (`run_explore.case_project_name`, #875) and a name already
taken refuses; its id is on the case the moment it exists (D7). Under the
child's existing writer locks (`shipped_fork.held`, G1) the `old` values are
compared and the changes written in one critical section (D3): the commercial
block through `run_shipped.apply` and the battery's overnight costs through
the single-edit derive (`network_crud._apply_investment_parts`, i.e.
`derive.apply_parts`), never a raw write. `require_current` runs again under
the lock right before the solve is queued. A raise after the fork leaves the
fork named on the case; one before the enqueue releases the reservation, one
after counts it (G6), and the case says which.

**Judging** (`judged`, from `run_explore.advance`). A case is judged on its
kind and the witness of that kind, read from the fork's own saved context
(G4). Without the witness it reads `not_evaluated` with the reason, never
passed. The base inputs (cases.json, the template series, the base's Library
refs) have no writer lock to hold, so the digest is also recorded on the
fork's case record with the fork's input fingerprint; a fork whose record or
inputs no longer match reads `not_evaluated` (G1).
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import math
import threading
from pathlib import Path

from harness import run_shipped

KIND = "shipped_cases"  # the record kind and the action's name
VERSION = 1
SHIPPED = "shipped"  # `case["engine"]`
EVENT = "explore"  # the envelope row of a case's reservation (as other explore cases)
SOLVES = 1  # an LP case's one queued solve
DESCRIPTION = "Harness shipped case"
FINANCE_WHY = "Investment case per case not run: this case moves only the settlement on the solved dispatch."
NOT_PICKED = "Not picked to run on the card."
ERROR_KINDS = (
    {"error_kind": "shipped_fork_exists"},
    {"error_kind": "shipped_base_unsaved"},
)
_TEMPLATES = Path(__file__).resolve().parents[1] / "project_templates"
_JOB_DONE = ("completed", "failed", "aborted", "cancelled", "interrupted")


# ── the frozen record (stage entry) ─────────────────────────────────────────


def _template(profile) -> str:
    tids = (profile or {}).get("templates") or []
    if len(tids) != 1:
        raise run_shipped.ShippedError(
            "shipped_case_not_runnable", "The profile does not name one template."
        )
    return tids[0]


def _cfg(ctx) -> dict:
    """The context's solver_config as the dict its saved file holds."""
    cfg = ctx.solver_state.get("solver_config")
    return json.loads(
        json.dumps(cfg if isinstance(cfg, dict) else dataclasses.asdict(cfg))
    )


def _sources(project, tid):
    """`run_shipped.load`'s sources for `project` as SAVED (what a fork copies)."""
    from services import workflow_tools

    ctx = workflow_tools._load_context(project)
    return ctx, {
        "template_dir": _TEMPLATES / tid,
        "cfg": _cfg(ctx),
        "network": ctx.network,
    }


def core(frozen) -> dict:
    return {k: v for k, v in frozen.items() if k != "digest"}


def card(frozen) -> dict:
    """What the step shows until S-B2c's card: every case and what runs."""
    return {
        "cases_digest": frozen["cases_digest"],
        "total": frozen["total"],
        "cases": frozen["cases"],
        "run": frozen["run"],
        "solves": frozen["solves"],
    }


def valid(frozen) -> bool:
    cases = {c["id"]: c for c in frozen.get("cases") or []}
    run = frozen.get("run") or []
    return (
        frozen.get("version") == VERSION
        and frozen.get("total") == len(cases)
        and all(cases.get(i, {}).get("status") == "admissible" for i in run)
        and frozen.get("solves")
        == sum(1 for i in run if cases[i]["kind"] == run_shipped.LP)
    )


def freeze(task) -> dict:
    """The record at stage entry, or `{"unfrozen": {state, message}}`."""
    from fastapi import HTTPException

    from harness import run_explore
    from services import chat_tools, project_registry, run_actions

    run = task["run"]
    try:
        profile = run_explore._profile(task)
        tid = _template(profile)
        with chat_tools._acting() as (db, user):
            project = project_registry.resolve_project(db, user, task["project_id"])
            _, sources = _sources(project, tid)
            shipped = run_shipped.load(
                project_registry.project_dir(project), tid, **sources
            )
        picked = (run.get("explore") or {}).get("shipped_pick")
        ids = run_shipped.choose(shipped, run_explore.caps(profile)[0], picked)
    except run_shipped.ShippedError as exc:
        return {"unfrozen": {"state": exc.kind, "message": str(exc)}}
    except (HTTPException, OSError):
        return {
            "unfrozen": {
                "state": "unreadable",
                "message": "the run's project cannot be read",
            }
        }
    kinds = {c["id"]: c["kind"] for c in shipped["cases"]}
    frozen = {
        "kind": KIND,
        "version": VERSION,
        "template": tid,
        "cases_digest": shipped["digest"],
        "total": shipped["total"],
        "cases": shipped["cases"],
        "run": ids,
        "solves": sum(1 for i in ids if kinds[i] == run_shipped.LP),
    }
    frozen["digest"] = run_actions.digest(core(frozen))
    return frozen


# ── admission and the step (the runner's native dispatch) ───────────────────


def _status(case, ids) -> tuple[str, str | None]:
    if case["status"] != "admissible":
        return "not_run", case["why"]
    if case["id"] not in ids:
        return "not_run", NOT_PICKED
    if case["kind"] == run_shipped.FINANCE:
        return "not_evaluated", FINANCE_WHY
    return "admitted", None


def _admit(path, step_id, frozen, held) -> list[str] | None:
    """Every case into `run.cases`, each LP case's solve reserved first (D6).
    The admitted ids; None when the attempt no longer holds the run."""
    import uuid

    from harness import runner
    from services import run_actions

    rows = []
    for case in frozen["cases"]:
        status, why = _status(case, frozen["run"])
        row = {
            "name": case["id"],
            "engine": SHIPPED,
            "kind": case["kind"],
            "label": case["label"],
            "changes": [dict(c) for c in case["changes"]],
            "digest": frozen["cases_digest"],
            "template": frozen["template"],
            "status": status,
            "solves": {"reserved": 0, "used": None},
        }
        if why:
            row["reason"] = why
        if status == "admitted":
            call_id = f"shipped-{uuid.uuid4().hex[:12]}"
            if run_actions._reserve(path, call_id, KIND, EVENT) is False:
                row.update(
                    status="not_run", reason="The run has no solve left for this case."
                )
            else:
                row["solves"] = {"reserved": SOLVES, "used": None, "call_id": call_id}
        rows.append(row)
    out = {}

    def change(task):
        step = next(s for s in task["steps"] if s["id"] == step_id)
        if not run_actions._held(task, step, *held):
            return False
        cases = task["run"].setdefault("cases", [])
        if any(c.get("engine") == SHIPPED for c in cases):
            return False  # admitted once per run
        cases.extend(rows)
        out["ids"] = [r["name"] for r in rows if r["status"] == "admitted"]

    try:
        runner._update(path, change)
    except Exception:
        out.pop("ids", None)
        _release_all(path, rows)
        raise
    if "ids" not in out:
        _release_all(path, rows)  # never admitted: nothing it reserved is spent
        return None
    return out["ids"]


def _release_all(path, rows):
    from services import run_actions

    for row in rows:
        if row["solves"].get("call_id"):
            run_actions.release(path, row["solves"]["call_id"], 0, EVENT)


def dispatch(path, step, frozen, attempt, instance) -> str:
    """The step: admit, then fork and queue each admitted case in turn."""
    from harness import runner
    from services import run_actions

    held = (attempt, instance)
    ids = _admit(path, step["id"], frozen, held)
    if ids is None:
        return "taken_over"
    for case_id in ids:
        try:
            run_case(path, case_id, frozen["cases_digest"])
        except run_shipped.ShippedError:
            pass  # the case carries its reason; the others still run

    def done(task):
        s = next(x for x in task["steps"] if x["id"] == step["id"])
        if not run_actions._held(task, s, *held):
            return False
        shipped = [c for c in task["run"]["cases"] if c.get("engine") == SHIPPED]
        s.update(
            status="completed",
            result={"cases": {c["name"]: c["status"] for c in shipped}},
        )

    runner._update(path, done)
    return KIND


# ── one case's fork (`run_case`) ─────────────────────────────────────────────


def _case(path, case_id):
    from harness import runner

    task = runner._read(path)
    hit = [
        c
        for c in ((task or {}).get("run") or {}).get("cases") or []
        if c.get("engine") == SHIPPED and c.get("name") == case_id
    ]
    return task, (hit[0] if len(hit) == 1 else None)


def _mark(path, case_id, **fields):
    from harness import runner

    def change(task):
        for c in task["run"].get("cases") or []:
            if c.get("engine") == SHIPPED and c.get("name") == case_id:
                for key, value in fields.items():
                    if key == "solves":
                        c["solves"].update(value)
                    else:
                        c[key] = value

    runner._update(path, change)


def _require(digest, project, tid):
    from services import project_registry

    _, sources = _sources(project, tid)
    run_shipped.require_current(
        digest, project_registry.project_dir(project), tid, **sources
    )


_MINT_GUARD = threading.Lock()
_MINT_LOCKS: dict[tuple[str, str], threading.Lock] = {}


@contextlib.contextmanager
def _mint_lock(org_id, name):
    """One hold per (org, series name): this process's lock, then the
    cross-process file lock in the org's Library dir (review-gk: fail closed
    is a `durable.file_lock`)."""
    from harness.durable import file_lock
    from services.library import series_store
    from services.storage_paths import library_dir

    with _MINT_GUARD:
        local = _MINT_LOCKS.setdefault((str(org_id), name), threading.Lock())
    folder = library_dir(series_store._default_root(), org_id)
    folder.mkdir(parents=True, exist_ok=True)
    with (
        local,
        file_lock(
            folder / f".mint-{series_store._slug(name)}.lock",
            timeout=30,
            busy="series mint busy",
        ),
    ):
        yield


def resolver(db, org_id, template_dir):
    """D1(a), G2: a series name's ref in the org's Library, minted from the
    VERIFIED template file when the Library has none. Per series name, one
    hold covers the verify, the hash check and the mint: two runs minting one
    name give one version. A Library series of that name with another hash is
    never re-minted: None (`shipped_series_unresolved`)."""
    from services.library import series_store, template_series

    def resolve(name):
        with _mint_lock(org_id, name):
            try:
                template_series.verify(Path(template_dir))
                found = [
                    s
                    for s in template_series.read(Path(template_dir))
                    if s.name == name
                ]
            except template_series.TemplateSeriesError:
                return None
            if len(found) != 1:
                return None
            series = found[0]
            sha = series_store.prepare_series(name, series.series, series.meta)[3]
            latest = series_store.latest_ref(db, org_id, name)
            if latest is None:
                latest = series_store.put_series(
                    db, org_id, name, series.series, series.meta
                )
            if latest.hash != sha:
                return None
            return latest.model_dump(mode="json")

    return resolve


def _fingerprint(ctx) -> str:
    from services import workflow_tools

    with workflow_tools._isolated(ctx):
        return workflow_tools._network_input_fingerprint(ctx)


def _battery(ctx, changes) -> None:
    """The battery's overnight costs through the single-edit derive (the parts
    and the capital cost they annuitise), each `old` compared first (D3)."""
    from fastapi import HTTPException

    from services import network_crud, workflow_tools
    from services.asset_schema.derive import part_columns

    if not changes:
        return
    df = ctx.network.storage_units
    by_name: dict[str, dict] = {}
    for change in changes:
        _, name, col = change["path"].split(".")
        found = df.at[name, col] if name in df.index and col in df.columns else None
        if not (
            run_shipped._finite(found)
            and math.isclose(float(found), change["old"], rel_tol=1e-9)
        ):
            run_shipped._stale(change["path"], change["old"], found)
        by_name.setdefault(name, {})[col] = change["new"]
    with workflow_tools._isolated(ctx):
        for name, parts in by_name.items():
            row = df.loc[name].to_dict()
            merged = {
                c: row.get(c) for c in part_columns("StorageUnit") if c in row
            } | parts
            try:
                network_crud._check_investment_parts(
                    "StorageUnit", merged, row={**row, **parts}
                )
            except HTTPException as exc:
                raise run_shipped.ShippedError(
                    "shipped_cases_invalid",
                    f"The battery cost does not derive: {exc.detail}",
                ) from exc
            network_crud._apply_investment_parts(
                ctx.network, "StorageUnit", name, parts
            )


def _at(cfg, path):
    """The value at a commercial path of `cfg` (a ref: its id); None if absent."""
    parts = path.split(".")
    commercial = (cfg or {}).get("commercial") or {}
    if len(parts) == 3:
        value = commercial.get(parts[1])
    else:
        hits = [
            c
            for c in commercial.get("contracts") or []
            if isinstance(c, dict) and c.get("id") == parts[2]
        ]
        value = hits[0].get(parts[3]) if len(hits) == 1 else None
    if run_shipped._is_ref(path):
        return value.get("id") if isinstance(value, dict) else None
    return value


def _facts(ctx, changes) -> dict:
    """What the witnesses compare: each changed value and the battery's capital
    cost, read from `ctx`."""
    cfg = _cfg(ctx)
    df = ctx.network.storage_units
    out = {"values": {}, "capital_cost": {}}
    for change in changes:
        parts = change["path"].split(".")
        if parts[0] == "StorageUnit":
            name, col = parts[1], parts[2]
            ok = name in df.index and col in df.columns
            out["values"][change["path"]] = float(df.at[name, col]) if ok else None
            out["capital_cost"][name] = (
                float(df.at[name, "capital_cost"]) if name in df.index else None
            )
        else:
            out["values"][change["path"]] = _at(cfg, change["path"])
    return out


def run_case(path, case_id, digest) -> dict:
    """Fork, write and queue one admitted case. Takes only the case id and the
    approved digest: the project, the changes and the fork's name are the
    run's own (#875). Refusals are `ShippedError`; the case carries them."""
    from harness import run_explore, shipped_fork
    from routers.projects import _solver_config_from_dict
    from services import chat_tools, dirty_state, project_registry, run_actions
    from services.pypsa_service import PyPSAService

    task, case = _case(path, case_id)
    if case is None or case.get("status") != "admitted":
        raise run_shipped.ShippedError(
            "shipped_case_not_runnable",
            f"{case_id} is not an admitted case of this run.",
        )
    if case.get("digest") != digest:
        raise run_shipped.ShippedError(
            "shipped_cases_changed", "The case was admitted under another approval."
        )
    tid, call_id = case["template"], case["solves"].get("call_id")
    reached = {"stage": "require_current", "fork": None, "queued": False}
    try:
        with chat_tools._acting() as (db, user):
            base = project_registry.resolve_project(db, user, task["project_id"])
            resident = PyPSAService.get_context(project_registry.registry_key(base))
            if resident is not None and dirty_state.is_dirty(resident):
                raise run_shipped.ShippedError(
                    "shipped_base_unsaved", "Save the project before its cases fork it."
                )
            _require(digest, base, tid)
            name = run_explore.case_project_name(task, case)
            if project_registry.find_project(db, user, name) is not None:
                raise run_shipped.ShippedError(
                    "shipped_fork_exists",
                    f"A project named {name} already exists; nothing was overwritten.",
                )
            base_ctx, _ = _sources(base, tid)
            base_fp = _fingerprint(base_ctx)
            before = _facts(base_ctx, case["changes"])
            reached["stage"] = "fork"
            try:
                child = shipped_fork.create(db, user, base, name, DESCRIPTION)
            except Exception:
                # D7: a create that raised after its row exists still names the fork.
                left = project_registry.find_project(db, user, name)
                if left is not None:
                    reached["fork"] = str(left.id)
                    _mark(path, case_id, project_id=reached["fork"])
                raise
            reached["fork"] = str(child.id)
            _mark(
                path, case_id, project_id=str(child.id)
            )  # D7: named the moment it exists
            with shipped_fork.held(child, task["task_id"]) as ctx:
                reached["stage"] = "patch"
                patched, battery = run_shipped.apply(
                    _cfg(ctx),
                    case["changes"],
                    resolver(db, base.org_id, _TEMPLATES / tid),
                )
                reached["stage"] = "edit"
                _battery(ctx, battery)
                ctx.solver_state["solver_config"] = _solver_config_from_dict(patched)
                reached["stage"] = "save"
                spec = {
                    "baseline_id": str(base.id),
                    "baseline_fingerprint": base_fp,
                    "input_fingerprint": _fingerprint(ctx),
                    "shipped": {
                        "case": case_id,
                        "digest": digest,
                        "template": tid,
                        "changes": case["changes"],
                        "base": before,
                    },
                }
                shipped_fork.save(ctx, child, spec, db=db, user=user)
                reached["stage"] = "require_current_again"
                _require(
                    digest, base, tid
                )  # D4: again, under the lock, right before the solve
                reached["stage"] = "enqueue"
                # G6: a case that reached the enqueue counts its solve, even when
                # the call raises (it may have queued one): fail closed.
                reached["queued"] = True
                job = chat_tools.solve_queue_enqueue(str(child.id))
    except Exception as exc:  # noqa: BLE001 -- every failure settles the case, never the run
        kind = (
            getattr(exc, "kind", None)
            or (
                (getattr(exc, "detail", None) or {}).get("error_kind")
                if isinstance(getattr(exc, "detail", None), dict)
                else None
            )
            or type(exc).__name__
        )
        outcome = "counted" if reached["queued"] else "released"
        if call_id:
            run_actions.release(path, call_id, 1 if reached["queued"] else 0, EVENT)
        # D2: the validator's refusal of the patched block reads not_run.
        invalid = getattr(exc, "kind", None) == "shipped_cases_invalid"
        _mark(
            path,
            case_id,
            status="not_run" if invalid else "failed",
            reason=f"{reached['stage']}: {kind}: {exc}"[:480],
            solves={"used": 1 if reached["queued"] else 0, "outcome": outcome},
        )
        if isinstance(exc, run_shipped.ShippedError):
            raise
        raise run_shipped.ShippedError("shipped_case_not_runnable", str(exc)) from exc
    _mark(
        path,
        case_id,
        status="running",
        job={"job_id": job.get("id")},
        solves={"outcome": "counted"},
    )
    return {"project_id": reached["fork"], "job_id": job.get("id")}


# ── judging (from `run_explore.advance`) ─────────────────────────────────────


def witness(case, ctx, record) -> str | None:
    """Why the fork in `ctx` (its own saved context, G4) does not show that the
    case reached its solve; None when it does. Per kind (GK B (58), (59)):
    every change's new value is on the fork, the fork's inputs are the ones
    written for the approved digest, and they differ from the base's; a price
    reference: the fork's ref id differs from the base's; the battery: its
    overnight cost and its capital cost differ from the base's (the derive
    ran, not a raw write); a dispatch contract's number: its value is the new
    one and the fingerprint moved."""
    shipped = (record or {}).get("shipped") or {}
    if shipped.get("digest") != case.get("digest"):
        return "The fork's case record does not carry the approved cases."
    fp = _fingerprint(ctx)
    if fp != record.get("input_fingerprint"):
        return "The fork's inputs changed after the case wrote them."
    if fp == record.get("baseline_fingerprint"):
        return "The fork's inputs are the base's: the case did not reach the solve."
    base = shipped.get("base") or {}
    now = _facts(ctx, case["changes"])
    for change in case["changes"]:
        path, new = change["path"], change["new"]
        was, got = base.get("values", {}).get(path), now["values"].get(path)
        if isinstance(new, str):
            if got != new:
                return f"{path} is {got} on the fork, not {new}."
            if got == was:
                return f"{path} is the base's {was} on the fork."
            continue
        if not (run_shipped._finite(got) and math.isclose(got, new, rel_tol=1e-9)):
            return f"{path} is {got} on the fork, not {new}."
        if run_shipped._finite(was) and math.isclose(got, was, rel_tol=1e-9):
            return f"{path} is the base's {was} on the fork."
        if path.startswith("StorageUnit."):
            name = path.split(".")[1]
            cc_was = base.get("capital_cost", {}).get(name)
            cc_got = now["capital_cost"].get(name)
            if cc_got is None or cc_was == cc_got:
                return f"The {name} capital cost did not move on the fork: its cost was not derived."
    return None


def _job(case):
    from routers.solve_queue import _visible_job_or_404
    from services import chat_tools

    job_id = (case.get("job") or {}).get("job_id")
    if not job_id:
        return None
    with chat_tools._acting() as (db, user):
        return _visible_job_or_404(db, user, job_id)


def judged(task, profile) -> dict:
    """`{case name: (status, reason, gate, solves)}` for each running shipped
    case whose job ended. Read outside the run's lock."""
    from fastapi import HTTPException

    from harness import run_explore
    from services import chat_tools, project_registry, workflow_tools

    out = {}
    for case in (task.get("run") or {}).get("cases") or []:
        if case.get("engine") != SHIPPED or case.get("status") != "running":
            continue
        try:
            job = _job(case)
        except HTTPException:
            job = None
        if job is None or job.get("status") not in _JOB_DONE:
            continue
        if job["status"] != "completed":
            out[case["name"]] = (
                "failed",
                f"The case's solve ended {job['status']}.",
                None,
                SOLVES,
            )
            continue
        try:
            with chat_tools._acting() as (db, user):
                row = project_registry.resolve_project(db, user, case["project_id"])
                ctx = workflow_tools._load_context(row)
                record = json.loads(
                    (
                        project_registry.project_dir(row)
                        / workflow_tools.SENSITIVITY_CASE_FILE
                    ).read_text()
                )
            why = witness(case, ctx, record)
        except (HTTPException, OSError, ValueError, KeyError) as exc:
            why = f"The fork cannot be read: {type(exc).__name__}."
        if why:
            out[case["name"]] = ("not_evaluated", why, None, SOLVES)
            continue
        gate = run_explore.case_gate(
            task, {"project_id": case["project_id"], "job": case.get("job")}, profile
        )
        out[case["name"]] = (None, None, gate, SOLVES)
    return out
