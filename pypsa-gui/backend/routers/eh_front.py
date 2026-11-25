"""GET /api/results/eh_front: the Energy Hub study's front (DC review 01).

The study's opt-in ``front`` stage (``services/adequacy/eh_study._stage_front``)
writes the ``eh_front`` sibling table. This serves it beside
``/api/results/eh_study`` in GET /api/results/front's record shape, so the
results panel reads both with one type. A router of its own, mounted at
/api/results like the reserves and operational-gap readers.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/eh_front")
def get_eh_front():
    """``status``, ``result`` (the engine's points), ``edited_since_study``
    read off the EH study's own record. 204 when the last study ran no front
    stage (or is running: a new study clears the table). Its points were
    solved on the study's private, pack-applied copy, so ``applicable`` is
    False: the apply route reads only GET /front's study."""
    from routers.simulation import _state
    from services import study_state

    result = _state.get("eh_front")
    if not result:
        return Response(status_code=204)
    study = _state.get("eh_study") or {}
    return {
        "source": "eh_study",
        "status": "aborted" if result.get("aborted") else "done",
        "result": result,
        "error": None,
        "finished_at": study.get("finished_at"),
        "edited_since_study": study_state.edited_since(study),
        "applicable": False,
    }
