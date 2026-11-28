"""
Explanation: explain_investment: dispatchers.

Moved out of `services/chat_tools.py` (RETRO-FRAG-1), function bodies
unchanged. Schemas and routes: `harness/catalogue_tools/explanation.py`.

The chat_tools helpers stay in chat_tools and are reached through the
proxies below, at call time: chat_tools imports this module part-way through
its own import, and tests that patch `chat_tools.<helper>` still apply.
The helpers and tables only this family uses moved here with it; chat_tools
re-exports them, which is a binding, so a later monkeypatch of
`chat_tools.<moved name>` does not reach this module: patch it here.

"""

from __future__ import annotations

from fastapi import HTTPException

from services.pypsa_service import PyPSAService
from services.study import explain as _explain


def _ct():
    from services import chat_tools

    return chat_tools


def get_results(*args, **kwargs):
    return _ct().get_results(*args, **kwargs)


def get_asset_results(*args, **kwargs):
    return _ct().get_asset_results(*args, **kwargs)


def _explain_kpis(component_class: str, name: str) -> dict:
    return get_asset_results(component_class, name, category="summary")


def explain_investment(component_class: str, name: str) -> dict:
    """
    Assemble the evidence behind one sizing decision: what the LP built, WHICH
    CONSTRAINT stopped it there, what the asset earned, and the system signals
    it was priced against (`services/study/explain.py::explain_asset`, on the
    active network).
    """
    try:
        return _explain.explain_asset(
            PyPSAService.get_network(),
            component_class,
            name,
            results=get_results,
            asset_kpis=_explain_kpis,
        )
    except _explain.ExplainError as exc:
        raise HTTPException(exc.status, exc.message) from None


DISPATCHERS = {
    "explain_investment": explain_investment,
}
