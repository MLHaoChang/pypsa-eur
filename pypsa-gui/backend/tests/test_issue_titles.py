"""Every preflight code the validator can raise has a plain title (UX assessment
Q8). Reads the validator source, so a new code without a title fails here
instead of shipping as a raw identifier. Ported from the frontend's
``issueTitles.test.ts`` when the titles moved to the server."""
import pathlib
import re

from services.issue_titles import ISSUE_TITLES, issue_title
from services.validation_service import Issue

SRC = pathlib.Path(__file__).parent.parent / "services" / "validation_service.py"


def _backend_codes() -> list[str]:
    text = SRC.read_text(encoding="utf8")
    return sorted(set(re.findall(r"_(?:err|warn|info)\(\s*\"([a-z][a-z0-9_]+)\"", text)))


def test_the_code_scan_finds_the_codes():
    assert len(_backend_codes()) > 50


def test_every_code_has_a_title():
    assert [c for c in _backend_codes() if c not in ISSUE_TITLES] == []


def test_titles_are_plain_words_not_identifiers():
    for code, title in ISSUE_TITLES.items():
        assert "_" not in title, code
        assert title[0] == title[0].upper(), code


def test_an_unknown_code_reads_as_a_sentence():
    assert issue_title("brand_new_check") == "Brand new check"


def test_a_validation_issue_serialises_with_its_title():
    d = Issue("error", "bus_v_nom_invalid", "Bus", "b", "v_nom must be > 0 (got 0).").to_dict()
    assert d["title"] == "Bus voltage must be positive"
    assert d["message"].startswith("v_nom")
