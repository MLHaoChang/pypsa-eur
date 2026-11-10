"""The Memory UI shows concept keys as plain words (UX M2). Its word maps in
`frontend/src/api/memory.ts` cover every key the backend registries accept,
with the backend's own labels for result views."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api" / "memory.ts"


def _map(name: str) -> dict[str, str]:
    text = SRC.read_text(encoding="utf-8")
    m = re.search(
        rf"export const {name}: Record<string, string> = \{{(.*?)\n\}}", text, re.S
    )
    assert m, f"{name} not found in {SRC.name}"
    return dict(re.findall(r"(\w+): '([^']*)'", m.group(1)))


def test_view_words_are_the_result_view_labels():
    from services.result_views import RESULT_VIEWS

    assert _map("VIEW_WORDS") == {v.view_id: v.label for v in RESULT_VIEWS}


def test_every_topic_has_words():
    from services.memory_store import TOPICS

    assert set(_map("TOPIC_WORDS")) == set(TOPICS)
