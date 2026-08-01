"""
Tier-2 F: Live-backend chatbot smoke harness.

Drives a battery of realistic prompts against the running backend's
``POST /api/chat/stream`` endpoint and asserts each turn completed without
``tool_error`` frames AND landed the expected on-disk side effects.

This catches the class of bugs the v6 chatbot session shipped one-at-a-time
in browser - schema/signature drift, MIME mis-sniffing, Pydantic SSE leaks,
multi-period misalignment - in batches, before the user touches the GUI.

USAGE
-----
1. Ensure backend is running and ANTHROPIC_API_KEY is set
   (``/api/chat/health`` should return ``anthropic_api_key_present: true``).
2. CLOSE your browser tab on the GUI (autosave from the browser races with
   the smoke's project create/delete - per ``feedback_no_live_backend_project_io``
   memory rule).
3. Run from the repo root:
       pixi run python pypsa-gui/backend/smoke/run_chat_smoke.py

OPTIONS
-------
   --prompts N      run only the first N prompts (default: all)
   --keep-project   skip cleanup so you can inspect the smoke project
   --base-url URL   override backend URL (default: http://127.0.0.1:8000)
   --verbose        print every SSE frame (default: only headlines)

EXIT CODES
----------
   0  all prompts passed
   1  one or more prompts failed (parse output for details)
   2  setup precondition failed (backend down, no API key, etc.)

COST
----
   ~$0.05-0.30 per full run (5-8 prompts at Sonnet pricing). Cheap enough
   to run before each phase-end /qa-phase, or once per day during heavy
   chatbot iteration. Each prompt is ~30-90s wall-clock.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterator

try:
    import requests
except ImportError:
    sys.stderr.write(
        "ERROR: requests not installed in this Python env. "
        "Try: <pixi-python> -m pip install requests\n"
    )
    sys.exit(2)


# ── Config ──────────────────────────────────────────────────────────────────


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MODEL = None  # Use whatever /api/chat/health reports as default.
# Per-turn wall-clock cap. Sonnet 4.6 with 1-2 tool calls is typically 15-30s;
# a multi-step "create scenario + N components" turn can be 90-150s. Set to
# 240s so a complex prompt (6+ tool calls) finishes without flapping.
TURN_TIMEOUT_SECONDS = 240
# requests read-timeout (between chunks). SSE streams CAN have long silences
# during agent reasoning (Anthropic API call in flight, no chunks emitted to
# the client). Set generous so a long thinking pass doesn't kill the stream;
# the per-frame deadline check inside the loop is the real ceiling.
HTTP_READ_TIMEOUT_SECONDS = 240


# ── ANSI colour helpers (best-effort; degrade gracefully on plain consoles) ──


def _colour(s: str, code: str) -> str:
    if os.environ.get("NO_COLOR"):
        return s
    return f"\033[{code}m{s}\033[0m"


def _green(s: str) -> str:   return _colour(s, "32")
def _red(s: str) -> str:     return _colour(s, "31")
def _yellow(s: str) -> str:  return _colour(s, "33")
def _dim(s: str) -> str:     return _colour(s, "2")


# ── SSE parsing ─────────────────────────────────────────────────────────────


@dataclass
class SSEFrame:
    event: str
    data: dict[str, Any]


def _iter_sse(response: requests.Response) -> Iterator[SSEFrame]:
    """
    Parse SSE frames from a streaming response. The chat endpoint emits
    one frame per ``event: <name>\\ndata: <json>\\n\\n`` block (see
    ``services.chat_service.sse_frame``). We accumulate raw lines until a
    blank-line separator, then emit one SSEFrame.
    """
    event_name = None
    data_buf: list[str] = []
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        if raw == "":  # frame separator
            if event_name is not None and data_buf:
                joined = "\n".join(data_buf)
                try:
                    payload = json.loads(joined)
                except json.JSONDecodeError:
                    payload = {"_raw": joined}
                yield SSEFrame(event=event_name, data=payload)
            event_name = None
            data_buf = []
            continue
        if raw.startswith("event:"):
            event_name = raw[len("event:"):].strip()
        elif raw.startswith("data:"):
            data_buf.append(raw[len("data:"):].lstrip())
        # other lines (comments, etc.) ignored


# ── Smoke prompt + assertion contracts ──────────────────────────────────────


@dataclass
class PromptResult:
    """Outcome of one prompt run."""

    name: str
    passed: bool
    seconds: float = 0.0
    tool_errors: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    # In the real run_turn flow, `turn_done` is the success terminator. The
    # stream is closed by the backend right after - iter_lines() returns
    # cleanly. `session_done` is only emitted on error paths (abort, cap
    # exceeded, project_switched_mid_turn, invalid_attachment, etc.) - see
    # services/chat_service.py:1612-2140. So success = turn_done seen AND
    # no session_done seen (OR session_done.reason == 'complete' for the
    # stub-driven path).
    turn_done_seen: bool = False
    session_done_reason: str | None = None
    raw_frames_seen: int = 0
    extra_failure: str | None = None  # set when a post-run assertion fails


@dataclass
class SmokePrompt:
    """One prompt + post-run on-disk assertion."""

    name: str
    message: str
    # If non-empty: every tool_error frame whose ``error_kind`` is NOT in this
    # set fails the prompt. Use to accept INTENTIONAL errors (e.g. probing
    # save_project_as on an existing name expects ``project_exists``).
    expected_tool_errors: set[str] = field(default_factory=set)
    # Optional post-run network assertion. Callable receives the smoke
    # project's name + base_url + a session.requests.Session for cheap reads.
    # Should return None on PASS, or a failure string on FAIL.
    post_assertion: Any = None  # Callable[[ctx], str | None] - kept loose to avoid forward-ref noise
    # Optional shared session id — when two SmokePrompts set the SAME
    # session_id, _run_one_prompt reuses it instead of minting a fresh
    # smoke-{uuid} for each, giving them one continuous chat.session_id
    # for multi-turn continuity testing (P8a/P8b).
    session_id: str | None = None


# ── Smoke runner ────────────────────────────────────────────────────────────


@dataclass
class SmokeContext:
    base_url: str
    smoke_project: str
    original_project: str | None  # so we can re-activate after smoke
    session: requests.Session
    verbose: bool
    model: str | None


def _print_frame_compact(frame: SSEFrame, verbose: bool) -> None:
    if verbose:
        print(_dim(f"    [{frame.event}] {json.dumps(frame.data)[:200]}"))
        return
    # Headline-only mode: surface only the load-bearing events.
    if frame.event == "tool_request":
        tool = frame.data.get("tool_name", "?")
        print(_dim(f"    -> {tool}"))
    elif frame.event == "tool_error":
        kind = frame.data.get("error_kind") or frame.data.get("message", "?")
        tool = frame.data.get("tool_name", "?")
        print(_dim(f"    ! tool_error {tool}: {kind}"))
    elif frame.event == "error":
        print(_dim(f"    !! error: {frame.data.get('error_kind') or frame.data}"))
    elif frame.event == "tool_pending_confirmation":
        tool = frame.data.get("tool_name", "?")
        print(_dim(f"    ? pending confirmation: {tool}"))


def _run_one_prompt(prompt: SmokePrompt, ctx: SmokeContext) -> PromptResult:
    """Drive ONE chat turn end-to-end. Returns PromptResult."""
    session_id = prompt.session_id or f"smoke-{uuid.uuid4().hex[:12]}"
    body: dict[str, Any] = {
        "session_id": session_id,
        "message": prompt.message,
    }
    if ctx.model:
        body["model"] = ctx.model

    result = PromptResult(name=prompt.name, passed=False)
    start = time.monotonic()

    try:
        resp = ctx.session.post(
            f"{ctx.base_url}/api/chat/stream",
            json=body,
            stream=True,
            # (connect_timeout, read_timeout). Read is per-chunk - SSE
            # silence between agent steps can be long, so use a generous
            # value. The real ceiling is TURN_TIMEOUT_SECONDS, checked
            # per-frame inside the loop below.
            timeout=(10, HTTP_READ_TIMEOUT_SECONDS),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        result.extra_failure = f"HTTP failure: {exc}"
        result.seconds = time.monotonic() - start
        return result

    # Stream frames until session_done or hard timeout.
    pending_confirms: list[dict[str, Any]] = []
    deadline = start + TURN_TIMEOUT_SECONDS
    for frame in _iter_sse(resp):
        result.raw_frames_seen += 1
        _print_frame_compact(frame, ctx.verbose)
        if time.monotonic() > deadline:
            result.extra_failure = "turn exceeded TURN_TIMEOUT_SECONDS"
            break
        if frame.event == "tool_request":
            result.tool_calls.append(frame.data.get("tool_name", "?"))
        elif frame.event == "tool_error":
            result.tool_errors.append(dict(frame.data))
        elif frame.event == "tool_pending_confirmation":
            pending_confirms.append(dict(frame.data))
            # Auto-approve to keep the smoke unattended. The harness is meant
            # to exercise the full path including destructive tools.
            # Field name is `confirmation_token` per chat_service.py:1252 -
            # NOT `token` (a misnamed lookup hangs the SSE stream waiting
            # for an approval that never goes through).
            token = frame.data.get("confirmation_token")
            if token:
                try:
                    ctx.session.post(
                        f"{ctx.base_url}/api/chat/{session_id}/confirm",
                        json={"token": token, "decision": "approve"},
                        timeout=10,
                    )
                except requests.RequestException as exc:
                    result.extra_failure = f"confirm POST failed: {exc}"
                    break
        elif frame.event == "turn_done":
            # Real LLM flow's success terminator. The backend will close
            # the SSE stream right after this. iter_lines() will return on
            # the next iteration; we don't break here so we still see any
            # trailing session_done (e.g. stub path) for diagnostics.
            result.turn_done_seen = True
        elif frame.event == "session_done":
            result.session_done_reason = frame.data.get("reason")
            break
        elif frame.event == "error":
            result.extra_failure = (
                f"top-level error frame: "
                f"{frame.data.get('error_kind') or frame.data.get('message')}"
            )
            break

    result.seconds = time.monotonic() - start

    # Default PASS gates:
    #  - turn_done arrived (real flow), OR session_done arrived with
    #    reason == 'complete' (stub path)
    #  - every tool_error's error_kind is in the prompt's expected set
    end_ok = result.turn_done_seen or (result.session_done_reason == "complete")
    if not end_ok:
        result.extra_failure = result.extra_failure or (
            f"neither turn_done nor session_done(complete) arrived "
            f"(session_done reason={result.session_done_reason!r})"
        )
        return result
    if result.session_done_reason is not None and result.session_done_reason != "complete":
        # session_done with a non-complete reason is a hard failure regardless
        # of turn_done (e.g. cap-exceeded, project_switched_mid_turn, abort).
        result.extra_failure = (
            f"session_done with error reason: {result.session_done_reason!r}"
        )
        return result

    unexpected = [
        e for e in result.tool_errors
        if (e.get("error_kind") or e.get("message") or "unknown")
            not in prompt.expected_tool_errors
    ]
    if unexpected:
        kinds = [e.get("error_kind") or e.get("message", "?") for e in unexpected]
        result.extra_failure = f"unexpected tool_error(s): {kinds}"
        return result

    # Post-run on-disk assertion (optional).
    if prompt.post_assertion is not None:
        try:
            fail = prompt.post_assertion(ctx)
        except Exception as exc:  # noqa: BLE001 - surface as failure
            fail = f"post_assertion raised: {exc!r}"
        if fail is not None:
            result.extra_failure = f"post_assertion: {fail}"
            return result

    result.passed = True
    return result


# ── Pre/post smoke setup ────────────────────────────────────────────────────


def _preflight(base_url: str, session: requests.Session) -> tuple[bool, str | None]:
    """Verify backend is up, key is present, no solve is running."""
    try:
        h = session.get(f"{base_url}/api/chat/health", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        return False, f"/api/chat/health unreachable: {exc}"
    if not h.get("ok"):
        return False, f"/api/chat/health returned ok=false: {h}"
    if not h.get("anthropic_api_key_present"):
        return False, "anthropic_api_key_present=false - set backend/.env"
    try:
        s = session.get(f"{base_url}/api/simulation/status", timeout=5).json()
    except Exception as exc:  # noqa: BLE001
        return False, f"/api/simulation/status unreachable: {exc}"
    if s.get("status") == "running":
        return False, "solver is running - aborting smoke (would interfere)"
    return True, None


def _reset_in_memory_network(base_url: str, session: requests.Session) -> bool:
    """
    Wipe the in-memory pypsa-gui network so prompt assertions start from a
    known empty state. Without this, a previous smoke run's residual buses /
    generators / loads persist across runs (in-memory state isn't cleared
    by deleting the on-disk project file in cleanup). Symptom: P3 fails
    with `Generator 'X' already exists` after a previous run added X.
    """
    try:
        r = session.post(f"{base_url}/api/network/reset", timeout=10)
        return r.status_code in (200, 204)
    except Exception:  # noqa: BLE001
        return False


def _capture_current_project(base_url: str, session: requests.Session) -> str | None:
    """
    Read the currently loaded project name so we can restore it after the run.

    There is no ``/api/projects/current`` endpoint - the source of truth is
    ``/api/network/meta``'s ``loaded_project`` field.
    """
    try:
        r = session.get(f"{base_url}/api/network/meta", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return data.get("loaded_project")
    except Exception:  # noqa: BLE001
        pass
    return None


def _restore_project(base_url: str, session: requests.Session,
                     name: str | None) -> None:
    """Re-activate the user's original project (no-op if name is None)."""
    if not name:
        return
    try:
        session.post(
            f"{base_url}/api/projects/{name}/activate",
            timeout=10,
        )
    except Exception:  # noqa: BLE001 - best-effort restore
        pass


def _delete_smoke_project(base_url: str, session: requests.Session,
                           name: str) -> bool:
    """Best-effort cleanup. Returns True if cleanup succeeded."""
    try:
        r = session.delete(f"{base_url}/api/projects/{name}", timeout=10)
        return r.status_code in (200, 204, 404)
    except Exception:  # noqa: BLE001
        return False


# ── Post-run assertions ─────────────────────────────────────────────────────


def assert_smoke_project_exists(ctx: SmokeContext) -> str | None:
    # Trailing slash matters: /api/projects/ is the listing endpoint.
    r = ctx.session.get(f"{ctx.base_url}/api/projects/", timeout=10)
    if r.status_code != 200:
        return f"GET /api/projects/ returned {r.status_code}"
    names = {p.get("name") for p in r.json()}
    if ctx.smoke_project not in names:
        return (
            f"smoke project {ctx.smoke_project!r} not in /api/projects "
            f"after create_scenario"
        )
    return None


def assert_bus_count_at_least(min_count: int):
    def _check(ctx: SmokeContext) -> str | None:
        r = ctx.session.get(f"{ctx.base_url}/api/network/buses", timeout=10)
        if r.status_code != 200:
            return f"GET /api/network/buses returned {r.status_code}"
        rows = r.json()
        if len(rows) < min_count:
            return f"expected >= {min_count} buses, got {len(rows)}"
        return None
    return _check


def assert_generator_named(name: str):
    def _check(ctx: SmokeContext) -> str | None:
        r = ctx.session.get(f"{ctx.base_url}/api/network/generators", timeout=10)
        if r.status_code != 200:
            return f"GET /api/network/generators returned {r.status_code}"
        rows = r.json()
        names = {row.get("name") for row in rows}
        if name not in names:
            return f"expected generator {name!r} in {sorted(names)}"
        return None
    return _check


def assert_component_field(component: str, name: str, field_name: str, expected: Any):
    def _check(ctx: SmokeContext) -> str | None:
        r = ctx.session.get(f"{ctx.base_url}/api/network/{component}", timeout=10)
        if r.status_code != 200:
            return f"GET /api/network/{component} returned {r.status_code}"
        rows = r.json()
        row = next((row for row in rows if row.get("name") == name), None)
        if row is None:
            return f"expected {component[:-1]} {name!r} to exist"
        actual = row.get(field_name)
        if actual != expected:
            return f"expected {name}.{field_name} == {expected!r}, got {actual!r}"
        return None
    return _check


def assert_component_absent(component: str, name: str):
    def _check(ctx: SmokeContext) -> str | None:
        r = ctx.session.get(f"{ctx.base_url}/api/network/{component}", timeout=10)
        if r.status_code != 200:
            return f"GET /api/network/{component} returned {r.status_code}"
        rows = r.json()
        names = {row.get("name") for row in rows}
        if name in names:
            return f"expected {component[:-1]} {name!r} to be deleted, still in {sorted(names)}"
        return None
    return _check


# ── Prompt battery ──────────────────────────────────────────────────────────


def build_prompts(smoke_project: str) -> list[SmokePrompt]:
    """
    The smoke battery. Order matters - each prompt builds on the prior state.

    Add new prompts when a chatbot tool ships. The discipline is:
        schema (chat_tools_schema.py)
      + signature (chat_tools.py)
      + consistency test (test_tool_schema_signature_consistency.py)
      + smoke prompt (here)
    Three doors, three independent failure surfaces.
    """
    p8_session_id = f"smoke-{uuid.uuid4().hex[:12]}"
    return [
        SmokePrompt(
            name="P1_save_project_empty",
            message=(
                f"Save the current network to disk as a project named "
                f"{smoke_project}. This is the simplest persist - call "
                f"save_project directly with that name; don't branch a "
                f"scenario, don't create a base project first."
            ),
            post_assertion=assert_smoke_project_exists,
        ),
        SmokePrompt(
            name="P2_create_buses",
            message=(
                "Add 3 buses to the current network. Name them exactly Bus0, "
                "Bus1, Bus2. Set v_nom=380 on each. No need to connect them."
            ),
            post_assertion=assert_bus_count_at_least(3),
        ),
        SmokePrompt(
            name="P3_add_generator",
            message=(
                "Add a Generator named SmokeSolar at Bus0 with p_nom=100 "
                "and carrier=solar."
            ),
            post_assertion=assert_generator_named("SmokeSolar"),
        ),
        SmokePrompt(
            name="P4_read_solver_config",
            message="What is the current solver configuration?",
        ),
        SmokePrompt(
            name="P5_validate_network",
            message="Validate the current network for solver-readiness.",
        ),
        SmokePrompt(
            name="P6_update_generator_via_chat",
            message="Change SmokeSolar's p_nom to 150.",
            post_assertion=assert_component_field("generators", "SmokeSolar", "p_nom", 150.0),
        ),
        SmokePrompt(
            name="P7_delete_generator_via_chat",
            message="Delete the generator SmokeSolar.",
            post_assertion=assert_component_absent("generators", "SmokeSolar"),
        ),
        SmokePrompt(
            name="P8a_add_load_multiturn",
            message="Add a Load named SmokeLoad at Bus0 with p_set=42.",
            session_id=p8_session_id,
        ),
        SmokePrompt(
            name="P8b_update_that_load_multiturn",
            message="Set that load's p_set to 84 instead.",
            session_id=p8_session_id,
            post_assertion=assert_component_field("loads", "SmokeLoad", "p_set", 84.0),
        ),
    ]


# ── Top-level ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, default=None,
                        help="Run only the first N prompts.")
    parser.add_argument("--keep-project", action="store_true",
                        help="Skip cleanup so you can inspect the smoke project.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"Backend URL (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every SSE frame.")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="Override the chat model (default: backend default).")
    args = parser.parse_args()

    session = requests.Session()

    print(_dim("==== Preflight ===="))
    ok, reason = _preflight(args.base_url, session)
    if not ok:
        print(_red(f"Preflight failed: {reason}"))
        return 2

    smoke_project = (
        "smoke_" + time.strftime("%Y%m%d_%H%M%S")
        + "_" + uuid.uuid4().hex[:4]
    )
    original_project = _capture_current_project(args.base_url, session)
    print("  backend ok, key present, no solve running")
    print(f"  smoke_project: {_yellow(smoke_project)}")
    if original_project:
        print(f"  original_project: {_dim(original_project)} (will restore after run)")

    reset_ok = _reset_in_memory_network(args.base_url, session)
    print(f"  in-memory network reset: {'ok' if reset_ok else 'FAILED (smoke may fail with stale state)'}")

    ctx = SmokeContext(
        base_url=args.base_url,
        smoke_project=smoke_project,
        original_project=original_project,
        session=session,
        verbose=args.verbose,
        model=args.model,
    )

    prompts = build_prompts(smoke_project)
    if args.prompts is not None:
        prompts = prompts[: args.prompts]

    results: list[PromptResult] = []
    overall_start = time.monotonic()

    try:
        for i, p in enumerate(prompts, 1):
            print()
            print(_dim(f"==== [{i}/{len(prompts)}] {p.name} ===="))
            print(f"  prompt: {p.message[:120]}{'...' if len(p.message) > 120 else ''}")
            r = _run_one_prompt(p, ctx)
            results.append(r)
            tag = _green("PASS") if r.passed else _red("FAIL")
            tools_str = ", ".join(r.tool_calls) if r.tool_calls else "(none)"
            print(f"  {tag} in {r.seconds:.1f}s  tools=[{tools_str}]  frames={r.raw_frames_seen}")
            if not r.passed:
                print(f"  {_red('failure:')} {r.extra_failure}")
                if r.tool_errors:
                    print(f"  tool_errors: {r.tool_errors}")
    finally:
        print()
        print(_dim("==== Cleanup ===="))
        if not args.keep_project:
            cleaned = _delete_smoke_project(args.base_url, session, smoke_project)
            print(f"  delete smoke project: {'ok' if cleaned else 'FAILED (manual cleanup needed)'}")
        else:
            print(f"  --keep-project: smoke project {smoke_project} left on disk")
        _restore_project(args.base_url, session, original_project)
        print(f"  original_project restored: {original_project or '(none)'}")

    overall = time.monotonic() - overall_start
    passes = sum(1 for r in results if r.passed)
    print()
    print(_dim("==== Summary ===="))
    print(f"  {passes}/{len(results)} passed  in  {overall:.1f}s total")
    for r in results:
        tag = _green("PASS") if r.passed else _red("FAIL")
        print(f"    {tag}  {r.name:<28}  {r.seconds:>5.1f}s  tools={len(r.tool_calls)}  errors={len(r.tool_errors)}")
    return 0 if passes == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
