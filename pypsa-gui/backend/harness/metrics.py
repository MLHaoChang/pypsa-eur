"""
Process-wide chat metrics (counters, latency percentiles, token totals)
behind `GET /api/chat/metrics`.

Moved from harness/loop.py (the former services/chat_service.py) on
2026-10-05, chat harness issue 08, by AST selection of whole top-level
nodes; the loop re-imports every name, so `chat_service.<name>` is the same
object. A tunable here is patched on THIS module (see harness/README.md,
"Splitting the loop").
"""
from __future__ import annotations

from typing import Any
import collections
import threading


# ── Observability counters (#20) ──────────────────────────────────────────
# Module-global metrics, mutated from the SSE worker thread (run_turn) AND read
# from the /metrics request thread, so EVERY read/write goes under _METRICS_LOCK.
# turn_durations is a bounded deque (p50/p95 computed in _metrics_snapshot);
# errors_by_kind counts TURN-TERMINAL error_kinds only (not the ~10 tool_error
# spots — those stay out of scope to avoid touching every emit site).
_METRICS: dict[str, Any] = {
    "turns": 0,
    "retries": 0,
    "errors_by_kind": collections.Counter(),
    "turn_durations": collections.deque(maxlen=1000),  # seconds
    "cumulative_tokens": {"input": 0, "output": 0},
}


_METRICS_LOCK = threading.Lock()


def _metric_incr(key: str, n: int = 1) -> None:
    """Bump an int counter (`turns` / `retries`) under _METRICS_LOCK."""
    with _METRICS_LOCK:
        _METRICS[key] = int(_METRICS.get(key, 0)) + n


def _metric_error(kind: str) -> None:
    """Record one TURN-TERMINAL error_kind in the errors_by_kind Counter."""
    with _METRICS_LOCK:
        _METRICS["errors_by_kind"][kind] += 1


def _metric_record_duration(seconds: float) -> None:
    """Append one turn wall-duration (seconds) to the bounded durations deque."""
    with _METRICS_LOCK:
        _METRICS["turn_durations"].append(float(seconds))


def _metric_add_tokens(input_tokens: int, output_tokens: int) -> None:
    """Accrue cumulative token totals across all turns (process-lifetime)."""
    with _METRICS_LOCK:
        _METRICS["cumulative_tokens"]["input"] += int(input_tokens or 0)
        _METRICS["cumulative_tokens"]["output"] += int(output_tokens or 0)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """
    Nearest-rank percentile (q in [0, 1]) over a pre-sorted list — no numpy.
    Returns 0.0 on an empty list.
    """
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _metrics_snapshot() -> dict[str, Any]:
    """
    Return a JSON-serialisable snapshot of the chat metrics for GET /metrics.
    Computes p50/p95 turn latency in MILLISECONDS from the durations deque.
    Reads under _METRICS_LOCK so a concurrent run_turn write can't tear it.
    """
    with _METRICS_LOCK:
        durations = sorted(_METRICS["turn_durations"])
        return {
            "turns": _METRICS["turns"],
            "retries": _METRICS["retries"],
            "errors_by_kind": dict(_METRICS["errors_by_kind"]),
            "samples": len(durations),
            "p50_ms": round(_percentile(durations, 0.50) * 1000.0, 2),
            "p95_ms": round(_percentile(durations, 0.95) * 1000.0, 2),
            "cumulative_tokens": dict(_METRICS["cumulative_tokens"]),
        }


def _reset_metrics_for_tests() -> None:
    """Test-only — zero the metrics so turn counts can't bleed across tests."""
    with _METRICS_LOCK:
        _METRICS["turns"] = 0
        _METRICS["retries"] = 0
        _METRICS["errors_by_kind"] = collections.Counter()
        _METRICS["turn_durations"] = collections.deque(maxlen=1000)
        _METRICS["cumulative_tokens"] = {"input": 0, "output": 0}
