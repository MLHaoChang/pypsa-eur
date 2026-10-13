"""
In-app guides (plan 2026-09-26 P21/P22): ONE catalogue per topic under
``backend/data/guides/``, served to the GUI's guided tours and read by the
chat assistant's ``get_feature_guide`` — so the tour and the assistant
cannot describe a field differently.
"""
from __future__ import annotations

import json
import pathlib
from functools import lru_cache

GUIDE_DIR: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[1] / "data" / "guides")
# topic id → file. An allow-list: the route never builds a path from input.
GUIDE_FILES: dict[str, str] = {"eh_fmea": "eh_fmea_guide.json"}


class GuideError(ValueError):
    pass


@lru_cache(maxsize=None)
def load_guide(topic: str) -> dict:
    """The validated catalogue for ``topic`` (cached; files ship read-only)."""
    fname = GUIDE_FILES.get(topic)
    if fname is None:
        raise GuideError(
            f"unknown guide {topic!r}; available: {sorted(GUIDE_FILES)}")
    raw = json.loads((GUIDE_DIR / fname).read_text(encoding="utf-8"))
    tours = raw.get("tours")
    if not isinstance(tours, dict) or not tours:
        raise GuideError(f"guide {topic!r} has no tours")
    for tid, tour in tours.items():
        steps = tour.get("steps") or []
        if not steps:
            raise GuideError(f"tour {tid!r} has no steps")
        for i, st in enumerate(steps):
            for key in ("target", "title", "body"):
                if not isinstance(st.get(key), str) or not st[key].strip():
                    raise GuideError(f"tour {tid!r} step {i + 1} lacks {key!r}")
    if not isinstance(raw.get("fields"), dict):
        raise GuideError(f"guide {topic!r} has no fields map")
    return raw


def guide_topics() -> list[str]:
    return sorted(GUIDE_FILES)
