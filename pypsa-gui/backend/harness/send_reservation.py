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
(`durable_conversation._reserve_in` / `_settle_in`): idempotent by key, the
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
import json
import logging
import math
import os
import threading
import time
import uuid
import weakref
from typing import Any
from collections.abc import Iterator

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


def _walk_blocks(value: Any) -> Iterator[dict]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_blocks(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_blocks(item)


def input_bound(request: LLMRequest) -> int | None:
    """An upper bound on the request's input tokens. None when its size in
    bytes cannot bound it: an image or document block, or content that does
    not serialise."""
    parts = [request.system_blocks, request.messages, request.tools]
    if any(b.get("type") in _UNBOUNDED_BLOCKS for b in _walk_blocks(parts)):
        return None
    try:
        size = len(json.dumps(parts, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return None
    server = server_tool_allowance(request)
    if server is None:
        return None
    return size + FRAMING_TOKENS + FRAMING_TOKENS_PER_MESSAGE * len(request.messages or ()) + server[0]


def _server_tools(request: LLMRequest) -> list[dict]:
    """Provider-run tools (a `type`, no `input_schema`), e.g. web search or
    fetch: each use adds a fee and fetched content to the call's input."""
    return [t for t in request.tools or () if isinstance(t, dict) and "type" in t and "input_schema" not in t]


def server_tool_costs() -> dict[str, dict[str, float]]:
    """`PYPSA_GUI_SERVER_TOOL_COSTS_EUR`: {type prefix: {"fee_eur": per use,
    "content_tokens": per use}}, owner values. None set: none known. Raises
    ValueError for an invalid table."""
    raw = os.environ.get(SERVER_TOOL_COSTS_ENV)
    if not raw:
        return {}
    table = json.loads(raw)
    if not isinstance(table, dict):
        raise ValueError("the server tool cost table is not an object")
    for prefix, entry in table.items():
        if not isinstance(prefix, str) or not prefix or not isinstance(entry, dict) \
                or set(entry) != {"fee_eur", "content_tokens"}:
            raise ValueError("a server tool cost names fee_eur and content_tokens")
        for value in entry.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("a server tool cost is a finite number of at least 0")
    return table


def server_tool_allowance(request: LLMRequest) -> tuple[int, float] | None:
    """(input tokens, EUR fees) the request's server tools may add at most;
    None when a tool has no `max_uses` or no known cost."""
    tools = _server_tools(request)
    if not tools:
        return 0, 0.0
    try:
        table = server_tool_costs()
    except ValueError:
        return None
    tokens, fees = 0, 0.0
    for tool in tools:
        uses = tool.get("max_uses")
        match = max((p for p in table if str(tool["type"]).startswith(p)), key=len, default=None)
        if isinstance(uses, bool) or not isinstance(uses, int) or uses <= 0 or match is None:
            return None
        content = tool.get("max_content_tokens", table[match]["content_tokens"])
        if isinstance(content, bool) or not isinstance(content, (int, float)) or content < 0:
            return None
        tokens += uses * int(math.ceil(content))
        fees += uses * float(table[match]["fee_eur"])
    return tokens, fees


def _rate(model: str) -> dict[str, float]:
    from harness import runner
    try:
        return runner._rate(model)
    except ValueError as exc:  # no price, or an invalid rate table
        raise SendRefused("no_price", f"No price for this model; the send is refused ({exc}).") from None


def _eur_bound(request: LLMRequest) -> float:
    inputs = input_bound(request)
    if inputs is None:
        raise SendRefused("unbounded_input", "This request's input has no upper bound; a priced send is refused.")
    rate = _rate(request.model)
    worst_in = max(rate["input_tokens"], rate["cache_read_tokens"], rate["cache_create_tokens"])
    fees = server_tool_allowance(request)[1]
    return round((inputs * worst_in + int(request.max_tokens) * rate["output_tokens"]) / 1e6 + fees, 6)


def bound(request: LLMRequest, current: str) -> int | float:
    """The hold for one send in the mode's unit."""
    out = int(request.max_tokens)
    if out < 0:
        raise SendRefused("unbounded_output", "This request has no output limit.")
    if current == "output_tokens":
        return out
    if current == "total_tokens":
        inputs = input_bound(request)
        if inputs is None:
            raise SendRefused("unbounded_input", "This request's input has no upper bound; the send is refused.")
        return inputs + out
    return _eur_bound(request)


def _counts(usage: Any) -> dict[str, int] | None:
    """The provider's four usage counts, absent keys as 0 (protocol). None
    for no usage at all or an invalid count."""
    from harness.runner import USAGE_KEYS
    if not isinstance(usage, dict) or not any(usage.get(k) for k in USAGE_KEYS):
        return None
    counts = {}
    for key in USAGE_KEYS:
        value = usage.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        counts[key] = value
    return counts


def _actual(counts: dict[str, int], current: str, request: LLMRequest) -> int | float:
    """The send's spend from the provider's usage. Token usage does not say
    how many server-tool uses ran, so money counts their fees at `max_uses`."""
    if current == "output_tokens":
        return counts["output_tokens"]
    if current == "total_tokens":
        return sum(counts.values())
    rate = _rate(request.model)
    fees = (server_tool_allowance(request) or (0, 0.0))[1]
    return round(sum(counts[k] * rate[k] for k in counts) / 1e6 + fees, 6)


def _eur_actual(counts: dict[str, int] | None, request: LLMRequest) -> float | None:
    if counts is None:
        return None
    try:
        return _actual(counts, "money_eur", request)
    except SendRefused:
        return None


# ── The ledger ───────────────────────────────────────────────────────────────


def _ledger(run: dict) -> dict:
    from services import durable_conversation
    return durable_conversation._budgets(run)


def _rows(run: dict) -> list[dict]:
    budgets = _ledger(run)
    return list(budgets.get("final_charges", [])) + list(budgets.get("reservations", []))


def _spent(run: dict, unit: str) -> float:
    return sum(float(r.get("amount") or 0) for r in _rows(run) if r.get("unit") == unit)


def _research_spent(run: dict) -> float:
    return sum(float(r.get("eur") or 0) for r in _rows(run) if r.get("kind") == KINDS["research"])


def cap(task: dict, current: str) -> float | None:
    """The run's cap in the mode's unit; None when the run sets none."""
    from harness import budget, runner
    limit = runner._budget(task)["limit"]
    if current == "money_eur":
        return float(limit["cost_eur"])
    if current == "output_tokens":
        return float(limit.get("output_tokens", budget.MAX_OUTPUT_TOKENS_PER_SESSION))
    value = limit.get("total_tokens")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def research_cap(path) -> float:
    """SH-RESEARCH-CAP-1, the one research enforcement: the envelope's
    `research.cost_cap_eur` (as `run_envelope._check_research` validates it),
    EUR 2 when there is none, never above EUR 10."""
    from services import run_envelope
    try:
        value = ((run_envelope.read(path) or {}).get("research") or {}).get("cost_cap_eur")
    except Exception:  # noqa: BLE001 — an unreadable envelope keeps the default
        value = None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value:
        return run_envelope.RESEARCH_DEFAULT_EUR
    return min(float(value), run_envelope.RESEARCH_CAP_EUR)


def digest(request: LLMRequest) -> str:
    body = json.dumps([request.model, request.max_tokens, request.system_blocks, request.messages, request.tools],
                      sort_keys=True, ensure_ascii=False, default=repr)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def new_key(task_id: str) -> str:
    """A fresh key for one send: two sends never share a hold, even with
    identical requests."""
    return f"run:{task_id}:{uuid.uuid4().hex[:16]}"


def admit(path, request: LLMRequest, *, boot: str, scope: str = "run", key: str | None = None) -> str:
    """Reserve one send's hold before it goes out, or raise SendRefused.
    `key` is the hold of an earlier attempt of this same send (a retry): if
    it is still open, nothing more is reserved. A refusal for the cap ends
    the run `needs_user` with reason `budget`, the same stop as issue 02's
    cost cap. Returns the hold's key.

    Research is enforced whatever the mode (GK B (b)): with the mode off, a
    research hold is in EUR."""
    from harness import runner
    current = mode()
    if current == "invalid":
        raise SendRefused("invalid_mode", f"{MODE_ENV} is not one of {', '.join(MODES)}; sends are refused.")
    if scope not in KINDS:
        raise ValueError(f"unknown reservation scope {scope!r}")
    if current == "off":
        if scope != "research":
            raise ValueError("no run-scope reservation while the mode is off")
        current = "money_eur"
    unit = UNITS[current]
    amount = bound(request, current)
    eur = _eur_bound(request) if scope == "research" else None
    research_limit = research_cap(path) if scope == "research" else None
    out: dict[str, Any] = {}

    def change(task):
        run = task["run"]
        if key is not None and any(r.get("key") == key for r in _ledger(run).get("reservations", [])):
            out["key"] = key
            return False  # a retry of the same send reserves once
        out["key"] = new_key(task["task_id"])
        limit = cap(task, current)
        if limit is None:
            out["refused"] = ("no_cap", f"The run sets no {unit} cap; the send is refused.")
        elif _spent(run, unit) + amount > limit:
            out["refused"] = ("budget", "The run's model budget is spent; the send is refused.")
        elif eur is not None and _research_spent(run) + eur > research_limit:
            out["refused"] = ("research_budget", "The research budget is spent; research stops.")
        if out.get("refused"):
            if out["refused"][0] == "research_budget":
                return False  # research stops; the stage goes on without it (§6.5)
            if run["status"] in runner._DRIVEN:
                runner._stop_in(task, "needs_user", "budget")
            return None
        from services import durable_conversation
        durable_conversation._reserve_in(run, key=out["key"], kind=KINDS[scope], amount=amount, unit=unit)
        hold = next(r for r in _ledger(run)["reservations"] if r["key"] == out["key"])
        hold["dispatch"] = {"boot": boot, "model": request.model, "at": time.time()}
        if eur is not None:
            hold["eur"] = eur
        return None

    if runner._update(path, change) is None:
        raise SendRefused("not_a_run", "This task is not a harness run.")
    if out.get("refused"):
        raise SendRefused(*out["refused"])
    return out["key"]


def _settle_row(run: dict, hold: dict, amount, flags: list[str] | None, eur=None) -> None:
    from services import durable_conversation
    durable_conversation._settle_in(run, key=hold["key"], amount=amount, unit=hold["unit"], flags=flags)
    charge = next(c for c in reversed(_ledger(run)["final_charges"]) if c["key"] == hold["key"])
    if "eur" in hold:
        charge["eur"] = hold["eur"] if eur is None else eur


def settle(path, key: str, request: LLMRequest, usage: Any) -> None:
    """Settle one send to the provider's usage; no usage settles at the
    hold's bound (flag `no_usage`), never zero."""
    from harness import runner
    counts = _counts(usage)

    def change(task):
        run = task["run"]
        hold = next((r for r in _ledger(run).get("reservations", []) if r.get("key") == key), None)
        if hold is None:
            return False  # already reconciled: it counted at its bound
        unit = hold["unit"]
        current = next((m for m, u in UNITS.items() if u == unit), None)
        amount, flags = hold["amount"], ["no_usage"]
        if counts is not None and current is not None:
            try:
                amount, flags = _actual(counts, current, request), None
            except SendRefused:
                pass
        _settle_row(run, hold, amount, flags, eur=_eur_actual(counts, request) if counts else None)
        return None

    try:
        runner._update(path, change)
    except HTTPException:
        logger.exception("send reservation: settling %s failed; the hold stays and counts", key)


def _settle_uncertain(task: dict, which) -> int:
    run = task["run"]
    holds = [r for r in _ledger(run).get("reservations", []) if which(r)]
    for hold in holds:
        _settle_row(run, hold, hold["amount"], ["uncertain"])
    return len(holds)


def close_open(path, *, boot: str) -> int:
    """Turn end: a hold this boot left open (a send that errored or was
    abandoned) settles `uncertain` at its bound."""
    from harness import runner
    out = {"n": 0}

    def change(task):
        out["n"] = _settle_uncertain(task, lambda r: (r.get("dispatch") or {}).get("boot") == boot)
        return None if out["n"] else False
    runner._update(path, change)
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
        out["n"] = _settle_uncertain(task, lambda r: (r.get("dispatch") or {}).get("boot") != boot)
        if not out["n"]:
            return False
        if run["status"] in runner._DRIVEN:
            runner._stop_in(task, "needs_user", "uncertain")
        return None
    runner._update(path, change)
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
              if c.get("unit") == unit and set(c.get("flags") or ()) & {"uncertain", "no_usage"}]
    return {"unit": unit, "spent": round(_spent(run, unit), 6),
            "open": round(sum(float(r.get("amount") or 0) for r in held), 6),
            "at_bound": len(bounds)}


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


class ReservingProvider:
    """Wraps a provider: every `stream(request)` reserves first and settles
    after. Only a harness run's own sends go through it."""

    def __init__(self, inner, path, *, boot: str, scope: str = "run") -> None:
        self._inner, self._path, self._boot, self._scope = inner, path, boot, scope
        self.name = getattr(inner, "name", "reserving")
        # The open send of each request object: the loop retries by streaming
        # the same request again, and that attempt reuses the send's key.
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
        key = admit(self._path, request, boot=self._boot, scope=self._scope, key=self._retry_key(request))
        self._remember(request, key)
        usage = None
        for event in self._inner.stream(request):
            if event.type == "message_done":
                usage = event.usage
            yield event
        settle(self._path, key, request, usage)
        self._remember(request, None)
