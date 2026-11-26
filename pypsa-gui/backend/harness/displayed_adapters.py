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
from typing import Any
from collections.abc import Callable, Mapping


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
    # Binding keys holding the int index the view was called with, or null for
    # the route's default (S3). A window must not start after it ends.
    index_keys: frozenset[str] = frozenset()


# ── Load flow (live-voice 07, LC-03) ────────────────────────────────────────


def _loadflow_current():
    from services.loadflow_summary import get_loadflow_view

    return get_loadflow_view()


def _loadflow_reread(binding):
    from services.loadflow_summary import get_loadflow_view

    return get_loadflow_view(
        binding["source"], binding["window_from"], binding["window_to"]
    )


_LOADFLOW_REF = ("source", "window_from", "window_to")


def _loadflow_read_ref(result):
    if isinstance(result, dict) and result.get("status") == "ok":
        ref = result.get("ui_reference") or {}
        return {k: ref.get(k) for k in _LOADFLOW_REF}
    return None


ADAPTERS: Mapping[str, DisplayedAdapter] = MappingProxyType(
    {
        "loadflow": DisplayedAdapter(
            current=_loadflow_current,
            reread=_loadflow_reread,
            binding_keys=frozenset(
                {
                    "version",
                    "project_id",
                    "project_name",
                    "scope",
                    "source",
                    "input_sha256",
                    "data_sha256",
                    "window_from",
                    "window_to",
                }
            ),
            read_tool="get_loadflow_summary",
            ref_keys=_LOADFLOW_REF,
            read_ref=_loadflow_read_ref,
        ),
    }
)


# ── Single-payload tabs (A5.2 S2): routers/results_displayed.py ─────────────

_VIEW_BINDING = frozenset(
    {
        "version",
        "project_id",
        "project_name",
        "scope",
        "view",
        "input_sha256",
        "data_sha256",
    }
)


def _view(tab):
    def read(_binding=None):
        from routers import results_displayed

        return results_displayed.VIEWS[tab]()

    return read


# What services.chat_tools._with_freshness adds to a results read.
_FRESHNESS_KEYS = frozenset(
    {"inputs_changed_since_solve", "results_current", "freshness_note"}
)


def _payload_ref(is_payload, key="data_sha256"):
    """A chat read of the same payload: its hash in the view's encoding, so the
    words say "showing" only when the tab shows exactly what the turn read."""

    def ref(result):
        if not is_payload(result):
            return None
        import hashlib

        from routers.results_displayed import canonical

        # get_results adds the LC-12 freshness words to the route's payload.
        payload = {k: v for k, v in result.items() if k not in _FRESHNESS_KEYS}
        return {key: hashlib.sha256(canonical(payload).encode()).hexdigest()}

    return ref


def _is_emissions(result):
    return isinstance(result, dict) and {"total_tCO2", "by_carrier"} <= set(result)


def _is_grid_risk(result):
    return (
        isinstance(result, dict)
        and result.get("available") is True
        and "omitted" in result
        and "project_id" in result
    )


ADAPTERS = MappingProxyType(
    {
        **ADAPTERS,
        "emissions": DisplayedAdapter(
            current=_view("emissions"),
            reread=_view("emissions"),
            binding_keys=_VIEW_BINDING,
            read_tool="get_results",
            ref_keys=("data_sha256",),
            read_ref=_payload_ref(_is_emissions),
        ),
        "grid_risk": DisplayedAdapter(
            current=_view("grid_risk"),
            reread=_view("grid_risk"),
            binding_keys=_VIEW_BINDING,
            read_tool="get_grid_risk_production",
            ref_keys=("data_sha256",),
            read_ref=_payload_ref(_is_grid_risk),
        ),
    }
)


# ── Windowed and multi-payload tabs (A5.2 S3a) ──────────────────────────────


def _params_view(tab, keys):
    """Issue time reads the tab's default view (every index null); the ACK
    re-reads with exactly the indexes the binding recorded."""

    def read(binding=None):
        from routers import results_displayed

        args = {} if binding is None else {k: binding[k] for k in keys}
        return results_displayed.VIEWS[tab](**args)

    return read


def _no_read_ref(_result):
    """The chat read is a bounded summary of the route payload, not the payload,
    so its facts cannot be matched to the displayed hash: the words say "opened"."""
    return None


def _is_curtailment(result):
    """A per-snapshot results payload (curtailment's shape). Another series of
    the same shape hashes differently, so it can only make the words "opened"."""
    return isinstance(result, dict) and {"index", "columns", "data"} <= set(result)


_WINDOW = ("window_from", "window_to")
_S3A = {
    "flows": (("period", *_WINDOW), "get_energy_flows", ("data_sha256",), _no_read_ref),
    "loadshape": (
        ("period", *_WINDOW, "week"),
        "get_load_shape",
        ("data_sha256",),
        _no_read_ref,
    ),
    "curtailment": (
        _WINDOW,
        "get_results",
        ("read_sha256",),
        _payload_ref(_is_curtailment, "read_sha256"),
    ),
}

ADAPTERS = MappingProxyType(
    {
        **ADAPTERS,
        **{
            tab: DisplayedAdapter(
                current=_params_view(tab, keys),
                reread=_params_view(tab, keys),
                binding_keys=_VIEW_BINDING
                | frozenset(keys)
                | frozenset(ref for ref in refs if ref != "data_sha256"),
                read_tool=read_tool,
                ref_keys=refs,
                read_ref=read_ref,
                index_keys=frozenset(keys),
            )
            for tab, (keys, read_tool, refs, read_ref) in _S3A.items()
        },
    }
)


def for_target(target) -> DisplayedAdapter | None:
    """The adapter for exactly a results-tab target, else None."""
    if (
        isinstance(target, dict)
        and target.get("panel_id") == "results"
        and set(target) <= {"panel_id", "results_tab", "highlight"}
        and isinstance(target.get("results_tab"), str)
    ):
        return ADAPTERS.get(target["results_tab"])
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
        if key in adapter.index_keys:
            if value is not None and (type(value) is not int or value < 0):
                return False
        elif key.startswith("window_"):
            if type(value) is not int or value < 0:
                return False
        elif not isinstance(value, str):
            return False
    start, end = binding.get("window_from"), binding.get("window_to")
    if adapter.index_keys and start is not None and end is not None and start > end:
        return False
    return True


def check_registry(views, adapters: Mapping[str, DisplayedAdapter] = ADAPTERS) -> None:
    """Raise when a view claims displayed data without an adapter, or the reverse."""
    claimed = {v.tab for v in views if v.verification == "displayed_data"}
    if claimed != set(adapters):
        raise AssertionError(
            f"displayed_data views {sorted(claimed)} != adapters {sorted(adapters)}"
        )
