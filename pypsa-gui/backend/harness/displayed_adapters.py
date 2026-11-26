"""Displayed-data adapters (assistant-assurance 06, A5.2).

A results tab can prove the data it displayed only through an adapter here.
Each adapter names how the server reads the tab's default view at issue time,
how it reads the same view again from a browser's source binding at ACK time,
the exact keys that binding carries, and which chat read names the same facts
(so a turn says "showing" only when the shown data is the data it read).

A tab with no adapter ACKs as `browser_mounted_view`: the tab mounted, not
which numbers it rendered. `result_views.ResultView.verification` must say
`displayed_data` exactly for the tabs listed here; `check_registry` is the
guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class DisplayedAdapter:
    """How one results tab proves the data it displayed."""

    # The tab's default view as the browser fetches it: {"status", "binding", ...}.
    current: Callable[[], dict]
    # The same view again, read from the binding the browser echoed.
    reread: Callable[[dict], dict]
    # The exact keys of the v2 `source_binding` this tab renders.
    binding_keys: frozenset[str]
    # The chat read that names the same facts, and the facts compared.
    read_tool: str
    ref_keys: tuple[str, ...]
    read_ref: Callable[[Any], dict | None]


# ── Load flow (live-voice 07, LC-03) ────────────────────────────────────────

def _loadflow_current():
    from services.loadflow_summary import get_loadflow_view

    return get_loadflow_view()


def _loadflow_reread(binding):
    from services.loadflow_summary import get_loadflow_view

    return get_loadflow_view(binding["source"], binding["window_from"], binding["window_to"])


_LOADFLOW_REF = ("source", "window_from", "window_to")


def _loadflow_read_ref(result):
    if isinstance(result, dict) and result.get("status") == "ok":
        ref = result.get("ui_reference") or {}
        return {k: ref.get(k) for k in _LOADFLOW_REF}
    return None


ADAPTERS: Mapping[str, DisplayedAdapter] = MappingProxyType({
    "loadflow": DisplayedAdapter(
        current=_loadflow_current,
        reread=_loadflow_reread,
        binding_keys=frozenset({"version", "project_id", "project_name", "scope", "source",
                                "input_sha256", "data_sha256", "window_from", "window_to"}),
        read_tool="get_loadflow_summary",
        ref_keys=_LOADFLOW_REF,
        read_ref=_loadflow_read_ref,
    ),
})


def for_target(target) -> DisplayedAdapter | None:
    """The adapter for exactly a results-tab target, else None."""
    if (isinstance(target, dict) and target.get("panel_id") == "results"
            and set(target) <= {"panel_id", "results_tab", "highlight"}):
        return ADAPTERS.get(target.get("results_tab"))
    return None


def valid_binding(adapter: DisplayedAdapter, binding) -> bool:
    """A browser-echoed binding has exactly the adapter's keys and plain types:
    version 1, window bounds non-negative ints, every other key a string."""
    if not isinstance(binding, dict) or set(binding) != adapter.binding_keys:
        return False
    if type(binding.get("version")) is not int or binding["version"] != 1:
        return False
    for key in adapter.binding_keys - {"version"}:
        value = binding[key]
        if key.startswith("window_"):
            if type(value) is not int or value < 0:
                return False
        elif not isinstance(value, str):
            return False
    return True


def check_registry(views, adapters: Mapping[str, DisplayedAdapter] = ADAPTERS) -> None:
    """Raise when a view claims displayed data without an adapter, or the reverse."""
    claimed = {v.tab for v in views if v.verification == "displayed_data"}
    if claimed != set(adapters):
        raise AssertionError(
            f"displayed_data views {sorted(claimed)} != adapters {sorted(adapters)}")
