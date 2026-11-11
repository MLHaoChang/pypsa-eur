"""Generate the profile-backed workflow files (study harness issue 17).

`harness/workflows/sources/<id>.md` holds the authored half: the menu text,
the steps and their prose. Each step names the profile stage it belongs to
(`stage:`); this script adds what the profile owns, so chat guidance and
harness runs cannot drift:

* `done_when` gains the ids of the gate rules the stage enforces;
* the step body gains the tools the profile's stage actions call.

The output is `harness/workflows/<id>.md`, which the registry loads as it
loads any workflow. Edit the source or the profile, then run
`python tools/generate_workflows.py`; `tests/test_workflows_from_profiles.py`
fails while a checked-in file differs from the generated one.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from services import run_profiles  # noqa: E402

WORKFLOWS = _BACKEND / "harness" / "workflows"
SOURCES = WORKFLOWS / "sources"
_FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_STEP = re.compile(r"^(## Step: ([a-z0-9-]+)\s*\n)", re.M)


def _stage_facts(profile: dict, stage: str) -> tuple[list[str], list[str]]:
    spec = profile["stages"][stage]
    rules = [r["id"] for r in spec.get("rules", [])]
    tools = list(dict.fromkeys(a["tool"] for a in spec.get("actions", [])))
    return rules, tools


def generate(source: Path) -> str:
    m = _FRONT.match(source.read_text(encoding="utf-8"))
    if not m:
        raise ValueError(f"{source.name}: missing front matter")
    meta, body = yaml.safe_load(m.group(1)), m.group(2)
    profile = run_profiles.resolve(meta.pop("profile"))
    extra: dict[str, str] = {}
    for step in meta["steps"]:
        stage = step.pop("stage", None)
        if stage is None:
            continue
        rules, tools = _stage_facts(profile, stage)
        if rules:
            step["done_when"] = (
                f"{step['done_when']} Gate rules ({profile['id']} {stage}): "
                + ", ".join(rules)
                + "."
            )
        if tools:
            extra[step["id"]] = (
                "Harness tools for this stage (profile "
                f"{profile['id']}, {stage}): "
                + ", ".join(f"`{t}`" for t in tools)
                + "."
            )
    parts = _STEP.split(body)
    out = [parts[0]]
    for i in range(1, len(parts), 3):
        text = parts[i + 2].rstrip("\n") + "\n"
        if parts[i + 1] in extra:
            text += "\n" + extra[parts[i + 1]] + "\n"
        out += [parts[i], text + ("\n" if i + 3 < len(parts) else "")]
    front = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=10_000)
    return f"---\n{front}---\n" + "".join(out)


def sources() -> list[Path]:
    return sorted(SOURCES.glob("*.md"))


def main() -> None:
    for src in sources():
        (WORKFLOWS / src.name).write_text(generate(src), encoding="utf-8")
        print("wrote", src.name)


if __name__ == "__main__":
    main()
