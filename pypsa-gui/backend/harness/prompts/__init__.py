"""
The system-prompt fragments, as Markdown (chat harness issue 02).

Each `*.md` in this folder is one fragment: front matter (`constant`, the
name `chat_service` binds it to; `trailing_space`, the sections that end
with a space so FACTS + CHAINING concatenate without a join), a note for
humans, then one `## <section>` per text. Sections are `facts` / `chaining`
(the tools-on / tools-off split of 2026-08, "Task 8"), `full` where the
pre-split literal is not the concatenation of the halves, or `text` for a
single fragment.

The loader REFLOWS a section — newlines become spaces, runs of spaces
collapse — so the files can be wrapped for reading while the strings stay
byte-identical to the constants they replaced. That identity is the gate:
`tests/test_harness_prompts.py` pins every fragment's sha256, and
`test_chat_profile_binding.test_default_prompt_bytes_unchanged` pins the
assembled default. The doctrine the fragments follow is in README.md here.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

_DIR = Path(__file__).resolve().parent
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_SECTION = re.compile(r"^## ([a-z_]+)\s*$", re.M)


class PromptError(ValueError):
    """A fragment file is malformed. Raised at import of chat_service, so a
    test — not a user — sees it."""


def reflow(text: str) -> str:
    """One line, single spaces, no surrounding whitespace."""
    return " ".join(text.split())


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, str]:
    """The sections of `<name>.md`, reflowed, with the declared trailing
    space re-applied. Cached: the strings are module constants downstream."""
    path = _DIR / f"{name}.md"
    if not path.is_file():
        raise PromptError(f"no prompt fragment {name!r} at {path}")
    m = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
    if not m:
        raise PromptError(f"{path.name}: missing front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    trailing = set(meta.get("trailing_space") or [])
    parts = _SECTION.split(m.group(2))
    sections: dict[str, str] = {}
    for i in range(1, len(parts), 2):
        key, body = parts[i], reflow(parts[i + 1])
        if not body:
            raise PromptError(f"{path.name}: section {key!r} is empty")
        sections[key] = body + (" " if key in trailing else "")
    unknown = trailing - set(sections)
    if unknown:
        raise PromptError(f"{path.name}: trailing_space names unknown sections {sorted(unknown)}")
    if not sections:
        raise PromptError(f"{path.name}: no '## <section>' headings")
    return sections


def names() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.md") if p.name.upper() != "README.MD")
