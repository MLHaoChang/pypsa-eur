"""The workbook route and the Export tab give the same cause for "no run" (UX ruling ah).

The route says the cause ("Not run yet: nothing has been solved."); the Export tab words what to
do per mode (Guided: run the study; Expert: click Run LOPF) after the same "Not run yet" lead.
"""
import re
from pathlib import Path

from routers.results import WORKBOOK_NO_RUN_MESSAGE

PICKER = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "layout" / "sidebar"
          / "ResultsWorkbookPicker.tsx")


def test_the_picker_and_the_route_share_the_cause():
    m = re.search(r"\bexport\s+const\s+WORKBOOK_NO_RUN\s*=\s*(['\"])(.*?)\1", PICKER.read_text(encoding="utf-8"))
    assert m, "WORKBOOK_NO_RUN not found in ResultsWorkbookPicker.tsx"
    assert WORKBOOK_NO_RUN_MESSAGE.startswith(m.group(2) + ":")
