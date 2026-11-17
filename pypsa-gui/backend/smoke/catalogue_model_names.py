"""
Write or check `frontend/src/catalogue/modelNames.generated.json` (spec expert-catalogue §7.2).

The file maps each Component class's model names (raw columns such as
`p_nom`) to their catalogue plain names. The frontend raw-name guard (slice
02) reads it; nothing edits it by hand. `--write` regenerates it from the
Parameter catalogue; `--check` (the default) exits 1 when it is stale.
`tests/test_catalogue_model_names.py` runs the check.
"""

from __future__ import annotations

import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
TARGET = BACKEND.parent / "frontend" / "src" / "catalogue" / "modelNames.generated.json"


def render() -> str:
    sys.path.insert(0, str(BACKEND))
    from services.asset_schema.catalogue import model_names

    names = {scope: dict(sorted(cols.items())) for scope, cols in sorted(model_names().items())}
    return json.dumps(names, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--write" in argv:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(text, encoding="utf-8")
        return 0
    if not TARGET.is_file() or TARGET.read_text("utf-8") != text:
        print(f"{TARGET.name} is stale: run python smoke/catalogue_model_names.py --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
