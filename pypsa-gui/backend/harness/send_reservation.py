# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT
"""A durable reservation before each model send of a harness run (issue 24, HH-03).

Before a harness run's provider call, the server writes a hold and a
dispatch intent to the run's ledger, `task["run"]["budgets"]`, which has
LV-09's shape `{reservations, final_charges, cached_usage}`. It admits the
send only when `settled + open holds + this hold <= cap`. After the reply,
the hold settles to the provider's usage. A reply with no usage settles at
the hold's bound. A hold left open settles `uncertain` at its bound: at the
end of the turn, or by the restart reconciliation (the runner's boot sweep
and its lease takeover). It never settles at zero.

The ledger rows are written by LV-09's in-memory cores
(`durable_conversation.reserve_in` / `settle_in`): idempotent by key, the
same schema, no second store. Each write happens under the task lock
(`runner._update`), so two sends of one run cannot both pass admission.

One switch, off by default: `PYPSA_GUI_CHAT_DURABLE_BUDGET_MODE` =
`off` | `output_tokens` | `total_tokens` | `money_eur`. An unknown value
refuses every send (fail closed). A hold is an upper bound, not an estimate:
input tokens are bounded by the request's UTF-8 byte count plus a framing
allowance. A request whose size bytes cannot bound (an image or document
block) refuses the token-total and money modes, and a model with no price
refuses the money mode. The existing caps (`_turn_budget_block`, the
session and daily limits, `TaskCap`, issue 02's metered cost cap) are
separate AND conditions. This module charges none of them.

Research (SH-RESEARCH-CAP-1) is a second scope in the same ledger. A
research hold counts toward the run's cap and also toward the research cap:
EUR 2 by default, set on the envelope (`research.cost_cap_eur`) up to a
hard EUR 10. A provider-run tool (web search or fetch) adds its `max_uses` ×
(fee + fetched content) to the bound, and is refused when either is unknown.
"""

from __future__ import annotations

import hashlib
import contextlib
import contextvars
import copy
import json
import logging
import math
import os
import threading
import time
import uuid
import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from harness.protocol import LLMEvent, LLMRequest, ProviderError

logger = logging.getLogger(__name__)

MODE_ENV = "PYPSA_GUI_CHAT_DURABLE_BUDGET_MODE"
MODES = ("off", "output_tokens", "total_tokens", "money_eur")
UNITS = {"output_tokens": "output_tokens", "total_tokens": "total_tokens", "money_eur": "eur"}
KINDS = {"run": "model_send", "research": "research_send"}
# Tokens a provider adds around the serialised request (role and turn
# markers, its tool-use preamble). The byte count already over-counts JSON
# punctuation; this covers what the bytes cannot see.
FRAMING_TOKENS = 2048
FRAMING_TOKENS_PER_MESSAGE = 16
SERVER_TOOL_COSTS_ENV = "PYPSA_GUI_SERVER_TOOL_COSTS_EUR"
# Owner defaults (SH-TOOL-COSTS-1); the env (JSON, the same shape) replaces
# the table. Web search: USD 10 per 1,000 uses at the model rate table's
# conversion, with 10,000 content tokens per use. Web fetch: no fee; a
# tool's own `max_content_tokens` wins over the 50,000-token allowance.
DEFAULT_SERVER_TOOL_COSTS_EUR = {
    "web_search_": {"fee_eur": 0.0093, "content_tokens": 10_000},
    "web_fetch_": {"fee_eur": 0.0, "content_tokens": 50_000},
}
# The usage count a provider reports for each server tool, by tool name.
SERVER_TOOL_COUNT_KEYS = {"web_search": "search_requests", "web_fetch": "fetch_requests"}
INPUT_KEYS = ("input_tokens", "cache_read_tokens", "cache_create_tokens")
# The flags of a charge that counts at its hold's bound because the usage was lost.
AT_BOUND_FLAGS = frozenset({"uncertain", "no_usage"})
_UNBOUNDED_BLOCKS = frozenset({"image", "document", "input_image", "input_file"})


class SendRefused(ProviderError):
    """A send this module will not admit. The turn ends: the kind is one the
    loop never retries."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__("invalid_request", message)
        self.reason = reason


def mode() -> str:
    """The configured mode; `invalid` for a value outside MODES."""
    value = os.environ.get(MODE_ENV, "off").strip() or "off"
    return value if value in MODES else "invalid"


def enabled() -> bool:
    return mode() != "off"


# ── Bounds ───────────────────────────────────────────────────────────────────


def _content_blocks(value: Any) -> Iterator[dict]:
    """Every block dict in the system blocks and messages (tool schemas are
    not content: their `type` is a JSON-schema type and may be a list)."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _content_blocks(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _content_blocks(item)


def _number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def _server_tools(request: LLMRequest) -> list[dict]:
    """Provider-run tools (a `type`, no `input_schema`), e.g. web search or
    fetch: each use adds a fee and fetched content to the call's input."""
    return [t for t in request.tools or () if isinstance(t, dict) and "type" in t and "input_schema" not in t]


def server_tool_costs() -> dict[str, dict[str, float]]:
    """`PYPSA_GUI_SERVER_TOOL_COSTS_EUR`: {type prefix: {"fee_eur": per use,
    "content_tokens": per use}}, replacing the owner defaults. A type no
    prefix matches has no known cost. Raises ValueError for an invalid
    table."""
    raw = os.environ.get(SERVER_TOOL_COSTS_ENV)
    if not raw:
        return copy.deepcopy(DEFAULT_SERVER_TOOL_COSTS_EUR)  # a caller's change never reaches the defaults
    table = json.loads(raw)
    if not isinstance(table, dict):
        raise ValueError("the server tool cost table is not an object")
    for prefix, entry in table.items():
        if not isinstance(prefix, str) or not prefix or not isinstance(entry, dict) \
                or set(entry) != {"fee_eur", "content_tokens"} or not all(_number(v) for v in entry.values()):
            raise ValueError("a server tool cost is {fee_eur, content_tokens}, finite numbers of at least 0")
    return table


@dataclass(frozen=True)
class ServerTool:
    count_key: str  # the usage count the provider reports for its uses
    max_uses: int
    fee_eur: float
    content_tokens: int  # per use, at most


@dataclass(frozen=True)
class Shape:
    """What bounds one send: input tokens (None: no bound), the output
    limit, and its server tools (None: one has no known cost or no
    `max_uses`)."""

    inputs: int | None
    output: int
    tools: tuple[ServerTool, ...] | None

    @property
    def fees_eur(self) -> float:
        return sum(t.max_uses * t.fee_eur for t in self.tools or ())


def _tools_of(request: LLMRequest) -> tuple[ServerTool, ...] | None:
    tools = _server_tools(request)
    if not tools:
        return ()
    try:
        table = server_tool_costs()
    except ValueError:
        return None
    out = []
    for tool in tools:
        uses, kind = tool.get("max_uses"), tool.get("type")
        match = max((p for p in table if isinstance(kind, str) and kind.startswith(p)), key=len, default=None)
        if isinstance(uses, bool) or not isinstance(uses, int) or uses <= 0 or match is None:
            return None
        content = tool.get("max_content_tokens", table[match]["content_tokens"])
        if not _number(content):
            return None
        name = str(tool.get("name") or "")
        out.append(ServerTool(SERVER_TOOL_COUNT_KEYS.get(name, f"{name}_requests"), uses,
                              float(table[match]["fee_eur"]), int(math.ceil(content))))
    return tuple(out)


def shape(request: LLMRequest) -> Shape:
    """The bound's parts for one request; SendRefused for no output limit."""
    out = request.max_tokens
    if isinstance(out, bool) or not isinstance(out, int) or out < 0:
        raise SendRefused("unbounded_output", "This request has no output limit; the send is refused.")
    tools = _tools_of(request)
    parts = [request.system_blocks, request.messages]
    inputs = None
    if not any(isinstance(b.get("type"), str) and b["type"] in _UNBOUNDED_BLOCKS for b in _content_blocks(parts)):
        try:
            size = len(json.dumps([*parts, request.tools], ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            size = None
        if size is not None and tools is not None:
            inputs = (size + FRAMING_TOKENS + FRAMING_TOKENS_PER_MESSAGE * len(request.messages or ())
                      + sum(t.max_uses * t.content_tokens for t in tools))
    return Shape(inputs, out, tools)


def input_bound(request: LLMRequest) -> int | None:
    """An upper bound on the request's input tokens; None when there is none."""
    return shape(request).inputs


def _rate(model: str) -> dict[str, float]:
    from harness import runner
    try:
        return runner._rate(model)
    except ValueError as exc:  # no price, or an invalid rate table
        raise SendRefused("no_price", f"No price for this model; the send is refused ({exc}).") from None


def _worst_input_rate(rate: dict[str, float]) -> float:
    return max(rate[k] for k in INPUT_KEYS)


def _bounded_inputs(s: Shape) -> int:
    if s.inputs is None:
        raise SendRefused("unbounded_input", "This request's input has no upper bound; the send is refused.")
    return s.inputs


def amount(s: Shape, unit: str, model: str) -> int | float:
    """The hold for one send in `unit`."""
    if unit == "output_tokens":
        return s.output
    inputs = _bounded_inputs(s)
    if unit == "total_tokens":
        return inputs + s.output
    rate = _rate(model)
    return round((inputs * _worst_input_rate(rate) + s.output * rate["output_tokens"]) / 1e6 + s.fees_eur, 6)


def bound(request: LLMRequest, current: str) -> int | float:
    """The hold for one send in the mode's unit."""
    return amount(shape(request), UNITS[current], request.model)


def _count(value: Any) -> int | None:
    return value if not isinstance(value, bool) and isinstance(value, int) and value >= 0 else None


def actual(s: Shape, unit: str, model: str, usage: Any) -> tuple[int | float, list[str]]:
    """The spend of one send from the provider's usage, and its flags. No
    usage at all is the whole bound (`no_usage`). A part that is missing or
    invalid, the input or the output count, settles at that part's bound
    (`partial_usage`). Server-tool fees are the reported use counts × fee,
    or `max_uses` × fee when a count is missing. Never below what was
    reported, never zero for lost usage."""
    usage = usage if isinstance(usage, dict) else {}
    from harness.runner import USAGE_KEYS
    if not any(k in usage for k in USAGE_KEYS):
        return amount(s, unit, model), ["no_usage"]
    flags = []
    output = _count(usage.get("output_tokens"))
    if output is None:
        output, flags = s.output, ["partial_usage"]
    inputs = {k: _count(usage.get(k, 0)) for k in INPUT_KEYS}
    input_ok = "input_tokens" in usage and all(v is not None for v in inputs.values())
    if not input_ok and unit != "output_tokens":
        flags = ["partial_usage"]
    if unit == "output_tokens":
        return output, flags
    if unit == "total_tokens":
        return (sum(inputs.values()) if input_ok else _bounded_inputs(s)) + output, flags
    rate = _rate(model)
    cost_in = (sum(inputs[k] * rate[k] for k in INPUT_KEYS) if input_ok
               else _bounded_inputs(s) * _worst_input_rate(rate))
    fees = 0.0
    for tool in s.tools or ():
        used = _count(usage.get(tool.count_key))
        fees += (tool.max_uses if used is None else used) * tool.fee_eur
    return round((cost_in + output * rate["output_tokens"]) / 1e6 + fees, 6), flags


# ── The ledger ───────────────────────────────────────────────────────────────


def _ledger(run: dict) -> dict:
    from services import durable_conversation
    return durable_conversation._budgets(run)


def _rows(run: dict) -> list[dict]:
    budgets = _ledger(run)
    return list(budgets.get("final_charges", [])) + list(budgets.get("reservations", []))


def _sum(rows, field: str) -> float:
    total = 0.0
    for row in rows:
        value = row.get(field)
        if not _number(value):
            raise SendRefused("ledger_invalid", "The run's spend ledger has an unreadable amount; sends are refused.")
        total += float(value)
    return total


def _spent(run: dict, unit: str) -> float:
    return _sum([r for r in _rows(run) if r.get("unit") == unit], "amount")


def _research_spent(run: dict) -> float:
    return _sum([r for r in _rows(run) if r.get("kind") == KINDS["research"]], "eur")


def cap(task: dict, unit: str) -> float | None:
    """The run's cap in `unit`; None when the run sets none."""
    from harness import budget, runner
    limit = runner._budget(task)["limit"]
    if unit == "eur":
        return float(limit["cost_eur"])
    if unit == "output_tokens":
        return float(limit.get("output_tokens", budget.MAX_OUTPUT_TOKENS_PER_SESSION))
    value = limit.get("total_tokens")
    return float(value) if _number(value) else None


def research_cap(path) -> float:
    """SH-RESEARCH-CAP-1, the one research enforcement: the granted
    envelope's `research.cost_cap_eur`, never above EUR 10. Refuses (fails
    closed) when the envelope cannot be read, is not granted or does not
    allow research."""
    from services import run_envelope
    try:
        envelope = run_envelope.read(path)
    except Exception:  # noqa: BLE001 — an unreadable envelope admits no research
        envelope = None
    if not isinstance(envelope, dict) or envelope.get("state") != "granted":
        raise SendRefused("research_unavailable", "No granted envelope for this run; research is refused.")
    research = envelope.get("research")
    if not isinstance(research, dict) or research.get("allowed") is not True:
        raise SendRefused("research_not_allowed", "The envelope does not allow research.")
    value = research.get("cost_cap_eur")
    if not _number(value):
        raise SendRefused("research_unavailable", "The envelope's research cap is unreadable; research is refused.")
    return min(float(value), run_envelope.RESEARCH_CAP_EUR)


def digest(request: LLMRequest) -> str:
    body = json.dumps([request.model, request.max_tokens, request.system_blocks, request.messages, request.tools],
                      sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def new_key(task_id: str) -> str:
    """A fresh key for one send: two sends never share a hold, even with
    identical requests."""
    return f"run:{task_id}:{uuid.uuid4().hex[:16]}"


def _locked(path, change):
    """`change` on the task under the cross-process sidecar lock (the
    runner's lease lock) and the task lock: the ledger's read-modify-write
    is atomic across processes and threads."""
    from harness import runner
    with runner._file_lock(path):
        return runner._update(path, change)


def _stop(path, reason: str) -> None:
    from harness import runner

    def change(task):
        if task["run"]["status"] not in runner._DRIVEN or task["run"]["status"] == "needs_user":
            return False  # an earlier stop keeps its reason
        runner._stop_in(task, "needs_user", reason)
    _locked(path, change)


def admit(path, request: LLMRequest, *, boot: str, scope: str = "run", key: str | None = None,
          clock=time.time) -> str:
    """Reserve one send's hold before it goes out, or raise SendRefused.
    `key` is the open hold of an earlier attempt of this same send that
    failed before any response byte: it is reused, nothing more is
    reserved. Returns the hold's key.

    A run-scope refusal of any reason stops the run `needs_user` with that
    reason (`budget` for the cap, issue 02's stop). A research refusal
    stops research only; the stage goes on (§6.5). Research is enforced
    whatever the mode (GK B (b)): with the mode off, its hold is in EUR. A
    research hold is ONE row, counted in the run's total and in the
    research cap.

    Only the holder of the run's live lease admits (GK B): `boot` must be
    `run.runner.lease_owner` with `lease_until` ahead of `clock()`.
    Otherwise the send is refused `lease_lost` and nothing is written, not
    even a stop: a lapsed process's write could be erased by the new
    owner's save, and the run is the new owner's to stop."""
    if scope not in KINDS:
        raise ValueError(f"unknown reservation scope {scope!r}")
    try:
        return _admit(path, request, boot=boot, scope=scope, key=key, clock=clock)
    except SendRefused as refused:
        if scope == "run" and refused.reason not in ("not_a_run", "lease_lost"):
            _stop(path, refused.reason)
        raise


def _admit(path, request, *, boot, scope, key, clock):
    current = mode()
    if current == "invalid":
        raise SendRefused("invalid_mode", f"{MODE_ENV} is not one of {', '.join(MODES)}; sends are refused.")
    if current == "off" and scope != "research":
        raise ValueError("no run-scope reservation while the mode is off")
    unit = UNITS["money_eur" if current == "off" else current]
    s = shape(request)
    hold = amount(s, unit, request.model)
    eur = hold if unit == "eur" else (amount(s, "eur", request.model) if scope == "research" else None)
    research_limit = research_cap(path) if scope == "research" else None
    out: dict[str, Any] = {}

    def change(task):
        run = task["run"]
        lease = run.get("runner") or {}
        if lease.get("lease_owner") != boot or not float(lease.get("lease_until") or 0) > clock():
            out["refused"] = SendRefused("lease_lost", "This process no longer holds the run; "
                                                       "the send is refused.")
            return False
        if key is not None and any(r.get("key") == key for r in _ledger(run).get("reservations", [])):
            out["key"] = key
            return False  # a retry before any response byte reserves once
        try:
            limit = cap(task, unit)
            if limit is None:
                raise SendRefused("no_cap", f"The run sets no {unit} cap; the send is refused.")
            if _spent(run, unit) + hold > limit:
                raise SendRefused("budget", "The run's model budget is spent; the send is refused.")
            if research_limit is not None and _research_spent(run) + eur > research_limit:
                raise SendRefused("research_budget", "The research budget is spent; research stops.")
        except SendRefused as refused:
            out["refused"] = refused
            return False
        from services import durable_conversation
        out["key"] = new_key(task["task_id"])
        durable_conversation.reserve_in(run, key=out["key"], kind=KINDS[scope], amount=hold, unit=unit)
        row = next(r for r in _ledger(run)["reservations"] if r["key"] == out["key"])
        row["dispatch"] = {"boot": boot, "model": request.model, "at": time.time()}
        if scope == "research":
            row["eur"] = eur
        return None

    if _locked(path, change) is None:
        raise SendRefused("not_a_run", "This task is not a harness run.")
    if out.get("refused"):
        raise out["refused"]
    return out["key"]


def _settle_row(run: dict, hold: dict, amount_, flags: list[str] | None, eur=None) -> None:
    from services import durable_conversation
    durable_conversation.settle_in(run, key=hold["key"], amount=amount_, unit=hold["unit"], flags=flags or None)
    charge = next(c for c in reversed(_ledger(run)["final_charges"]) if c["key"] == hold["key"])
    if "eur" in hold:
        charge["eur"] = hold["eur"] if eur is None else eur


def settle(path, key: str, request: LLMRequest, usage: Any) -> None:
    """Settle one send to the provider's usage (see `actual`)."""
    s = shape(request)

    def change(task):
        run = task["run"]
        hold = next((r for r in _ledger(run).get("reservations", []) if r.get("key") == key), None)
        if hold is None:
            return False  # already reconciled: it counted at its bound
        try:
            spent, flags = actual(s, hold["unit"], request.model, usage)
        except SendRefused:
            spent, flags = hold["amount"], ["no_usage"]
        eur = None
        if "eur" in hold:
            try:
                eur = actual(s, "eur", request.model, usage)[0]
            except SendRefused:
                eur = hold["eur"]
        _settle_row(run, hold, spent, flags, eur=eur)
        return None

    try:
        _locked(path, change)
    except (HTTPException, TimeoutError, OSError):
        logger.exception("send reservation: settling %s failed; the hold stays open and counts", key)


def _settle_uncertain(task: dict, which) -> int:
    run = task["run"]
    holds = [r for r in _ledger(run).get("reservations", []) if which(r)]
    for hold in holds:
        _settle_row(run, hold, hold["amount"], ["uncertain"])
    return len(holds)


def _boot_of(row: dict) -> str | None:
    return (row.get("dispatch") or {}).get("boot")


def settle_uncertain(path, key: str) -> None:
    """One hold settles `uncertain` at its bound: an attempt that failed
    after the provider started answering."""
    def change(task):
        return None if _settle_uncertain(task, lambda r: r.get("key") == key) else False
    _locked(path, change)


def close_open(path, *, boot: str) -> int:
    """Turn end: a hold this boot left open (a send that errored or was
    abandoned) settles `uncertain` at its bound."""
    out = {"n": 0}

    def change(task):
        out["n"] = _settle_uncertain(task, lambda r: _boot_of(r) == boot)
        return None if out["n"] else False
    _locked(path, change)
    return out["n"]


def reconcile(path, *, boot: str, now: float | None = None) -> int:
    """Restart reconciliation: a hold another boot left open settles
    `uncertain` at its bound, and a driven run goes `needs_user`. Skipped
    while another instance holds the run's live lease."""
    from harness import runner
    out = {"n": 0}
    now = time.time() if now is None else now

    def change(task):
        run = task["run"]
        if not has_open_holds(run):
            return False
        lease = run.get("runner") or {}
        if lease.get("lease_owner") not in (None, boot) and float(lease.get("lease_until") or 0) > now:
            return False
        out["n"] = _settle_uncertain(task, lambda r: _boot_of(r) != boot)
        if not out["n"]:
            return False
        if run["status"] in runner._DRIVEN:
            runner._stop_in(task, "needs_user", "uncertain")
        return None
    _locked(path, change)
    return out["n"]


def summary(run: dict) -> dict | None:
    """The run view's line (GK B R3): spend in the ledger's unit, open holds,
    and how many charges count at a bound because the usage was lost."""
    if not run.get("budgets"):
        return None
    rows = _rows(run)
    if not rows:
        return None
    unit = rows[-1].get("unit")
    held = [r for r in _ledger(run).get("reservations", []) if r.get("unit") == unit]
    bounds = [c for c in _ledger(run).get("final_charges", [])
              if c.get("unit") == unit and set(c.get("flags") or ()) & AT_BOUND_FLAGS]
    try:
        spent, open_ = _spent(run, unit), _sum(held, "amount")
    except SendRefused:
        return None
    return {"unit": unit, "spent": round(spent, 6), "open": round(open_, 6), "at_bound": len(bounds)}


def has_open_holds(run: dict) -> bool:
    return bool((run.get("budgets") or {}).get("reservations"))


def sweep(paths, *, boot: str, now: float | None = None) -> int:
    """The boot sweep (GK B, 3 Nov): reconcile every run file."""
    total = 0
    for path in paths:
        try:
            total += reconcile(path, boot=boot, now=now)
        except Exception:  # noqa: BLE001 — one run's fault never stops the sweep
            logger.exception("send reservation: reconciling %s failed", path)
    return total


_SCOPE: contextvars.ContextVar[str | None] = contextvars.ContextVar("send_reservation_scope", default=None)


@contextlib.contextmanager
def research():
    """Sends streamed inside this block are research (SH-09): the runner's
    own wrapper reserves them under `scope="research"`, one hold each. A
    research step never nests a second ReservingProvider."""
    token = _SCOPE.set("research")
    try:
        yield
    finally:
        _SCOPE.reset(token)


class ReservingProvider:
    """Wraps a provider: every `stream(request)` reserves first and settles
    after. Only a harness run's own sends go through it. With the mode off,
    run-scope sends pass straight through and only research is reserved."""

    def __init__(self, inner, path, *, boot: str, scope: str = "run", clock=time.time) -> None:
        self._inner, self._path, self._boot, self._scope = inner, path, boot, scope
        self._clock = clock
        self.name = getattr(inner, "name", "reserving")
        # The open send of each request object: the loop retries by streaming
        # the same request again; an attempt that failed before any response
        # byte leaves its key here for the retry to reuse.
        self._open: dict[int, tuple[Any, str, str]] = {}
        self._lock = threading.Lock()

    def _retry_key(self, request: LLMRequest) -> str | None:
        with self._lock:
            ref, body, key = self._open.get(id(request), (None, None, None))
            return key if ref is not None and ref() is request and body == digest(request) else None

    def _remember(self, request: LLMRequest, key: str | None) -> None:
        with self._lock:
            if key is None:
                self._open.pop(id(request), None)
            else:
                self._open[id(request)] = (weakref.ref(request), digest(request), key)

    def __getattr__(self, attr):
        return getattr(self._inner, attr)

    def stream(self, request: LLMRequest) -> Iterator[LLMEvent]:
        scope = _SCOPE.get() or self._scope
        if scope == "run" and mode() == "off":
            yield from self._inner.stream(request)
            return
        key = admit(self._path, request, boot=self._boot, scope=scope, key=self._retry_key(request),
                    clock=self._clock)
        self._remember(request, key)
        usage, answered = None, False
        try:
            for event in self._inner.stream(request):
                answered = True
                if event.type == "message_done":
                    usage = event.usage
                yield event
        except Exception:
            if answered:  # the provider started answering: it may have billed; a retry takes a new hold
                self._remember(request, None)
                settle_uncertain(self._path, key)
            raise
        self._remember(request, None)
        settle(self._path, key, request, usage)
