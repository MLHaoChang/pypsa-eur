"""LC-46: the Assumptions card's bulk-button words are the chat's words (UX A8)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
CARD = ROOT / "frontend/src/pages/hubDesign/AssumptionsCard.tsx"


def _card_words() -> str:
    m = re.search(r"export const KEEP_ALL_DEFAULTS = '([^']+)'", CARD.read_text())
    assert m, "AssumptionsCard.tsx must export KEEP_ALL_DEFAULTS"
    return m.group(1)


def test_keep_all_defaults_wording_matches_everywhere():
    from services.not_via_chat import NOT_VIA_CHAT

    words = _card_words()
    reason = next(e for e in NOT_VIA_CHAT if e["id"] == "accept_assumed_defaults")["reason"]
    assert words in reason
    for name in ("investment-decision.md", "renewable-profile.md"):
        text = (ROOT / "backend/harness/workflows" / name).read_text()
        assert f'"{words}"' in text, f"{name} must offer the card button {words}"
        assert "Accept all" not in text
