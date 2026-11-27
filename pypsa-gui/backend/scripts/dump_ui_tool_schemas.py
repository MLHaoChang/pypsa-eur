# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
# SPDX-License-Identifier: MIT
"""Write the input schemas of the assistant's navigation tools to a JSON file.

The result-navigation browser journey (frontend/scripts/smoke-result-navigation.mjs)
builds every mocked ``ui_event`` frame from this file, so a renamed tool or
argument fails the journey. CI generates it from the same checkout; it is never
committed.

    python pypsa-gui/backend/scripts/dump_ui_tool_schemas.py <out.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.catalogue import TOOLS  # noqa: E402

NAMES = ("ui_open_panel", "discuss_project_run")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: dump_ui_tool_schemas.py <out.json>", file=sys.stderr)
        return 2
    by_name = {tool["name"]: tool for tool in TOOLS}
    missing = [name for name in NAMES if name not in by_name]
    if missing:
        print(f"tools not in catalogue.TOOLS: {', '.join(missing)}", file=sys.stderr)
        return 1
    out = {name: {"input_schema": by_name[name]["input_schema"]} for name in NAMES}
    Path(argv[1]).write_text(
        json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
