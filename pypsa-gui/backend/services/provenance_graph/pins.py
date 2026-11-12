"""
Pins: the person-facing checks of slice 06 (campus study, site bindings, assistant brief), where a producer
recorded a value when it made a result (a hash, a uid, a fingerprint) and "current" is one read of the live input.

A check builds a `derived` node for what it vouches for, with one `derived_from` edge per input carrying a `Pin`.
The comparison is the producer's own read (`Pin.read`); the graph only compares it with what was recorded. A read
that raises, or one that returns `UNREADABLE`, breaks the edge: unknown is not current (ADR-0001).
A check with nothing recorded builds no pin and stays what it was (no run, nothing to compare).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from services.provenance_graph.types import Broken, Edge

#: What a `read` returns, or a raise stands for, when the current value cannot be read.
UNREADABLE = object()


@dataclass(frozen=True)
class Pin:
    """One recorded value of an input and how to read it now. `recorded` is compared with `read()` by `==`."""

    label: str
    recorded: Any
    read: Callable[[], Any]


def compare_pin(g, edge: Edge) -> Broken | None:
    pin: Pin = edge.recorded
    try:
        current = pin.read()
    except Exception as exc:  # noqa: BLE001 - an unreadable input is stale, never fresh
        return Broken(
            edge.kind,
            edge.src,
            edge.dst,
            pin.label,
            pin.recorded,
            None,
            None,
            f"cannot be read now: {exc}",
        )
    if current is UNREADABLE:
        return Broken(
            edge.kind,
            edge.src,
            edge.dst,
            pin.label,
            pin.recorded,
            None,
            None,
            "cannot be read now",
        )
    if current == pin.recorded:
        return None
    return Broken(
        edge.kind,
        edge.src,
        edge.dst,
        pin.label,
        pin.recorded,
        pin.label,
        current,
        "changed",
    )
